"""
[#T433] Un fichier écrit à côté du dépôt n'entre dans aucun commit.

Mesuré le 21/08 : pendant une tâche autonome, l'agent a déposé
`analyse_wake_word.py`, `filter_audio_results.py` et `find_active_configs.py`
à la RACINE de l'espace de travail, à côté du clone. `git add -A` dans le clone
n'a rien vu, la branche est restée vide, la tâche a été comptée en échec — alors
que le travail existait, sur le disque, hors de portée.

Lire ailleurs est légitime (#T425 : une analyse Tab5 doit lire `00ProjetTab/`).
Écrire ailleurs ne l'est jamais.
"""

import os

import pytest

from tools import system as systeme
from tools.system import read_file, write_file


@pytest.fixture
def espace(tmp_path, monkeypatch):
    """Un espace de travail avec un clone et un projet voisin."""
    racine = tmp_path / "espace"
    clone = racine / "moteur_agents_dc"
    voisin = racine / "00ProjetTab"
    clone.mkdir(parents=True)
    voisin.mkdir()
    (voisin / "README.md").write_text("firmware", encoding="utf-8")
    monkeypatch.setattr(systeme, "_WORKSPACE_ROOT", os.path.realpath(str(racine)))
    return racine, clone, voisin


def test_ecriture_dans_le_depot_de_travail_acceptee(espace):
    racine, clone, _ = espace
    jeton = systeme.definir_racine_travail(str(clone))
    try:
        res = write_file(str(clone / "docs" / "analyses" / "rapport.md"), "constat")
        assert "Erreur" not in res, res
        assert (clone / "docs" / "analyses" / "rapport.md").read_text(encoding="utf-8") == "constat"
    finally:
        systeme.reinitialiser_racine_travail(jeton)


def test_ecriture_a_cote_du_depot_refusee(espace):
    """Le cas réel : un script d'analyse déposé à la racine de l'espace."""
    racine, clone, _ = espace
    jeton = systeme.definir_racine_travail(str(clone))
    try:
        res = write_file(str(racine / "analyse_wake_word.py"), "print('x')")
        assert "refusée hors du dépôt de travail" in res
        assert not (racine / "analyse_wake_word.py").exists()
        # Le message doit dire OÙ écrire, sinon l'agent recommence.
        assert str(clone) in res
    finally:
        systeme.reinitialiser_racine_travail(jeton)


def test_ecriture_dans_un_projet_voisin_refusee(espace):
    """Une analyse Tab5 LIT le firmware ; elle ne le modifie pas SI la tâche est moteur."""
    racine, clone, voisin = espace
    jeton = systeme.definir_racine_travail(str(clone))
    try:
        res = write_file(str(voisin / "README.md"), "modifié")
        assert "refusée hors du dépôt de travail" in res
        assert (voisin / "README.md").read_text(encoding="utf-8") == "firmware"
    finally:
        systeme.reinitialiser_racine_travail(jeton)


def test_ecriture_dans_tab5_quand_c_est_le_depot_de_travail(espace):
    """Le cas voulu : une tâche Tab5 ÉCRIT dans 00ProjetTab."""
    _racine, clone, voisin = espace
    jeton = systeme.definir_racine_travail(str(voisin))
    try:
        res = write_file(str(voisin / "tab5-styles.yaml"), "lvgl:")
        assert "Erreur" not in res, res
        assert (voisin / "tab5-styles.yaml").read_text(encoding="utf-8") == "lvgl:"
        # …et pas dans le clone moteur.
        res_moteur = write_file(str(clone / "docs" / "x.md"), "non")
        assert "refusée hors du dépôt de travail" in res_moteur
    finally:
        systeme.reinitialiser_racine_travail(jeton)


def test_la_lecture_reste_ouverte_a_l_espace(espace):
    """La garde porte sur l'écriture seule — sinon on casse #T425."""
    racine, clone, voisin = espace
    jeton = systeme.definir_racine_travail(str(clone))
    try:
        assert read_file(str(voisin / "README.md")) == "firmware"
    finally:
        systeme.reinitialiser_racine_travail(jeton)


def test_hors_tache_autonome_rien_ne_change(espace):
    """Sans racine de travail posée (chat interactif), le comportement est intact."""
    racine, _, _ = espace
    res = write_file(str(racine / "note.md"), "libre")
    assert "Erreur" not in res, res
    assert (racine / "note.md").read_text(encoding="utf-8") == "libre"
