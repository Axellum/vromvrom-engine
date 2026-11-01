"""
tests/unit/test_nettoyer_branches_agent.py — Outil de ménage des branches d'agent.

Le script sert deux familles : `agent/run_*` (une par session) et `task/*` (une
par tâche DreamCoder, poussée sur origin depuis #T327). Ces tests figent la
propriété qui compte, et une seule : **une branche qui porte du travail non
fusionné n'est jamais supprimée**. Le 18/08, cinq branches `task/*` portaient
2,9 M tokens de travail que la base avait oublié de nommer (#T396) — un outil
de ménage qui les aurait effacées aurait transformé un oubli en perte sèche.

Les dépôts de test sont de vrais dépôts Git jetables : `git init` est la seule
façon honnête de tester un outil qui lit l'état de Git.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "nettoyer_branches_agent.py"


def _git(depot: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=depot, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return (res.stdout or "").strip()


def _lancer(depot: Path, *args: str) -> subprocess.CompletedProcess:
    """Lance le script sur `depot` avec l'interpréteur courant (portable Windows/Linux)."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--depot", str(depot), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    """Dépôt jetable avec un commit initial sur `master`."""
    d = tmp_path / "depot"
    d.mkdir()
    # -b master : git récent crée `main` par défaut, ce qui ferait diverger le test
    # de la réalité du dépôt (master) sans rien prouver.
    subprocess.run(["git", "init", "-b", "master"], cwd=d, capture_output=True)
    _git(d, "config", "user.email", "test@example.invalid")
    _git(d, "config", "user.name", "Test")
    (d / "README.md").write_text("initial", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-m", "commit initial")
    return d


def _branche_avec_travail(depot: Path, nom: str) -> None:
    _git(depot, "checkout", "-b", nom)
    (depot / f"{nom.replace('/', '_')}.txt").write_text("travail", encoding="utf-8")
    _git(depot, "add", ".")
    _git(depot, "commit", "-m", f"travail sur {nom}")
    _git(depot, "checkout", "master")


# ── La règle de sûreté ───────────────────────────────────────────────────────

def test_une_branche_qui_porte_du_travail_n_est_jamais_supprimee(depot):
    """La régression qui compte : #T396 en un test."""
    _branche_avec_travail(depot, "task/20_1787055570")
    _git(depot, "branch", "agent/run_vide_1")

    res = _lancer(depot, "--supprimer")

    assert res.returncode == 0, res.stderr
    assert "task/20_1787055570" in _git(depot, "branch", "--list", "task/*")
    assert "NON fusionné" in res.stdout
    # …et la branche réellement vide, elle, est bien partie.
    assert _git(depot, "branch", "--list", "agent/run_vide_1") == ""


def test_les_deux_familles_de_branches_sont_couvertes(depot):
    """`agent/run_*` (sessions) ET `task/*` (tâches DreamCoder, #T327)."""
    _git(depot, "branch", "agent/run_abc_123")
    _git(depot, "branch", "task/42_1787000000")
    _git(depot, "branch", "feat/une-vraie-branche")

    res = _lancer(depot, "--supprimer")

    assert res.returncode == 0, res.stderr
    assert _git(depot, "branch", "--list", "agent/run_*") == ""
    assert _git(depot, "branch", "--list", "task/*") == ""
    # Une branche de travail ordinaire n'est pas du ressort de cet outil.
    assert "feat/une-vraie-branche" in _git(depot, "branch", "--list", "feat/*")


# ── Inventaire : ne rien casser tant qu'on n'a pas dit --supprimer ───────────

def test_l_inventaire_ne_supprime_rien(depot):
    _git(depot, "branch", "agent/run_vide_1")

    res = _lancer(depot)

    assert "serait supprimée" in res.stdout.lower()
    assert "agent/run_vide_1" in _git(depot, "branch", "--list", "agent/run_*")


def test_l_inventaire_ne_deplace_pas_le_head(depot):
    """Lister n'est pas nettoyer : le HEAD de l'utilisateur ne bouge pas."""
    _git(depot, "checkout", "-b", "agent/run_courante")

    res = _lancer(depot)

    assert res.returncode == 0, res.stderr
    assert _git(depot, "rev-parse", "--abbrev-ref", "HEAD") == "agent/run_courante"


# ── Session mal terminée : le dépôt est resté SUR la branche éphémère ────────

def test_le_depot_reste_sur_sa_branche_ephemere_est_debloque(depot):
    _git(depot, "checkout", "-b", "agent/run_courante")

    res = _lancer(depot, "--supprimer")

    assert res.returncode == 0, res.stderr
    assert _git(depot, "rev-parse", "--abbrev-ref", "HEAD") == "master"
    assert _git(depot, "branch", "--list", "agent/run_courante") == ""


# ── Les deux pièges des variantes produites par l'agent ──────────────────────

def test_un_worktree_est_reconnu_comme_depot(depot, tmp_path):
    """
    Les trois variantes livrées testaient `os.path.isdir(depot/'.git')`. Dans un
    worktree — le dépôt en utilise — `.git` est un FICHIER : l'outil refusait de
    tourner là où on en a le plus besoin.
    """
    arbre = tmp_path / "worktree"
    _git(depot, "worktree", "add", "-b", "annexe", str(arbre))
    assert (arbre / ".git").is_file(), "prérequis : .git est bien un fichier ici"

    res = _lancer(arbre)

    assert res.returncode == 0, res.stderr
    assert "n'est pas un dépôt Git" not in res.stdout + res.stderr


def test_la_base_est_detectee_et_non_supposee(tmp_path):
    """Un dépôt sur `main` ne doit pas être comparé à un `master` inexistant."""
    d = tmp_path / "sur_main"
    d.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=d, capture_output=True)
    _git(d, "config", "user.email", "test@example.invalid")
    _git(d, "config", "user.name", "Test")
    (d / "README.md").write_text("initial", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-m", "commit initial")
    _git(d, "branch", "agent/run_vide_1")

    res = _lancer(d, "--supprimer")

    assert res.returncode == 0, res.stderr
    assert "base détectée : main" in res.stdout
    assert _git(d, "branch", "--list", "agent/run_*") == ""


def test_hors_depot_git_le_script_refuse_proprement(tmp_path):
    ailleurs = tmp_path / "pas_un_depot"
    ailleurs.mkdir()

    res = _lancer(ailleurs)

    assert res.returncode == 1
    assert "pas un dépôt Git" in res.stderr
