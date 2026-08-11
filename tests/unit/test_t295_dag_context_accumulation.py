"""
tests/unit/test_t295_dag_context_accumulation.py

Test de mesure et de régression (#T295) :
Vérifie qu'à chaque rejeu de Self-Healing d'une tâche DAG :
1. Son `relevant_context` ne s'accumule pas au fil des tentatives (stabilité stricte par rapport au 1er passage).
2. Un plafond global de 40 000 caractères est appliqué sur la somme des agrégations (dépendances + mémoire).
"""

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dag_runner import TOTAL_CONTEXT_MAX_CHARS, DAGRunner
from core.state import GlobalState, StateUpdate, TaskPayload


class MockFailingThenSucceedingAgent:
    """
    Agent mocké (100% factice, aucun LLM réel / aucun réseau / aucun jeton)
    qui enregistre la taille de task_payload.relevant_context à chaque tentative.
    """

    def __init__(self, name: str, fail_count: int = 3):
        self.name = name
        self.fail_count = fail_count
        self.attempts = 0
        self.recorded_context_lengths = []

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        self.attempts += 1
        current_len = len(payload.relevant_context or "")
        self.recorded_context_lengths.append(current_len)

        if self.attempts <= self.fail_count:
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data=None,
                error_message=f"Échec simulé tentative {self.attempts}",
                metadata={"task_id": payload.task_id}
            )
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data="Succès final",
            metadata={"task_id": payload.task_id}
        )


class MockEngine:
    def __init__(self):
        self.state = GlobalState(session_id="test_t295_session")
        self._history_lock = asyncio.Lock()
        self.on_event = None
        self.context_manager = None
        self.agents = {}
        self._repo_root = None

    async def _validate_modified_yamls(self):
        return None


@pytest.mark.asyncio
async def test_t295_context_accumulation_on_retry():
    """
    Mesure et valide que la taille de `relevant_context` lors des rejeux d'une tâche
    est 100% stable et ne s'accumule plus à chaque tentative.
    """
    engine = MockEngine()
    runner = DAGRunner(engine)

    # 1. Tâche parent avec un résultat volumineux (10 000 chars)
    parent_agent = MockFailingThenSucceedingAgent("parent_agent", fail_count=0)
    async def parent_invoke(payload):
        return StateUpdate(
            agent_name="parent_agent",
            status="success",
            result_data="A" * 10000,
            metadata={"task_id": "parent_task"}
        )
    parent_agent.invoke = parent_invoke
    engine.agents["parent_agent"] = parent_agent

    parent_payload = TaskPayload(
        task_id="parent_task",
        task_objective="Générer du code",
        relevant_context="Contexte d'origine parent",
        metadata={"stage_id": 1, "target_agent": "parent_agent"}
    )

    # 2. Tâche enfant qui dépend de parent_task et va échouer 3 fois avant de réussir
    child_payload = TaskPayload(
        task_id="child_task",
        task_objective="Analyser le code parent",
        relevant_context="Contexte initial enfant",
        depends_on=["parent_task"],
        metadata={"stage_id": 2, "target_agent": "child_agent"}
    )
    child_agent = MockFailingThenSucceedingAgent("child_agent", fail_count=3)
    engine.agents["child_agent"] = child_agent

    with patch.object(runner._healer, "attempt_healing", new_callable=AsyncMock) as mock_healing:
        mock_healing.return_value = True
        tasks = [parent_payload, child_payload]
        _, success = await runner.execute_dag(tasks, max_session_tokens=100000)

    print("\n--- MEASUREMENT T295 APRES FIX: tentative -> taille du contexte ---")
    for i, length in enumerate(child_agent.recorded_context_lengths, 1):
        print(f"Tentative {i} : {length} caractères")

    # Vérification de stabilité stricte : la taille doit être strictement identique d'une tentative à l'autre
    initial_length = child_agent.recorded_context_lengths[0]
    for idx, length in enumerate(child_agent.recorded_context_lengths, 1):
        assert length == initial_length, (
            f"Dérive détectée à la tentative {idx} : {length} chars vs initial {initial_length} chars !"
        )


@pytest.mark.asyncio
async def test_t295_global_context_cap(caplog):
    """
    Vérifie qu'en cas de dépendances multiples volumineuses, le plafond global
    TOTAL_CONTEXT_MAX_CHARS (40 000 chars) est appliqué et journalise un avertissement.
    """
    engine = MockEngine()
    runner = DAGRunner(engine)

    # Créer 5 tâches parents générant chacune 12 000 chars (total 60 000 chars)
    parent_ids = []
    for i in range(5):
        p_id = f"parent_{i}"
        parent_ids.append(p_id)
        p_agent = MockFailingThenSucceedingAgent(f"agent_{p_id}", fail_count=0)
        async def make_invoke(chunk_data):
            async def _inv(payload):
                return StateUpdate(
                    agent_name=payload.metadata.get("target_agent"),
                    status="success",
                    result_data=chunk_data,
                    metadata={"task_id": payload.task_id}
                )
            return _inv
        p_agent.invoke = await make_invoke("B" * 12000)
        engine.agents[f"agent_{p_id}"] = p_agent

    parent_payloads = [
        TaskPayload(
            task_id=p_id,
            task_objective=f"Générer chunk {p_id}",
            relevant_context="",
            metadata={"stage_id": 1, "target_agent": f"agent_{p_id}"}
        )
        for p_id in parent_ids
    ]

    child_payload = TaskPayload(
        task_id="child_reduce",
        task_objective="Réduire 5 dépendances volumineuses",
        relevant_context="C" * 5000,
        depends_on=parent_ids,
        metadata={"stage_id": 2, "target_agent": "child_reduce_agent"}
    )
    child_agent = MockFailingThenSucceedingAgent("child_reduce_agent", fail_count=0)
    engine.agents["child_reduce_agent"] = child_agent

    with caplog.at_level(logging.WARNING):
        tasks = parent_payloads + [child_payload]
        await runner.execute_dag(tasks, max_session_tokens=100000)

    final_context_len = child_agent.recorded_context_lengths[0]
    print(f"\nTaille finale avec 5 dépendances volumineuses : {final_context_len} chars (Plafond: {TOTAL_CONTEXT_MAX_CHARS})")

    # Le contexte final doit être plafonné (40k + suffixe de message de troncature)
    assert final_context_len <= TOTAL_CONTEXT_MAX_CHARS + 200
    assert "Plafond global de contexte dépassé" in caplog.text
