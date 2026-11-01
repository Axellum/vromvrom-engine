"""
tests/unit/test_chemins_prompt_executor.py — Le prompt annonce la vraie racine (#T399).

Mesure en production le 18/08 a 19h06 : l'ExecutorAgent, a qui le prompt
systeme annoncait « le workspace principal est /config » (chemin de l'ancien
add-on Home Assistant), lance `ls -R /config/moteur-master/api/routes/`, se
prend un « No such file or directory », relance `find /config`, meme resultat,
puis part fouiller `/home/deck/dev_station` en aveugle. Des tours de boucle
ReAct payes sur le quota gratuit pour retrouver un dossier que le moteur
connaissait deja.

La racine de travail existe pourtant depuis #T388 : `racine_travail_courante()`,
que DreamCoder positionne sur le clone du slot avant chaque tache.
"""

import os

from agents.executor import _regles_os
from tools.system import definir_racine_travail, reinitialiser_racine_travail


def test_les_chemins_suivent_la_racine_de_travail(tmp_path):
    clone = tmp_path / "moteur_agents_dc3"
    (clone / "core").mkdir(parents=True)
    jeton = definir_racine_travail(str(clone))
    try:
        regles = _regles_os(is_windows=False)
    finally:
        reinitialiser_racine_travail(jeton)

    assert os.path.realpath(str(clone)) in regles
    assert os.path.join(os.path.realpath(str(clone)), "core") in regles


def test_plus_aucune_racine_ecrite_en_dur(tmp_path):
    """Les deux chemins morts ne doivent plus jamais sortir du prompt."""
    clone = tmp_path / "slot"
    (clone / "core").mkdir(parents=True)
    jeton = definir_racine_travail(str(clone))
    try:
        for is_windows in (False, True):
            regles = _regles_os(is_windows=is_windows)
            assert "/config/moteur-master" not in regles
            assert "e:" not in regles.lower().replace("e:x", "")
    finally:
        reinitialiser_racine_travail(jeton)


def test_sans_racine_de_tache_on_retombe_sur_le_depot():
    """Hors DreamCoder (vocal, chat), la racine est celle du depot du moteur."""
    from core.env_bootstrap import REPO_ROOT

    jeton = definir_racine_travail(None)
    try:
        regles = _regles_os(is_windows=False)
    finally:
        reinitialiser_racine_travail(jeton)
    assert str(REPO_ROOT) in regles

def test_interdiction_pipes_et_chainage_dans_regles():
    """Vérifie que l'interdiction des pipes et du chaînage est bien présente."""
    regles_linux = _regles_os(is_windows=False)
    regles_windows = _regles_os(is_windows=True)
    
    interdiction = "ATTENTION : 'run_terminal_command' ne supporte ni les pipes ('|'), ni les redirections ('>', '<'), ni le chainage ('&&', ';'). Lance les commandes une par une."
    
    assert interdiction in regles_linux
    assert interdiction in regles_windows
