"""
[#T423] Un agent qui tape `dir` doit apprendre quoi faire, pas réessayer.

Mesuré le 21/08 sur la tâche autonome 49 : l'exécuteur a brûlé ses 10 tours de
boucle ReAct sur `dir`, `find`, puis `dir` encore, sans jamais appeler
`write_file`. Le pipeline s'est ensuite déclaré « satisfait » sur une branche
vide — le motif d'échec numéro un du cycle autonome (17 des 23 échecs).

Deux causes, toutes deux ici :
- `dir`, `type`, `echo`… sont des commandes INTERNES de cmd.exe. Sans shell
  (`shell=False`, choix de sécurité), elles n'ont pas de fichier exécutable, et
  le message « introuvable dans le PATH » invitait à donner un chemin complet ;
- le prompt de l'exécuteur les recommandait explicitement.
"""

import os

import pytest

from agents import executor
from tools import terminal
from tools.terminal import run_terminal_command


@pytest.mark.skipif(os.name != "nt", reason="comportement propre à Windows")
@pytest.mark.parametrize("commande", ["dir", "dir /b core", "type fichier.txt", "echo bonjour"])
def test_une_commande_interne_dit_quoi_faire_a_la_place(commande, monkeypatch):
    """Le message doit nommer les outils qui marchent, pas suggérer un chemin."""
    def _introuvable(*a, **k):
        raise FileNotFoundError(2, "Le fichier spécifié est introuvable")

    monkeypatch.setattr(terminal.subprocess, "run", _introuvable)
    res = run_terminal_command(commande)

    assert "INTERNE de cmd.exe" in res
    assert "mcp_filesystem_list_directory" in res
    assert "cmd /c" in res
    # Le piège d'origine : ne surtout pas renvoyer l'agent chercher un chemin.
    assert "chemin complet" not in res or "aucun chemin" in res


@pytest.mark.skipif(os.name != "nt", reason="comportement propre à Windows")
def test_un_executable_absent_garde_son_message_historique(monkeypatch):
    def _introuvable(*a, **k):
        raise FileNotFoundError(2, "Le fichier spécifié est introuvable")

    monkeypatch.setattr(terminal.subprocess, "run", _introuvable)
    res = run_terminal_command("outil_qui_nexiste_pas --version")

    assert "introuvable dans le PATH" in res
    assert "INTERNE de cmd.exe" not in res


def test_le_prompt_ne_conseille_plus_les_commandes_internes():
    """
    Le prompt de l'exécuteur recommandait 'dir' et 'type' — c'est lui qui
    envoyait l'agent dans le mur.
    """
    prompt = executor._build_system_prompt() if hasattr(executor, "_build_system_prompt") else ""
    if not prompt:
        import inspect
        prompt = inspect.getsource(executor)
    assert "commandes Windows natives ('dir', 'findstr', 'type')" not in prompt
    assert "SANS shell" in prompt
