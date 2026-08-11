"""
tools/testing.py — Outil "run_tests" exposé aux agents.

Lance pytest sur un chemin ciblé (fichier ou dossier de tests) pour permettre
à l'ExecutorAgent de vérifier ses propres modifications avant de conclure,
sur le même modèle que run_terminal_command (shell=False, timeout, troncature).
"""
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def run_tests(test_path: str = "tests/") -> str:
    """
    Lance pytest sur le chemin indiqué (fichier ou dossier) et renvoie un résumé.
    Args: test_path (chemin relatif ou absolu vers un fichier/dossier de tests,
    défaut "tests/" pour la suite complète).
    """
    logger.info(f"Exécution des tests: {test_path}")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resolved_path = test_path if os.path.isabs(test_path) else os.path.join(project_root, test_path)

    if not os.path.exists(resolved_path):
        return f"Erreur: le chemin de tests {resolved_path} n'existe pas."

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", resolved_path, "-q"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_root,
        )

        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")

        if not output.strip():
            output = "Aucune sortie pytest (vérifier que des tests existent à ce chemin)."

        status = "SUCCÈS" if result.returncode == 0 else f"ÉCHEC (code {result.returncode})"

        MAX_CHARS = 4000
        if len(output) > MAX_CHARS:
            logger.warning("Sortie pytest tronquée car trop volumineuse.")
            output = output[:MAX_CHARS] + "\n...[SORTIE TRONQUÉE POUR PRÉSERVER LE CONTEXTE]..."

        return f"Résultat des tests ({status}):\n{output}"

    except subprocess.TimeoutExpired:
        return "Erreur: les tests ont mis trop de temps à s'exécuter (> 120s) et ont été tués de force."
    except Exception as e:
        return f"Erreur système inattendue lors du lancement des tests: {e}"
