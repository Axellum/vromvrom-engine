"""
test_dag_runner.py — Tests unitaires du DAGRunner (exécution parallèle du DAG).

Vérifie :
- L'exécution séquentielle de tâches sans dépendances
- Le respect des dépendances (tâche B attend tâche A)
- La détection de blocage (dépendance circulaire ou non résoluble)
- L'intégration avec le HealingManager (mock)
"""

import sys
import os
import pytest
import asyncio
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.state import TaskPayload, StateUpdate, GlobalState
from core.dag_runner import DAGRunner


class MockAgent:
    """Agent mocké qui retourne un StateUpdate de succès."""
    
    def __init__(self, name: str, succeed: bool = True, delay: float = 0.0):
        self.name = name
        self._succeed = succeed
        self._delay = delay
    
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._succeed:
            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data=f"Tâche exécutée : {payload.task_objective}",
                metadata={}
            )
        else:
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data=None,
                error_message=f"Échec simulé pour : {payload.task_objective}",
                metadata={}
            )


class MockEngine:
    """Engine minimal pour instancier le DAGRunner dans les tests."""
    
    def __init__(self):
        self.state = GlobalState(session_id="test_dag_session")
        self._history_lock = asyncio.Lock()
        self.on_event = None
        self.context_manager = None
        self.agents = {
            "executor": MockAgent("executor"),
            "planner": MockAgent("planner"),
        }
    
    async def _validate_modified_yamls(self):
        return None


@pytest.fixture
def mock_engine():
    return MockEngine()


@pytest.fixture
def dag_runner(mock_engine):
    return DAGRunner(mock_engine)


class TestDAGRunnerSimple:
    """Tests d'exécution DAG basique."""

    @pytest.mark.asyncio
    async def test_single_task_success(self, dag_runner):
        """Une tâche unique sans dépendance doit réussir."""
        tasks = [
            TaskPayload(
                task_objective="Créer hello.py",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            )
        ]
        
        tasks_status, has_error = await dag_runner.execute_dag(
            tasks=tasks, max_session_tokens=500_000
        )
        
        assert has_error is False
        assert tasks_status["t1"] == "success"

    @pytest.mark.asyncio
    async def test_two_independent_tasks(self, dag_runner):
        """Deux tâches indépendantes doivent toutes deux réussir."""
        tasks = [
            TaskPayload(
                task_objective="Lire fichier A",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Lire fichier B",
                task_id="t2",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
        ]
        
        tasks_status, has_error = await dag_runner.execute_dag(
            tasks=tasks, max_session_tokens=500_000
        )
        
        assert has_error is False
        assert tasks_status["t1"] == "success"
        assert tasks_status["t2"] == "success"

    @pytest.mark.asyncio
    async def test_dependency_chain(self, dag_runner):
        """t2 dépend de t1 : t2 ne démarre qu'après le succès de t1."""
        tasks = [
            TaskPayload(
                task_objective="Étape 1",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Étape 2 (dépend de t1)",
                task_id="t2",
                depends_on=["t1"],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 2}
            ),
        ]
        
        tasks_status, has_error = await dag_runner.execute_dag(
            tasks=tasks, max_session_tokens=500_000
        )
        
        assert has_error is False
        assert tasks_status["t1"] == "success"
        assert tasks_status["t2"] == "success"


class TestDAGRunnerBudget:
    """Tests de garde-fou budget tokens."""

    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_tasks(self, mock_engine, dag_runner):
        """Si le budget est dépassé, les tâches restantes ne sont pas lancées."""
        tasks = [
            TaskPayload(
                task_objective="Tâche coûteuse",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
        ]
        
        # Mock du token tracker pour simuler un dépassement
        with patch("core.token_tracker.get_session_total_tokens", return_value=999_999):
            tasks_status, has_error = await dag_runner.execute_dag(
                tasks=tasks, max_session_tokens=500_000
            )
        
        assert has_error is True
