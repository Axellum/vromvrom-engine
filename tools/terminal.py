import os
import shlex
import subprocess
import logging

logger = logging.getLogger(__name__)

def run_terminal_command(command: str) -> str:
    """
    Exécute une commande système locale (outil critique, gated HITL).
    Renvoie la sortie standard (stdout) ou d'erreur (stderr).

    [P0-1.3] Exécution SANS shell : la commande est découpée en arguments
    (shlex) puis exécutée avec shell=False → pas d'interprétation des
    métacaractères shell (`;`, `|`, `&&`, backticks…), donc pas d'injection ni
    de chaînage de commandes. Les pipes/redirections ne sont volontairement plus
    supportés ; lancer les commandes une par une.
    L'exécution est contrainte par un timeout de 30 secondes.
    """
    logger.info(f"Exécution commande système: {command}")
    try:
        # posix=False sous Windows pour préserver les backslashes des chemins.
        argv = shlex.split(command, posix=(os.name != "nt"))
        if not argv:
            return "Erreur: commande vide."
        # Exécution avec un timeout strict (ex: ping infini bloquerait l'agent sinon)
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            output = result.stdout
        else:
            output = f"Erreur (code {result.returncode}): {result.stderr}"
        
        if not output.strip():
            return "Commande exécutée avec succès (aucune sortie dans la console)."
            
        # Protection contre la saturation du contexte LLM (Tronquage)
        MAX_CHARS = 4000
        if len(output) > MAX_CHARS:
            logger.warning("Sortie terminal tronquée car trop volumineuse.")
            return output[:MAX_CHARS] + "\n...[SORTIE TRONQUÉE POUR PRÉSERVER LE CONTEXTE]..."
            
        return output
        
    except subprocess.TimeoutExpired:
        return "Erreur: La commande a mis trop de temps à s'exécuter (> 30s) et a été tuée de force."
    except Exception as e:
        return f"Erreur système inattendue: {str(e)}"
