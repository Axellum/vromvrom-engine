import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import subprocess

from scripts.inventaire_stashs_agents import lister_stashs


def test_inventaire_stashs(tmp_path):
    # Créer un dépôt git temporaire
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    # Créer un fichier et le commiter
    (repo / "test.txt").write_text("contenu initial")
    subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Modifier le fichier et créer un fichier non suivi (untracked)
    (repo / "test.txt").write_text("modifié")
    (repo / "untracked.txt").write_text("untracked")

    # Faire un stash avec le tag agent
    subprocess.run(["git", "stash", "push", "-u", "-m", "user_pre_agent_test"], cwd=repo, check=True)

    # Tester la liste avec filtrage (tous=False)
    stashs = lister_stashs(str(repo), tous=False)
    assert len(stashs) == 1
    assert "user_pre_agent_test" in stashs[0]["message"]
    assert "test.txt" in stashs[0]["fichiers_suivis"]
    assert "untracked.txt" in stashs[0]["fichiers_non_suivis"]

    # Vérifier que le stash est toujours présent (lecture seule)
    res = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True, check=True)
    assert "user_pre_agent_test" in res.stdout

def test_filtre_stashs(tmp_path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    # Créer un fichier et le commiter
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Modifier et faire un stash sans le tag agent
    (repo / "a.txt").write_text("mod")
    subprocess.run(["git", "stash", "push", "-m", "autre_stash"], cwd=repo, check=True)

    # Avec tous=False (filtrage activé), devrait être vide
    stashs = lister_stashs(str(repo), tous=False)
    assert len(stashs) == 0

    # Avec tous=True (filtrage désactivé), devrait être présent
    stashs = lister_stashs(str(repo), tous=True)
    assert len(stashs) == 1
    assert "autre_stash" in stashs[0]["message"]
