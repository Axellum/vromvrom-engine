"""Tests vocal audit + mode HA execute helpers."""

import gc
import os
import shutil
import tempfile

import pytest

from core.ha_fuzzy_matcher import HAFuzzyMatcher, FUZZY_AMBIGUITY_DELTA, FUZZY_MATCH_THRESHOLD, FUZZY_MATCH_THRESHOLD_EMB
from core.runtime_db import get_connection, override_db_path
from core.source_router import ModeType, RequestSource, SourceType
from core.vocal_audit import get_recent_vocal_logs, is_vocal_request, log_vocal_request, log_vocal_response
from services.execute_service import (
    build_ha_mode_failure_response,
    get_execute_timeout,
    should_block_full_pipeline,
)


@pytest.fixture
def temp_runtime_db():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_runtime.db")
    override_db_path(db_path)
    conn = get_connection()
    # DELETE évite les fichiers WAL verrouillés sous Windows en teardown
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()
    conn.close()
    yield db_path
    gc.collect()
    default_db = os.path.join(os.path.dirname(os.path.dirname(__file__)), "moteur_runtime.db")
    override_db_path(default_db)
    try:
        import sqlite3
        c = sqlite3.connect(db_path, timeout=1.0)
        c.execute("PRAGMA journal_mode=DELETE")
        c.close()
    except OSError:
        pass
    shutil.rmtree(tmp, ignore_errors=True)


def test_is_vocal_request():
    assert is_vocal_request("voice")
    assert is_vocal_request("tab5")
    assert not is_vocal_request("web")


def test_vocal_audit_persistence(temp_runtime_db):
    log_vocal_request(
        session_id="s1",
        user_prompt="allume le salon",
        source_type="voice",
        source_mode="ha",
        tts_enabled=True,
    )
    log_vocal_response(
        session_id="s1",
        user_prompt="allume le salon",
        source_type="voice",
        source_mode="ha",
        routing_type="ha_fuzzy",
        agents_used=["ha_fuzzy"],
        response_text="Salon allumé.",
        latency_ms=120.5,
        tts_enabled=True,
    )
    logs = get_recent_vocal_logs(10)
    assert len(logs) >= 2
    assert any(row.get("phase") == "request" for row in logs)
    assert any(row.get("routing_type") == "ha_fuzzy" for row in logs)


def test_ha_mode_should_block_pipeline():
    """HA et Discussion (chat) bloquent le Planner/DAG — vocal_host / fast paths uniquement."""
    ha = RequestSource(type=SourceType.TAB5, mode=ModeType.HA, tts_enabled=True)
    chat = RequestSource(type=SourceType.VOICE, mode=ModeType.CHAT, tts_enabled=True)
    ide = RequestSource(type=SourceType.IDE, mode=ModeType.DEFAULT, tts_enabled=False)
    assert should_block_full_pipeline(ha)
    assert should_block_full_pipeline(chat)
    assert not should_block_full_pipeline(ide)


def test_ha_mode_failure_response_shape():
    result = build_ha_mode_failure_response("sess_x")
    assert result["agents_used"] == ["ha_mode_blocked"]
    assert "compris" in result["response"].lower()


def test_execute_timeout_ha_is_short():
    ha = RequestSource(mode=ModeType.HA)
    assert get_execute_timeout(ha, "default") == 2.0


def test_fuzzy_ambiguity_rejects_close_scores():
    scores = [0.76, 0.72, 0.40]
    assert HAFuzzyMatcher._is_ambiguous(scores, FUZZY_MATCH_THRESHOLD)
    clear = [0.90, 0.50, 0.30]
    assert not HAFuzzyMatcher._is_ambiguous(clear, FUZZY_MATCH_THRESHOLD)


def test_fuzzy_threshold_tab5_vocal():
    """Seuil difflib 0.68 pour STT vocal Tab5 ; embeddings restent plus stricts."""
    assert FUZZY_MATCH_THRESHOLD == 0.68
    assert FUZZY_MATCH_THRESHOLD_EMB >= 0.72
    assert FUZZY_AMBIGUITY_DELTA == 0.08


# ── Court-circuit HA déterministe (hors verrou global + hors routeur) ──

