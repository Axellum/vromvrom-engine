"""
tests/test_review_loop_escalation.py — Tests unitaires pour la cascade routing
qualité du ReviewLoop (#T117).

Vérifie :
- _get_dominant_category retrouve le domaine posé par le Router en metadata.
- _escalation_tier respecte le seuil, le domaine éligible et le flag enabled.
- _apply_corrections force le model_tier des tâches correctives quand force_tier est fourni.
"""

import sys
import os
import asyncio
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import TaskPayload, StateUpdate, GlobalState
from core.review_loop import ReviewLoop


class _StubEngine:
    def __init__(self):
        self.state = GlobalState(session_id="test_t117_session")
        self._history_lock = asyncio.Lock()
        self.agents = {}


def _config(**overrides):
    base = {
        "enabled": True,
        "quality_threshold": 6.0,
        "eligible_domains": ["code_generation", "analysis", "sysadmin"],
        "escalated_tier": "fort",
    }
    base.update(overrides)
    return {"cascade_quality_escalation": base}


def _make_loop_with_domain(domain):
    engine = _StubEngine()
    engine.state.history.append(
        StateUpdate(agent_name="router", status="success", metadata={"dominant_category": domain})
    )
    return ReviewLoop(engine)


class TestDominantCategory:
    def test_found(self):
        loop = _make_loop_with_domain("code_generation")
        assert loop._get_dominant_category() == "code_generation"

    def test_absent(self):
        loop = ReviewLoop(_StubEngine())
        assert loop._get_dominant_category() is None


class TestEscalationTier:
    def test_escalates_below_threshold_on_eligible_domain(self):
        loop = _make_loop_with_domain("code_generation")
        with patch("core.llm_gateway.load_config", return_value=_config()):
            assert loop._escalation_tier(4.0) == "fort"

    def test_no_escalation_above_threshold(self):
        loop = _make_loop_with_domain("code_generation")
        with patch("core.llm_gateway.load_config", return_value=_config()):
            assert loop._escalation_tier(7.0) is None

    def test_no_escalation_on_ineligible_domain(self):
        loop = _make_loop_with_domain("casual_chat")
        with patch("core.llm_gateway.load_config", return_value=_config()):
            assert loop._escalation_tier(2.0) is None

    def test_no_escalation_when_disabled(self):
        loop = _make_loop_with_domain("code_generation")
        with patch("core.llm_gateway.load_config", return_value=_config(enabled=False)):
            assert loop._escalation_tier(2.0) is None

    def test_no_escalation_when_score_none(self):
        loop = _make_loop_with_domain("code_generation")
        with patch("core.llm_gateway.load_config", return_value=_config()):
            assert loop._escalation_tier(None) is None

    def test_custom_threshold_respected(self):
        loop = _make_loop_with_domain("sysadmin")
        with patch("core.llm_gateway.load_config", return_value=_config(quality_threshold=8.0)):
            # 7.5 < 8.0 -> doit escalader avec un seuil relevé
            assert loop._escalation_tier(7.5) == "fort"


class TestApplyCorrectionsForcesTier:
    @pytest.mark.asyncio
    async def test_force_tier_overrides_planner_tier(self):
        """force_tier doit écraser le model_tier des tâches correctives du Planner."""
        engine = _StubEngine()

        planner_update = StateUpdate(
            agent_name="planner",
            status="success",
            new_tasks=[
                TaskPayload(
                    task_objective="corriger X",
                    metadata={"target_agent": "executor", "model_tier": "moyen"},
                    task_id="corr-1",
                ),
            ],
        )

        class _PlannerAgent:
            async def invoke(self, payload):
                return planner_update

        class _ExecutorAgent:
            def __init__(self):
                self.received_payloads = []

            async def invoke(self, payload):
                self.received_payloads.append(payload)
                return StateUpdate(agent_name="executor", status="success", metadata={"task_id": payload.task_id})

        executor_agent = _ExecutorAgent()
        engine.agents = {"planner": _PlannerAgent(), "executor": executor_agent}

        loop = ReviewLoop(engine)
        review_update = StateUpdate(
            agent_name="reviewer",
            status="error",
            error_message="échec",
            metadata={"corrections": ["fix X"]},
        )

        ok = await loop._apply_corrections(
            review_update, "objectif initial", 1, on_event=None, force_tier="fort"
        )

        assert ok is True
        assert len(executor_agent.received_payloads) == 1
        assert executor_agent.received_payloads[0].metadata["model_tier"] == "fort"

    @pytest.mark.asyncio
    async def test_no_force_tier_keeps_planner_tier(self):
        """Sans force_tier, le tier choisi par le Planner doit rester inchangé."""
        engine = _StubEngine()

        planner_update = StateUpdate(
            agent_name="planner",
            status="success",
            new_tasks=[
                TaskPayload(
                    task_objective="corriger X",
                    metadata={"target_agent": "executor", "model_tier": "moyen"},
                    task_id="corr-1",
                ),
            ],
        )

        class _PlannerAgent:
            async def invoke(self, payload):
                return planner_update

        class _ExecutorAgent:
            def __init__(self):
                self.received_payloads = []

            async def invoke(self, payload):
                self.received_payloads.append(payload)
                return StateUpdate(agent_name="executor", status="success", metadata={"task_id": payload.task_id})

        executor_agent = _ExecutorAgent()
        engine.agents = {"planner": _PlannerAgent(), "executor": executor_agent}

        loop = ReviewLoop(engine)
        review_update = StateUpdate(
            agent_name="reviewer",
            status="error",
            error_message="échec",
            metadata={"corrections": ["fix X"]},
        )

        ok = await loop._apply_corrections(
            review_update, "objectif initial", 1, on_event=None, force_tier=None
        )

        assert ok is True
        assert executor_agent.received_payloads[0].metadata["model_tier"] == "moyen"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
