"""
Tests du plafond de jetons par tentative.
Vérifie que :
1. Une tentative dépassant le plafond est interrompue.
2. Une nouvelle tentative repart de zéro (ne cumule pas avec les précédentes).
"""

import pytest
from unittest.mock import patch, MagicMock
from core.execution_budget import ExecutionBudget
from core.state import TaskPayload

def test_interruption_si_depassement_plafond():
    """
    Prouve qu'une tentative dépassant le plafond s'arrête.
    """
    session_id = "task_123_1000"
    # Plafond à 100k
    budget = ExecutionBudget(session_id=session_id, max_tokens=100_000)
    
    # Simulation : 110k jetons consommés
    with patch("core.token_tracker.get_session_total_tokens", return_value=110_000):
        violation = budget.check()
        assert violation is not None
        assert violation["reason"] == "tokens"
        assert violation["value"] == 110_000
        assert violation["limit"] == 100_000

def test_nouvelle_tentative_non_bloquee_par_historique():
    """
    Prouve qu'une tâche à sa 3e tentative n'est PAS bloquée par le coût des deux premières.
    L'identifiant de session doit être différent pour chaque tentative.
    """
    task_id = 57
    # Tentative 1 : session_1, a coûté 1M
    session_1 = f"task_{task_id}_1000"
    # Tentative 2 : session_2, a coûté 1M
    session_2 = f"task_{task_id}_2000"
    # Tentative 3 : session_3, vient de démarrer
    session_3 = f"task_{task_id}_3000"
    
    plafond = 500_000
    
    def mock_tokens(sid):
        # Simule la DB des tokens
        data = {
            session_1: 1_000_000,
            session_2: 1_000_000,
            session_3: 0,
        }
        return data.get(sid, 0)

    with patch("core.token_tracker.get_session_total_tokens", side_effect=mock_tokens):
        # La tentative 3 doit être dans le budget malgré les 2M cumulés des précédentes
        budget_3 = ExecutionBudget(session_id=session_3, max_tokens=plafond)
        violation = budget_3.check()
        
        assert violation is None, "La tentative 3 ne devrait pas être bloquée par les tentatives 1 et 2"
        assert budget_3.remaining_tokens() == plafond

def test_chargement_config_dreamcoder_max_tokens():
    """
    Vérifie que ExecutionBudget.from_config utilise bien la nouvelle clé.
    """
    config = {
        "dreamcoder_max_tokens_par_tentative": 800_000,
        "max_session_tokens": 500_000
    }
    budget = ExecutionBudget.from_config(session_id="test", config=config)
    assert budget.max_tokens == 800_000
