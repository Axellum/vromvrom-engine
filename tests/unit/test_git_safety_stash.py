"""
test_git_safety_stash.py — [#T278] Le cycle stash utilisateur de git_safety est
testé sur de VRAIS dépôts Git temporaires (`git init` + `tmp_path`), sans mocks.

Régression couverte : quand `git stash pop` échoue à la finalisation, l'ancien
comportement ajoutait une phrase au message de succès (« Fusion effectuée avec
succès. Attention : conflit… »), masquant un stash jamais dépilé — jusqu'à 3 en
production (incident du 11/08, config de prod restée enfermée). Désormais le
retour porte l'échec de façon non ambiguë, nomme le stash resté intact pour une
récupération manuelle, et `lister_stash_agents` permet de constater
l'accumulation sans rien supprimer.
"""

import subprocess

import pytest

from tools.git_safety import (
    git_finalize_agent_branch,
    git_prepare_agent_branch,
    lister_stash_agents,
)


def _git(repo, *args):
    """Exécute une commande git dans le dépôt, retourne (code, stdout, stderr)."""
    res = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


@pytest.fixture
def depot_git(tmp_path):
    """Dépôt Git réel : commit initial sur 'main' + remote canonique attendu."""
    repo = tmp_path / "depot_test"
    repo.mkdir()
    code, _, _ = _git(repo, "init", "-b", "main")
    if code != 0:
        # Git antérieur à 2.28 : initialiser puis renommer master en main
        _git(repo, "init")
        _git(repo, "checkout", "-b", "main")
    _git(repo, "config", "user.email", "test_unitaire@local")
    _git(repo, "config", "user.name", "Test Unitaire")
    # Remote canonique exigé par _require_canonical_repo (lecture locale, aucun réseau)
    _git(repo, "remote", "add", "origin", "https://github.com/Axellum/moteur_agents.git")
    (repo / "config.json").write_text(
        "ligne_1 = valeur_base\nligne_2 = valeur_base\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit initial")
    return repo


def test_cycle_nominal_restaure_la_modif_et_depile(depot_git):
    """Stash → branche éphémère → finalisation : modif restaurée, aucun stash résiduel."""
    repo = str(depot_git)
    config = depot_git / "config.json"
    config.write_text("ligne_1 = valeur_user\nligne_2 = valeur_base\n", encoding="utf-8")

    branch = git_prepare_agent_branch("session_nominale", repo_path=repo)
    assert branch.startswith("agent/run_")
    assert "user_pre_agent_session_nominale" in _git(repo, "stash", "list")[1]
    assert config.read_text(encoding="utf-8") == "ligne_1 = valeur_base\nligne_2 = valeur_base\n"

    # Travail de l'agent sur un AUTRE fichier : pas de conflit au pop
    (depot_git / "note_agent.txt").write_text("travail de l'agent", encoding="utf-8")

    statut = git_finalize_agent_branch(
        branch, success=True, session_id="session_nominale", repo_path=repo
    )
    assert "restaurées" in statut
    # La modification locale utilisateur est revenue dans le working tree
    assert config.read_text(encoding="utf-8") == "ligne_1 = valeur_user\nligne_2 = valeur_base\n"
    # Aucun stash résiduel : le cycle complet a dépilé le stash utilisateur
    _, stash_list, _ = _git(repo, "stash", "list")
    assert "user_pre_agent_" not in stash_list


def test_conflit_pop_echec_non_ambigu_et_stash_intact(depot_git):
    """Pop en conflit : échec clair, stash conservé, identifiant nommé dans le message."""
    repo = str(depot_git)
    config = depot_git / "config.json"
    # Modification locale utilisateur sur la ligne 1
    config.write_text("ligne_1 = valeur_user\nligne_2 = valeur_base\n", encoding="utf-8")
    branch = git_prepare_agent_branch("session_conflit", repo_path=repo)

    # L'agent modifie LA MÊME ligne sur sa branche éphémère → conflit garanti au pop
    config.write_text("ligne_1 = valeur_agent\nligne_2 = valeur_base\n", encoding="utf-8")

    statut = git_finalize_agent_branch(
        branch, success=True, session_id="session_conflit", repo_path=repo
    )

    # L'échec est non ambigu : plus de « Fusion effectuée avec succès. Attention… »
    assert "Fusion effectuée avec succès" not in statut
    assert "ÉCHEC" in statut
    # L'identifiant exact du stash resté intact est nommé pour récupération manuelle
    assert "stash@{0}" in statut
    assert "git stash apply stash@{0}" in statut
    # Le stash est TOUJOURS présent dans la pile (git ne le dépile qu'en cas de succès)
    _, stash_list, _ = _git(repo, "stash", "list")
    assert "user_pre_agent_session_conflit" in stash_list


def test_lister_stash_agents_filtre_agents_et_ne_supprime_rien(depot_git):
    """lister_stash_agents : seulement les user_pre_agent_*, sans aucune suppression."""
    repo = str(depot_git)
    for nom in ("session_1", "session_2"):
        (depot_git / f"fichier_{nom}.txt").write_text("contenu", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "stash", "push", "-m", f"user_pre_agent_{nom}")
    # Un stash utilisateur ordinaire créé à côté doit être ignoré
    (depot_git / "fichier_manuel.txt").write_text("contenu", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "stash", "push", "-m", "wip manuel de l'utilisateur")

    stashes = lister_stash_agents(repo)
    assert len(stashes) == 2
    messages = [s["message"] for s in stashes]
    assert any("user_pre_agent_session_1" in m for m in messages)
    assert any("user_pre_agent_session_2" in m for m in messages)
    assert not any("wip manuel" in m for m in messages)
    for s in stashes:
        assert s["id"].startswith("stash@{")
        assert s["age"]
    # Aucune suppression : les 3 stash sont toujours présents dans la pile
    assert len(_git(repo, "stash", "list")[1].splitlines()) == 3
