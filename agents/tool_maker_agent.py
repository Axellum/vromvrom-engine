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
4. Sauvegarder dans plugins/auto_generated/<tool_name>/
5. Enregistrer dans le ToolRegistry (hot-reload)

Sécurité :
- Le code généré est validé syntaxiquement (ast.parse) avant chargement
- Un flag auto_generated: true distingue les outils auto-créés
- L'exécution est wrappée dans un try/except global
"""

import os
import re
import ast
import sys          # [P0-1.4] Sandbox dans le même interpréteur (portable)
import json
import shutil       # [P0-1.4] Nettoyage des répertoires temp imprévisibles
import logging
import asyncio
import subprocess  # Sandbox validation
import tempfile    # Fichiers temporaires
from typing import Optional, Dict, Any

from core.state import TaskPayload, StateUpdate
from core.validation import is_valid_tool_name, is_valid_class_name  # [P0-1.4]
from agents.base_agent import BaseAgent

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


class ToolMakerAgent(BaseAgent):
    """
    Agent méta-générateur : crée des outils Python réutilisables
    à partir de séquences d'outils répétitives détectées par le SkillStore.
    """

    def __init__(self, llm_gateway=None, **kwargs):
        super().__init__(
            name="tool_maker",
            system_prompt="""Tu es le ToolMakerAgent, un ingénieur spécialisé dans la génération de code Python.
Ton rôle est de condenser des séquences d'outils répétitives en un outil atomique Python réutilisable.

RÈGLES DE GÉNÉRATION :
1. Le code généré doit être SIMPLE et LISIBLE (commentaires en français)
2. Chaque outil doit avoir une méthode execute(**kwargs) -> dict
3. Utiliser des try/except pour toute opération IO
4. Ne JAMAIS hardcoder de chemins absolus ou de clés API
5. Le résultat doit être un dict avec "success" (bool) et "result" (str)
6. Importer uniquement des modules standard Python (os, json, re, subprocess)

Tu dois répondre en JSON strict avec la structure :
{
  "tool_name": "nom_outil_snake_case",
  "class_name": "NomOutilPascalCase",
  "description": "Description courte de l'outil",
  "execution_logic": "code Python indenté pour le corps de execute()"
}"""
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
                    f"[TOOL-MAKER] Outil '{final_tool_name}' rejeté (sandbox échoué) "
                    f"— non sauvegardé. err={sandbox_result.get('last_error')}"
                )
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    error_message=(
                        f"Outil '{final_tool_name}' rejeté : validation sandbox "
                        f"échouée ({sandbox_result.get('last_error', 'raison inconnue')})."
                    ),
                    result_data=final_code,
                    metadata={"sandbox_passed": False, "persisted": False},
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
                    f"[TOOL-MAKER] Outil '{final_tool_name}' validé (sandbox OK) mais "
                    f"NON persisté : MOTEUR_ENABLE_TOOL_MAKER désactivé."
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

            # Étape 3 : Sauvegarder le fichier (uniquement si validé ET flag actif)
            save_path = self._save_tool(final_tool_name, final_code)

            logger.info(
                f"[TOOL-MAKER] ✅ Outil '{final_tool_name}' généré et sauvegardé : {save_path}"
            )

            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data=(
                    f"Outil '{final_tool_name}' généré avec succès.\n"
                    f"Fichier : {save_path}\n"
                    f"Séquence condensée : {sequence_str}\n"
                    f"Sandbox : valide"
                ),
                metadata={
                    "tool_name": final_tool_name,
                    "tool_path": save_path,
                    "auto_generated": True,
                    "sandbox_passed": True,
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
    ) -> Dict[str, Any]:
        """
        Valide le code généré dans un subprocess Python isolé.

        Workflow :
        1. Écrire le code + bloc de test dans un répertoire temp imprévisible
           (tempfile.mkdtemp — P0-1.4, anti TOCTOU/symlink)
        2. Exécuter via subprocess (timeout 10s, shell=False)
        3. Vérifier 'SANDBOX_OK' dans stdout
        4. Si échec < 3 tentatives : régénérer le spec via LLM (ReAct loop)
        5. Nettoyer les répertoires temp dans tous les cas

        [P0-1.4] L'appelant ne persiste QUE si "passed" est True ; on remonte
        donc le code/identifiants exactement validés (la boucle ReAct a pu les
        régénérer) via les clés "code"/"tool_name"/"class_name".

        Returns:
            {"passed": bool, "attempts": int, "last_error": str|None,
             "test_outputs": list, "code": str, "tool_name": str, "class_name": str}
        """
        result: Dict[str, Any] = {
            "passed": False,
            "attempts": 0,
            "last_error": None,
            "test_outputs": [],
            "code": tool_code,
            "tool_name": tool_name,
            "class_name": class_name,
        }
        MAX_ATTEMPTS = 3

        current_code = tool_code
        current_spec = {"tool_name": tool_name, "class_name": class_name}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            result["attempts"] = attempt
            # [P0-1.4] Répertoire temp imprévisible (mkdtemp) au lieu d'un chemin
            # /tmp/... prévisible : élimine le risque de symlink/TOCTOU et reste
            # portable (Windows dev). Nettoyé dans le finally.
            temp_dir = tempfile.mkdtemp(prefix="toolmaker_sandbox_")
            temp_path = os.path.join(temp_dir, f"{tool_name}.py")

            try:
                # Bloc de test injecté en fin du fichier temp
                test_block = f"""

