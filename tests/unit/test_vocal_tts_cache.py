"""Tests vocal_tts_cache."""

from core.vocal_tts_cache import (
    _TOOL_DUMP_FALLBACK,
    canonical_text_for_ha_action,
    load_phrase_catalog,
    looks_like_tool_or_code_dump,
    normalize_tts_text,
    resolve_phrase_id,
    sanitize_discussion_tts,
)


def test_normalize_tts_text():
    assert normalize_tts_text("  Lumière du salon allumée. ") == "lumière du salon allumée"


def test_resolve_phrase_id_from_catalog():
    catalog = load_phrase_catalog()
    assert "lum_salon_on" in catalog
    pid = resolve_phrase_id(catalog["lum_salon_on"])
    assert pid == "lum_salon_on"


def test_canonical_text_for_ha_action():
    text = canonical_text_for_ha_action("light.salon", "light.turn_on")
    assert text == "Lumière du salon allumée."


def test_looks_like_tool_dump_json():
    dump = (
        '{"tool":"tab5-engine","arguments":{"action":"get_models_catalog"}}'
        '{ "status": "success", "models": [] }'
    )
    assert looks_like_tool_or_code_dump(dump) is True
    assert sanitize_discussion_tts(dump) == _TOOL_DUMP_FALLBACK


def test_looks_like_tool_dump_mcp_prose():
    dump = 'We will call the MCP tool search_ha_entities with query "chimini".'
    assert looks_like_tool_or_code_dump(dump) is True
    assert "outils" in sanitize_discussion_tts(dump).lower() or "web" in sanitize_discussion_tts(dump).lower()


def test_sanitize_keeps_french_sentence():
    ok = "Gemini est une famille de modèles Google, mais je n'ai pas la date exacte de la dernière sortie."
    assert looks_like_tool_or_code_dump(ok) is False
    assert "Gemini" in sanitize_discussion_tts(ok)
