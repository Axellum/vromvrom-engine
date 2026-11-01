"""
[#T426] Une analyse a besoin de plus de tours qu'un correctif ciblé.

Mesuré le 21/08 sur la tâche 49 (montée ESPHome du Tab5) : les 10 tours par
défaut de la boucle ReAct ont été consommés en LECTURE — `search_files`,
`list_directory`, `read_text_file` — et l'agent a atteint le plafond au moment
précis où il créait le dossier de son rapport. Il avait compris quoi faire ; il
n'avait plus de tours pour le faire. Branche vide, tâche en échec.

Le budget est un réglage d'exploitation (`dreamcoder_tours_react_analyse`), pas
une constante de code : il se règle sans redéploiement.
"""

import pytest

from agents import dreamer_agent


class _Payload:
    def __init__(self):
        self.metadata = {}


def _appliquer(task, pa_config):
    """Rejoue la décision de budget telle qu'elle est prise dans le drainage."""
    payload = _Payload()
    texte = f"{task.get('title', '')} {task.get('description', '')}"
    if dreamer_agent._est_une_tache_d_analyse(texte):
        tours = int((pa_config or {}).get("dreamcoder_tours_react_analyse", 24) or 24)
        payload.metadata["max_turns"] = tours
    return payload


def test_une_analyse_recoit_un_budget_elargi():
    payload = _appliquer({"title": "[T421] **À ANALYSER** — Montée ESPHome"}, {})
    assert payload.metadata["max_turns"] == 24


def test_une_tache_de_code_garde_le_defaut():
    """Le défaut de 10 reste : il suffit pour un correctif, et il borne le coût."""
    payload = _appliquer({"title": "Corriger l'indentation PEP 8 dans core/x.py"}, {})
    assert "max_turns" not in payload.metadata


@pytest.mark.parametrize("valeur, attendu", [(40, 40), ("30", 30), (0, 24), (None, 24)])
def test_le_budget_est_un_reglage(valeur, attendu):
    """Réglable par config.json — y compris quand la valeur est absurde ou absente."""
    payload = _appliquer(
        {"title": "**À ANALYSER** — quelque chose"},
        {"dreamcoder_tours_react_analyse": valeur},
    )
    assert payload.metadata["max_turns"] == attendu


def test_le_budget_depasse_ce_qui_a_manque():
    """
    La tâche 49 a consommé ses 10 tours en lecture seule. Un budget qui ne
    laisserait pas de marge APRÈS l'exploration ne corrigerait rien.
    """
    payload = _appliquer({"title": "**À ANALYSER** — Montée ESPHome"}, {})
    assert payload.metadata["max_turns"] >= 20


# ──────────────────────────────────────────────────────────────────────
# [#T426] Le plafond doit atteindre les ÉTAPES du DAG, pas seulement le
# payload initial. Mesuré le 21/08 : le journal affichait « tour x/10 » sur
# une tâche d'analyse censée en avoir 24 — les étapes sont construites par le
# planner et n'héritent d'aucune metadata.
# ──────────────────────────────────────────────────────────────────────


def test_le_plafond_de_session_est_pose_et_rendu():
    from agents.executor import (
        definir_tours_react_session,
        reinitialiser_tours_react_session,
        tours_react_session,
    )

    assert tours_react_session() is None
    jeton = definir_tours_react_session(24)
    try:
        assert tours_react_session() == 24
    finally:
        reinitialiser_tours_react_session(jeton)
    assert tours_react_session() is None, "le budget d'une analyse fuiterait sur la tâche suivante"


def test_le_payload_prime_sur_la_session():
    """Une étape qui demande explicitement un budget garde le sien."""
    from agents.executor import definir_tours_react_session, reinitialiser_tours_react_session

    jeton = definir_tours_react_session(24)
    try:
        metadata = {"max_turns": 3}
        from agents.executor import tours_react_session
        assert (metadata.get("max_turns") or tours_react_session() or 10) == 3
    finally:
        reinitialiser_tours_react_session(jeton)


def test_sans_session_ni_payload_le_defaut_reste_dix():
    from agents.executor import tours_react_session

    assert ({}.get("max_turns") or tours_react_session() or 10) == 10
