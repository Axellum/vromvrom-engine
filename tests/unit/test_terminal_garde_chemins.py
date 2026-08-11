"""
tests/unit/test_terminal_garde_chemins.py — Garde des chemins du terminal (#T275).

Problème verrouillé, mesuré en production le 11/08 : `write_file` refusait
/tmp/preuve_t267.txt (politique de tools/system.py) mais `run_terminal_command`
n'avait AUCUN contrôle équivalent. L'agent s'auto-corrigeait en :

    python3 -c "with open('/tmp/preuve_t267.txt','w') as f: f.write('PREUVE-T267-OK\n')"

et le fichier était bien créé (15 octets) hors du workspace.

Règle posée : la garde (même politique que read_file/write_file) refuse AVANT
exécution les interpréteurs à code inline (`python -c`, `sh -c`, `node -e`…)
et vérifie que les chemins absolus des commandes d'écriture connues (`tee`,
`dd`, `cp`, `mv`, `install`, `truncate`, `rm`) restent dans le workspace.
Kill-switch : `MOTEUR_TERMINAL_GARDE_CHEMINS=0` restaure le comportement
d'avant.

Les refus sont testés sans exécuter la commande (le refus précède
subprocess.run) : ils passent donc partout, même sans python3/sh/tee installés.
Les autorisations avec exécution réelle sont portables (git, python) ou
skipées si l'exécutable est absent (cat, tee, ls).
"""
import logging
import os
import shutil
import sys
import tempfile

import pytest

from tools.system import _WORKSPACE_ROOT
from tools.terminal import _refus_garde_chemins, run_terminal_command

# Marqueur commun des messages de refus de la garde (cohérent avec system.py)
_MARQUEUR_REFUS = "Erreur de sécurité"


def _chemin_dans_workspace(nom: str = "fichier_t275.txt") -> str:
    """Chemin absolu garanti dans le bac à sable (source de vérité = system.py)."""
    return os.path.join(_WORKSPACE_ROOT, nom)


# Chemins POSIX hors du bac à sable, volontairement construits : c'est le
# format /tmp/… que les agents génèrent, y compris sous Windows. noqa S108 :
# le littéral est l'OUTIL de test (une cible hors workspace), pas une fuite.
_TMP = "/tmp"  # noqa: S108


def _chemin_hors_workspace(nom: str = "x") -> str:
    """Chemin POSIX hors du bac à sable (format généré par les agents en prod)."""
    return f"{_TMP}/{nom}"


# La commande exacte observée en production le 11/08 : refus de write_file sur
# /tmp/preuve_t267.txt puis contournement via le terminal.
_COMMANDE_PROD = (
    "python3 -c \"with open('" + _chemin_hors_workspace("preuve_t267.txt")
    + "','w') as f: f.write('PREUVE-T267-OK\\n')\""
)


# ── Interpréteurs à code inline : refus systématique ────────────────────────

@pytest.mark.parametrize("commande", [
    _COMMANDE_PROD,
    "python -c \"print('x')\"",
    'sh -c "echo a > ' + _chemin_hors_workspace() + '"',
    'bash -c "echo a > ' + _chemin_hors_workspace() + '"',
    "perl -e \"print 'x'\"",
    'ruby -e "puts \'x\'"',
    "node -e \"console.log(1)\"",
    "node --eval \"console.log(1)\"",
])
def test_code_inline_refuse(commande):
    """Un interpréteur à code inline ne s'exécute JAMAIS : refus avant subprocess."""
    resultat = run_terminal_command(commande)
    assert _MARQUEUR_REFUS in resultat
    assert "code inline" in resultat
    # Le message doit renvoyer l'agent vers l'outil d'écriture du bac à sable.
    assert "write_file" in resultat


def test_cas_prod_aucun_fichier_cree():
    """Le contournement exact de prod est refusé et n'écrit RIEN sur disque."""
    resultat = run_terminal_command(_COMMANDE_PROD)
    assert _MARQUEUR_REFUS in resultat
    assert not os.path.exists(_chemin_hors_workspace("preuve_t267.txt"))


