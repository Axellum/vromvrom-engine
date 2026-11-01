"""
tests/unit/test_recolte_hote_parametrable.py — hôte de récolte paramétrable.

Constat vérifié : `scripts/recolte_dreamcoder.py` portait `DECK_HOTE` en dur
(`deck@192.168.1.10`). Conséquence mesurée les 18-19/08 : les branches
produites par le moteur du PC étaient structurellement invisibles pour la
récolte, qui n'interrogeait que le Deck — 34,2 M de tokens de travail jamais
relus.

Ce test verrouille les trois niveaux de priorité de l'hôte :
    option argparse  >  variable d'environnement MOTEUR_RECOLTE_HOTE  >  défaut

Le comportement par défaut reste inchangé : sans option ni variable, on retombe
sur `DECK_HOTE`. Aucun réseau n'est touché (conftest `_interdire_reseau`).
"""

import os
import sys

import pytest

import scripts.recolte_dreamcoder as recolte


@pytest.fixture(autouse=True)
def _purge_env_hote(monkeypatch):
    """Chaque test part d'un environnement sans surcharge machine."""
    monkeypatch.delenv(recolte.ENV_HOTE, raising=False)


@pytest.fixture
def _main_contourne(monkeypatch):
    """
    Exécute `main()` sans toucher au réseau ni à la clé SSH.

    `recolter` est remplacé par un espion qui capture `args.hote` ; la clé SSH
    est déclarée présente pour passer le garde `os.path.exists`.
    """
    capteur = {}

    def _faux_recolter(args):
        capteur["hote"] = args.hote
        return 0

    monkeypatch.setattr(recolte, "recolter", _faux_recolter)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    return capteur


def _lancer_main(monkeypatch, argv):
    """Lance `main()` avec `sys.argv` donné et renvoie le code de sortie."""
    monkeypatch.setattr(sys, "argv", ["recolte_dreamcoder.py"] + argv)
    return recolte.main()


def test_defaut_sans_option_ni_variable(_main_contourne, monkeypatch):
    """Sans surcharge, on retombe sur DECK_HOTE : rien ne casse."""
    code = _lancer_main(monkeypatch, [])
    assert code == 0
    assert _main_contourne["hote"] == recolte.DECK_HOTE


def test_variable_d_environnement_prime_sur_le_defaut(_main_contourne, monkeypatch):
    """MOTEUR_RECOLTE_HOTE remplace le défaut quand aucune option n'est passée."""
    monkeypatch.setenv(recolte.ENV_HOTE, "deck@192.168.1.99")
    code = _lancer_main(monkeypatch, [])
    assert code == 0
    assert _main_contourne["hote"] == "deck@192.168.1.99"


def test_option_prime_sur_la_variable_d_environnement(_main_contourne, monkeypatch):
    """`--hote` explicite écrase la variable d'environnement."""
    monkeypatch.setenv(recolte.ENV_HOTE, "deck@192.168.1.99")
    code = _lancer_main(monkeypatch, ["--hote", "deck@192.168.1.77"])
    assert code == 0
    assert _main_contourne["hote"] == "deck@192.168.1.77"


def test_option_prime_sur_le_defaut_sans_variable(_main_contourne, monkeypatch):
    """`--hote` explicite écrase le défaut, même sans variable d'environnement."""
    code = _lancer_main(monkeypatch, ["--hote", "deck@192.168.1.55"])
    assert code == 0
    assert _main_contourne["hote"] == "deck@192.168.1.55"
