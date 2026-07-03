"""
tests/unit/test_router_layers.py — Tests des couches pures extraites du Router (Phase 2, D4).

Vérifie isolément _score_categories, _detect_complexity, _detect_grounding et
_resolve_target_agent, désormais testables sans orchestration complète.
"""

from core.router import Router


def _router():
    r = Router.__new__(Router)
    r.default_agent = "planner"
    r.categories = {
        "casual_chat": {"keywords": ["bonjour", "salut"], "weight": 1.0},
        "home_assistant": {"keywords": ["lumière", "volet", "température"], "weight": 1.5},
        "code_generation": {"keywords": ["python", "code"], "weight": 1.2},
        "files": {"keywords": ["fichier"], "weight": 1.0},
        "database": {"keywords": ["sqlite"], "weight": 1.4},
        "analysis": {"keywords": ["audit"], "weight": 1.2},
    }
    r._ha_commands = [
        {"service": "light.turn_on", "entity_id": "light.salon", "phrases": ["allume la lumiere"]},
    ]
    return r


# ── _score_categories ──

def test_score_picks_dominant_weighted_category():
    r = _router()
    words = r._tokenize("allume la lumière du salon")
    scores, dominant, max_score = r._score_categories(words, set(words))
    assert dominant == "home_assistant"
    assert max_score > 0


def test_score_empty_when_no_keyword():
    r = _router()
    words = r._tokenize("xyzzy plover")
    _, dominant, max_score = r._score_categories(words, set(words))
    assert dominant is None
    assert max_score == 0.0


# ── _detect_complexity ──

def test_complexity_by_length():
    r = _router()
    assert r._detect_complexity("a" * 221) is True


def test_complexity_by_keyword():
    r = _router()
    assert r._detect_complexity("fais un refactor du moteur") is True


def test_short_simple_not_complex():
    r = _router()
    assert r._detect_complexity("bonjour") is False


# ── _detect_grounding ──

def test_grounding_detected_on_fresh_data():
    r = _router()
    assert r._detect_grounding("quelle est la météo aujourd'hui ?") is True


def test_grounding_not_detected_on_static_query():
    r = _router()
    assert r._detect_grounding("explique-moi la récursion") is False


# ── _resolve_target_agent ──

def test_resolve_complex_goes_to_default():
    r = _router()
    agent, rtype, tier, meta = r._resolve_target_agent("peu importe", "code_generation", is_complex=True)
    assert agent == "planner"
    assert rtype == "default"


def test_resolve_casual_chat_to_executor():
    r = _router()
    agent, rtype, tier, _ = r._resolve_target_agent("salut", "casual_chat", is_complex=False)
    assert agent == "executor" and rtype == "casual_chat" and tier == "leger"


def test_resolve_ha_deterministic_shortcut():
    r = _router()
    agent, rtype, _, meta = r._resolve_target_agent("Allume la lumière", "home_assistant", is_complex=False)
    assert agent == "ha_agent" and rtype == "ha_deterministic"
    assert meta["direct_tool_call"]["arguments"]["service"] == "light.turn_on"


def test_resolve_ha_non_deterministic():
    r = _router()
    agent, rtype, _, meta = r._resolve_target_agent("règle le volet à moitié", "home_assistant", is_complex=False)
    assert agent == "ha_agent" and rtype == "ha_direct"
    assert meta.get("is_direct_command") is True


def test_resolve_files_to_executor_medium():
    r = _router()
    agent, rtype, tier, _ = r._resolve_target_agent("ouvre le fichier", "files", is_complex=False)
    assert agent == "executor" and tier == "moyen"
