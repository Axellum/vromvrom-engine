"""
test_state.py — Tests unitaires des modèles de données Pydantic (core/state.py).

Vérifie :
- La construction et validation des modèles
- La sérialisation JSON (model_dump_json / model_validate_json)
- Les valeurs par défaut et les énumérations
- La compatibilité rétroactive (champs optionnels)
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.state import (
    TaskPayload, StateUpdate, GlobalState,
    ExecutionPhase, TaskStatus, WorkflowMetadata
)


class TestTaskPayload:
    """Tests du modèle TaskPayload."""

    def test_creation_minimale(self):
        """Un TaskPayload avec uniquement task_objective est valide."""
        p = TaskPayload(task_objective="Test basique")
        assert p.task_objective == "Test basique"
        assert p.relevant_context == ""
        assert p.metadata == {}
        assert p.depends_on == []
        assert p.status == TaskStatus.PENDING

    def test_creation_complete(self):
        """Un TaskPayload avec tous les champs est valide."""
        p = TaskPayload(
            task_objective="Créer hello.py",
            relevant_context="Contexte de test",
            available_tools=["read_file", "write_file"],
            metadata={"session_id": "s1", "model_tier": "leger"},
            task_id="task_001",
            depends_on=["task_000"],
            status=TaskStatus.RUNNING,
            assigned_agent="executor",
            result_summary="En cours..."
        )
        assert p.task_id == "task_001"
        assert p.depends_on == ["task_000"]
        assert p.status == TaskStatus.RUNNING
        assert p.assigned_agent == "executor"

    def test_serialisation_json_roundtrip(self):
        """Un TaskPayload doit survivre à un cycle sérialisation/désérialisation JSON."""
        original = TaskPayload(
            task_objective="Roundtrip test",
            metadata={"key": "value", "count": 42},
            task_id="rt_001",
            depends_on=["rt_000"],
        )
        json_str = original.model_dump_json()
        restored = TaskPayload.model_validate_json(json_str)
        
        assert restored.task_objective == original.task_objective
        assert restored.metadata == original.metadata
        assert restored.task_id == original.task_id
        assert restored.depends_on == original.depends_on

    def test_status_enum_valide(self):
        """Tous les statuts de TaskStatus doivent être accessibles."""
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.ERROR == "error"
        assert TaskStatus.PARTIAL == "partial"
        assert TaskStatus.SKIPPED == "skipped"


class TestStateUpdate:
    """Tests du modèle StateUpdate."""

    def test_creation_succes(self):
        """Un StateUpdate de succès est valide."""
        u = StateUpdate(
            agent_name="executor",
            status="success",
            result_data="Fichier créé avec succès.",
            next_agent="END"
        )
        assert u.agent_name == "executor"
        assert u.status == "success"
        assert u.error_message is None

    def test_creation_erreur(self):
        """Un StateUpdate d'erreur contient un message d'erreur."""
        u = StateUpdate(
            agent_name="planner",
            status="error",
            result_data=None,
            error_message="JSON invalide dans la réponse du LLM"
        )
        assert u.status == "error"
        assert "JSON invalide" in u.error_message

    def test_new_tasks_liste(self):
        """Un StateUpdate peut contenir une liste de nouvelles tâches (Planner)."""
        tasks = [
            TaskPayload(task_objective="Tâche 1", task_id="t1"),
            TaskPayload(task_objective="Tâche 2", task_id="t2", depends_on=["t1"]),
        ]
        u = StateUpdate(
            agent_name="planner",
            status="success",
            result_data="Plan généré",
            new_tasks=tasks
        )
        assert len(u.new_tasks) == 2
        assert u.new_tasks[1].depends_on == ["t1"]


class TestGlobalState:
    """Tests du modèle GlobalState."""

    def test_creation_initiale(self):
        """Un GlobalState initialisé est en phase INIT."""
        state = GlobalState(session_id="test_001")
        assert state.session_id == "test_001"
        assert state.current_phase == ExecutionPhase.INIT
        assert state.history == []
        assert state.task_queue == []

    def test_phases_execution(self):
        """Toutes les phases d'exécution sont valides."""
        assert ExecutionPhase.INIT == "init"
        assert ExecutionPhase.PLANNING == "planning"
        assert ExecutionPhase.EXECUTING == "executing"
        assert ExecutionPhase.REVIEWING == "reviewing"
        assert ExecutionPhase.HEALING == "healing"
        assert ExecutionPhase.WAITING_APPROVAL == "waiting_approval"
        assert ExecutionPhase.COMPLETED == "completed"
        assert ExecutionPhase.FAILED == "failed"

    def test_serialisation_json_roundtrip(self):
        """Un GlobalState complet doit survivre à un cycle JSON."""
        state = GlobalState(
            session_id="roundtrip_001",
            current_phase=ExecutionPhase.EXECUTING,
            shared_memory={"clé": "valeur"},
        )
        # Ajouter une entrée d'historique
        state.history.append(StateUpdate(
            agent_name="planner",
            status="success",
            result_data="Plan OK"
        ))
        
        json_str = state.model_dump_json()
        restored = GlobalState.model_validate_json(json_str)
        
        assert restored.session_id == "roundtrip_001"
        assert restored.current_phase == ExecutionPhase.EXECUTING
        assert len(restored.history) == 1
        assert restored.history[0].agent_name == "planner"

    def test_workflow_metadata_defaults(self):
        """Les métadonnées de workflow ont des valeurs par défaut sensées."""
        state = GlobalState(session_id="meta_test")
        assert state.workflow_metadata.trace_id == ""
        assert state.workflow_metadata.total_tokens == 0
        assert state.workflow_metadata.total_cost_usd == 0.0


class TestWorkflowMetadata:
    """Tests du modèle WorkflowMetadata."""

    def test_creation(self):
        """Un WorkflowMetadata avec des valeurs est valide."""
        meta = WorkflowMetadata(
            trace_id="trace_abc123",
            total_tokens=15000,
            total_cost_usd=0.042,
            start_time="2026-05-25T12:00:00Z"
        )
        assert meta.trace_id == "trace_abc123"
        assert meta.total_tokens == 15000
        assert meta.total_cost_usd == pytest.approx(0.042)
