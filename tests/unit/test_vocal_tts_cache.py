"""Tests vocal_tts_cache."""

from core.vocal_tts_cache import (
    canonical_text_for_ha_action,
    load_phrase_catalog,
    normalize_tts_text,
    resolve_phrase_id,
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
