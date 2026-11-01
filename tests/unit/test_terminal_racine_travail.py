"""
tests/unit/test_terminal_racine_travail.py — Où le terminal de l'agent s'exécute (#T389).

`run_terminal_command` lançait ses commandes dans le répertoire du PROCESSUS
serveur, jamais dans le clone de la tâche. Mesuré le 18/08, tâche 9 jouée sur
`moteur_agents_dc2` :

    ls: cannot access '/home/deck/dev_station/tests/unit': No such file or directory
    find: '/home/deck/dev_station/tests': No such file or directory

L'agent explorait donc un dépôt qui n'était pas le sien, ne trouvait aucun des
fichiers à modifier, et terminait sans rien écrire — le pipeline se déclarant
« satisfait » sur une branche vide. #T388 avait corrigé `read_file`/`write_file`,
mais le terminal a son propre répertoire courant : c'est par lui que l'agent
EXPLORE, donc l'oublier suffisait à faire échouer toutes les tâches.
"""

import os
import sys

import pytest

from tools.system import definir_racine_travail, reinitialiser_racine_travail
from tools.terminal import run_terminal_command


@pytest.fixture
def racine_propre():
    """Aucune racine ne doit fuir d'un test à l'autre."""
    jeton = definir_racine_travail(None)
    yield
    reinitialiser_racine_travail(jeton)


def test_la_commande_s_execute_dans_la_racine_de_la_tache(tmp_path, monkeypatch, racine_propre):
    """Le cœur du défaut : la commande doit voir le contenu du clone, pas celui du serveur."""
    faux_workspace = tmp_path / "dev_station"
    clone = faux_workspace / "clone"
    (clone / "tests").mkdir(parents=True)
    (clone / "tests" / "marqueur.txt").write_text("dans le clone", encoding="utf-8")
    # Un homonyme au niveau du serveur : si le cwd est mauvais, il ressortira.
    (faux_workspace / "tests").mkdir()
    (faux_workspace / "tests" / "leurre.txt").write_text("hors clone", encoding="utf-8")

    # Script helper autorisé
    script = faux_workspace / "lister.py"
    script.write_text("import os; print(os.listdir('tests'))", encoding="utf-8")

    monkeypatch.setattr("tools.system._WORKSPACE_ROOT", os.path.realpath(str(faux_workspace)))
    monkeypatch.chdir(faux_workspace)

    jeton = definir_racine_travail(str(clone))
    try:
        cmd = f'python "{script}"'
        sortie = run_terminal_command(cmd)
    finally:
        reinitialiser_racine_travail(jeton)

    assert "marqueur.txt" in sortie
    assert "leurre.txt" not in sortie


def test_sans_racine_le_comportement_historique_est_conserve(tmp_path, monkeypatch, racine_propre):
    """Hors tâche de fond (chat, vocal), rien ne change : le cwd reste celui du serveur."""
    faux_workspace = tmp_path / "dev_station"
    (faux_workspace / "tests").mkdir(parents=True)
    (faux_workspace / "tests" / "leurre.txt").write_text("hors clone", encoding="utf-8")

    script = faux_workspace / "lister.py"
    script.write_text("import os; print(os.listdir('tests'))", encoding="utf-8")

    monkeypatch.setattr("tools.system._WORKSPACE_ROOT", os.path.realpath(str(faux_workspace)))
    monkeypatch.chdir(faux_workspace)

    cmd = f'python "{script}"'
    sortie = run_terminal_command(cmd)

    assert "leurre.txt" in sortie


def test_un_chemin_absolu_reste_absolu(tmp_path, monkeypatch, racine_propre):
    """Le `cwd` ne réinterprète pas ce que l'agent a déjà pleinement écrit."""
    faux_workspace = tmp_path / "dev_station"
    clone = faux_workspace / "clone"
    clone.mkdir(parents=True)
    cible = faux_workspace / "ailleurs"
    cible.mkdir()
    (cible / "vu.txt").write_text("x", encoding="utf-8")

    script = faux_workspace / "lister_abs.py"
    # Le script liste le premier argument passé
    script.write_text("import os, sys; print(os.listdir(sys.argv[1]))", encoding="utf-8")

    monkeypatch.setattr("tools.system._WORKSPACE_ROOT", os.path.realpath(str(faux_workspace)))
    monkeypatch.chdir(faux_workspace)

    jeton = definir_racine_travail(str(clone))
    try:
        cmd = f'python "{script}" "{cible}"'
        sortie = run_terminal_command(cmd)
    finally:
        reinitialiser_racine_travail(jeton)

    assert "vu.txt" in sortie

