"""
agents/planner.py — Agent "Cerveau" : décompose une requête en DAG JSON.

Génère un plan d'action structuré en stages parallélisables (`stage_id`),
consommé ensuite par `core/dag_runner.py`. Tier fort par défaut
(deepseek-reasoner), timeout 90s. Enrichi dynamiquement via WorkflowBridge
(liste des agents disponibles, y compris les agents custom).
"""
import asyncio
import json
import logging
import time

from agents.base_agent import BaseAgent
from core.fenetres_execution import duree_planner_s
from core.llm_gateway import LLMGateway
from core.state import StateUpdate, TaskPayload
from core.workflow_bridge import WorkflowBridge

logger = logging.getLogger(__name__)

# Instance singleton du pont workflow (chargé une fois, rechargeable via reload())
_workflow_bridge = WorkflowBridge()

class PlannerAgent(BaseAgent):
    """
    Agent 'Cerveau' qui analyse une requête complexe et génère un plan d'action
    au format JSON strict avec parallélisation intelligente par stages.
    Utilise deepseek-reasoner par défaut pour maximiser la qualité logique du plan.
    """
    def __init__(self, llm_gateway: LLMGateway, provider_name: str = "deepseek-reasoner"):
        super().__init__(
            name="planner",
            system_prompt="""Tu es le PlannerAgent, l'architecte du tab5-engine.
Ton unique rôle est de décomposer la demande de l'utilisateur en un plan d'action structuré en lots (stages) pouvant s'exécuter en parallèle ou séquentiellement.
Pour chaque tâche, tu dois définir un 'stage_id' (entier commençant à 1). 
Les tâches ayant le même 'stage_id' s'exécuteront EN PARALLÈLE et ne doivent donc pas dépendre les unes des autres.
Les tâches dépendantes les unes des autres doivent être dans des 'stage_id' successifs (ex: lire un fichier = stage 1, faire la synthèse = stage 2).
Tu dois UNIQUEMENT renvoyer un objet JSON contenant une liste d'étapes ('plan')."""
        )
        self.gateway = llm_gateway
        self.provider_name = provider_name

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        # Importation dynamique de la configuration LLM
        from core.llm_gateway import load_config
        config = load_config()
        session_id = payload.metadata.get("session_id")
        is_healing = payload.metadata.get("is_healing", False)

        # Détermination du fournisseur de modèle approprié
        if self.provider_name in ["leger", "moyen", "fort", "automatique"]:
            _, provider = self.gateway.get_provider_for_tier(self.provider_name, config)
        else:
            try:
                provider = self.gateway.get_provider(self.provider_name)
            except ValueError:
                _, provider = self.gateway.get_provider_for_tier("fort", config)

        import os
        import sys
        is_windows = (sys.platform == 'win32' or os.name == 'nt')
        if is_windows:
            sec_rule = "- Interdiction absolue de modifier des fichiers en dehors du workspace (comme C:\\Windows ou C:\\Windows\\System32)."
            cmd_rule_healing = "- Sous Windows, n'exécute jamais de commandes Unix telles que 'ls', 'grep', 'cat'. Utilise à la place les outils Python ('read_file', 'write_file') ou des commandes Windows ('dir', 'findstr')."
            cmd_rule_std = "- Si tu dois exécuter des commandes système via le terminal, assure-toi d'utiliser des commandes compatibles Windows (ex: 'dir' au lieu de 'ls', 'type' au lieu de 'cat') car la machine hôte tourne sous Windows."
        else:
            sec_rule = "- Interdiction absolue de modifier des fichiers système en dehors du workspace (/config)."
            cmd_rule_healing = "- Sous Linux (Alpine), n'exécute pas de commandes Windows comme 'dir', 'findstr', 'type'. Utilise à la place des commandes Unix standard comme 'ls', 'grep', 'cat', ou de préférence les outils Python."
            cmd_rule_std = "- Si tu dois exécuter des commandes système via le terminal, assure-toi d'utiliser des commandes compatibles Unix/Linux (ex: 'ls' au lieu de 'dir', 'cat' au lieu de 'type') car la machine hôte tourne sous Linux (Alpine)."

        # Détermination du system prompt selon qu'on est en cours d'auto-correction (Self-Healing) ou en planification standard
        if is_healing:
            system_prompt = (
                "Tu es le PlannerAgent en mode Auto-Correction (Self-Healing).\n"
                "Un des stages précédents a échoué. Ton but est d'analyser l'erreur fournie dans l'objectif, "
                "et de générer une ou plusieurs tâches correctives (ex: corriger un fichier, installer un package, créer un dossier manquant, etc.) "
                "afin de résoudre cette erreur et permettre au stage initial d'être ré-exécuté avec succès.\n"
                "Tu dois structurer les tâches correctives sous forme de plan JSON avec 'stage_id' commençant à 1.\n"
                "Tout commentaire ou code généré par les tâches correctives doit comporter des explications/commentaires en français.\n"
                "CONSIGNES DE SÉCURITÉ CRITIQUES :\n"
                f"{sec_rule}\n"
                "- Ne tente jamais d'écrire ou de modifier des exécutables ou des scripts système pour corriger des erreurs de commande terminal.\n"
                f"{cmd_rule_healing}"
            )
        else:
            # [#T188/#T340] Prompt standard externalisé (prompts/agents/planner.md) : le
            # placeholder {{CMD_RULE}} y est remplacé par la règle plateforme
            # (Windows/Linux). La chaîne ci-dessous reste le repli si absent.
            from core.prompt_loader import load_agent_prompt
            default_standard_prompt = (
                "Tu es le PlannerAgent, l'architecte du tab5-engine.\n"
                "Ton rôle est de décomposer la demande de l'utilisateur en un plan d'action structuré en lots (stages) "
                "pouvant s'exécuter en parallèle ou séquentiellement.\n"
                "Tu devez suivre une méthodologie d'ingénierie stricte :\n"
                "1. **Understand** : Comprendre le problème posé.\n"
                "2. **Search/Doc** : Prévoir si besoin de la recherche documentaire ou l'exploration du workspace.\n"
                "3. **Plan** : Décomposer le travail en tâches simples et claires.\n"
                "4. **Verify** : Inclure SYSTÉMATIQUEMENT une tâche de vérification/tests à la fin de ton plan (dernier stage_id) "
                "pour valider que l'objectif a été correctement atteint (ex: exécuter un script de test, compiler, ou vérifier un état).\n\n"
                "DIRECTIVE SDD (Spec-Driven Development) — TEST-FIRST MINDSET :\n"
                "Pour toute tâche impliquant la création ou modification de code (Python, YAML, C++), "
                "tu DOIS planifier dans le DERNIER stage_id une tâche dédiée de vérification.\n"
                "Cette tâche peut être :\n"
                "- Un script de test unitaire minimal (pytest ou assert basique)\n"
                "- Une commande de validation (python -m py_compile, esphome config)\n"
                "- Une vérification de non-régression (lire le fichier modifié et vérifier les sections clés)\n"
                "Le task_id de cette tâche DOIT être préfixé par 'verify_' et le target_agent DOIT être 'reviewer'.\n"
                "Le model_tier de la tâche de vérification DOIT être 'moyen' ou 'fort' pour valider le code.\n\n"
                "Pour chaque tâche, tu définis un 'stage_id' (entier commençant à 1).\n"
                "Les tâches ayant le même 'stage_id' s'exécutent EN PARALLÈLE et ne doivent pas dépendre les unes des autres.\n"
                "Les tâches dépendantes doivent être planifiées dans des stages successifs (ex: Stage 1 pour lire, Stage 2 pour analyser/modifier, Stage 3 pour tester).\n"
                "DIRECTIVE DE PARALLÉLISATION (#T245) — PENSE EN ÉVENTAIL :\n"
                "Dès que le travail est divisible (plusieurs fichiers à lire ou modifier, plusieurs entités à interroger, "
                "plusieurs pistes à explorer), tu DOIS émettre ces sous-tâches indépendantes dans un MÊME stage plutôt "
                "qu'une seule grosse tâche séquentielle : jusqu'à 8 s'exécutent réellement en parallèle.\n"
                "Ces sous-tâches parallèles sont du travail large et jetable : donne-leur 'model_tier': 'leger'.\n"
                "Fais-les converger vers UNE tâche finale d'agrégation, seule dans son stage, qui dépend de toutes "
                "(c'est elle qui synthétise, arbitre et vérifie) : donne-lui 'model_tier': 'fort'.\n"
                "N'invente pas de parallélisme artificiel — si la demande est réellement séquentielle, un plan linéaire reste correct.\n"
                "CONTRAT D'ACCEPTATION (#T253) — DIS COMMENT ON SAURA QUE C'EST FINI :\n"
                "En plus du plan, renseigne 'criteres_acceptation' : la liste des vérifications MÉCANIQUES qui prouveront "
                "que l'objectif est atteint. Elles seront exécutées telles quelles, sans qu'aucun modèle ne juge.\n"
                "Trois types : 'commande' (la commande sort en code 0 — uniquement des commandes de VÉRIFICATION : "
                "pytest, python -m py_compile, ruff check, esphome config, npm run, tsc, node --check), "
                "'fichier_contient' ('valeur' = chemin, 'attendu' = texte ou expression régulière qui doit s'y trouver), "
                "'fichier_existe' ('valeur' = chemin).\n"
                "Sois concret et minimal : 2 à 4 critères qui échouent AVANT le travail et réussissent APRÈS. "
                "Un critère toujours vrai ne sert à rien. Si l'objectif n'est pas vérifiable mécaniquement "
                "(question, analyse, discussion), renvoie une liste vide plutôt que d'inventer.\n"
                "Tous les codes créés ou modifiés par les agents exécutants devront être commentés en français (règle utilisateur).\n\n"
                "DIRECTIVES SUR LES OUTILS ET AGENTS :\n"
                "- Pour toute tâche liée à Home Assistant (état d'un équipement, appel de service, etc.) ou à sa base de données Recorder SQLite, cible obligatoirement 'ha_agent'.\n"
                "- Privilégie l'utilisation des outils spécifiques (comme 'read_file', 'write_file' ou les outils MCP 'mcp_...') plutôt que d'exécuter des commandes système via le terminal.\n"
                "{{CMD_RULE}}"
            )
            system_prompt = load_agent_prompt("planner", default_standard_prompt).replace(
                "{{CMD_RULE}}", cmd_rule_std
            )

        user_prompt = f"Objectif : {payload.task_objective}\nContexte : {payload.relevant_context}"

        # Schéma JSON strict attendu pour le plan avec DAG orienté
        schema = {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {
                                "type": "string",
                                "description": "Un identifiant unique pour cette tâche (sans espaces, ex: 'read_config', 'write_code')"
                            },
                            "objective": {"type": "string", "description": "L'action précise et unitaire à effectuer (ex: 'Créer le fichier X')"},
            "target_agent": {
                                "type": "string",
                                "enum": _workflow_bridge.get_planner_enum(),
                                "description": "L'agent cible. Choisis parmi les agents disponibles définis par le workflow actif."
                            },
                            "model_tier": {
                                "type": "string",
                                "enum": ["leger", "moyen", "fort", "automatique"],
                                "description": "Niveau de modèle requis: 'leger' (routine, local), 'moyen' (standard), 'fort' (réflexion complexe), 'automatique' (sélection automatique)."
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Liste des task_id dont dépend cette tâche. Ces dépendances doivent être terminées avant de démarrer."
                            }
                        },
                        "required": ["task_id", "objective", "target_agent", "model_tier", "depends_on"]
                    }
                },
                # [#T253] Contrat d'acceptation. **Obligatoire au schéma** depuis la
                # mesure du 10/08 : facultatif, il n'a JAMAIS été produit — 7 plans en
                # prod, 0 critère. Un modèle ne renseigne de façon fiable que ce que
                # `required` impose. La liste VIDE reste la réponse correcte pour un
                # objectif non vérifiable mécaniquement : c'est la clé qui devient
                # obligatoire, pas le fait d'inventer des critères.
                "criteres_acceptation": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["commande", "fichier_contient", "fichier_existe"],
                                "description": "Nature de la vérification mécanique."
                            },
                            "valeur": {
                                "type": "string",
                                "description": "La commande à exécuter, ou le chemin du fichier concerné."
                            },
                            "attendu": {
                                "type": "string",
                                "description": "Pour 'fichier_contient' : le texte ou motif qui doit s'y trouver."
                            },
                            "description": {
                                "type": "string",
                                "description": "Ce que ce critère prouve, en une phrase."
                            }
                        },
                        "required": ["type", "valeur"]
                    },
                    "description": "OBLIGATOIRE. Vérifications mécaniques prouvant que l'objectif est atteint. Liste vide [] si l'objectif n'est pas vérifiable mécaniquement (question, analyse, discussion) — mais la clé doit toujours être présente."
                },
                # [Incident du 17/08] Substitution TRACÉE d'un critère du contrat
                # d'origine devenu impossible parce que le plan correctif a
                # légitimement changé d'approche (autre nom de fichier, autre
                # chemin). Jamais de remplacement silencieux : chaque
                # substitution doit être déclarée ici, elle sera journalisée,
                # et elle n'est honorée que si le critère remplaçant figure
                # dans 'criteres_acceptation' et n'était pas déjà satisfait
                # avant le travail. Un plan qui ne substitue rien laisse [].
                "substitutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "origine": {
                                "type": "object",
                                "description": "Le critère du contrat d'origine remplacé (mêmes champs qu'un critère).",
                            },
                            "remplacant": {
                                "type": "object",
                                "description": "Le critère qui le remplace — il DOIT aussi figurer dans criteres_acceptation.",
                            },
                            "motif": {
                                "type": "string",
                                "description": "Pourquoi le critère d'origine est devenu impossible (ex: changement de chemin décidé par la correction).",
                            },
                        },
                        "required": ["origine", "remplacant"],
                    },
                    "description": "Facultatif. Substitutions déclarées de critères du contrat d'origine, chacune tracée et journalisée. Liste vide par défaut.",
                }
            },
            "required": ["plan", "criteres_acceptation"]
        }

        logger.info("[PLANNER] Réflexion sur le plan d'action (V5 DAG)...")

        try:
            # Enrichir le prompt du Planner avec la liste dynamique des agents du workflow
            enriched_prompt = _workflow_bridge.inject_into_planner_prompt(system_prompt)

            # [#T277] Fenêtre du Planner, calculée pour que la cascade puisse
            # réellement BASCULER. Elle valait 90 s en dur, alors qu'UNE tentative
            # provider dure jusqu'à 120 s : le premier modèle lent consommait
            # toute la fenêtre et les suivants n'étaient jamais essayés. Mesuré
            # deux fois en prod le 11/08 — une seule tentative, plan vide, et un
            # message d'erreur vide puisque `asyncio.TimeoutError` n'en porte pas.
            fenetre_planner = duree_planner_s()
            try:
                response_json = await asyncio.wait_for(
                    provider.generate_structured_async(
                        system_prompt=enriched_prompt + "\nFormat de sortie OBLIGATORY (JSON): " + json.dumps(schema),
                        user_prompt=user_prompt,
                        schema=schema,
                        session_id=session_id,
                    ),
                    timeout=fenetre_planner,
                )
            except TimeoutError as expiration:
                # Sans ce ré-emballage, le journal affiche « Échec de la génération
                # du plan : » suivi de RIEN, et la cause doit être devinée dans une
                # autre ligne, 30 s plus loin.
                raise TimeoutError(
                    f"Aucun modèle n'a répondu dans la fenêtre du Planner "
                    f"({fenetre_planner:.0f}s, cascade comprise)."
                ) from expiration

            plan = response_json.get("plan", [])
            if not plan:
                raise ValueError("Le Planner a généré un DAG vide (plan: []).")

            new_tasks = []

            # Calcul dynamique des stage_id pour la rétrocompatibilité (Dashboard V4)
            task_by_id = {step["task_id"]: step for step in plan}
            memo_stages = {}

            def get_stage_id(t_id: str, visited: set) -> int:
                if t_id in memo_stages:
                    return memo_stages[t_id]
                if t_id in visited:
                    return 1  # Sécurité contre les cycles
                visited.add(t_id)
                step_data = task_by_id.get(t_id)
                if not step_data:
                    return 1
                deps = step_data.get("depends_on", [])
                if not deps:
                    memo_stages[t_id] = 1
                    return 1
                max_dep_stage = 0
                for dep_id in deps:
                    max_dep_stage = max(max_dep_stage, get_stage_id(dep_id, visited.copy()))
                memo_stages[t_id] = max_dep_stage + 1
                return memo_stages[t_id]

            # Injection du stage_id calculé pour chaque tâche
            for step in plan:
                step["stage_id"] = get_stage_id(step["task_id"], set())

            # Transformation du JSON en liste de TaskPayload
            for step in plan:
                new_tasks.append(
                    TaskPayload(
                        task_objective=step["objective"],
                        relevant_context=f"Fait partie du plan global : {payload.task_objective}",
                        task_id=step["task_id"],
                        depends_on=step.get("depends_on", []),
                        metadata={
                            "task_id": step["task_id"],
                            "depends_on": step.get("depends_on", []),
                            "stage_id": step.get("stage_id", 1),
                            "target_agent": step.get("target_agent", "executor"),
                            "model_tier": step.get("model_tier", "moyen"),
                            "session_id": session_id
                        }
                    )
                )

            # [SDD V5.2] Garde-fou : injection automatique d'une tâche verify_* si absente
            has_verify = any(t.task_id and t.task_id.startswith("verify_") for t in new_tasks)
            if not has_verify and len(new_tasks) > 1:
                # Calcul du stage maximal pour injecter la vérification au dernier niveau + 1
                max_stage = max(t.metadata.get("stage_id", 1) for t in new_tasks)
                # Collecte des task_id du dernier stage pour les dépendances
                last_stage_ids = [
                    t.task_id for t in new_tasks
                    if t.metadata.get("stage_id") == max_stage and t.task_id
                ]
                verify_task = TaskPayload(
                    task_objective=(
                        f"Vérification automatique SDD : Valider que les modifications "
                        f"apportées par le plan '{payload.task_objective[:100]}' sont correctes. "
                        f"Lire les fichiers modifiés et vérifier la syntaxe (py_compile ou lecture)."
                    ),
                    relevant_context=f"Fait partie du plan global : {payload.task_objective}",
                    task_id=f"verify_sdd_{int(time.time())}",
                    depends_on=last_stage_ids,
                    metadata={
                        "task_id": f"verify_sdd_{int(time.time())}",
                        "depends_on": last_stage_ids,
                        "stage_id": max_stage + 1,
                        "target_agent": "reviewer",
                        "model_tier": "moyen",
                        "session_id": session_id,
                        "is_sdd_verify": True
                    }
                )
                new_tasks.append(verify_task)
                logger.info(f"[PLANNER] [SDD] Tâche de vérification auto-injectée : verify_sdd (stage {max_stage + 1})")

            # [#T245] Le prompt demande un plan en éventail ; la normalisation le
            # garantit : maps parallèles sur le tier léger, nœud d'agrégation sur
            # le tier fort. Déterministe, donc vérifiable — un prompt, non.
            from core.dag.node_tier_policy import normaliser_tiers_du_plan
            rapport_tiers = normaliser_tiers_du_plan(new_tasks, config)
            if rapport_tiers["maps"] or rapport_tiers["reduce"]:
                logger.info(
                    f"[PLANNER] [T245] Tiers normalisés : {len(rapport_tiers['maps'])} map(s) en tier léger, "
                    f"reduce = {rapport_tiers['reduce']}, {rapport_tiers['inchangees']} tâche(s) inchangée(s)."
                )

            # [#T253] Contrat d'acceptation transporté dans les metadata du
            # StateUpdate : la boucle de revue le retrouvera dans l'historique.
            from core.acceptance_contract import (
                CLE_CONTRAT,
                CLE_ETAT_INITIAL,
                CLE_SUBSTITUTIONS,
                constater_etat_initial,
                normaliser_criteres,
            )
            criteres = normaliser_criteres(response_json.get("criteres_acceptation"))

            # [Incident du 17/08] Constat de l'état des critères fichier À LA
            # POSE DU PLAN, donc avant tout travail du lot : à l'évaluation,
            # tout critère déjà satisfait à cet instant sera écarté du verdict
            # comme « satisfait d'avance » — un critère vrai avant le travail
            # n'est pas une preuve de travail. C'est ce constat qui rend
            # inopérant un contrat correctif tautologique du type « vérifier
            # qu'un fichier préexistant est accessible ».
            etat_initial = constater_etat_initial(criteres) if criteres else {}

            # [Incident du 17/08] Substitutions DÉCLARÉES par ce plan : elles
            # voyagent avec le contrat et seront validées puis journalisées par
            # la boucle de revue — jamais appliquées silencieusement ici.
            substitutions_declarees = [
                s for s in (response_json.get("substitutions") or [])
                if isinstance(s, dict) and s.get("origine") and s.get("remplacant")
            ]
            for substitution in substitutions_declarees:
                logger.warning(
                    "[PLANNER] [CONTRAT] Substitution déclarée par le plan : "
                    f"{substitution['origine']!r} → {substitution['remplacant']!r} "
                    f"(motif : {substitution.get('motif') or 'non renseigné'}). "
                    "Elle sera validée et tracée à l'évaluation."
                )

            if criteres:
                logger.info(
                    f"[PLANNER] [T253] Contrat d'acceptation : {len(criteres)} critère(s) — "
                    + " | ".join(c.libelle() for c in criteres[:4])
                )
            else:
                # Journalisé explicitement : sans cette ligne, « 0 contrat évalué »
                # ne permet pas de distinguer « le Planner n'en produit pas » de
                # « la revue n'a pas tourné ». Les deux se sont produits le 10/08.
                logger.warning(
                    "[PLANNER] [T253] Aucun critère d'acceptation produit pour ce plan "
                    "— la revue retombera sur l'avis du Reviewer."
                )

            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data=f"Plan DAG généré avec {len(new_tasks)} étapes réparties sur {max((step.get('stage_id', 1) for step in plan), default=1)} niveaux logiques.",
                next_agent="END",
                new_tasks=new_tasks,
                metadata={
                    CLE_CONTRAT: [
                        {"type": c.type, "valeur": c.valeur, "attendu": c.attendu, "description": c.description}
                        for c in criteres
                    ],
                    # [Incident du 17/08] Le constat d'avant-travail et les
                    # substitutions déclarées voyagent avec le contrat.
                    CLE_ETAT_INITIAL: etat_initial,
                    CLE_SUBSTITUTIONS: substitutions_declarees,
                } if criteres else {},
            )

        except Exception as e:
            # [#T277] `type(e).__name__` : une exception sans message (asyncio
            # TimeoutError, CancelledError) produisait une ligne de journal qui
            # se terminait par « : » et ne disait rien de la cause.
            logger.error(f"[PLANNER] Échec de la génération du plan : {type(e).__name__}: {e}")
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data=None,
                next_agent="END",
                error_message=str(e)
            )
