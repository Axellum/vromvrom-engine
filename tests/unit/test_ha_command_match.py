"""Tests match_ha_command (tolérance STT)."""

from services.execute_service import (
    build_natural_ha_response,
    match_ha_command,
    normalize_ha_command_prompt,
    prompt_has_domotic_action,
)


def test_normalize_stt_verbs():
    assert normalize_ha_command_prompt("Allumé le salon") == "allume le salon"
    assert normalize_ha_command_prompt("Éteint la chambre ?") == "eteins la chambre"


def test_match_ha_command_exact():
    m = match_ha_command("allume le salon")
    assert m is not None
    assert m.entity_id == "light.salon"
    assert m.service == "light.turn_on"


def test_match_ha_command_stt_variant():
    m = match_ha_command("allumé le salon")
    assert m is not None
    assert m.entity_id == "light.salon"


def test_prompt_has_domotic_action():
    assert prompt_has_domotic_action("bonjour allume le salon")
    assert not prompt_has_domotic_action("bonjour axel")


def test_build_natural_ha_response_cached():
    text = build_natural_ha_response("light.salon", "light.turn_on", "Salon")
    assert text == "Lumière du salon allumée."


def test_build_natural_volet_stop():
    text = build_natural_ha_response(
        "",
        "script.tab5_volet_action",
        service_data={"action": "stop"},
    )
    assert text == "Volet arrêté."


def test_build_natural_ha_response_no_entity_slug():
    text = build_natural_ha_response("light.h6008_2", "light.turn_on", "Lumiere chambre")
    assert "h6008" not in text.lower()
    assert "lumière" in text.lower()


def test_volet_status_phrase_not_command():
    """Écho TTS « les volets sont fermés » ne doit pas relancer close."""
    assert match_ha_command("les volets du salon sont fermes") is None
    assert match_ha_command("les volets du salon sont ouverts") is None


def test_volet_imperative_still_matches():
    m = match_ha_command("descend les volets du salon")
    assert m is not None
    assert m.service_data == {"action": "close"}
    m2 = match_ha_command("ouvre les volets du salon")
    assert m2 is not None
    assert m2.service_data == {"action": "open"}
