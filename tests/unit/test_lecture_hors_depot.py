"""
[#T425] Une analyse qui porte sur un autre projet doit pouvoir le LIRE.

Le prompt de tâche autonome imposait « des chemins ABSOLUS sous cette racine
pour read_file/write_file/run_terminal_command » — la racine étant le clone du
moteur. Les tâches Tab5 (T421 firmware, T422 wake word, T423 LVGL…) ne pouvaient
donc pas lire le firmware, qui vit dans `00ProjetTab/`, à côté.

Le garde-fou réel de `read_file` autorise déjà tout l'espace de travail
(`_resolve_within_workspace`) : c'était le prompt, et lui seul, qui interdisait.
La règle dit maintenant ce qui est vrai — on écrit ici, on lit dans l'espace.
"""

import os

from tools import system as systeme
from tools.system import read_file


def test_read_file_autorise_tout_l_espace_de_travail(tmp_path, monkeypatch):
    """
    Le garde-fou porte sur l'ESPACE DE TRAVAIL, pas sur le dépôt du moteur.

    Un projet voisin (le firmware Tab5 sous `00ProjetTab/`) doit être lisible
    depuis une tâche qui tourne dans le clone du moteur.
    """
    espace = tmp_path / "espace"
    (espace / "moteur_agents").mkdir(parents=True)
    voisin = espace / "00ProjetTab"
    voisin.mkdir()
    (voisin / "README.md").write_text("firmware Tab5", encoding="utf-8")

    monkeypatch.setattr(systeme, "_WORKSPACE_ROOT", os.path.realpath(str(espace)))

    contenu = read_file(str(voisin / "README.md"))
    assert contenu == "firmware Tab5", contenu[:200]


def test_read_file_refuse_hors_espace_de_travail():
    """La permission s'arrête à l'espace de travail — elle ne devient pas globale."""
    interdit = os.path.join(os.path.abspath(os.sep), "Windows", "System32", "drivers", "etc", "hosts")
    contenu = read_file(interdit)
    assert "Erreur" in contenu or "refus" in contenu.lower(), contenu[:200]
