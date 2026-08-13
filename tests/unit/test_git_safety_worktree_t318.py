"""
test_git_safety_worktree_t318.py — [#T318] git_safety ne doit plus détruire de
travail non commité. Tests sur de VRAIS dépôts Git temporaires, sans mocks.

Incident reproduit le 12/08 (worktree + moteur lancé depuis celui-ci) :
`tools/comptes.py` et son fichier de tests — 12 tests verts, non commités — ont
disparu du disque. Enchaînement mesuré :
  1. `git_prepare_agent_branch` stashe les fichiers non suivis (ils quittent le
     disque) ;
  2. `git_finalize_agent_branch` tente `checkout main` puis `checkout master` —
     impossible dans un worktree, la branche est déjà prise par le dépôt
     principal (`fatal: 'master' is already used by worktree at ...`) ;
  3. le `return` d'erreur saute la restauration du stash. Les fichiers restent
     enfermés dans un stash que rien ne signale.

S'y ajoutaient deux défauts du même code, couverts ici :
  - le retour se faisait sur 'main'/'master' EN DUR alors que la branche de
    départ était calculée puis jetée (travail fusionné dans master au lieu de
    la branche courante) ;
  - sur échec, `reset --hard` + `clean -fd` s'exécutaient APRÈS le retour sur la
    branche parente, donc sur l'arbre de travail de l'utilisateur.
"""

import subprocess

import pytest

from tools.git_safety import (
    est_worktree_lie,
    git_finalize_agent_branch,
    git_prepare_agent_branch,
    git_rollback_checkpoint,
)


def _git(repo, *args):
    """Exécute une commande git dans le dépôt, retourne (code, stdout, stderr)."""
    res = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def _branche_courante(repo) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1]


@pytest.fixture
def depot_master(tmp_path):
    """Dépôt Git réel sur 'master' + remote canonique exigé par le garde-fou."""
    repo = tmp_path / "principal"
    repo.mkdir()
    code, _, _ = _git(repo, "init", "-b", "master")
    if code != 0:  # Git < 2.28
        _git(repo, "init")
        _git(repo, "checkout", "-b", "master")
    _git(repo, "config", "user.email", "test_unitaire@local")
    _git(repo, "config", "user.name", "Test Unitaire")
    _git(repo, "remote", "add", "origin", "https://github.com/Axellum/moteur_agents.git")
    (repo / "README.md").write_text("depot\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit initial")
    return repo


@pytest.fixture
def worktree_lie(depot_master, tmp_path):
    """Worktree lié au dépôt principal, sur sa propre branche — le cas du 12/08."""
    wt = tmp_path / "worktree_claude"
    code, _, err = _git(depot_master, "worktree", "add", "-b", "claude/travail", str(wt))
    if code != 0:
        pytest.skip(f"git worktree indisponible : {err}")
    yield wt
    # Détacher le worktree avant que pytest ne supprime tmp_path
    _git(depot_master, "worktree", "remove", "--force", str(wt))


# ── Détection ────────────────────────────────────────────────────────────────

def test_worktree_lie_detecte(depot_master, worktree_lie):
    """Le worktree est reconnu comme lié, le dépôt principal comme ordinaire."""
    assert est_worktree_lie(str(worktree_lie)) is True
    assert est_worktree_lie(str(depot_master)) is False


# ── Le cas mesuré : plus aucune perte ────────────────────────────────────────

def test_preparation_refusee_dans_un_worktree_fichier_intact(worktree_lie):
    """Dans un worktree : refus explicite, fichier non suivi TOUJOURS sur le disque."""
    travail = worktree_lie / "comptes.py"
    travail.write_text("# 12 tests verts, pas encore commite\n", encoding="utf-8")
    branche_avant = _branche_courante(worktree_lie)

    resultat = git_prepare_agent_branch("chat_repro", repo_path=str(worktree_lie))

    assert resultat.startswith("Erreur")
    assert "#T318" in resultat
    # Le fichier n'a pas bougé — c'est tout l'enjeu de la tâche
    assert travail.exists()
    assert travail.read_text(encoding="utf-8") == "# 12 tests verts, pas encore commite\n"
    # Aucun stash créé, aucune branche créée, HEAD inchangé
    assert _git(worktree_lie, "stash", "list")[1] == ""
    assert _branche_courante(worktree_lie) == branche_avant
    assert "agent/run_" not in _git(worktree_lie, "branch", "--list")[1]


