"""
agents/executor.py — Agent d'exécution générique (boucle ReAct).

Exécute des actions techniques via les outils du ToolRegistry, avec mode
Sandbox (dry_run) pour les outils destructeurs, retries à backoff exponentiel
(MAX_TOOL_RETRIES=2), et un micro-cycle de revue locale avant de remonter au
DAG. Classe de base héritée par `HACommandAgent` et `AntigravityAgent`
(core/mcp_tools).
"""
import asyncio
import json
import logging
import unicodedata

from agents.base_agent import BaseAgent
from core.errors import classify_error
from core.llm_gateway import LLMGateway
from core.state import StateUpdate, TaskPayload
from tools.sanitizer import OutputSanitizer
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _sans_accents(texte: str) -> str:
    """
    [#T274] Normalise les accents pour la comparaison de verbes.

    Les objectifs de tâches arrivent tantôt accentués (« vérifier »), tantôt non
    (plan généré par un modèle, transcription vocale). Comparer sans accent évite
    qu'un contrôle de sécurité se comporte différemment selon l'orthographe.
    """
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


# Nombre maximum de retries internes avant de remonter au DAG/Self-Healing
MAX_TOOL_RETRIES = 2
# Délai de base entre les retries (secondes)
BASE_RETRY_DELAY = 1.0
# [#T341] Borne haute d'appels d'outil par TOUR de boucle ReAct (et non par
# session : le ToolRegistry plafonne déjà à 150 appels/session par outil via
# `_TOOL_RATE_LIMITS["default"]` — ce correctif s'articule en amont de ce
# dernier filet, il ne le remplace pas). Mesuré le 12/08, session
# `chat_aa88ff7bed` : une réponse de modèle contenait 2 181 tool_calls pour
# 2 tours (1 500 en une seconde) pour une question domotique. La dizaine est
# l'ordre de grandeur légitime observé dans les traces (lire 3 entités,
# écrire 2 fichiers) ; 20 laisse de la marge aux plans multi-étapes sans
# jamais approcher du millier.
MAX_TOOL_CALLS_PER_TURN = 20

