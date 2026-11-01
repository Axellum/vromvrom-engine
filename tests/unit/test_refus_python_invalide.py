"""
tests/unit/test_refus_python_invalide.py — Un livrable qui ne parse pas (#T394).

Mesuré le 18/08 sur la branche `task/18_1787055040`, produite en autonomie :
`services/ha_state_query.py` livré avec une chaîne non fermée —
« SyntaxError: unterminated string literal », ligne 279. Le module ne
s'importait plus. Ce fichier sert le VOCAL, le chemin le plus utilisé de la
production.

Le pipeline avait pourtant déclaré la tâche réussie, le commit était fait, et
la branche poussée sur GitHub. Aucune couche ne vérifiait que le code produit
restait du code.

Le contrôle est volontairement minimal : ni style, ni logique, ni pertinence —
seulement le plancher en dessous duquel un livrable n'a aucune valeur.
"""

import subprocess

from agents.dreamer_agent import _python_invalide_dans_index


def _depot(tmp_path):
    depot = tmp_path / "slot"
    depot.mkdir()

    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=str(depot),
                              capture_output=True, text=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@local")
    git("config", "user.name", "T")
    return depot, git


def test_du_python_valide_passe(tmp_path):
    depot, git = _depot(tmp_path)
    (depot / "module.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    git("add", "-A")

    assert _python_invalide_dans_index(str(depot)) == []


def test_une_chaine_non_fermee_est_refusee(tmp_path):
    """Le cas réel : `"opening": "Le volet es` — la ligne 279 de task/18."""
    depot, git = _depot(tmp_path)
    (depot / "casse.py").write_text(
        'MESSAGES = {\n    "opening": "Le volet es\n}\n', encoding="utf-8")
    git("add", "-A")

    problemes = _python_invalide_dans_index(str(depot))

    assert len(problemes) == 1
    assert "casse.py" in problemes[0]
    assert "ligne" in problemes[0]


def test_les_fichiers_non_python_sont_ignores(tmp_path):
    """Un Markdown mal formé n'est pas une erreur de syntaxe."""
    depot, git = _depot(tmp_path)
    (depot / "notes.md").write_text('# titre "non ferme\n', encoding="utf-8")
    (depot / "compo.tsx").write_text("const x = (\n", encoding="utf-8")
    git("add", "-A")

    assert _python_invalide_dans_index(str(depot)) == []


def test_un_fichier_supprime_ne_bloque_pas(tmp_path):
    """
    Une tâche a le droit de supprimer un fichier : son chemin apparaît encore
    dans l'index, mais il n'existe plus sur le disque.
    """
    depot, git = _depot(tmp_path)
    (depot / "vieux.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")
    (depot / "vieux.py").unlink()
    git("add", "-A")

    assert _python_invalide_dans_index(str(depot)) == []


def test_index_vide(tmp_path):
    depot, _ = _depot(tmp_path)
    assert _python_invalide_dans_index(str(depot)) == []


def test_plusieurs_fichiers_casses_sont_tous_rapportes(tmp_path):
    depot, git = _depot(tmp_path)
    (depot / "a.py").write_text("def (:\n", encoding="utf-8")
    (depot / "b.py").write_text("class ???\n", encoding="utf-8")
    (depot / "c.py").write_text("ok = True\n", encoding="utf-8")
    git("add", "-A")

    problemes = _python_invalide_dans_index(str(depot))

    assert len(problemes) == 2
    assert not any("c.py" in p for p in problemes)
