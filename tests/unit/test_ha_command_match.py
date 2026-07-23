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


def test_climate_temperature_and_mode():
    m = match_ha_command("mets la clim a 21 en mode froid")
    assert m is not None
    assert m.service == "climate.set_temperature"
    assert m.entity_id == "climate.salon_daikinap71273_clim"
    assert m.service_data == {"temperature": 21, "hvac_mode": "cool"}


def test_climate_temperature_only():
    m = match_ha_command("regle la clim sur 19")
    assert m is not None
    assert m.service == "climate.set_temperature"
    assert m.service_data == {"temperature": 19}


def test_climate_mode_only():
    m = match_ha_command("mets la clim en mode chaud")
    assert m is not None
    assert m.service == "climate.set_hvac_mode"
    assert m.service_data == {"hvac_mode": "heat"}


def test_climate_without_temp_or_mode_falls_back_to_on_off():
    """« allume la clim du salon » : sans temp/mode, le fast-path clim ne doit pas intercepter."""
    m = match_ha_command("allume la clim du salon")
    assert m is not None
    assert m.service == "climate.turn_on"


def test_build_natural_climate_temperature_response():
    text = build_natural_ha_response(
        "climate.salon_daikinap71273_clim",
        "climate.set_temperature",
        service_data={"temperature": 21, "hvac_mode": "cool"},
    )
    assert text == "Climatisation réglée sur 21 degrés, mode froid."
