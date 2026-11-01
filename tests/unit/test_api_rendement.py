"""
tests/unit/test_api_rendement.py — Tests unitaires pour les routes de rendement DreamCoder.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.rendement import router as rendement_router


@pytest.fixture
def client():
    """Fixture pour créer un client de test FastAPI."""
    app = FastAPI()
    app.include_router(rendement_router)
    return TestClient(app)


def test_get_rendement_success(client):
    """Teste que GET /api/dreamcoder/rendement renvoie l'entonnoir correctement."""
    mock_data = {
        "tente": 10,
        "pousse": 8,
        "recolte": 5,
        "merge": 3,
        "tokens_par_pr": 1500.5
    }

    with patch("api.routes.rendement.get_rendement", return_value=mock_data):
        response = client.get("/api/dreamcoder/rendement")
        assert response.status_code == 200
        assert response.json() == mock_data


def test_get_rendement_error(client):
    """Teste la gestion d'erreur de GET /api/dreamcoder/rendement."""
    with patch("api.routes.rendement.get_rendement", side_effect=Exception("Erreur DB")):
        response = client.get("/api/dreamcoder/rendement")
        assert response.status_code == 500
        assert "Erreur de lecture du rendement" in response.json()["detail"]


def test_sync_merges_success(client):
    """Teste que POST /api/dreamcoder/rendement/sync-merges renvoie le nombre de PR et le rendement."""
    mock_nouveaux = 2
    mock_rendement = {
        "tente": 11,
        "pousse": 9,
        "recolte": 6,
        "merge": 5,
        "tokens_par_pr": 1200.0
    }

    with patch("api.routes.rendement.synchroniser_merges", return_value=mock_nouveaux):
        with patch("api.routes.rendement.get_rendement", return_value=mock_rendement):
            response = client.post("/api/dreamcoder/rendement/sync-merges")
            assert response.status_code == 200
            data = response.json()
            assert data["nouvelles_prs_mergees"] == mock_nouveaux
            assert data["rendement"] == mock_rendement


def test_sync_merges_error(client):
    """Teste la gestion d'erreur de POST /api/dreamcoder/rendement/sync-merges."""
    with patch("api.routes.rendement.synchroniser_merges", side_effect=Exception("Erreur GH")):
        response = client.post("/api/dreamcoder/rendement/sync-merges")
        assert response.status_code == 500
        assert "Erreur de synchronisation des merges" in response.json()["detail"]


def test_drain_ok(client):
    faux = {"processed": 1, "success": 1, "failed": 0}
    with patch("agents.dreamer_agent.dreamer_state", {"running": False}), \
         patch("agents.dreamer_agent._load_persistent_config",
               return_value={"dreamcoder_enabled": True}), \
         patch("agents.dreamer_agent._run_dreamcoder_drain_loop",
               new_callable=AsyncMock, return_value=faux):
        response = client.post("/api/dreamcoder/drain")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "drain": faux}


def test_drain_refuse_si_cycle_en_cours(client):
    with patch("agents.dreamer_agent.dreamer_state", {"running": True}):
        response = client.post("/api/dreamcoder/drain")
    assert response.status_code == 409
    assert "déjà en cours" in response.json()["detail"]
