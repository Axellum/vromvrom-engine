"""
[#T441] La consigne de livrable doit atteindre le PLANNER, pas seulement l'exécuteur.

Le pipeline reçoit `work_prompt`, qui porte le marqueur `[LIVRABLE ATTENDU`.
Mais le Planner reconstruit son prompt depuis le PAYLOAD, bâti par le routeur à
partir de la seule description de la tâche. Le marqueur n'y figurait donc pas, et
le critère `fichier_existe` de #T440 n'était jamais posé — mesuré le 21/08 sur la
tâche 62, deux fois de suite, sans une ligne « Livrable d'analyse ajouté » au
journal.
"""

from agents.dreamer_agent import consigne_de_livrable
from agents.planner import _chemin_du_livrable_attendu, _exiger_le_livrable_d_analyse

DEPOT = "H:" + chr(92) + "clone"
TACHE_ANALYSE = {
    "id": 62,
    "title": "[TAB5] A ANALYSER — micro_wake_word",
    "description": "Perimetre strict : un seul fichier.",
}


class _Payload:
    def __init__(self, contexte=""):
        self.relevant_context = contexte
        self.task_objective = "Analyser le wake word"


def _propager(payload, tache, depot):
    """Ce que fait le harnais avant de lancer le pipeline."""
    consigne = consigne_de_livrable(tache, depot)
    if consigne:
        payload.relevant_context = (payload.relevant_context or "") + consigne
    return payload


def test_le_contexte_du_payload_porte_la_consigne():
    payload = _propager(_Payload("Contexte initial."), TACHE_ANALYSE, DEPOT)
    assert "[LIVRABLE ATTENDU" in payload.relevant_context
    assert "Contexte initial." in payload.relevant_context, "le contexte existant est conservé"


def test_le_planner_y_retrouve_le_chemin_du_rapport():
    """Bout en bout : ce que le harnais pose, le Planner doit savoir le lire."""
    payload = _propager(_Payload(), TACHE_ANALYSE, DEPOT)
    prompt_planner = f"Objectif : {payload.task_objective}\nContexte : {payload.relevant_context}"

    chemin = _chemin_du_livrable_attendu(prompt_planner)

    assert chemin is not None
    assert chemin.startswith(DEPOT)
    assert chemin.endswith(".md")


def test_le_critere_est_alors_bien_pose():
    payload = _propager(_Payload(), TACHE_ANALYSE, DEPOT)
    prompt_planner = f"Objectif : x\nContexte : {payload.relevant_context}"

    criteres = _exiger_le_livrable_d_analyse([], prompt_planner)

    assert len(criteres) == 1
    assert criteres[0].type == "fichier_existe"


def test_une_tache_de_code_ne_touche_pas_au_contexte():
    tache = {"id": 42, "title": "Corriger l'indentation PEP 8", "description": "core/x.py"}
    payload = _propager(_Payload("Contexte initial."), tache, DEPOT)
    assert payload.relevant_context == "Contexte initial."
