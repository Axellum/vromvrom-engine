"""
agents/tool_maker_agent.py — Agent méta-générateur d'outils Python.

Tool-Maker : Le moteur s'auto-écrit des outils.

Quand le SkillStore détecte qu'une séquence d'outils a été utilisée avec
succès plus de 3 fois (seuil configurable), le ToolMakerAgent est invoqué
pour condenser cette séquence en un outil Python atomique et réutilisable.

Workflow :
1. Recevoir une séquence d'outils répétitive depuis le SkillStore
2. Générer un script Python héritant d'une interface standard
3. Valider le script (ast.parse + import check)
4. Générer un test minimal (import + appel + retour non None) à côté de l'outil
5. Exécuter ce test dans le runner distant : échec → outil rejeté, jamais enregistré
6. Sauvegarder outil + test dans plugins/auto_generated/<tool_name>/
7. Enregistrer dans le ToolRegistry (hot-reload)

Sécurité :
- Le code généré est validé syntaxiquement (ast.parse) avant chargement
- [#T207] La validation d'exécution se fait dans un runner GitHub Actions
  jetable (core/toolmaker_sandbox.py) — JAMAIS dans un subprocess local :
  le code candidat est non maîtrisé (RCE si exécuté avec les droits moteur)
- [#T190] Le fichier de test généré est exécuté dans le même runner : sans
  SANDBOX_OK en sortie, l'outil n'est ni sauvegardé ni enregistré
- Un flag auto_generated: true distingue les outils auto-créés
- L'exécution est wrappée dans un try/except global
"""

import ast
import asyncio
import json
import logging
import os
import re
from typing import Any

from agents.base_agent import BaseAgent
from core.state import StateUpdate, TaskPayload
from core.validation import is_valid_class_name, is_valid_tool_name  # [P0-1.4]

logger = logging.getLogger(__name__)


def _tool_maker_persist_enabled() -> bool:
    """[P0-1.4] La persistance d'un outil auto-généré écrit du code produit par le
    LLM sur disque, où il devient exécutable par hot-reload. Cette opération est
    gardée par un flag explicite, désactivé par défaut (fail-closed) — comme le
    chargement des plugins. Mettre MOTEUR_ENABLE_TOOL_MAKER=1 pour l'autoriser."""
    return os.getenv("MOTEUR_ENABLE_TOOL_MAKER", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )

# Répertoire de destination des outils auto-générés
AUTO_TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins",
    "auto_generated",
)

# Template Python pour les outils auto-générés
TOOL_TEMPLATE = '''"""
{tool_name}.py — Outil auto-généré par le ToolMakerAgent.

Description : {description}
Séquence originale : {tools_sequence}
Généré automatiquement — ne pas modifier manuellement.
"""

import logging

logger = logging.getLogger(__name__)


class {class_name}:
    """
    Outil auto-généré condensant la séquence : {tools_sequence}
    
    {description}
    """
    
    name = "{tool_name}"
    description = """{description}"""
    timeout_seconds = 120
    auto_generated = True
    
    async def execute(self, **kwargs) -> dict:
        """
        Exécute la logique condensée de la séquence d'outils.
        
        Args:
            **kwargs: Paramètres variables selon le contexte.
            
        Returns:
            dict avec "success" (bool) et "result" (str).
        """
        try:
            results = []
            {execution_logic}
            
            return {{
                "success": True,
                "result": "\\n".join(results) if results else "Exécution terminée.",
                "tool_name": self.name,
            }}
        except Exception as e:
            logger.error(f"[{{self.name}}] Erreur d'exécution : {{e}}")
            return {{
                "success": False,
                "result": f"Erreur : {{str(e)}}",
                "tool_name": self.name,
            }}
'''


