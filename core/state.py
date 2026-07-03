"""
core/state.py — Modèle de données centralisé de l'orchestration (Pydantic V2).

Migration depuis dataclasses  vers Pydantic BaseModel  :
- Validation automatique des types à la construction
- Sérialisation JSON native via .model_dump_json() / .model_validate_json()
- Support du checkpointing disque et de la reprise après crash
- Machine à états (ExecutionPhase) pour le suivi du workflow
- Task Ledger enrichi (TaskStatus) pour le suivi individuel des sous-tâches
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
from enum import Enum


class ExecutionPhase(str, Enum):
    """Machine à états du workflow d'orchestration."""
    INIT = "init"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    HEALING = "healing"
    WAITING_APPROVAL = "waiting_approval"  # Nœud d'approbation humaine (HITL)
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, Enum):
    """Statut individuel d'une sous-tâche du DAG."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"    # Résultat partiel (dégradation gracieuse)
    SKIPPED = "skipped"


class TaskPayload(BaseModel):
    """
    Représente les données isolées envoyées à un agent pour une exécution spécifique.
    Rétrocompatible avec l'ancienne version dataclass (tous les nouveaux champs ont des défauts).
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_objective: str
    relevant_context: str = ""
    available_tools: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    # Task Ledger enrichi — suivi individuel pour l'IHM
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    result_summary: Optional[str] = None


class StateUpdate(BaseModel):
    """
    Représente le 'delta' généré par un agent après exécution.
    Rétrocompatible avec l'ancienne version dataclass.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_name: str
    status: str
    result_data: Any = None
    next_agent: Optional[str] = None
    error_message: Optional[str] = None
    new_tasks: List[TaskPayload] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowMetadata(BaseModel):
    """Métadonnées système de l'orchestration (trace, tokens, timing)."""
    trace_id: str = ""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    start_time: Optional[str] = None
    timeout_deadline: Optional[str] = None


class GlobalState(BaseModel):
    """
    État centralisé et sérialisable de l'orchestration.

    Améliorations V5 :
    - current_phase : machine à états (INIT → PLANNING → EXECUTING → ...)
    - workflow_metadata : trace_id, tokens, timing
    - entity_memory : suivi des entités HA manipulées dans une session
    - Sérialisation : .model_dump_json() / .model_validate_json()
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    current_phase: ExecutionPhase = ExecutionPhase.INIT
    history: List[StateUpdate] = Field(default_factory=list)
    current_payload: Optional[TaskPayload] = None
    shared_memory: Dict[str, Any] = Field(default_factory=dict)
    task_queue: List[TaskPayload] = Field(default_factory=list)
    # Métadonnées système et mémoire d'entités
    workflow_metadata: WorkflowMetadata = Field(default_factory=WorkflowMetadata)
    entity_memory: Dict[str, Any] = Field(default_factory=dict)
    # Mémoire de travail partagée entre agents pendant le DAG.
    # Accessible en lecture/écriture par tous les agents via state.working_memory.
    # Protégée par _history_lock dans Engine pour la thread-safety.
    working_memory: Dict[str, Any] = Field(default_factory=dict)