def test_rollback_checkpoint_refuse_dans_un_worktree(worktree_lie):
    """git_rollback_checkpoint (reset --hard + clean -fd) refuse aussi le worktree."""
    non_suivi = worktree_lie / "brouillon.py"
    non_suivi.write_text("travail en cours\n", encoding="utf-8")
    (worktree_lie / "README.md").write_text("modifie par un humain\n", encoding="utf-8")

    resultat = git_rollback_checkpoint(repo_path=str(worktree_lie))

    assert resultat.startswith("Erreur")
    assert "#T318" in resultat
    assert non_suivi.exists()
    assert (worktree_lie / "README.md").read_text(encoding="utf-8") == "modifie par un humain\n"


# ── Retour sur la branche de DÉPART, pas sur master en dur ───────────────────

def test_retour_sur_la_branche_de_depart_pas_master(depot_master):
    """Session lancée depuis une branche de feature : le merge y atterrit, pas sur master."""
    repo = depot_master
    _git(repo, "checkout", "-b", "feat/en-cours")

    branche = git_prepare_agent_branch("sess_feature", repo_path=str(repo))
    assert branche.startswith("agent/run_")

    (repo / "sortie_agent.txt").write_text("travail agent\n", encoding="utf-8")
    statut = git_finalize_agent_branch(
        branche, success=True, session_id="sess_feature", repo_path=str(repo)
    )

    assert "Fusion effectuée avec succès" in statut
    # On est revenu sur la branche de départ — pas sur master
    assert _branche_courante(repo) == "feat/en-cours"
    # Le travail de l'agent est bien sur feat/en-cours, et master est intact
    assert (repo / "sortie_agent.txt").exists()
    assert "sortie_agent.txt" not in _git(repo, "ls-tree", "-r", "--name-only", "master")[1]
    # La clé de config temporaire a été nettoyée
    assert _git(repo, "config", "--local", "--get", f"branch.{branche}.agentOrigin")[1] == ""


# ── Le stash est restauré même quand le retour échoue ────────────────────────

def test_stash_restaure_meme_si_le_retour_de_branche_echoue(depot_master):
    """Aucune branche de retour disponible : erreur signalée MAIS stash dépilé."""
    repo = depot_master
    _git(repo, "checkout", "-b", "feat/ephemere")
    travail = repo / "comptes.py"
    travail.write_text("# travail non commite\n", encoding="utf-8")

    branche = git_prepare_agent_branch("sess_orpheline", repo_path=str(repo))
    assert branche.startswith("agent/run_")
    # Le stash a bien retiré le fichier du disque (comportement hors worktree)
    assert not travail.exists()

    # On rend TOUTES les branches de retour inaccessibles (départ + replis)
    _git(repo, "branch", "-D", "feat/ephemere")
    _git(repo, "branch", "-D", "master")

    statut = git_finalize_agent_branch(
        branche, success=True, session_id="sess_orpheline", repo_path=str(repo)
    )

    assert "Erreur lors du retour à la branche principale" in statut
    # ⚠️ Le cœur de #T318 : le travail de l'utilisateur est revenu malgré l'erreur
    assert travail.exists()
    assert travail.read_text(encoding="utf-8") == "# travail non commite\n"
    assert "user_pre_agent_" not in _git(repo, "stash", "list")[1]


# ── Rollback : ne détruit plus les fichiers non suivis ───────────────────────

def test_rollback_conserve_les_fichiers_non_suivis(depot_master):
    """success=False : les modifications suivies sont annulées, les fichiers non suivis restent."""
    repo = depot_master
    branche = git_prepare_agent_branch("sess_echec", repo_path=str(repo))
    assert branche.startswith("agent/run_")

    # Un humain écrit dans le même dépôt PENDANT que l'agent travaille
    ecrit_pendant = repo / "note_humaine.md"
    ecrit_pendant.write_text("ecrit pendant le run\n", encoding="utf-8")
    # L'agent, lui, modifie un fichier suivi
    (repo / "README.md").write_text("modifie par l agent\n", encoding="utf-8")

    statut = git_finalize_agent_branch(
        branche, success=False, session_id="sess_echec", repo_path=str(repo)
    )

    assert "Rollback effectué" in statut
    # La modification de l'agent sur un fichier SUIVI est annulée
    assert (repo / "README.md").read_text(encoding="utf-8") == "depot\n"
    # ⚠️ Mais le fichier non suivi survit : `clean -fd` ne s'exécute plus
    assert ecrit_pendant.exists()
    assert ecrit_pendant.read_text(encoding="utf-8") == "ecrit pendant le run\n"
    # La branche éphémère a bien été détruite et on est revenu sur master
    assert _branche_courante(repo) == "master"
    assert branche not in _git(repo, "branch", "--list")[1]