# [#T190] Template du test minimal généré à côté de chaque outil auto-généré.
# Le fichier est exécutable seul (python test_<tool_name>.py) et imprime
# SANDBOX_OK sur succès, SANDBOX_FAIL <raison> sinon (code 1) : c'est ce que
# le runner GitHub Actions ToolMaker Validate attend en sortie (#T207).
TOOL_TEST_TEMPLATE = '''"""
test_{tool_name}.py — Test minimal de l'outil auto-généré {tool_name} (#T190).

Généré automatiquement par le ToolMakerAgent en même temps que {tool_name}.py
— ne pas modifier manuellement.

Vérifie, dans l'ordre :
1. L'import du module outil (attrape la moitié des erreurs : syntaxe, nom de
   classe, dépendance manquante) ;
2. L'appel de execute() avec des arguments valides déduits de sa signature
   (les clés réellement lues dans **kwargs) ;
3. Que le retour n'est ni None ni une exception.

Exécution locale : python test_{tool_name}.py
Sortie : SANDBOX_OK si tout passe, SANDBOX_FAIL <raison> sinon (code 1).
Le runner GitHub Actions ToolMaker Validate (#T207/#T190) exécute ce fichier :
sans SANDBOX_OK en sortie, l'outil est rejeté et jamais enregistré.
"""

import asyncio
import os
import sys

# L'import doit résoudre le module outil situé À CÔTÉ de ce fichier : même
# mécanique dans le runner GitHub Actions (candidats dans .toolmaker_sandbox/)
# et dans plugins/auto_generated/<tool_name>/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_execute():
    """Importe le module outil puis vérifie l'appel et le retour."""
    # Import DANS la fonction : un échec d'import (syntaxe, classe absente)
    # est attrapé par le __main__ et produit un verdict SANDBOX_FAIL avec la
    # raison exacte, plutôt qu'une traceback sans verdict.
    from {tool_name} import {class_name}

    tool = {class_name}()
    result = asyncio.run(tool.execute({kwargs_repr}))
    # Ni None ni exception : un appel qui lève fait échouer le test.
    assert result is not None, "execute() a retourné None"
    assert isinstance(result, dict), (
        f"execute() a retourné {{type(result).__name__}}, dict attendu"
    )
    assert "success" in result, "le retour ne contient pas la clé 'success'"


if __name__ == "__main__":
    try:
        test_execute()
        print("SANDBOX_OK")
    except Exception as exc:
        print(f"SANDBOX_FAIL: {{exc}}")
        sys.exit(1)
'''


