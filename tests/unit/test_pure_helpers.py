"""
tests/unit/test_pure_helpers.py — Couverture de fonctions pures critiques (Phase 2, #15).

Cible des logiques jusqu'ici non testées et pourtant déterminantes :
- core.errors.classify_error (pilote le retry/self-healing)
- tools.sanitizer.OutputSanitizer (masquage de secrets — sécurité)
- core.intent_splitter.IntentSplitter (décomposition multi-intent)
- core.llm_gateway helpers purs (migration d'IDs, scoring heuristique)
"""

import pytest

from core.errors import classify_error, ErrorCategory, RETRIABLE_CATEGORIES


# ── classify_error ──

@pytest.mark.parametrize("msg,expected", [
    ("Connection timed out after 30s", ErrorCategory.TIMEOUT),
    ("HTTP 429 Too Many Requests", ErrorCategory.RATE_LIMIT),
    ("Connection refused by host", ErrorCategory.NETWORK),
    ("401 Unauthorized: invalid api key", ErrorCategory.AUTH),
    ("Invalid JSON: JSONDecodeError", ErrorCategory.VALIDATION),
    ("Permission denied", ErrorCategory.PERMISSION),
    ("File not found: no such file", ErrorCategory.NOT_FOUND),
    ("Token limit / budget exceeded", ErrorCategory.BUDGET),
    ("Model overloaded, 500 internal server error", ErrorCategory.PROVIDER_DOWN),
    ("OOM: disk full", ErrorCategory.SYSTEM),
])
def test_classify_error_categories(msg, expected):
    assert classify_error(msg).category == expected


def test_retriable_flag_consistency():
    assert classify_error("timed out").is_retriable is True
    assert classify_error("401 unauthorized").is_retriable is False
    # Cohérence avec l'ensemble RETRIABLE_CATEGORIES.
    assert classify_error("connection refused").category in RETRIABLE_CATEGORIES


def test_classify_unknown_and_logic():
    assert classify_error("Erreur inattendue dans l'outil").category == ErrorCategory.LOGIC
    assert classify_error("blabla complètement neutre").category == ErrorCategory.UNKNOWN


def test_agent_error_source_preserved():
    err = classify_error("timeout", source="tool:read_file")
    assert err.source == "tool:read_file"
    assert "TIMEOUT" in str(err)


# ── OutputSanitizer ──

def test_sanitizer_masks_api_key():
    from tools.sanitizer import OutputSanitizer
    s = OutputSanitizer()
    out = s.sanitize("voici la clé api_key=sk-abcdef0123456789ABCDEF stockée")
    assert "sk-abcdef0123456789ABCDEF" not in out
    assert "MASQUÉE" in out


def test_sanitizer_masks_email_and_private_ip():
    from tools.sanitizer import OutputSanitizer
    s = OutputSanitizer()
    out = s.sanitize("contact test.user@example.com sur ${HA_HOST:-192.168.1.x}")
    assert "test.user@example.com" not in out
    assert "${HA_HOST:-192.168.1.x}" not in out


def test_sanitizer_disabled_is_passthrough():
    from tools.sanitizer import OutputSanitizer
    s = OutputSanitizer(enabled=False)
    raw = "api_key=sk-abcdef0123456789ABCDEF"
    assert s.sanitize(raw) == raw


def test_sanitizer_stats_tracked():
    from tools.sanitizer import OutputSanitizer
    s = OutputSanitizer()
    s.sanitize("mail a@b.co et b@c.io")
    assert s.get_stats()["total_sanitized"] >= 2


# ── IntentSplitter ──

def test_split_multi_intent_distinct_domains():
    from core.intent_splitter import IntentSplitter
    sp = IntentSplitter()
    res = sp.split("Allume la lumière du salon puis donne-moi la météo de demain")
    assert len(res) >= 2


def test_split_mono_intent_returns_single():
    from core.intent_splitter import IntentSplitter
    sp = IntentSplitter()
    res = sp.split("Allume la lumière du salon s'il te plaît")
    assert res == ["Allume la lumière du salon s'il te plaît"]


def test_split_short_prompt_not_split():
    from core.intent_splitter import IntentSplitter
    sp = IntentSplitter()
    assert sp.split("ok") == ["ok"]


# ── llm_gateway helpers purs ──

def test_migrate_old_gemini_id_known_and_unknown():
    from core.llm_gateway import migrate_old_gemini_id
    assert migrate_old_gemini_id("gemini-2.5-flash") == "gemini-2.5-flash-free"
    assert migrate_old_gemini_id("deepseek-chat") == "deepseek-chat"  # inchangé


def test_heuristic_base_score_ordering():
    from core.llm_gateway import LLMGateway
    local = LLMGateway._heuristic_base_score("local-llm", "local")
    free = LLMGateway._heuristic_base_score("gemini", "gemini-free-flash")
    paid_pro = LLMGateway._heuristic_base_score("some-pro-model", "other")
    # Le local doit être le moins coûteux (score le plus bas), le Pro le plus haut.
    assert local < free < paid_pro
