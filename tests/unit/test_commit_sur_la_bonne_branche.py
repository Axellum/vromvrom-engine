"""
[#T432] Le harnais commitait sur la branche où l'agent l'avait laissé.

Mesuré le 21/08 sur le Deck : la tâche 66 a produit du vrai travail — un
correctif, ses tests, et un rapport d'analyse — puis l'agent, qui dispose
d'outils Git, a ramené le dépôt sur `master`. Le `git commit` du harnais a donc
écrit sur `master`, et la vérification `orig_branch..HEAD` (où `orig_branch` EST
`master`) n'a vu aucune différence.

Verdict rendu : « la branche task/66 est identique à master ». Trois tâches
déclarées en échec ce jour-là pour cette seule raison, et leur travail retrouvé
uniquement en inspectant le clone à la main.
"""

import subprocess

import pytest

from agents import dreamer_agent


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def depot(tmp_path):
    """Un dépôt jetable avec un commit initial et une branche de tâche."""
    racine = tmp_path / "clone"
    racine.mkdir()
    _git("init", "-q", "-b", "master", cwd=racine)
    _git("config", "user.email", "test@example.invalid", cwd=racine)
    _git("config", "user.name", "Test", cwd=racine)
    (racine / "fichier.txt").write_text("origine", encoding="utf-8")
    _git("add", "-A", cwd=racine)
    _git("commit", "-qm", "initial", cwd=racine)
    _git("checkout", "-q", "-b", "task/66_1787", cwd=racine)
    return str(racine)


def test_la_branche_courante_est_lue_correctement(depot):
    assert dreamer_agent._branche_courante(depot) == "task/66_1787"


def test_rien_a_faire_si_on_est_deja_sur_la_bonne_branche(depot):
    assert dreamer_agent._revenir_sur_la_branche_de_tache(depot, "task/66_1787") is None
    assert dreamer_agent._branche_courante(depot) == "task/66_1787"


def test_un_retour_sur_master_est_rattrape_avant_le_commit(depot):
    """Le cas réel : l'agent a changé de branche, le commit doit être ramené."""
    _git("checkout", "-q", "master", cwd=depot)
    assert dreamer_agent._branche_courante(depot) == "master"

    erreur = dreamer_agent._revenir_sur_la_branche_de_tache(depot, "task/66_1787")

    assert erreur is None
    assert dreamer_agent._branche_courante(depot) == "task/66_1787"


def test_le_travail_non_commite_survit_au_retour(depot):
    """Ramener la branche ne doit pas jeter ce que l'agent vient d'écrire."""
    _git("checkout", "-q", "master", cwd=depot)
    (open_path := __import__("pathlib").Path(depot) / "livrable.md").write_text(
        "rapport d'analyse", encoding="utf-8")

    dreamer_agent._revenir_sur_la_branche_de_tache(depot, "task/66_1787")

    assert open_path.exists(), "le livrable a été perdu en changeant de branche"
    assert open_path.read_text(encoding="utf-8") == "rapport d'analyse"


def test_une_branche_introuvable_refuse_de_commiter(depot):
    """
    Mieux vaut un échec explicite qu'un commit sur la mauvaise branche : c'est
    ce dernier qui a fait passer du vrai travail pour une branche vide.
    """
    _git("checkout", "-q", "master", cwd=depot)
    erreur = dreamer_agent._revenir_sur_la_branche_de_tache(depot, "task/inexistante")
    assert erreur is not None
    assert "mauvaise branche" in erreur or "a échoué" in erreur