def test_python_sans_code_inline_autorise():
    """`python -V` (lecture, pas de code inline) reste autorisé : le filtre est
    précis, il ne bloque QUE le flag de code inline."""
    resultat = run_terminal_command(f"{sys.executable} -V")
    assert _MARQUEUR_REFUS not in resultat
    assert "Python" in resultat


def test_script_fichier_non_refuse_limite_documentee():
    """Limite assumée, écrite noir sur blanc dans la PR : `python3 script.py`
    (script déjà présent sur disque) passe — la garde est une liste noire, pas
    un confinement (le vrai serait un conteneur ou un utilisateur dédié)."""
    assert _refus_garde_chemins(["python3", "script.py"]) is None


# ── Commandes d'écriture : chemins absolus vérifiés ─────────────────────────

@pytest.mark.parametrize("argv", [
    ["tee", _chemin_hors_workspace()],
    ["dd", "if=" + _chemin_hors_workspace("src"), "of=" + _chemin_hors_workspace("dst")],
    ["dd", "if=" + _chemin_hors_workspace("etc_hostname"), "of=" + _chemin_dans_workspace("dd_dst.txt")],
    ["cp", _chemin_hors_workspace("src"), _chemin_dans_workspace()],
    ["cp", _chemin_dans_workspace(), _chemin_hors_workspace("dst")],
    ["mv", _chemin_hors_workspace("x"), _chemin_hors_workspace("y")],
    ["install", _chemin_hors_workspace("src"), _chemin_dans_workspace()],
    ["truncate", "-s", "0", _chemin_hors_workspace()],
    ["rm", "-rf", _chemin_hors_workspace()],
])
def test_ecriture_hors_workspace_refusee(argv):
    """Tout chemin absolu hors du workspace (source comme destination) refuse
    la commande d'écriture — y compris les formes `option=valeur` de dd."""
    assert _refus_garde_chemins(argv) is not None


@pytest.mark.parametrize("argv", [
    ["tee", _chemin_dans_workspace()],
    ["cp", _chemin_dans_workspace(), _chemin_dans_workspace("dst.txt")],
    ["mv", _chemin_dans_workspace(), _chemin_dans_workspace("dst.txt")],
    ["install", _chemin_dans_workspace(), _chemin_dans_workspace("dst.txt")],
    ["truncate", "-s", "0", _chemin_dans_workspace()],
    ["rm", "-rf", _chemin_dans_workspace()],
])
def test_ecriture_dans_workspace_autorisee(argv):
    """Les écritures dont tous les chemins restent dans le workspace passent."""
    assert _refus_garde_chemins(argv) is None


def test_tee_hors_workspace_refuse_avant_execution():
    """`tee /tmp/x` est refusé par run_terminal_command lui-même — pas besoin
    que l'exécutable tee existe : le refus précède l'exécution."""
    resultat = run_terminal_command("tee " + _chemin_hors_workspace())
    assert _MARQUEUR_REFUS in resultat


def test_tee_fichier_workspace_autorise():
    """`tee` vers un fichier du workspace s'exécute réellement (fichier créé)."""
    if shutil.which("tee") is None:
        pytest.skip("tee absent sur cette machine (Windows natif)")
    fichier = _chemin_dans_workspace("tee_t275.txt")
    try:
        resultat = run_terminal_command(f"tee {fichier}")
        assert _MARQUEUR_REFUS not in resultat
    finally:
        if os.path.exists(fichier):
            os.remove(fichier)


# ── Lectures ordinaires : aucun changement de comportement ──────────────────

def test_git_status_autorise():
    """`git status` passe tel quel : la garde n'interfère pas avec la lecture."""
    resultat = run_terminal_command("git status")
    assert _MARQUEUR_REFUS not in resultat


def test_ls_la_autorise():
    """`ls -la` : lecture ordinaire, aucun refus."""
    if shutil.which("ls") is None:
        pytest.skip("ls absent sur cette machine (Windows natif)")
    resultat = run_terminal_command("ls -la")
    assert _MARQUEUR_REFUS not in resultat


