"""
tools/terminal.py — Outil "run_terminal_command" exposé aux agents.

Exécute une commande système locale sans shell (shlex + shell=False, anti-
injection), outil critique gated HITL/Sandbox selon le mode de l'agent
appelant.

[#T275] Garde des chemins AVANT exécution, mesurée en production le 11/08 :
`write_file` refusait /tmp/preuve_t267.txt (politique de tools/system.py) mais
le terminal n'avait AUCUN contrôle équivalent — l'agent s'auto-corrigeait en
`python3 -c "open('/tmp/preuve_t267.txt','w')…"` et écrivait hors du workspace.
La garde réutilise la politique de tools/system.py : refus des interpréteurs à
code inline (leur code peut écrire n'importe où, une vérification d'arguments
serait illusoire) et vérification des chemins absolus des commandes d'écriture
connues. Kill-switch : MOTEUR_TERMINAL_GARDE_CHEMINS=0 restaure le
comportement d'avant.

Limite assumée (liste noire, pas une fermeture) : un binaire compilé, un
script déjà présent sur disque (`python3 script.py`) ou un `make` passent — le
vrai confinement serait un conteneur ou un utilisateur dédié.
"""
import logging
import os
import shlex
import subprocess

from tools.system import resoudre_dans_workspace

logger = logging.getLogger(__name__)

# Interpréteurs acceptant du code inline : c'est LE contournement observé en
# prod (#T275), leur code s'exécute hors de tout contrôle d'arguments.
# `pythonw`, `python3.12`… sont couverts via le préfixe `python`.
_INTERPRETEURS_CODE_INLINE = {
    "python": {"-c"},
    "python3": {"-c"},
    "sh": {"-c"},
    "bash": {"-c"},
    "perl": {"-e"},
    "ruby": {"-e"},
    "node": {"-e", "--eval"},
}

# Commandes dont on sait qu'elles écrivent ou suppriment des fichiers : leurs
# chemins absolus doivent rester dans le workspace, source comme destination.
_COMMANDES_ECRITURE = {"tee", "dd", "cp", "mv", "install", "truncate", "rm"}


def _garde_chemins_active() -> bool:
    """[#T275] Kill-switch : `MOTEUR_TERMINAL_GARDE_CHEMINS=0` restaure le
    comportement d'avant la garde (même pattern que MOTEUR_ROUTER_GARDE_HITL)."""
    return os.environ.get("MOTEUR_TERMINAL_GARDE_CHEMINS", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _flags_code_inline(prog: str) -> set[str] | None:
    """Flags de code inline acceptés par l'interpréteur `prog`, ou None si
    `prog` n'est pas un interpréteur à code inline connu."""
    base = os.path.basename(prog)
    if base.startswith("python"):
        return {"-c"}
    return _INTERPRETEURS_CODE_INLINE.get(base)


# Extensions de script : au-delà de cet argument, ce qui suit appartient au
# script et non plus à l'interpréteur.
_EXTENSIONS_SCRIPT = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".rb", ".pl")


def _flag_inline_present(argv: list[str], flags: set[str]) -> bool:
    """
    Cherche un flag de code inline parmi les OPTIONS de l'interpréteur.

    Ne pas se contenter de `argv[1]` : `python3 -X utf8 -c "…"` et `sh -e -c "…"`
    passeraient au travers alors qu'ils exécutent bien du code inline.

    On ne peut pas non plus s'arrêter au premier argument sans tiret : `utf8`
    dans `-X utf8` est la VALEUR d'une option, pas un script. On s'arrête donc
    au premier argument qui ressemble à un fichier de script — au-delà, ce sont
    les arguments du script, pas ceux de l'interpréteur (`bash script.sh -c`
    n'est pas du code inline).
    """
    for arg in argv[1:]:
        if arg in flags:
            return True
        if arg.lower().endswith(_EXTENSIONS_SCRIPT):
            return False
    return False


def _ressemble_a_un_chemin_absolu(arg: str) -> bool:
    r"""Vrai si `arg` désigne un chemin absolu : forme Windows (`C:\…`,
    `\\serveur\…`) ou POSIX (`/…` — les agents génèrent souvent du POSIX même
    sous Windows)."""
    return arg.startswith("/") or os.path.isabs(arg)


def _candidats_chemins(argv: list[str]) -> list[str]:
    """Arguments de `argv` qui peuvent désigner des chemins absolus, y compris
    les formes `option=valeur` (dd `if=/… of=/…`, GNU `--output=/…`). La
    valeur extraite est re-filtrée : `--preserve=mode`, `-s 0`… ne déclenchent
    rien."""
    candidats = []
    for arg in argv[1:]:
        if _ressemble_a_un_chemin_absolu(arg):
            candidats.append(arg)
        elif "=" in arg:
            valeur = arg.split("=", 1)[1]
            if _ressemble_a_un_chemin_absolu(valeur):
                candidats.append(valeur)
    return candidats


def _refus_garde_chemins(argv: list[str]) -> str | None:
    """[#T275] Message de refus si la commande contourne le bac à sable de
    chemins, None sinon. Filtre de rejet pur : aucune commande autorisée n'est
    modifiée, et rien n'est exécuté quand un refus est retourné."""
    if not argv:
        return None
    prog = os.path.basename(argv[0])

    # 1) Interpréteur avec code inline : le code peut écrire n'importe où
    # (open(), os.system, subprocess…), vérifier ses arguments serait
    # illusoire — refus systématique avec renvoi vers write_file.
    flags = _flags_code_inline(prog)
    if flags and _flag_inline_present(argv, flags):
        return (
            "Erreur de sécurité : exécution de code inline refusée "
            f"({argv[0]} {argv[1]}) — le bac à sable de chemins ne peut pas "
            "inspecter le code d'un interpréteur. Pour créer ou modifier un "
            "fichier, utilise l'outil write_file ; pour en lire un, read_file."
        )

    # 2) Commandes d'écriture connues : tout chemin absolu (source comme
    # destination) doit rester dans le workspace, via la politique existante
    # de tools/system.py (_resolve_within_workspace, même message d'erreur).
    if prog in _COMMANDES_ECRITURE:
        for arg in _candidats_chemins(argv):
            _resolu, erreur = resoudre_dans_workspace(arg)
            if erreur:
                return (
                    f"Erreur de sécurité : la commande d'écriture '{prog}' "
                    f"vise un chemin hors du bac à sable — {erreur} Pour "
                    "écrire dans le workspace, utilise l'outil write_file."
                )
    return None

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

    [#T275] Garde des chemins AVANT exécution (kill-switch
    MOTEUR_TERMINAL_GARDE_CHEMINS=0) : refuse les interpréteurs à code inline
    et les commandes d'écriture visant hors du workspace — même politique que
    read_file/write_file (tools/system.py).
    """
    logger.info(f"Exécution commande système: {command}")
    try:
        # posix=False sous Windows pour préserver les backslashes des chemins.
        argv = shlex.split(command, posix=(os.name != "nt"))
        if not argv:
            return "Erreur: commande vide."
        # [#T275] Garde des chemins AVANT exécution : en cas de refus, le
        # sous-processus n'est jamais lancé. Journalisé en warning avec la
        # commande tronquée à 120 caractères (logs saturés = logs ignorés).
        if _garde_chemins_active():
            refus = _refus_garde_chemins(argv)
            if refus:
                logger.warning(
                    "Refus commande terminal (garde chemins #T275): %.120s",
                    command.replace("\n", " "),
                )
                return refus
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
