"""
[#T440] « Terminé » doit impliquer « le rapport existe ».

Le prompt d'une tâche d'analyse exige un rapport écrit (#T424). Mais une consigne
textuelle ne contraint rien : mesuré le 21/08 sur la tâche 62 — l'agent a lu le
fichier visé, interrogé un modèle local, et s'est arrêté là. Le Reviewer a jugé
le travail satisfaisant, le pipeline s'est déclaré terminé, et la branche est
restée vide. Aucun critère d'acceptation n'exigeait le livrable.

Le critère est donc ajouté par le code, pas laissé au jugement du modèle.
"""

from agents.planner import _chemin_du_livrable_attendu, _exiger_le_livrable_d_analyse

CHEMIN_RAPPORT = "H:" + chr(92) + "clone" + chr(92) + "docs" + chr(92) + "analyses" + chr(92) + "tache_62_wake_word.md"

PROMPT = (
    "[DEPOT DE TRAVAIL]" + chr(10)
    + "Analyse le wake word." + chr(10) + chr(10)
    + "[LIVRABLE ATTENDU - TACHE D ANALYSE]" + chr(10)
    + "Ton livrable est un rapport Markdown a ecrire avec write_file dans :" + chr(10)
    + "    " + CHEMIN_RAPPORT + chr(10)
    + "Il doit contenir : (1) ce que tu as CONSTATE..." + chr(10)
)


def test_le_chemin_du_rapport_est_extrait():
    assert _chemin_du_livrable_attendu(PROMPT) == r"H:\clone\docs\analyses\tache_62_wake_word.md"


def test_un_prompt_sans_livrable_ne_donne_rien():
    assert _chemin_du_livrable_attendu("Corrige l'indentation de core/x.py") is None
    assert _chemin_du_livrable_attendu("") is None


def test_le_critere_est_ajoute_au_contrat():
    criteres = _exiger_le_livrable_d_analyse([{"type": "commande", "cible": "pytest"}], PROMPT)
    assert len(criteres) == 2
    ajoute = criteres[-1]
    assert ajoute.type == "fichier_existe"
    assert ajoute.valeur.endswith("tache_62_wake_word.md")


def test_un_contrat_vide_recoit_quand_meme_le_critere():
    """Le cas réel : le modèle n'avait produit aucun critère sur le livrable."""
    criteres = _exiger_le_livrable_d_analyse([], PROMPT)
    assert len(criteres) == 1
    assert criteres[0].type == "fichier_existe"


def test_l_ajout_est_idempotent():
    """Un plan correctif rejoué ne doit pas empiler le même critère."""
    une_fois = _exiger_le_livrable_d_analyse([], PROMPT)
    deux_fois = _exiger_le_livrable_d_analyse(une_fois, PROMPT)
    assert len(deux_fois) == 1


def test_une_tache_de_code_garde_son_contrat_intact():
    """Aucune tâche de code ne doit se voir imposer un rapport Markdown."""
    contrat = [{"type": "commande", "cible": "pytest tests/unit/test_x.py"}]
    assert _exiger_le_livrable_d_analyse(contrat, "Corrige l'indentation PEP 8") == contrat
