# -*- coding: utf-8 -*-
"""Tests du module d'observabilité (santé circuit breakers + Prometheus)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.observability import router
from core.llm.circuit_breaker import CircuitBreaker


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_circuit_breakers_json_structure():
    """L'endpoint JSON expose le résumé agrégé attendu."""
    client = _client()
    r = client.get("/api/observability/circuit-breakers")
    assert r.status_code == 200
    data = r.json()
    for key in ("uptime_seconds", "total", "open", "half_open", "healthy", "circuit_breakers"):
        assert key in data
    assert isinstance(data["circuit_breakers"], list)


def test_circuit_breaker_appears_in_metrics():
    """Un breaker créé apparaît dans le JSON et l'exposition Prometheus."""
    cb = CircuitBreaker.get_or_create("test_obs_provider")
    assert cb is not None

    client = _client()
    data = client.get("/api/observability/circuit-breakers").json()
    names = {b["name"] for b in data["circuit_breakers"]}
    assert "test_obs_provider" in names

    prom = client.get("/api/observability/prometheus")
    assert prom.status_code == 200
    body = prom.text
    # Format Prometheus : HELP/TYPE + métrique labellisée par breaker.
    assert "# TYPE moteur_circuit_breaker_state gauge" in body
    assert 'moteur_circuit_breaker_state{breaker="test_obs_provider"}' in body
    assert "moteur_uptime_seconds" in body


def test_dashboard_html_served():
    """Le dashboard HTML est servi."""
    client = _client()
    r = client.get("/api/observability/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Circuit Breakers" in r.text
