"""Tests normalisation STT + match pièce."""

from core.vocal_stt_normalize import normalize_vocal_stt
from services.execute_service import match_ha_command, match_ha_room_keywords, normalize_ha_command_prompt


def test_stt_est_elle_to_eteins():
    assert "eteins" in normalize_vocal_stt("Est-elle la lumière de la chambre ?")


def test_stt_et_tel_salon():
    norm = normalize_ha_command_prompt("et tel les lumières du salon")
    assert "salon" in norm
    m = match_ha_room_keywords("et tel les lumières du salon")
    assert m is not None
    assert m.entity_id == "light.living_room"
    assert m.service == "light.turn_off"


def test_stt_groupe_salon():
    norm = normalize_vocal_stt("Est-elle le groupe de lumière du salon ?")
    m = match_ha_command(norm)
    assert m is not None
    assert m.entity_id == "light.living_room"


def test_stt_somme_chambre():
    norm = normalize_vocal_stt("Et t'as l'alumière de la somme.")
    assert "chambre" in norm


def test_eteindre_lumiere_salon():
    m = match_ha_command("éteindre la lumière du salon")
    assert m is not None
    assert m.entity_id == "light.living_room"
    assert m.service == "light.turn_off"


def test_descend_volet_salon():
    m = match_ha_command("Descend le volet du salon")
    assert m is not None
    assert m.service == "script.blind_action"
    assert m.service_data == {"action": "close"}


def test_baisse_volet_salon_not_light():
    """« baisse le volet du salon » ne doit pas éteindre light.living_room."""
    m = match_ha_command("baisse le volet du salon")
    assert m is not None
    assert m.service == "script.blind_action"
    assert m.service_data == {"action": "close"}


def test_stop_volet_phrase():
    m = match_ha_command("stop le volet du salon")
    assert m is not None
    assert m.service == "script.blind_action"
    assert m.service_data == {"action": "stop"}