class ToolMakerAgent(BaseAgent):
    """
    Agent méta-générateur : crée des outils Python réutilisables
    à partir de séquences d'outils répétitives détectées par le SkillStore.
    """

    def __init__(self, llm_gateway=None, **kwargs):
        # [#T188/#T340] Prompt externalisé (prompts/agents/tool_maker.md) ;
        # la chaîne ci-dessous reste le repli si le fichier est absent.
        from core.prompt_loader import load_agent_prompt
        super().__init__(
            name="tool_maker",
            system_prompt=load_agent_prompt("tool_maker", """Tu es le ToolMakerAgent, un ingénieur spécialisé dans la génération de code Python.
Ton rôle est de condenser des séquences d'outils répétitives en un outil atomique Python réutilisable.

RÈGLES DE GÉNÉRATION :
1. Le code généré doit être SIMPLE et LISIBLE (commentaires en français)
2. Chaque outil doit avoir une méthode execute(**kwargs) -> dict
3. Utiliser des try/except pour toute opération IO
4. Ne JAMAIS hardcoder de chemins absolus ou de clés API
5. Le résultat doit être un dict avec "success" (bool) et "result" (str)
6. Importer uniquement des modules standard Python (os, json, re, subprocess)
7. Lire les paramètres via kwargs.get('nom', valeur_par_defaut) : l'outil est
   teste automatiquement avec des valeurs simples avant d'etre enregistre

Tu dois répondre en JSON strict avec la structure :
{
  "tool_name": "nom_outil_snake_case",
  "class_name": "NomOutilPascalCase",
  "description": "Description courte de l'outil",
  "execution_logic": "code Python indenté pour le corps de execute()"
}""")
        )
        self._gateway = llm_gateway

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        """
        Génère un outil Python à partir de la séquence d'outils fournie.
        
        Le payload doit contenir dans ses metadata :
        - skill_pattern: Description du skill
        - tools_sequence: Liste des outils à condenser
        - objective: Objectif original de la tâche
        """
        skill_pattern = payload.metadata.get("skill_pattern", "")
        tools_sequence = payload.metadata.get("tools_sequence", [])
        objective = payload.metadata.get("objective", "")

        if not tools_sequence:
            return StateUpdate(
                agent_name=self.name,
                status="error",
                error_message="Aucune séquence d'outils fournie.",
                result_data=None,
            )

        # Calculé hors f-string (backslash en expression f-string interdit < Py3.12).
        sequence_str = " → ".join(tools_sequence)
        logger.info(
            f"[TOOL-MAKER] Génération d'outil pour la séquence : "
            f"{sequence_str}"
        )

        try:
            # Étape 1 : Générer le code de l'outil via LLM
            tool_spec = await self._generate_tool_spec(
                skill_pattern, tools_sequence, objective, payload
            )

            if not tool_spec:
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message="Le LLM n'a pas pu générer la spécification de l'outil.",
                    result_data=None,
                )

            # [P0-1.4] Assainir les identifiants venus du LLM AVANT toute
            # construction de code ou écriture disque : `tool_name` sert de nom de
            # dossier/fichier (risque de traversée de chemin), `class_name` est
            # interpolé dans le code généré et le bloc de test (risque d'injection).
            tool_name = tool_spec.get("tool_name", "")
            class_name = tool_spec.get("class_name", "")
            if not is_valid_tool_name(tool_name) or not is_valid_class_name(class_name):
                logger.warning(
                    f"[TOOL-MAKER] Identifiants rejetés : "
                    f"tool_name={tool_name!r}, class_name={class_name!r}"
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message="Identifiants d'outil invalides (tool_name/class_name).",
                    result_data=None,
                )

            # Étape 2 : Construire et valider le code Python
            tool_code = self._build_tool_code(tool_spec, tools_sequence)
            validation_result = self._validate_code(tool_code)

            if not validation_result["valid"]:
                logger.warning(
                    f"[TOOL-MAKER] Code invalide : {validation_result['error']}"
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message=f"Code généré invalide : {validation_result['error']}",
                    result_data=tool_code,
                )

            # Étape 2.5 : Validation sandbox subprocess (non-bloquante)
            sandbox_result = await self._validate_in_sandbox(
                tool_name=tool_name,
                class_name=class_name,
                tool_code=tool_code,
                skill_pattern=skill_pattern,
                tools_sequence=tools_sequence,
                objective=objective,
                payload=payload,
            )
            logger.info(
                f"[TOOL-MAKER] Sandbox : passed={sandbox_result['passed']} "
                f"({sandbox_result['attempts']} tentatives, "
                f"err={sandbox_result.get('last_error', 'aucune')[:60] if sandbox_result.get('last_error') else 'aucune'})"
            )

            # [P0-1.4] On persiste exactement le code qui a passé le sandbox (la
            # boucle ReAct a pu le régénérer). Les identifiants finaux ont déjà été
            # validés (entrée + chaque régénération), on revérifie par sûreté.
            final_tool_name = sandbox_result.get("tool_name", tool_name)
            final_code = sandbox_result.get("code", tool_code)

            # [P0-1.4] Fail-closed : ne JAMAIS écrire sur disque un outil non validé.
            if not sandbox_result["passed"]:
                logger.warning(
                    f"[TOOL-MAKER] Outil '{final_tool_name}' rejeté — son test "
                    f"minimal (test_{final_tool_name}.py) a échoué dans le runner "
                    f"GitHub Actions ; non sauvegardé. err={sandbox_result.get('last_error')}"
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message=(
                        f"Outil '{final_tool_name}' rejeté : la validation sandbox a "
                        f"échoué — le test minimal n'a pas passé "
                        f"({sandbox_result.get('last_error', 'raison inconnue')})."
                    ),
                    result_data=final_code,
                    metadata={"sandbox_passed": False, "test_passed": False, "persisted": False},
                )

            if not is_valid_tool_name(final_tool_name):
                logger.warning(
                    f"[TOOL-MAKER] Nom d'outil final invalide après sandbox : {final_tool_name!r}"
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message="Nom d'outil final invalide.",
                    result_data=None,
                )

            # [P0-1.4] La persistance (rendre du code LLM exécutable par hot-reload)
            # est gardée par un flag explicite, désactivé par défaut.
            if not _tool_maker_persist_enabled():
                logger.info(
                    f"[TOOL-MAKER] Outil '{final_tool_name}' validé (sandbox OK, "
                    f"test exécuté) mais NON persisté : MOTEUR_ENABLE_TOOL_MAKER désactivé."
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="success",
                    result_data=(
                        f"Outil '{final_tool_name}' généré et validé (sandbox OK), "
                        f"mais non persisté.\n"
                        f"Activer MOTEUR_ENABLE_TOOL_MAKER=1 pour l'écrire.\n"
                        f"Séquence condensée : {sequence_str}"
                    ),
                    metadata={
                        "tool_name": final_tool_name,
                        "auto_generated": True,
                        "sandbox_passed": True,
                        "persisted": False,
                    },
                )

            # [#T190] Le test persisté est exactement celui exécuté dans le
            # runner (remonté par la sandbox) ; repli : le reconstruire.
            final_test_code = sandbox_result.get("test_code") or self._build_tool_test(
                final_code,
                final_tool_name,
                sandbox_result.get("class_name", class_name),
            )

            # Étape 3 : Sauvegarder outil + test + métadonnées (uniquement si
            # validé ET flag actif)
            save_path = self._save_tool(final_tool_name, final_code, final_test_code)
            test_path = os.path.join(
                os.path.dirname(save_path), f"test_{final_tool_name}.py"
            )

            logger.info(
                f"[TOOL-MAKER] ✅ Outil '{final_tool_name}' accepté : test "
                f"'{os.path.basename(test_path)}' exécuté avec succès dans le runner "
                f"(import + appel + retour non None). Sauvegardé : {save_path}"
            )

            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data=(
                    f"Outil '{final_tool_name}' généré avec succès.\n"
                    f"Fichier : {save_path}\n"
                    f"Test : {test_path}\n"
                    f"Séquence condensée : {sequence_str}\n"
                    f"Sandbox : valide (test exécuté)"
                ),
                metadata={
                    "tool_name": final_tool_name,
                    "tool_path": save_path,
                    "test_path": test_path,
                    "auto_generated": True,
                    "sandbox_passed": True,
                    "test_passed": True,
                    "persisted": True,
                },
            )

        except Exception as e:
            logger.error(f"[TOOL-MAKER] Erreur de génération : {e}")
            return StateUpdate(
                agent_name=self.name,
                status="error",
                error_message=f"Erreur de génération d'outil : {str(e)}",
                result_data=None,
            )


    async def _validate_in_sandbox(
        self,
        tool_name: str,
        class_name: str,
        tool_code: str,
        skill_pattern: str,
        tools_sequence: list,
        objective: str,
        payload: TaskPayload,
    ) -> dict[str, Any]:
        """
        Valide le code généré dans un runner GitHub Actions jetable (#T207).

        Workflow :
        1. Construire le code candidat + son fichier de test minimal
           (test_<tool_name>.py, cf. _build_tool_test) — [#T190]
        2. Le pousser sur une branche éphémère toolmaker/validate-* et attendre
           le verdict du workflow toolmaker_validate.yml (VM GitHub isolée,
           aucun accès au LAN Deck/HA) — cf. core/toolmaker_sandbox.py
        3. Si échec < 3 tentatives : régénérer le spec via LLM (ReAct loop)
        4. La branche éphémère est supprimée dans tous les cas

        [#T207] Le code candidat est non maîtrisé (généré par LLM) : il ne doit
        JAMAIS être exécuté localement (l'ancien subprocess.run tournait avec
        les droits du process moteur → RCE). Fail-closed : sans PAT dédié
        (MOTEUR_TOOLMAKER_PAT) ou en cas d'erreur API, l'outil est rejeté.

        [#T190] L'exécution du test généré (import + appel + retour non None)
        a lieu dans ce même runner : un outil dont le test échoue est rejeté
        et jamais persisté. Le test exactement validé est remonté via
        "test_code" pour être écrit sur disque à côté de l'outil.

        [P0-1.4] L'appelant ne persiste QUE si "passed" est True ; on remonte
        donc le code/identifiants exactement validés (la boucle ReAct a pu les
        régénérer) via les clés "code"/"tool_name"/"class_name".

        Returns:
            {"passed": bool, "attempts": int, "last_error": str|None,
             "test_outputs": list, "code": str, "test_code": str,
             "tool_name": str, "class_name": str}
        """
        result: dict[str, Any] = {
            "passed": False,
            "attempts": 0,
            "last_error": None,
            "test_outputs": [],
            "code": tool_code,
            "test_code": "",
            "tool_name": tool_name,
            "class_name": class_name,
        }
        MAX_ATTEMPTS = 3

        current_code = tool_code
        current_spec = {"tool_name": tool_name, "class_name": class_name}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            result["attempts"] = attempt

            try:
                # [#T190] Candidat poussé au runner : l'outil + SON fichier de
                # test minimal, généré depuis le code exact de cette tentative
                # (la boucle ReAct peut le régénérer). Le test importe le module
                # depuis son propre dossier : même mécanique dans le runner et
                # dans plugins/auto_generated/ une fois persisté.
                current_test_code = self._build_tool_test(
                    current_code,
                    current_spec.get("tool_name", tool_name),
                    current_spec.get("class_name", class_name),
                )

                # [#T207] Exécution DISTANTE dans un runner GitHub Actions jetable
                # (VM isolée, sans accès au LAN Deck/HA) — plus jamais de
                # subprocess local : le code candidat est non maîtrisé.
                from core.toolmaker_sandbox import validate_candidate_remote
                candidate_name = current_spec.get("tool_name", tool_name)
                remote = await validate_candidate_remote(files={
                    f"{candidate_name}.py": current_code,
                    f"test_{candidate_name}.py": current_test_code,
                })

                if remote["passed"]:
                    result["passed"] = True
                    # [P0-1.4] Remonter le code/identifiants exactement validés.
                    result["code"] = current_code
                    result["test_code"] = current_test_code
                    result["tool_name"] = current_spec.get("tool_name", tool_name)
                    result["class_name"] = current_spec.get("class_name", class_name)
                    result["test_outputs"].append({
                        "attempt": attempt,
                        "run_url": remote.get("run_url"),
                        "status": "ok"
                    })
                    logger.info(f"[TOOL-MAKER] [SANDBOX] ✅ Tentative {attempt}/{MAX_ATTEMPTS} : test exécuté avec succès")
                    break  # Succès → arrêter la boucle
                else:
                    error_msg = (remote.get("error") or "Échec sandbox distante")[:300]
                    result["last_error"] = f"[Tentative {attempt}] {error_msg}"
                    result["test_outputs"].append({
                        "attempt": attempt,
                        "run_url": remote.get("run_url"),
                        "error": error_msg,
                        "status": "fail",
                    })
                    logger.warning(
                        f"[TOOL-MAKER] [SANDBOX] ⚠️  Tentative {attempt}/{MAX_ATTEMPTS} : FAIL\n"
                        f"  err: {error_msg[:150]}"
                    )

                    # Sans PAT configuré, chaque tentative échouera à l'identique :
                    # inutile de regénérer le spec et de re-payer des appels LLM.
                    if "MOTEUR_TOOLMAKER_PAT" in error_msg:
                        break

                    # ReAct : régénérer le spec si encore des tentatives
                    if attempt < MAX_ATTEMPTS:
                        logger.info(f"[TOOL-MAKER] [SANDBOX] ReAct : régénération spec (tentative {attempt+1})...")
                        new_spec = await self._generate_tool_spec(
                            skill_pattern=skill_pattern,
                            tools_sequence=tools_sequence,
                            objective=objective,
                            payload=payload,
                        )
                        if new_spec:
                            # [P0-1.4] Revalider les identifiants régénérés : la
                            # spec LLM est de nouveau non maîtrisée.
                            new_name = new_spec.get("tool_name", "")
                            new_class = new_spec.get("class_name", "")
                            if not is_valid_tool_name(new_name) or not is_valid_class_name(new_class):
                                result["last_error"] = (
                                    (result["last_error"] or "")
                                    + " | spec régénérée rejetée (identifiants invalides)"
                                )
                                break
                            current_code = self._build_tool_code(new_spec, tools_sequence)
                            class_name = new_class
                            current_spec = new_spec
                        # Revalider la syntaxe avant de retenter
                        syntax = self._validate_code(current_code)
                        if not syntax["valid"]:
                            result["last_error"] += f" | SyntaxError: {syntax['error']}"
                            break

            except Exception as e:
                result["last_error"] = f"[Tentative {attempt}] Exception : {str(e)[:200]}"
                logger.warning(f"[TOOL-MAKER] [SANDBOX] Erreur tentative {attempt} : {e}")

        # Logger l'échec dans SkillStore si dispo
        if not result["passed"]:
            try:
                from memory.skills import SkillStore
                store = SkillStore()
                if hasattr(store, 'log_skill_failure'):
                    await asyncio.to_thread(
                        store.log_skill_failure,
                        tool_name,
                        result.get("last_error", "Sandbox échoué"),
                    )
            except Exception:
                pass  # SkillStore non disponible ou interface différente

        return result

    async def _generate_tool_spec(
        self,
        skill_pattern: str,
        tools_sequence: list,
        objective: str,
        payload: TaskPayload,
    ) -> dict[str, Any] | None:
        """
        Appelle le LLM pour générer la spécification de l'outil.
        Si pas de gateway, génère un template par défaut.
        """
        sequence_str = " → ".join(tools_sequence)
        if self._gateway:
            try:
                from core.llm_gateway import load_config
                config = load_config()
                _, provider = self._gateway.get_provider_for_tier("moyen", config)

                prompt = (
                    f"Génère un outil Python condensant cette séquence d'outils répétitive :\n"
                    f"Séquence : {sequence_str}\n"
                    f"Pattern : {skill_pattern}\n"
                    f"Objectif type : {objective}\n\n"
                    f"L'outil doit automatiser cette séquence en un seul appel."
                )

                schema = {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "class_name": {"type": "string"},
                        "description": {"type": "string"},
                        "execution_logic": {"type": "string"},
                    },
                    "required": ["tool_name", "class_name", "description", "execution_logic"],
                }

                result = await provider.generate_structured_async(
                    self.system_prompt,
                    prompt,
                    schema,
                    session_id=payload.metadata.get("session_id"),
                    # [T287] Le ToolMaker génère du code Python destiné au dépôt :
                    # il reçoit les conventions du projet (commentaires en français,
                    # FileLock sur les fichiers partagés, etc.).
                    conventions_projet=True,
                )

                if result and result.get("tool_name"):
                    return result

            except Exception as e:
                logger.warning(f"[TOOL-MAKER] Fallback template (LLM indisponible) : {e}")

        # Fallback : générer un template par défaut sans LLM.
        # [P0-1.4] Garantir un identifiant conforme à is_valid_tool_name :
        # snake_case, démarrant par une lettre, borné en longueur.
        safe_name = re.sub(r'[^a-z0-9_]', '_', skill_pattern.lower()[:40])
        safe_name = re.sub(r'_+', '_', safe_name).strip('_') or "auto_tool"
        if not safe_name[:1].isalpha():
            safe_name = f"auto_{safe_name}"
        safe_name = safe_name[:64]

        return {
            "tool_name": safe_name,
            "class_name": "".join(w.capitalize() for w in safe_name.split("_")),
            "description": f"Outil auto-généré pour : {skill_pattern}",
            "execution_logic": (
                f'# Séquence condensée : {" → ".join(tools_sequence)}\n'
                f'            results.append("Exécution de la séquence : {" → ".join(tools_sequence)}")\n'
                f'            # TODO: Implémenter la logique de chaque étape'
            ),
        }

    def _build_tool_code(self, spec: dict, tools_sequence: list) -> str:
        """Construit le code Python final à partir de la spécification."""
        return TOOL_TEMPLATE.format(
            tool_name=spec.get("tool_name", "auto_tool"),
            class_name=spec.get("class_name", "AutoTool"),
            description=spec.get("description", "Outil auto-généré"),
            tools_sequence=" → ".join(tools_sequence),
            execution_logic=spec.get("execution_logic", "pass"),
        )

    def _deduce_execute_args(self, tool_code: str) -> dict[str, str]:
        """
        Déduit les arguments d'appel de execute(**kwargs) depuis le corps généré.

        La signature du template est `async def execute(self, **kwargs)` : on ne
        peut pas deviner les noms de paramètres depuis la signature seule, on
        les déduit donc des clés réellement lues dans kwargs par le code
        (`kwargs.get('nom')` ou `kwargs['nom']`). Chaque clé reçoit une valeur
        de test simple ("test_value").

        Returns:
            Dictionnaire ordonné {nom_de_paramètre: valeur_de_test}, vide si
            le corps ne lit aucune clé (appel sans argument, valide pour **kwargs).
        """
        try:
            tree = ast.parse(tool_code)
        except SyntaxError:
            return {}

        import keyword

        names: list[str] = []
        for node in ast.walk(tree):
            # kwargs.get('nom', défaut) — appel de méthode sur la variable kwargs
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "kwargs"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.append(node.args[0].value)
            # kwargs['nom'] — subscript direct sur la variable kwargs
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "kwargs"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                names.append(node.slice.value)

        # On ne garde que les identifiants Python valides : une clé exotique
        # (espaces, guillemets…) ne peut pas devenir un argument nommé.
        safe_names = [
            n for n in dict.fromkeys(names)
            if n.isidentifier() and not keyword.iskeyword(n)
        ]
        # dict.fromkeys : dédoublonnage en conservant l'ordre de première apparition
        return {name: "test_value" for name in safe_names}

    def _build_tool_test(self, tool_code: str, tool_name: str, class_name: str) -> str:
        """
        Construit le fichier de test minimal de l'outil généré (#T190).

        Le test vérifie dans l'ordre :
        1. l'import du module outil (attrape la moitié des erreurs) ;
        2. l'appel de execute() avec des arguments valides déduits de sa
           signature (les clés lues dans **kwargs, cf. _deduce_execute_args) ;
        3. que le retour n'est ni None ni une exception.

        Le fichier est exécutable seul (`python test_<tool_name>.py`) et
        imprime SANDBOX_OK / SANDBOX_FAIL : c'est lui que le runner GitHub
        Actions exécute (#T207). Les identifiants interpolés ont déjà été
        validés par is_valid_tool_name / is_valid_class_name (anti-injection).
        """
        kwargs_repr = ", ".join(
            f"{name}='{value}'" for name, value in self._deduce_execute_args(tool_code).items()
        )
        return TOOL_TEST_TEMPLATE.format(
            tool_name=tool_name,
            class_name=class_name,
            kwargs_repr=kwargs_repr,
        )

    def _validate_code(self, code: str) -> dict[str, Any]:
        """
        Valide syntaxiquement le code Python généré via ast.parse.
        
        Returns:
            {"valid": bool, "error": str|None}
        """
        try:
            ast.parse(code)
            return {"valid": True, "error": None}
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError ligne {e.lineno}: {e.msg}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    def _save_tool(self, tool_name: str, code: str, test_code: str) -> str:
        """
        Sauvegarde l'outil ET son test minimal dans plugins/auto_generated/<tool_name>/.
        Crée aussi un plugin.json pour la découverte automatique.

        [P0-1.4] N'est appelé QUE pour un outil dont les identifiants sont
        validés et qui a passé le sandbox (cf. invoke) → sandbox_passed=True
        est désormais un invariant. [#T190] Le fichier de test écrit ici est le
        même que celui exécuté dans le runner (test_code remonté par la
        sandbox) : outil, test et métadonnées sont persistés ensemble, ou pas
        du tout. Défense en profondeur : on revérifie le nom (anti traversée
        de chemin) car il sert de nom de dossier/fichier.
        """
        if not is_valid_tool_name(tool_name):
            raise ValueError(f"Nom d'outil invalide pour la sauvegarde : {tool_name!r}")

        tool_dir = os.path.join(AUTO_TOOLS_DIR, tool_name)
        os.makedirs(tool_dir, exist_ok=True)

        # Sauvegarder le code Python
        tool_path = os.path.join(tool_dir, f"{tool_name}.py")
        with open(tool_path, 'w', encoding='utf-8') as f:
            f.write(code)

        # [#T190] Sauvegarder le test minimal à côté de l'outil, à un
        # emplacement déterministe : test_<tool_name>.py
        test_path = os.path.join(tool_dir, f"test_{tool_name}.py")
        with open(test_path, 'w', encoding='utf-8') as f:
            f.write(test_code)

        # Créer le plugin.json pour la découverte automatique
        plugin_meta = {
            "name": tool_name,
            "version": "1.0.0",
            "auto_generated": True,
            "sandbox_passed": True,  # [P0-1.4] invariant : seul du validé est persisté
            "test_passed": True,     # [#T190] invariant : le test minimal a réussi
            "description": f"Outil auto-généré : {tool_name}",
            "entry_point": f"{tool_name}.py",
            "test_entry_point": f"test_{tool_name}.py",  # [#T190] emplacement déterministe
        }
        meta_path = os.path.join(tool_dir, "plugin.json")
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(plugin_meta, f, indent=2, ensure_ascii=False)

        logger.info(f"[TOOL-MAKER] Outil + test sauvegardés : {tool_path} (+ {test_path})")
        return tool_path
