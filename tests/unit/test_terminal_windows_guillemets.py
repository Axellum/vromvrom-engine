"""
tests/unit/test_terminal_windows_guillemets.py — le terminal utilisable sur Windows (#T313).

Mesuré le 11/08 sur une campagne de 8 demandes réelles. Deux scénarios système
(« Pourquoi mon PC rame, qu'est-ce qui tourne ? » et « Résume-moi les mails
d'hier ») ont brûlé leurs 10 tours de boucle ReAct sans rien produire, en
répétant quasiment la même commande :

    Résultat 'run_terminal_command' : Get-Date -Format 'yyyy-MM-dd'
    Résultat 'run_terminal_command' : Get-Date -Format 'yyyy-MM-dd'
    … (9 fois)

Deux défauts distincts, tous deux propres à Windows :

1. **Les guillemets partaient littéralement.** `shlex.split(cmd, posix=False)`
   — choisi pour préserver les backslashes des chemins Windows — conserve aussi
   les guillemets DANS le token :

       posix=False → ['powershell', '-Command', '"Get-Process | …"']
       posix=True  → ['powershell', '-Command', 'Get-Process | …']

   PowerShell recevait donc une chaîne entre guillemets, l'évaluait comme un
   littéral et la renvoyait telle quelle. C'est très exactement pourquoi la
   « sortie » observée en campagne était la commande elle-même.

2. **`[WinError 2]` n'indiquait rien.** Sur un hôte Windows, un modèle propose
   spontanément des cmdlets PowerShell (`Get-Process`, `Get-Date`). Ce ne sont
   pas des exécutables : `subprocess` échouait avec « Le fichier spécifié est
   introuvable », sans dire quoi faire — donc le modèle réessayait à l'identique.

Les deux corrections gardent `shell=False` : le découpage reste sans shell, on
retire seulement les guillemets APRÈS découpage. Aucune interprétation de
métacaractère n'est réintroduite (#P0-1.3).
"""

import os

import pytest

import tools.terminal as terminal
from tools.terminal import run_terminal_command

WINDOWS_SEULEMENT = pytest.mark.skipif(
    os.name != "nt",
    reason="Comportement propre au découpage posix=False de Windows.",
)


def _denuder_guillemets(token: str) -> str:
    """Accès indirect : sur master le helper n'existe pas encore, et on veut que
    l'échec porte sur le comportement testé, pas sur la collecte du module."""
    fonction = getattr(terminal, "_denuder_guillemets", None)
    assert fonction is not None, "tools.terminal._denuder_guillemets absent (#T313)"
    return fonction(token)


# ── Le dénudage des guillemets (pur, testé partout) ──────────────────────────

def test_guillemets_doubles_encadrants_retires():
    """Le cas de production : la commande passée à `powershell -Command`."""
    assert _denuder_guillemets('"Get-Process | Select -First 2"') == "Get-Process | Select -First 2"


def test_guillemets_simples_encadrants_retires():
    """`-Format 'yyyy-MM-dd'` du scénario mail."""
    assert _denuder_guillemets("'yyyy-MM-dd'") == "yyyy-MM-dd"


def test_backslashes_intacts():
    """La raison d'être de posix=False : les chemins Windows survivent."""
    assert _denuder_guillemets(r"C:\Users\axell") == r"C:\Users\axell"


def test_guillemets_internes_preserves():
    """On ne touche qu'aux guillemets ENCADRANTS, pas au contenu."""
    assert _denuder_guillemets('dit-il "oui"') == 'dit-il "oui"'


def test_guillemets_depareilles_preserves():
    """Une seule extrémité citée n'est pas une paire : on ne touche à rien."""
    assert _denuder_guillemets('"pas fermé') == '"pas fermé'


def test_token_trop_court_intact():
    """Un token d'un seul caractère ne peut pas encadrer quoi que ce soit."""
    assert _denuder_guillemets('"') == '"'
    assert _denuder_guillemets("") == ""


# ── Le message actionnable sur cmdlet (le vrai déclencheur de la boucle) ─────

@WINDOWS_SEULEMENT
def test_cmdlet_nue_donne_la_marche_a_suivre():
    """Sans ça, le modèle réessaie la même commande jusqu'à épuiser ses tours."""
    res = run_terminal_command("Get-Process")
    assert "cmdlet PowerShell" in res
    assert "powershell -NoProfile -Command" in res
    assert "WinError" not in res


@WINDOWS_SEULEMENT
def test_cmdlet_du_scenario_mail_aussi():
    """`Get-Date -Format 'yyyy-MM-dd'`, répétée 9 fois en campagne."""
    res = run_terminal_command("Get-Date -Format 'yyyy-MM-dd'")
    assert "cmdlet PowerShell" in res


@WINDOWS_SEULEMENT
def test_executable_inconnu_reste_distinct_d_une_cmdlet():
    """Un binaire absent n'est pas une cmdlet : le message doit différer."""
    res = run_terminal_command("nexistepas42")
    assert "introuvable dans le PATH" in res
    assert "cmdlet" not in res


# ── L'exécution réelle, bout en bout ─────────────────────────────────────────

@WINDOWS_SEULEMENT
def test_powershell_execute_vraiment_la_commande_citee():
    """
    Le cœur du défaut n° 1 : avant, PowerShell renvoyait la commande au lieu de
    l'exécuter. On vérifie qu'on obtient une sortie, et surtout PAS l'écho.
    """
    cmd = 'powershell -NoProfile -Command "Write-Output T313-OK"'
    res = run_terminal_command(cmd)
    assert "T313-OK" in res
    assert "Write-Output" not in res, "PowerShell renvoie encore la commande au lieu de l'exécuter."


@WINDOWS_SEULEMENT
def test_chemin_windows_toujours_transmis_intact():
    """Non-régression de la raison d'être de posix=False."""
    res = run_terminal_command(r"cmd /c echo C:\Users\axell")
    assert r"C:\Users\axell" in res


# ── Le garde-fou #T275 ne doit pas bouger ────────────────────────────────────

@WINDOWS_SEULEMENT
def test_code_inline_toujours_refuse():
    """Le refus des interpréteurs à code inline reste entier."""
    res = run_terminal_command('python -c "import os"')
    assert "code inline refusée" in res


@WINDOWS_SEULEMENT
def test_code_inline_cite_desormais_refuse_aussi():
    """
    Durcissement au passage : sur master, `python '-c' "…"` ne déclenchait PAS
    la garde — le drapeau cité ne correspondait pas à `-c`, et la commande
    partait à l'exécution (elle échouait pour une autre raison, mais le
    garde-fou #T275 restait muet). Le dénudage la fait correspondre.
    """
    res = run_terminal_command("python '-c' \"import os\"")
    assert "code inline refusée" in res