class ExecutorAgent(BaseAgent):
    """
    Agent autonome chargé d'exécuter des actions techniques en utilisant les outils enregistrés.
    Supporte le mode Sandbox (dry_run) pour les outils destructeurs .
    """
    def __init__(self, llm_gateway: LLMGateway, tool_registry: ToolRegistry, provider_name: str = "deepseek", sandbox_mode: bool = False, allowed_tools: list[str] | None = None):
        import os
        import sys
        is_windows = (sys.platform == 'win32' or os.name == 'nt')
        if is_windows:
            os_rules = """2. COMPATIBILITÉ WINDOWS : La machine hôte tourne sous Windows. N'utilise jamais de commandes terminal Unix comme 'ls', 'grep' ou 'cat' via 'run_terminal_command'. Utilise à la place les outils de manipulation de fichier de Python ('read_file', 'write_file') ou des commandes Windows natives ('dir', 'findstr', 'type').
3. SÉCURITÉ DU SYSTÈME : Ne modifie jamais de fichiers système Windows (comme C:\\Windows ou C:\\Windows\\System32). Tout fichier créé ou modifié doit l'être uniquement dans le répertoire de l'application ou du workspace de l'utilisateur (e:\\AuxFilsDesIdees).
4. CHEMINS : Le workspace principal est 'e:\\AuxFilsDesIdees'. Le tab5-engine (code Python) est dans 'e:\\AuxFilsDesIdees\\moteur_agents\\'. Les fichiers core/ sont donc dans 'e:\\AuxFilsDesIdees\\moteur_agents\\core\\'. Utilise TOUJOURS des chemins absolus."""
        else:
            os_rules = """2. COMPATIBILITÉ LINUX : La machine hôte tourne sous Linux (Alpine). N'exécute pas de commandes Windows comme 'dir', 'type', 'findstr' via 'run_terminal_command'. Utilise à la place des commandes Unix standard comme 'ls', 'cat', 'grep', ou de préférence les outils intégrés Python.
3. SÉCURITÉ DU SYSTÈME : Ne tente pas de modifier les répertoires système protégés. Tout fichier créé ou modifié doit l'être uniquement dans le workspace (/config).
4. CHEMINS : Le workspace principal est '/config'. Le tab5-engine (code Python) est dans '/config/moteur-master/'. Les fichiers core/ sont donc dans '/config/moteur-master/core/'. Utilise TOUJOURS des chemins absolus."""

        from core.prompt_loader import load_agent_prompt
        default_prompt = (
            "Tu es l'ExecutorAgent. Ton but est d'accomplir la tâche technique demandée en utilisant tes outils.\n"
            "CRITIQUE : Analyse attentivement la section 'RÉSULTATS PHASES PRÉCÉDENTES' dans le contexte.\n"
            "Si la tâche consiste à écrire ou synthétiser des données (par exemple, fusionner des contenus de fichiers) "
            "et que les contenus de ces fichiers ont DÉJÀ été lus et figurent dans la section 'RÉSULTATS PHASES PRÉCÉDENTES', "
            "tu ne dois pas les relire.\n"
            "Utilise DIRECTEMENT ces contenus du contexte et appelle uniquement 'write_file' pour enregistrer le résultat final. "
            "Ne fais aucun appel à 'read_file' dans ce cas.\n\n"
            "CONSIGNES DE SÉCURITÉ ET DE COMPATIBILITÉ CRITIQUES :\n"
            "1. PRIVILÉGIE LES OUTILS MCP : Si des outils MCP (commençant par 'mcp_') sont enregistrés et correspondent à ta tâche "
            "(par exemple pour Home Assistant ou SQLite), tu DOIS les utiliser en priorité absolue plutôt que de lancer des "
            "commandes shell ou de développer des scripts personnalisés.\n"
            "{{OS_RULES}}\n"
            "5. SOIS PÉDAGOGUE : Explique brièvement en français les opérations effectuées.\n"
            "6. VÉRIFICATION OBLIGATOIRE : Après toute modification d'un fichier .py via 'write_file', appelle 'run_tests' "
            "sur le fichier de test correspondant (ou 'tests/' si aucun fichier ciblé n'est identifiable) AVANT de conclure. "
            "Si les tests échouent, corrige et relance-les avant de terminer la tâche."
        )
        prompt = load_agent_prompt("executor", default_prompt).replace("{{OS_RULES}}", os_rules)

        super().__init__(
            name="executor",
            system_prompt=prompt
        )
        # Conservé pour interpoler {{OS_RULES}} à chaque invoke (relecture Markdown).
        self._os_rules = os_rules
        self.gateway = llm_gateway
        self.tool_registry = tool_registry
        self.provider_name = provider_name
        self.sandbox_mode = sandbox_mode

        # [#T233] Liste des outils autorisés (absent/None = tous les outils,
        # [] = aucun). Déclarée dans config.json (custom_agents) ou
        # agents_workflows.json ; synchronisée dans le registre partagé à
        # chaque invoke (les agents custom changent de nom après construction).
        self.allowed_tools = allowed_tools

        # Sanitizer de sorties d'outils (masquage des secrets)
        self._sanitizer = OutputSanitizer()

        # Encapsulation sandbox si le mode dry_run est actif
        self._sandbox = None
        if sandbox_mode:
            from tools.sandbox import SandboxWrapper
            self._sandbox = SandboxWrapper(tool_registry, dry_run=True)
            self.tool_registry = self._sandbox
            logger.info(f"[{self.name}] Mode Sandbox activé : les outils destructeurs généreront des diffs preview.")

    def _prompt_systeme_actuel(self) -> str:
        """Relit le Markdown à chaque appel — un PUT IHM ne doit pas exiger un restart.

        `self.name` distingue executor et ha_agent (ce dernier écrase le nom
        après super().__init__). Le cache mtime de prompt_loader suffit :
        fichier inchangé = pas d'I/O disque.
        """
        from core.prompt_loader import load_agent_prompt
        texte = load_agent_prompt(self.name, self.system_prompt)
        os_rules = getattr(self, "_os_rules", "")
        if os_rules and "{{OS_RULES}}" in texte:
            texte = texte.replace("{{OS_RULES}}", os_rules)
        return texte

    # ──────────────────────────────────────────────────────────────────
    # [#T274] « Aucun outil appelé » : quand est-ce vraiment une faute ?
    # ──────────────────────────────────────────────────────────────────
    # Une MUTATION ne peut pas se faire sans outil : si l'agent prétend avoir
    # écrit un fichier sans jamais appeler `write_file`, il ment, et le contrôle
    # doit échouer. C'est sa raison d'être.
    _VERBES_MUTATION = (
        "cree", "creer", "ecris", "ecrire", "ecrit", "modifie", "modifier",
        "supprime", "supprimer", "efface", "effacer", "renomme", "renommer",
        "deplace", "deplacer", "ajoute", "ajouter", "remplace", "remplacer",
        "installe", "installer", "deploie", "deployer", "execute", "executer",
        "lance", "lancer", "commit", "push",
        "create", "write", "modify", "delete", "remove", "rename", "move",
        "append", "install", "deploy", "run ", "execute",
    )
    # Une LECTURE ou une VÉRIFICATION, elle, peut légitimement se conclure sans
    # outil : si la donnée est déjà dans le contexte (résultat d'une tâche
    # amont), la relire serait un appel inutile — et le prompt système DEMANDE
    # explicitement de ne pas le faire (« Utilise DIRECTEMENT ces contenus du
    # contexte […] Ne fais aucun appel à 'read_file' dans ce cas »).
    _VERBES_LECTURE = (
        "lis", "lire", "relis", "relire", "verifie", "verifier", "valide",
        "valider", "confirme", "confirmer", "controle", "controler",
        "compare", "comparer", "analyse", "analyser", "resume", "resumer",
        "read", "verify", "check", "validate", "confirm", "compare", "analyze",
    )
    # En dessous de ce volume, le « contexte » ne porte aucune donnée
    # exploitable (chaîne vide, « None », un mot isolé) : l'agent doit alors
    # aller chercher l'information au lieu de l'inventer.
    _CONTEXTE_MINIMAL = 20

    @classmethod
    def exige_un_appel_d_outil(cls, task_objective: str, relevant_context: str | None) -> bool:
        """
        [#T274] Décide si l'absence d'appel d'outil doit faire échouer la tâche.

        L'ancienne règle listait aussi des SUBSTANTIFS (« fichier », « file »,
        « dossier », « git ») : toute tâche mentionnant un fichier exigeait donc
        un appel d'outil, y compris une simple vérification. Mesuré en production
        le 11/08 : sur un plan dont l'écriture ET la relecture avaient réussi, la
        tâche `verify_preuve` a échoué sur ce contrôle alors qu'elle avait la
        donnée sous les yeux — et comme `_finalize_git` exige que TOUTES les
        tâches soient en succès, le travail réussi a été annulé (rollback).

        Le contrôle punissait exactement le comportement que le prompt système
        réclame. Deux règles opposées dans le même agent.
        """
        objectif = _sans_accents((task_objective or "").lower())
        contexte = (relevant_context or "").strip()
        outil_requis_faute_de_contexte = len(contexte) < cls._CONTEXTE_MINIMAL

        # 1. Le VERBE DE TÊTE l'emporte. « Vérifier que le fichier a bien été
        #    créé » est une vérification, pas une création : sans cette règle, le
        #    « créé » de la subordonnée la ferait classer en mutation et on
        #    reproduirait exactement le bug qu'on corrige.
        tete = " ".join(objectif.split()[:4])
        if any(v in tete for v in cls._VERBES_LECTURE):
            return outil_requis_faute_de_contexte

        # 2. Mutation : un outil est indispensable, sans exception.
        if any(v in objectif for v in cls._VERBES_MUTATION):
            return True

        # 3. Lecture / vérification mentionnée plus loin dans la phrase.
        if any(v in objectif for v in cls._VERBES_LECTURE):
            return outil_requis_faute_de_contexte

        # 4. Ni mutation ni lecture explicite : on n'exige rien (inchangé).
        return False

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        # [#T233] Synchronisation des permissions d'outils de l'agent : le
        # registre est partagé, la restriction (ou son absence) se re-déclare à
        # chaque invoke car un agent custom peut changer de nom APRÈS
        # construction (cf. core/custom_agents.py, core/factory.py).
        self.tool_registry.set_agent_allowed_tools(self.name, self.allowed_tools)

        # [Raccourci Déterministe V5.1 - Zero-LLM Latency]
        direct_call = payload.metadata.get("direct_tool_call")
        if direct_call:
            func_name = direct_call.get("name")
            kwargs = direct_call.get("arguments", {})
            logger.info(f"[{self.name}] Court-circuit déterministe détecté : exécution directe de '{func_name}' sans LLM.")
            try:
                res = await self.tool_registry.execute(func_name, kwargs, agent_name=self.name)
                logger.info(f"[{self.name}] Résultat court-circuit direct : {res}")

                # Détecter une erreur de l'outil pour propager au Self-Healing
                if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                    return StateUpdate(
                        agent_name=self.name,
                        status="error",
                        result_data=res,
                        next_agent="END",
                        error_message=res
                    )
                return StateUpdate(
                    agent_name=self.name,
                    status="success",
                    result_data=f"Résultat '{func_name}' : {res}",
                    next_agent="END"
                )
            except Exception as e:
                err_msg = f"Exception lors du court-circuit de l'outil '{func_name}' : {e}"
                logger.error(f"[{self.name}] {err_msg}")
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    result_data=err_msg,
                    next_agent="END",
                    error_message=err_msg
                )

        from core.llm_gateway import load_config
        config = load_config()
        tier = payload.metadata.get("model_tier", self.provider_name)

        resolved_model_name, provider = self.gateway.get_provider_for_tier(tier, config)
        logger.info(f"[{self.name}] Résolution du modèle pour le Tier '{tier}' : {resolved_model_name}")

        user_prompt = f"Objectif : {payload.task_objective}\nContexte pertinent : {payload.relevant_context}"
        tools_schemas = self.tool_registry.get_all_schemas(payload.task_objective, agent_name=self.name)

        logger.info(f"[{self.name}] Analyse de la requête et décision...")
        prompt_systeme = self._prompt_systeme_actuel()
        logger.info(f"[{self.name}] System Prompt: {prompt_systeme}")
        logger.info(f"[{self.name}] User Prompt: {user_prompt}")

        session_id = payload.metadata.get("session_id")

        # Initialisation de l'historique des messages pour la boucle ReAct multi-turn
        messages = [
            {"role": "system", "content": prompt_systeme},
            {"role": "user", "content": user_prompt}
        ]

        max_turns = payload.metadata.get("max_turns", 10)
        use_search_grounding = payload.metadata.get("use_search_grounding", False)

        # Lancement de la boucle ReAct initiale
        last_results, final_text_response, tool_executed, last_tool_error = await self._execute_react_loop(
            messages=messages,
            user_prompt=user_prompt,
            provider=provider,
            tools_schemas=tools_schemas,
            max_turns=max_turns,
            session_id=session_id,
            use_search_grounding=use_search_grounding,
            is_correction=False
        )

        # Validation de sécurité : si aucun outil n'a été exécuté sur l'ensemble des tours
        # et que la tâche requiert une action, on lève une erreur.
        if not tool_executed:
            if self.exige_un_appel_d_outil(payload.task_objective, payload.relevant_context):
                logger.warning(f"[{self.name}] Aucun outil n'a été exécuté pour une tâche requérant une action. Retour d'une erreur.")
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    result_data=final_text_response or "Aucune réponse",
                    next_agent="END",
                    error_message="L'agent exécuteur n'a appelé aucun outil pour réaliser cette tâche. La réponse textuelle était : " + (final_text_response or "")
                )

        # Si un outil a échoué en dernier et n'a pas été corrigé
        if last_tool_error:
            logger.error(f"[{self.name}] Échec final de la tâche après auto-correction locale infructueuse. Dernière erreur : {last_tool_error}")
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data="\n".join(last_results) if last_results else last_tool_error,
                next_agent="END",
                error_message=last_tool_error
            )

        # Micro-cycle de revue locale ReAct-Review (Phase 4)
        is_healing_or_correction = (
            payload.metadata.get("is_healing", False)
            or payload.metadata.get("is_review_correction", False)
            or payload.metadata.get("is_local_review_correction", False)
        )

        max_local_review_rounds = 2
        local_round = 1

        while tool_executed and self.name != "ha_agent" and not last_tool_error and not is_healing_or_correction and local_round <= max_local_review_rounds:
            logger.info(f"[{self.name}] [LOCAL-REVIEW] Démarrage du round de revue locale {local_round}/{max_local_review_rounds}...")

            # Instanciation locale du ReviewerAgent
            from agents.reviewer import ReviewerAgent
            reviewer = ReviewerAgent(llm_gateway=self.gateway, provider_name="moyen")

            review_payload = TaskPayload(
                task_objective=payload.task_objective,
                relevant_context=(
                    f"Voici les modifications apportées par l'agent exécuteur :\n"
                    f"{chr(10).join(last_results) if last_results else final_text_response}"
                ),
                metadata={
                    "session_id": session_id,
                    "model_tier": "moyen"
                }
            )

            review_update = await reviewer.invoke(review_payload)

            if review_update.status == "success":
                logger.info(f"[{self.name}] [LOCAL-REVIEW] ✅ Verdict : Code validé et approuvé localement au round {local_round}.")
                local_review_feedback = review_update.result_data
                break
            else:
                severity = review_update.metadata.get("severity", "major")
                feedback = review_update.error_message or review_update.result_data
                logger.warning(f"[{self.name}] [LOCAL-REVIEW] ❌ Verdict : Code REJETÉ localement (round {local_round}, sévérité: {severity}).")

                if local_round >= max_local_review_rounds:
                    logger.error(f"[{self.name}] [LOCAL-REVIEW] Nombre maximum de rounds de revue locale atteint. Échec.")
                    return StateUpdate(
                        agent_name=self.name,
                        status="error",
                        result_data=feedback,
                        next_agent="END",
                        error_message=f"Le code a été rejeté par la revue locale au round {local_round} : {feedback}"
                    )

                # Relancer la boucle ReAct pour corriger localement
                logger.info(f"[{self.name}] [LOCAL-REVIEW] Relancement de la boucle ReAct pour appliquer les corrections demandées...")
                local_round += 1

                messages.append({
                    "role": "user",
                    "content": (
                        f"Ton travail précédent a été REJETÉ par le Reviewer local avec le retour suivant :\n"
                        f"{feedback}\n\n"
                        f"Corrige immédiatement ces points dans le code en utilisant tes outils (ex: ré-écriture de fichiers) "
                        f"puis termine pour soumettre les corrections."
                    )
                })

                # Relancer la boucle corrective unique
                last_results_corr, final_text_response_corr, tool_executed_corr, last_tool_error = await self._execute_react_loop(
                    messages=messages,
                    user_prompt=user_prompt,
                    provider=provider,
                    tools_schemas=tools_schemas,
                    max_turns=max_turns,
                    session_id=session_id,
                    use_search_grounding=use_search_grounding,
                    is_correction=True
                )

                if last_results_corr:
                    last_results.extend(last_results_corr)
                elif final_text_response_corr:
                    last_results.append(final_text_response_corr)
                    final_text_response = final_text_response_corr

                if last_tool_error:
                    logger.error(f"[{self.name}] [LOCAL-REVIEW-CORRECTION] Échec de la boucle corrective suite à erreur d'outil : {last_tool_error}")
                    break

        # [#T311] La réponse rédigée par le modèle prime sur l'écho brut des outils.
        #
        # Cette ligne retournait le dump des résultats d'outils dès qu'un seul outil
        # avait tourné, et jetait `final_text_response` — c'est-à-dire la seule chose
        # que l'utilisateur avait demandée. Mesuré le 11/08 sur « Qu'est-ce que j'ai
        # de prévu demain ? » : l'executor appelait bien `get_calendar_events`,
        # rédigeait sa réponse au tour 5 (131 tokens de complétion, aucun appel
        # d'outil = sortie de boucle par `break`), et l'utilisateur recevait
        # « Résultat 'get_calendar_events' : 📅 15 événement(s) à venir… » — la liste
        # brute de tout le calendrier, sans le filtre « demain » qu'il avait demandé.
        # Même symptôme sur les scénarios mail, système et analyse comparative.
        #
        # La boucle ReAct ne pose `final_text_response` que lorsque le modèle répond
        # SANS appeler d'outil (cf. `_execute_react_loop`, étape 3) : sa présence
        # signifie donc « le modèle a fini et a rédigé ». Quand elle est vide (tours
        # épuisés), l'écho brut reste le seul contenu disponible et on le garde.
        # La trace des outils n'est jamais perdue : elle part dans les métadonnées.
        if final_text_response and final_text_response.strip():
            result_data = final_text_response
        else:
            result_data = "\n".join(last_results)

        # Ajout des informations sandbox au résultat si actif
        sandbox_info = {}
        if self._sandbox:
            sandbox_info = self._sandbox.get_pending_summary()
            if sandbox_info.get("pending_writes", 0) > 0:
                result_data += f"\n\n[SANDBOX] {sandbox_info['pending_writes']} écriture(s) en attente de validation."

        metadata = {"sandbox": sandbox_info} if sandbox_info else {}
        if 'local_review_feedback' in locals() and local_review_feedback:
            metadata["local_review"] = local_review_feedback
        # [#T311] La trace brute des outils reste consultable (IHM, debug, audit)
        # même quand la réponse rendue est la synthèse du modèle.
        if last_results:
            metadata["tool_trace"] = last_results

        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=result_data,
            next_agent="END",
            metadata=metadata
        )

    async def _execute_react_loop(
        self,
        messages: list,
        user_prompt: str,
        provider,
        tools_schemas: list,
        max_turns: int,
        session_id: str,
        use_search_grounding: bool,
        is_correction: bool = False
    ) -> tuple[list, str, bool, str | None]:
        """
        Boucle ReAct unique, réutilisée en mode initial et correction de revue.
        """
        tool_executed = False
        last_results = []
        final_text_response = ""
        last_tool_error = None
        prefix = "[LOCAL-REVIEW-CORRECTION]" if is_correction else ""

        for turn in range(max_turns):
            logger.info(f"[{self.name}]{prefix} Lancement du tour {turn + 1}/{max_turns} de la boucle ReAct...")

            response = await provider.generate_async(
                system_prompt=self._prompt_systeme_actuel(),
                user_prompt=user_prompt,
                tools=tools_schemas,
                session_id=session_id,
                messages=messages,
                use_search_grounding=use_search_grounding,
                # [T287] L'Executor écrit du code : il reçoit CONVENTIONS.md.
                # Elles ne partent plus sur les chemins chat et vocal.
                conventions_projet=True,
            )

            # Étape 1 : Le LLM a-t-il décidé d'appeler un outil via l'API native (tool_calls) ?
            if isinstance(response, dict) and "tool_calls" in response and response["tool_calls"]:
                tool_calls = response["tool_calls"]
                assistant_message = {
                    "role": "assistant",
                    "content": response.get("content") or "",
                    "tool_calls": tool_calls
                }
                messages.append(assistant_message)

                turn_results = []
                # [#T341] Borne et déduplication des appels d'un même tour.
                # Mesuré le 12/08 : 2 181 `tool_calls` (1 500 en une seconde)
                # pour une seule question. Le ToolRegistry plafonnait bien (150
                # exécutions réelles), mais les 2 031 échecs rate_limit étaient
                # renvoyés au modèle et la boucle continuait. Deux garde-fous :
                # 1) déduplication sur (nom, arguments EXACTS) — jamais le nom
                #    seul : get_state('salon') puis get_state('chambre') sont
                #    deux appels légitimes ;
                # 2) borne haute MAX_TOOL_CALLS_PER_TURN pour les appels
                #    distincts en rafale.
                # Chaque appel écarté reçoit quand même un message `tool`
                # (l'API exige une réponse pour CHAQUE tool_call du message
                # assistant) : le modèle sait ce qui a été écarté et pourquoi —
                # jamais de troncature silencieuse.
                vus_dans_le_tour: set[tuple] = set()
                nb_ecartes = 0
                nb_doublons = 0
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    tool_call_id = tool_call.get("id", "call_123")
                    kwargs_str = tool_call["function"]["arguments"]
                    try:
                        kwargs = json.loads(kwargs_str)
                    except Exception as e:
                        res = f"Erreur de décodage des arguments JSON : {e}"
                        kwargs = {}

                    # Clé de déduplication : arguments normalisés (l'ordre des
                    # clés JSON ne doit pas créer de faux doublons), chaîne
                    # brute si le JSON est invalide.
                    try:
                        cle_appel = (func_name, json.dumps(kwargs, sort_keys=True, default=str))
                    except Exception:
                        cle_appel = (func_name, kwargs_str)

                    if cle_appel in vus_dans_le_tour:
                        nb_doublons += 1
                        raison = (
                            f"doublon exact de {func_name}("
                            f"{json.dumps(kwargs, ensure_ascii=False)}), déjà exécuté "
                            "avec exactement les mêmes arguments dans ce tour"
                        )
                    elif len(vus_dans_le_tour) >= MAX_TOOL_CALLS_PER_TURN:
                        raison = f"borne de {MAX_TOOL_CALLS_PER_TURN} appels d'outil par tour atteinte"
                    else:
                        raison = None

                    if raison:
                        nb_ecartes += 1
                        logger.warning(
                            f"[{self.name}]{prefix} Appel d'outil écarté ({raison}) : {func_name}"
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": func_name,
                            "content": (
                                f"⚠️ Appel d'outil non exécuté : {raison}. "
                                f"{nb_ecartes} appel(s) écarté(s) au total ce tour. "
                                "Termine le tour ; relance au tour suivant les appels "
                                "encore nécessaires."
                            )
                        })
                        continue

                    vus_dans_le_tour.add(cle_appel)

                    logger.info(f"[{self.name}]{prefix} Appel d'outil détecté : {func_name}")

                    res = await self._execute_tool_with_retry(func_name, kwargs)
                    tool_executed = True

                    res_str = str(res)
                    res_str = self._sanitizer.sanitize(res_str, source=f"tool:{func_name}")

                    if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                        agent_error = classify_error(res, source=f"tool:{func_name}")
                        logger.warning(f"[{self.name}]{prefix} Erreur d'outil détectée ({agent_error.category.value}) de {func_name}: {res}. Tentative d'auto-correction locale...")
                        last_tool_error = f"Erreur dans l'outil '{func_name}' : {res_str}"

                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": func_name,
                            "content": f"Erreur lors de l'exécution : {res_str}. Corrige cette erreur et réessaie."
                        }
                        messages.append(tool_message)
                    else:
                        last_tool_error = None
                        turn_results.append(f"Résultat '{func_name}' : {res_str}")

                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": func_name,
                            "content": res_str
                        }
                        messages.append(tool_message)

                if nb_ecartes:
                    logger.warning(
                        f"[{self.name}]{prefix} {nb_ecartes} appel(s) d'outil écarté(s) ce tour "
                        f"({nb_doublons} doublon(s), {nb_ecartes - nb_doublons} au-delà de la "
                        f"borne de {MAX_TOOL_CALLS_PER_TURN})"
                    )

                last_results.extend(turn_results)
                continue

            # Étape 2 : Fallback JSON
            text_response = ""
            if isinstance(response, str):
                text_response = response
            elif isinstance(response, dict) and "content" in response:
                text_response = response.get("content") or ""

            json_blocks = []
            if text_response:
                import re
                json_blocks = re.findall(r"```json\s*(.*?)\s*```", text_response, re.DOTALL)

            if json_blocks:
                messages.append({"role": "assistant", "content": text_response})

                turn_results = []
                for block in json_blocks:
                    try:
                        tool_call_data = json.loads(block.strip())
                        if isinstance(tool_call_data, dict) and "action" in tool_call_data:
                            func_name = tool_call_data["action"]
                            kwargs = tool_call_data.get("params", {})

                            if func_name == "write_file":
                                if "file_path" in kwargs and "filepath" not in kwargs:
                                    kwargs["filepath"] = kwargs.pop("file_path")
                                if "contents" in kwargs and "content" not in kwargs:
                                    kwargs["content"] = kwargs.pop("contents")
                            elif func_name == "read_file":
                                if "file_path" in kwargs and "filepath" not in kwargs:
                                    kwargs["filepath"] = kwargs.pop("file_path")

                            logger.info(f"[{self.name}]{prefix} Appel d'outil extrait du texte (fallback JSON) : {func_name}")
                            res = await self._execute_tool_with_retry(func_name, kwargs)
                            tool_executed = True

                            res_str = str(res)
                            res_str = self._sanitizer.sanitize(res_str, source=f"tool:{func_name}")

                            if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                                agent_error = classify_error(res, source=f"tool:{func_name}")
                                logger.warning(f"[{self.name}]{prefix} Erreur d'outil détectée ({agent_error.category.value}) de {func_name} (fallback JSON): {res}. Tentative d'auto-correction locale...")
                                last_tool_error = f"Erreur dans l'outil '{func_name}' : {res_str}"

                                messages.append({
                                    "role": "user",
                                    "content": f"Erreur lors de l'exécution de '{func_name}' : {res_str}. Corrige cette erreur et réessaie."
                                })
                            else:
                                last_tool_error = None
                                turn_results.append(f"Résultat '{func_name}' : {res_str}")
                                messages.append({"role": "user", "content": f"Résultat '{func_name}' : {res_str}"})
                    except Exception as e:
                        logger.warning(f"Échec de l'analyse du bloc JSON extrait : {e}")

                if turn_results:
                    last_results.extend(turn_results)
                    continue

            # Étape 3 : Réponse finale
            final_text_response = text_response
            break

        return last_results, final_text_response, tool_executed, last_tool_error

    async def _execute_tool_with_retry(
        self, func_name: str, kwargs: dict, max_retries: int = MAX_TOOL_RETRIES
    ) -> str:
        """
        Exécute un outil avec retry intelligent.
        
        Les erreurs retriables (réseau, timeout, rate_limit) déclenchent
        un retry automatique avec backoff exponentiel. Les erreurs non-retriables
        (logique, auth, permission) sont remontées immédiatement.
        
        Args:
            func_name: Nom de l'outil à exécuter
            kwargs: Arguments de l'outil
            max_retries: Nombre maximum de tentatives (défaut: MAX_TOOL_RETRIES)
            
        Returns:
            Le résultat de l'outil (string)
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                res = await self.tool_registry.execute(func_name, kwargs, agent_name=self.name)

                # Vérifier si le résultat est une erreur retriable
                if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                    agent_error = classify_error(res, source=f"tool:{func_name}")

                    if agent_error.is_retriable and attempt < max_retries:
                        delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))  # Backoff exponentiel
                        logger.warning(
                            f"[{self.name}] Erreur retriable ({agent_error.category.value}) "
                            f"de '{func_name}' (tentative {attempt}/{max_retries}). "
                            f"Retry dans {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        last_error = res
                        continue
                    else:
                        # Erreur non-retriable ou retries épuisés
                        return res

                # Succès — retourner le résultat
                return res

            except Exception as e:
                agent_error = classify_error(str(e), source=f"tool:{func_name}")

                if agent_error.is_retriable and attempt < max_retries:
                    delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"[{self.name}] Exception retriable ({agent_error.category.value}) "
                        f"de '{func_name}' (tentative {attempt}/{max_retries}). "
                        f"Retry dans {delay:.1f}s... Exception: {e}"
                    )
                    await asyncio.sleep(delay)
                    last_error = str(e)
                    continue
                else:
                    raise  # Remonter l'exception non-retriable

        # Retries épuisés — retourner la dernière erreur
        return last_error or f"Erreur : retries épuisés pour '{func_name}'"