def test_cat_fichier_workspace_autorise():
    """`cat` d'un fichier du workspace : exécuté tel quel, contenu retourné."""
    if shutil.which("cat") is None:
        pytest.skip("cat absent sur cette machine (Windows natif)")
    fd, chemin = tempfile.mkstemp(suffix=".txt", dir=_WORKSPACE_ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("CONTENU-LECTURE-T275\n")
        resultat = run_terminal_command(f"cat {chemin}")
        assert _MARQUEUR_REFUS not in resultat
        assert "CONTENU-LECTURE-T275" in resultat
    finally:
        os.remove(chemin)


# ── Kill-switch ─────────────────────────────────────────────────────────────

def test_kill_switch_desactive_tout_repart(monkeypatch):
    """`MOTEUR_TERMINAL_GARDE_CHEMINS=0` restaure le comportement d'avant :
    le code inline (même le contournement de prod) s'exécute à nouveau.

    `print(42)` sans guillemets : sous Windows shlex(posix=False) conserve les
    guillemets, qui deviendraient une chaîne Python littérale (no-op) — `42`
    prouve l'exécution réelle du code de façon portable."""
    monkeypatch.setenv("MOTEUR_TERMINAL_GARDE_CHEMINS", "0")
    commande = f"{sys.executable} -c print(42)"
    resultat = run_terminal_command(commande)
    assert _MARQUEUR_REFUS not in resultat
    assert "42" in resultat


def test_kill_switch_desactive_ecritures_aussi(monkeypatch):
    """La garde entière (interpréteurs ET commandes d'écriture) est coupée."""
    monkeypatch.setenv("MOTEUR_TERMINAL_GARDE_CHEMINS", "0")
    resultat = run_terminal_command("tee " + _chemin_hors_workspace())
    assert _MARQUEUR_REFUS not in resultat


# ── Journalisation des refus ────────────────────────────────────────────────

def test_refus_journalise_warning_commande_tronquee(caplog):
    """Chaque refus laisse un warning avec la commande tronquée à 120 caractères."""
    commande = 'sh -c "' + "echo " + "a" * 250 + '"'
    with caplog.at_level(logging.WARNING, logger="tools.terminal"):
        run_terminal_command(commande)
    messages = [m for m in caplog.messages if "garde chemins #T275" in m]
    assert messages, "aucun warning de refus journalisé"
    fragment = messages[0].split("garde chemins #T275): ", 1)[1]
    assert len(fragment) <= 120
    assert "a" * 250 not in messages[0]


# ── [#T275] Options avant le flag de code inline ────────────────────────────
# Ajouté à la relecture : la première version ne testait que `argv[1]`, donc un
# interpréteur appelé avec une option AVANT `-c` passait au travers alors qu'il
# exécute bien du code inline.

@pytest.mark.parametrize("argv", [
    ["python3", "-X", "utf8", "-c", "open('/tmp/x','w')"],
    ["python3", "-B", "-c", "print(1)"],
    ["sh", "-e", "-c", "echo a > /tmp/x"],
    ["bash", "--norc", "-c", "rm -rf /tmp/y"],
    ["node", "--no-warnings", "-e", "require('fs').writeFileSync('/tmp/z','')"],
])
def test_code_inline_precede_d_options_refuse(argv):
    assert _refus_garde_chemins(argv) is not None, f"non refusé : {argv}"


@pytest.mark.parametrize("argv", [
    # `-c` APRÈS le script : c'est un argument du script, pas de l'interpréteur.
    ["bash", "script.sh", "-c"],
    ["python3", "outil.py", "-c", "valeur"],
    # Options sans code inline.
    ["python3", "-V"],
    ["node", "--version"],
])
def test_flag_apres_le_script_ou_sans_code_inline_autorise(argv):
    assert _refus_garde_chemins(argv) is None, f"refusé à tort : {argv}"
