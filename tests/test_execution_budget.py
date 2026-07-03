# -*- coding: utf-8 -*-
"""
tests/test_execution_budget.py — Budget global d'exécution (P2-3.4).

Vérifie le plafonnement par tokens, durée et coût, et la désactivation par axe
(limite 0). Les getters de session sont monkeypatchés pour rester hors-DB.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.execution_budget import ExecutionBudget


def _patch_usage(monkeypatch, tokens=0, cost=0.0):
    monkeypatch.setattr("core.token_tracker.get_session_total_tokens", lambda s: tokens)
    monkeypatch.setattr("core.token_tracker.get_session_total_cost", lambda s: cost)


def test_within_budget_returns_none(monkeypatch):
    _patch_usage(monkeypatch, tokens=100, cost=0.01)
    b = ExecutionBudget("s1", max_tokens=1000, max_duration_s=60, max_cost_usd=1.0)
    assert b.check() is None


def test_tokens_limit_trips(monkeypatch):
    _patch_usage(monkeypatch, tokens=1000)
    b = ExecutionBudget("s1", max_tokens=1000)
    v = b.check()
    assert v is not None and v["reason"] == "tokens"
    assert v["value"] == 1000 and v["limit"] == 1000


def test_cost_limit_trips(monkeypatch):
    _patch_usage(monkeypatch, tokens=0, cost=2.5)
    b = ExecutionBudget("s1", max_tokens=0, max_cost_usd=2.0)
    v = b.check()
    assert v is not None and v["reason"] == "cost"
    assert v["value"] == 2.5 and v["limit"] == 2.0


def test_duration_limit_trips(monkeypatch):
    _patch_usage(monkeypatch)
    # start_time dans le passé → durée écoulée dépasse la limite
    b = ExecutionBudget("s1", max_duration_s=1.0, start_time=time.time() - 5)
    v = b.check()
    assert v is not None and v["reason"] == "duration"
    assert v["limit"] == 1.0


def test_zero_limits_are_disabled(monkeypatch):
    _patch_usage(monkeypatch, tokens=10_000_000, cost=999.0)
    b = ExecutionBudget("s1", max_tokens=0, max_duration_s=0, max_cost_usd=0,
                        start_time=time.time() - 10_000)
    assert b.check() is None  # tous les axes désactivés


def test_tokens_checked_before_cost(monkeypatch):
    """L'ordre de priorité : tokens d'abord."""
    _patch_usage(monkeypatch, tokens=1000, cost=999.0)
    b = ExecutionBudget("s1", max_tokens=1000, max_cost_usd=1.0)
    assert b.check()["reason"] == "tokens"


def test_from_config_reads_keys():
    cfg = {"max_session_tokens": 123, "max_execution_seconds": 45, "max_execution_cost_usd": 6.7}
    b = ExecutionBudget.from_config("s1", config=cfg)
    assert b.max_tokens == 123
    assert b.max_duration_s == 45.0
    assert b.max_cost_usd == 6.7


def test_from_config_defaults_tokens_only():
    """Par défaut (config vide) : tokens 500k actif, durée/coût désactivés."""
    b = ExecutionBudget.from_config("s1", config={})
    assert b.max_tokens == 500_000
    assert b.max_duration_s == 0.0
    assert b.max_cost_usd == 0.0


def test_event_payload_structure(monkeypatch):
    _patch_usage(monkeypatch, tokens=1000)
    b = ExecutionBudget("s1", max_tokens=1000)
    payload = b.event_payload(b.check(), blocked="planner")
    assert payload["reason"] == "tokens"
    assert payload["blocked"] == "planner"
    assert payload["max_tokens"] == 1000
