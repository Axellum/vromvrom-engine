"""Tests vocal audit + mode HA execute helpers."""

import gc
import os
import shutil
import tempfile

import pytest

from core.ha_fuzzy_matcher import HAFuzzyMatcher, FUZZY_AMBIGUITY_DELTA, FUZZY_MATCH_THRESHOLD, FUZZY_MATCH_THRESHOLD_EMB
from core.runtime_db import get_connection, override_db_path
from core.source_router import ModeType, RequestSource
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
    ha = RequestSource(type="tab5", mode=ModeType.HA, tts_enabled=True)
    chat = RequestSource(type="voice", mode=ModeType.CHAT, tts_enabled=True)
    assert should_block_full_pipeline(ha)
    assert should_block_full_pipeline(chat)


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
