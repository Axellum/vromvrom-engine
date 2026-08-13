"""Tests du générateur de noyau de secours local (scripts/gen_ha_local_fallback)."""

import json
from pathlib import Path

import yaml

from scripts.gen_ha_local_fallback import build_fallback

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_commands() -> list[dict]:
    data = json.loads((_REPO_ROOT / "ha_commands.json").read_text(encoding="utf-8"))
    return data.get("commands", [])


def test_only_core_services_exported():
    """Le noyau n'exporte que lumières/volet/clim on-off — pas les switches wake word."""
    sentences, intents = build_fallback(_load_commands())
    for name, spec in intents.items():
        service = spec["action"][0]["service"]
        assert service in {
            "light.turn_on", "light.turn_off",
            "climate.turn_on", "climate.turn_off",
            "script.tab5_volet_action",
        }, f"{name} → service hors noyau : {service}"
    # Les commandes wake-word (switch.*) ne doivent pas fuiter dans le secours.
    assert not any("WakeWord" in n or "Micro" in n for n in intents)


def test_sentences_and_intents_are_consistent():
    """Chaque intent de phrases a une définition intent_script correspondante."""
    sentences, intents = build_fallback(_load_commands())
    assert set(sentences["intents"]) == set(intents)
    assert sentences["language"] == "fr"


def test_volet_actions_use_script_with_action_data():
    """Le volet passe par script.tab5_volet_action avec la bonne action."""
    _, intents = build_fallback(_load_commands())
    for action_kind in ("open", "close", "stop"):
        name = f"Tab5CoreVolet{action_kind.title()}"
        assert name in intents, f"intent volet manquant : {name}"
        act = intents[name]["action"][0]
        assert act["service"] == "script.tab5_volet_action"
        assert act["data"]["action"] == action_kind


def test_light_intent_has_entity_target_and_speech():
    _, intents = build_fallback(_load_commands())
    salon_on = intents["Tab5CoreLightSalonOn"]
    assert salon_on["action"][0]["target"]["entity_id"] == "light.salon"
    assert salon_on["speech"]["text"] == "Lumière allumée."


def test_output_is_valid_yaml_serialisable():
    sentences, intents = build_fallback(_load_commands())
    # Ne doit pas lever — YAML HA valide.
    assert yaml.safe_dump(sentences, allow_unicode=True)
    assert yaml.safe_dump(intents, allow_unicode=True)


def test_committed_files_match_source():
    """Les fichiers générés commités sont à jour vis-à-vis de ha_commands.json."""
    sentences, intents = build_fallback(_load_commands())
    out_dir = _REPO_ROOT / "deploy" / "ha_local_fallback"
    committed_sentences = yaml.safe_load(
        (out_dir / "custom_sentences" / "fr" / "tab5_core.yaml").read_text(encoding="utf-8")
    )
    committed_intents = yaml.safe_load(
        (out_dir / "tab5_local_fallback_intents.yaml").read_text(encoding="utf-8")
    )
    assert committed_sentences == sentences, "Régénère : python3 -m scripts.gen_ha_local_fallback"
    assert committed_intents == intents, "Régénère : python3 -m scripts.gen_ha_local_fallback"