@pytest.mark.asyncio
async def test_ha_shortcut_returns_none_without_match(monkeypatch):
    """Aucune commande reconnue → None (la cascade normale doit prendre le relais)."""
    from api.routes import agents
    from services.execute_service import HACommandMatch  # noqa: F401

    async def _no_match(_prompt):
        return None

    monkeypatch.setattr(agents, "resolve_ha_command_for_execute", _no_match)
    body = agents.ExecuteRequestBody(user_prompt="raconte ta vie", source={"type": "tab5", "mode": "ha"})
    src = RequestSource(type=SourceType.TAB5, mode=ModeType.HA)
    assert await agents._try_ha_deterministic_shortcut(body, src, "sess_none") is None


@pytest.mark.asyncio
async def test_ha_shortcut_executes_and_returns_command(temp_runtime_db, monkeypatch):
    """Commande reconnue → exécution HA + réponse ha_command (métadonnée shortcut)."""
    from api.routes import agents
    from services.execute_service import HACommandMatch

    async def _match(_prompt):
        return HACommandMatch(
            service="script.tab5_volet_action",
            entity_id="",
            matched_phrase="volet:close",
            service_data={"action": "close"},
        )

    async def _exec(service, entity, service_data=None, **_kw):
        assert service == "script.tab5_volet_action"
        assert service_data == {"action": "close"}
        return True, "Volet fermé."

    monkeypatch.setattr(agents, "resolve_ha_command_for_execute", _match)
    monkeypatch.setattr(agents, "execute_ha_service", _exec)
    body = agents.ExecuteRequestBody(user_prompt="ferme le volet du salon", source={"type": "tab5", "mode": "ha"})
    src = RequestSource(type=SourceType.TAB5, mode=ModeType.HA, tts_enabled=True)

    result = await agents._try_ha_deterministic_shortcut(body, src, "sess_ok")
    assert result is not None
    assert result["response"] == "Volet fermé."
    assert result["agents_used"] == ["ha_command"]
    assert result["history"][0]["metadata"]["shortcut"] is True


@pytest.mark.asyncio
async def test_ha_shortcut_bypasses_global_lock(temp_runtime_db, monkeypatch):
    """Le bénéfice clé : une commande HA passe même si une exécution IHM tourne (pas de 409)."""
    import asyncio as _asyncio

    from api.routes import agents
    from services.execute_service import HACommandMatch

    class _FakeState:
        def __init__(self):
            # Simule une exécution IHM en cours → l'ancien code aurait renvoyé 409.
            self.execution_state = {"status": "running"}
            self.execution_lock = _asyncio.Lock()

    fake_state = _FakeState()

    async def _match(_prompt):
        return HACommandMatch(
            service="light.turn_on", entity_id="light.salon",
            matched_phrase="allume le salon", service_data=None,
        )

    async def _exec(service, entity, service_data=None, **_kw):
        return True, "Lumière allumée."

    monkeypatch.setattr(agents, "get_app_state", lambda: fake_state)
    monkeypatch.setattr(agents, "resolve_ha_command_for_execute", _match)
    monkeypatch.setattr(agents, "execute_ha_service", _exec)

    body = agents.ExecuteRequestBody(user_prompt="allume le salon", source={"type": "tab5", "mode": "ha"})
    result = await agents.execute_chat(body, _auth=None)

    # Pas de 409 levé, réponse HA renvoyée, et l'état IHM "running" resté intact.
    assert result["response"] == "Lumière allumée."
    assert result["agents_used"] == ["ha_command"]
    assert fake_state.execution_state["status"] == "running"


@pytest.mark.asyncio
async def test_ha_state_query_shortcut_bypasses_lock(temp_runtime_db, monkeypatch):
    """Une question d'état est lue et répond, même IHM 'running' (avant l'action)."""
    import asyncio as _asyncio

    from api.routes import agents

    class _FakeState:
        def __init__(self):
            self.execution_state = {"status": "running"}
            self.execution_lock = _asyncio.Lock()

    fake_state = _FakeState()

    async def _fake_ha_state(_entity):
        return {"state": "off", "attributes": {}}

    # Patche la lecture HA au niveau du module ha_state_query (import tardif dans le shortcut).
    import services.ha_state_query as hsq
    monkeypatch.setattr(hsq, "read_ha_state", _fake_ha_state)
    monkeypatch.setattr(agents, "get_app_state", lambda: fake_state)

    body = agents.ExecuteRequestBody(
        user_prompt="est-ce que la clim est allumee ?",
        source={"type": "tab5", "mode": "ha"},
    )
    result = await agents.execute_chat(body, _auth=None)

    assert result["response"] == "La climatisation est éteinte."
    assert result["agents_used"] == ["ha_state_query"]
    assert fake_state.execution_state["status"] == "running"
