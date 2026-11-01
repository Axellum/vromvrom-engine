"""
tests/unit/test_projects_registry.py — Un projet = un git + un dossier local.

Sans registre, une tâche `tab5/firmware` écrivait dans le clone moteur (ou
était refusée par git_safety, remote ≠ Axellum/moteur_agents).
"""

from core.projects import (
    projet_moteur,
    projet_par_id,
    racine_espace_de_travail,
    remotes_enregistres,
    resoudre_projet_pour_tache,
)
from tools.git_safety import is_canonical_repo, remote_correspond


def test_tab5_zone_pointe_vers_le_git_tab5():
    projet = resoudre_projet_pour_tache({
        "title": "[T421] (tab5/firmware) Montée ESPHome",
        "zone": "tab5/firmware",
        "description": "Vérifier le board esp32-p4-evboard",
    })
    assert projet.id == "tab5"
    assert "M5-Tab5-ESPHome-LVGL" in projet.git_remote


def test_project_id_explicite_prime_sur_la_zone():
    projet = resoudre_projet_pour_tache({
        "project_id": "ecosystem",
        "zone": "tab5/ui",
        "title": "docs",
    })
    assert projet.id == "ecosystem"


def test_todo_moteur_reste_sur_le_moteur():
    projet = resoudre_projet_pour_tache({
        "title": "TODO dans core/engine.py — nettoyer",
        "description": "Fichier core/engine.py ligne 12: nettoyer",
        "zone": "core/engine.py",
    })
    assert projet.id == "moteur"


def test_objectif_tab5_sans_zone():
    projet = resoudre_projet_pour_tache({}, objectif="Corrige le YAML LVGL du Tab5")
    assert projet.id == "tab5"


def test_repli_moteur():
    projet = resoudre_projet_pour_tache({"title": "une tâche vague"})
    assert projet.id == "moteur"
    assert projet.git_remote == projet_moteur().git_remote


def test_remotes_incluent_tab5_et_ecosysteme():
    remotes = remotes_enregistres()
    assert "Axellum/moteur_agents" in remotes
    assert "Axellum/M5-Tab5-ESPHome-LVGL" in remotes
    assert "Axellum/AuxFilsDesIdees" in remotes


def test_workspace_remonte_jusqua_tab5(tmp_path, monkeypatch):
    """Un moteur imbriqué (moteur_agents_into/moteur_agents) ne doit pas
    perdre 00ProjetTab, qui vit à la racine écosystème."""
    eco = tmp_path / "AuxFilsDesIdees"
    (eco / "00ProjetTab").mkdir(parents=True)
    (eco / "contexte_ia").mkdir()
    moteur = eco / "moteur_agents_into" / "moteur_agents"
    moteur.mkdir(parents=True)
    monkeypatch.delenv("MOTEUR_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr("core.projects._ENGINE_ROOT", moteur)
    assert racine_espace_de_travail() == str(eco.resolve())


def test_git_safety_accepte_le_remote_tab5(tmp_path):
    import subprocess
    repo = tmp_path / "00ProjetTab"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)

    code = git("init", "-b", "main").returncode
    if code != 0:
        git("init")
        git("checkout", "-b", "main")
    git("remote", "add", "origin", "https://github.com/Axellum/M5-Tab5-ESPHome-LVGL.git")
    assert is_canonical_repo(str(repo))


def test_git_safety_accepte_le_remote_casse_github(tmp_path):
    """GitHub ignore la casse owner/repo ; origin est souvent en minuscules."""
    import subprocess
    repo = tmp_path / "moteur"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)

    code = git("init", "-b", "master").returncode
    if code != 0:
        git("init")
        git("checkout", "-b", "master")
    git("remote", "add", "origin", "https://github.com/axellum/moteur_agents.git")
    assert is_canonical_repo(str(repo))
    assert remote_correspond("Axellum/moteur_agents", "https://github.com/axellum/moteur_agents.git")


def test_tache_tab5_ne_prend_pas_le_slot_moteur(tmp_path, monkeypatch):
    """Une tâche firmware doit viser 00ProjetTab, pas le clone DreamCoder moteur."""
    import subprocess
    from agents.dreamer_agent import _projet_et_depot_de_tache

    repo = tmp_path / "00ProjetTab"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)

    code = git("init", "-b", "main").returncode
    if code != 0:
        git("init")
        git("checkout", "-b", "main")
    git("remote", "add", "origin", "https://github.com/Axellum/M5-Tab5-ESPHome-LVGL.git")
    monkeypatch.setenv("MOTEUR_PROJECT_TAB5_PATH", str(repo))

    projet_id, racine, branche = _projet_et_depot_de_tache(
        {"title": "YAML LVGL Tab5", "zone": "tab5/firmware"},
        pa_config={"dreamcoder_repo_path": str(tmp_path / "moteur_agents_dc3")},
        slot_moteur=str(tmp_path / "moteur_agents_dc3"),
    )
    import os
    assert projet_id == "tab5"
    assert os.path.realpath(racine) == os.path.realpath(str(repo))
    assert branche == "main"


def test_git_safety_refuse_un_remote_inconnu(tmp_path):
    import subprocess
    repo = tmp_path / "orphelin"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)

    code = git("init", "-b", "main").returncode
    if code != 0:
        git("init")
        git("checkout", "-b", "main")
    git("remote", "add", "origin", "https://github.com/exemple/pas-un-projet.git")
    assert not is_canonical_repo(str(repo))


def test_projet_par_id_inconnu():
    assert projet_par_id("nexistepas") is None
    assert projet_par_id("tab5") is not None