import asyncio as _asyncio

def _run_sandbox():
    try:
        tool = {class_name}()
        res = _asyncio.run(tool.execute(param='test_value', value='test_data'))
        if isinstance(res, dict) and 'success' in res:
            print('SANDBOX_OK' if res['success'] else 'SANDBOX_FAIL')
        else:
            print('SANDBOX_OK')  # Outil sans retour bool : accepter
    except Exception as _e:
        print(f'SANDBOX_ERROR: {{_e}}')

_run_sandbox()
"""
                full_code = current_code + test_block

                # Écriture du fichier temp
                with open(temp_path, 'w', encoding='utf-8') as f:
                    f.write(full_code)

                # Exécution subprocess dans asyncio.to_thread (non-bloquant).
                # [P0-1.4] sys.executable (interpréteur courant) au lieu de
                # 'python3' codé en dur : portable et cohérent avec le venv.
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, temp_path],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )

                stdout = proc.stdout or ""
                stderr = proc.stderr or ""

                if 'SANDBOX_OK' in stdout:
                    result["passed"] = True
                    # [P0-1.4] Remonter le code/identifiants exactement validés.
                    result["code"] = current_code
                    result["tool_name"] = current_spec.get("tool_name", tool_name)
                    result["class_name"] = current_spec.get("class_name", class_name)
                    result["test_outputs"].append({
                        "attempt": attempt,
                        "stdout": stdout[:200],
                        "status": "ok"
                    })
                    logger.info(f"[TOOL-MAKER] [SANDBOX] ✅ Tentative {attempt}/{MAX_ATTEMPTS} : OK")
                    break  # Succès → arrêter la boucle
                else:
                    error_msg = (stderr or stdout or "Sortie vide")[:300]
                    result["last_error"] = f"[Tentative {attempt}] {error_msg}"
                    result["test_outputs"].append({
                        "attempt": attempt,
                        "stdout": stdout[:200],
                        "stderr": stderr[:200],
                        "status": "fail",
                    })
                    logger.warning(
                        f"[TOOL-MAKER] [SANDBOX] ⚠️  Tentative {attempt}/{MAX_ATTEMPTS} : FAIL\n"
                        f"  stderr: {stderr[:150]}"
                    )

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

            except subprocess.TimeoutExpired:
                result["last_error"] = f"[Tentative {attempt}] Timeout 10s dépassé"
                logger.warning(f"[TOOL-MAKER] [SANDBOX] ⏱️  Tentative {attempt}/{MAX_ATTEMPTS} : Timeout")
            except Exception as e:
                result["last_error"] = f"[Tentative {attempt}] Exception : {str(e)[:200]}"
                logger.warning(f"[TOOL-MAKER] [SANDBOX] Erreur tentative {attempt} : {e}")
            finally:
                # [P0-1.4] Nettoyage du répertoire temp imprévisible (mkdtemp).
                shutil.rmtree(temp_dir, ignore_errors=True)

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
    ) -> Optional[Dict[str, Any]]:
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

    def _build_tool_code(self, spec: Dict, tools_sequence: list) -> str:
        """Construit le code Python final à partir de la spécification."""
        return TOOL_TEMPLATE.format(
            tool_name=spec.get("tool_name", "auto_tool"),
            class_name=spec.get("class_name", "AutoTool"),
            description=spec.get("description", "Outil auto-généré"),
            tools_sequence=" → ".join(tools_sequence),
            execution_logic=spec.get("execution_logic", "pass"),
        )

    def _validate_code(self, code: str) -> Dict[str, Any]:
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

    def _save_tool(self, tool_name: str, code: str) -> str:
        """
        Sauvegarde le code dans plugins/auto_generated/<tool_name>/.
        Crée aussi un plugin.json pour la découverte automatique.

        [P0-1.4] N'est appelé QUE pour un outil dont les identifiants sont
        validés et qui a passé le sandbox (cf. invoke) → sandbox_passed=True
        est désormais un invariant. Défense en profondeur : on revérifie le nom
        (anti traversée de chemin) car il sert de nom de dossier/fichier.
        """
        if not is_valid_tool_name(tool_name):
            raise ValueError(f"Nom d'outil invalide pour la sauvegarde : {tool_name!r}")

        tool_dir = os.path.join(AUTO_TOOLS_DIR, tool_name)
        os.makedirs(tool_dir, exist_ok=True)

        # Sauvegarder le code Python
        tool_path = os.path.join(tool_dir, f"{tool_name}.py")
        with open(tool_path, 'w', encoding='utf-8') as f:
            f.write(code)

        # Créer le plugin.json pour la découverte automatique
        plugin_meta = {
            "name": tool_name,
            "version": "1.0.0",
            "auto_generated": True,
            "sandbox_passed": True,  # [P0-1.4] invariant : seul du validé est persisté
            "description": f"Outil auto-généré : {tool_name}",
            "entry_point": f"{tool_name}.py",
        }
        meta_path = os.path.join(tool_dir, "plugin.json")
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(plugin_meta, f, indent=2, ensure_ascii=False)

        logger.info(f"[TOOL-MAKER] Outil sauvegardé : {tool_path}")
        return tool_path
