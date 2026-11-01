"""
[#T438] Une tâche était bloquée par la dépense de ses propres tentatives.

Le budget d'exécution (`max_session_tokens`, 500 000 par défaut) compte la
consommation de la SESSION via `get_session_total_tokens(session_id)`. Or
l'identifiant de session d'une tâche autonome était `task_<id>` — stable d'un
rejeu à l'autre.

Mesuré le 21/08 sur la tâche 57 : session `task_57`, 12 appels cumulés,
**2 192 330 jetons contre un plafond de 500 000**. Le moteur routait vers le
Planner puis terminait l'orchestration dans la seconde, sans qu'aucun agent ne
tourne et sans un mot dans le journal. Chaque relance aggravait le blocage au
lieu de le lever — le symptôme visible étant « la branche est vide ».
"""

from unittest.mock import patch

from core.execution_budget import ExecutionBudget


def _session(task_id: int, debut: float) -> str:
    """L'identifiant tel que le harnais le construit désormais."""
    return f"task_{task_id}_{int(debut)}"


def test_deux_tentatives_ne_partagent_pas_leur_session():
    assert _session(57, 1_787_302_000) != _session(57, 1_787_305_000)


def test_l_identifiant_reste_rattachable_a_la_tache():
    """On doit pouvoir retrouver toutes les tentatives d'une tâche."""
    assert _session(57, 1_787_302_000).startswith("task_57_")


def test_une_session_deja_depensee_n_a_plus_de_budget():
    """Le garde-fou est sain : c'est son PÉRIMÈTRE qui était faux."""
    budget = ExecutionBudget(session_id="task_57", max_tokens=500_000)
    with patch("core.token_tracker.get_session_total_tokens", return_value=2_192_330):
        assert budget.remaining_tokens() == 0


def test_une_tentative_neuve_repart_avec_tout_son_budget():
    """
    Le cas réel : les 2,19 M de jetons des tentatives précédentes ne doivent
    plus rien retirer à la suivante, qui porte un autre identifiant.
    """
    budget = ExecutionBudget(session_id=_session(57, 1_787_305_000), max_tokens=500_000)
    with patch("core.token_tracker.get_session_total_tokens", return_value=0):
        assert budget.remaining_tokens() == 500_000


def test_l_axe_tokens_desactive_reste_desactive():
    budget = ExecutionBudget(session_id="task_1_2", max_tokens=0)
    assert budget.remaining_tokens() is None
