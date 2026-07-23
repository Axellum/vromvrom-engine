#!/usr/bin/env python3
"""
scripts/gen_ha_local_fallback.py — Génère le NOYAU DE SECOURS LOCAL de HA Assist.

Source de vérité UNIQUE = ha_commands.json (moteur). Ce script en extrait le
sous-ensemble VITAL (lumières on/off, volet open/close/stop, clim on/off) et
produit la config HA Assist native correspondante :

  deploy/ha_local_fallback/custom_sentences/fr/tab5_core.yaml  (phrases → intents)
  deploy/ha_local_fallback/tab5_local_fallback_intents.yaml    (intent_script)

Ainsi, quand le moteur (Steam Deck) est éteint/injoignable, l'agent LOCAL de HA
répond quand même à ces commandes vitales — sans redéfinir les phrases à la main
(donc sans divergence avec le moteur). Les fonctions riches (clim température/mode,
lecture d'état, discussion) restent au moteur.

Usage : python3 -m scripts.gen_ha_local_fallback  (depuis la racine du repo)
Régénérer après toute modification de ha_commands.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HA_COMMANDS = _REPO_ROOT / "ha_commands.json"
_OUT_DIR = _REPO_ROOT / "deploy" / "ha_local_fallback"
_SENTENCES_OUT = _OUT_DIR / "custom_sentences" / "fr" / "tab5_core.yaml"
_INTENTS_OUT = _OUT_DIR / "tab5_local_fallback_intents.yaml"

# Services retenus pour le noyau vital (le reste = moteur uniquement).
_CORE_SERVICES = frozenset({
    "light.turn_on", "light.turn_off",
    "climate.turn_on", "climate.turn_off",
    "script.tab5_volet_action",
})

# Phrase TTS courte par action (cohérente avec build_natural_ha_response du moteur).
_SPEECH: dict[str, str] = {
    "light.turn_on": "Lumière allumée.",
    "light.turn_off": "Lumière éteinte.",
    "climate.turn_on": "Climatisation allumée.",
    "climate.turn_off": "Climatisation éteinte.",
    "volet.open": "Volet ouvert.",
    "volet.close": "Volet fermé.",
    "volet.stop": "Volet arrêté.",
}


def _camel(text: str) -> str:
    """Slug CamelCase alphanumérique pour un nom d'intent stable."""
    parts = re.split(r"[^0-9A-Za-z]+", text)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _intent_name(cmd: dict[str, Any]) -> str:
    service = cmd["service"]
    if service == "script.tab5_volet_action":
        action = (cmd.get("service_data") or {}).get("action", "")
        return f"Tab5CoreVolet{action.title()}"
    domain, verb = service.split(".", 1)
    suffix = "On" if verb.endswith("on") else "Off"
    entity = cmd.get("entity_id", "")
    return f"Tab5Core{_camel(domain)}{_camel(entity.split('.', 1)[-1])}{suffix}"


def _speech_for(cmd: dict[str, Any]) -> str:
    service = cmd["service"]
    if service == "script.tab5_volet_action":
        action = (cmd.get("service_data") or {}).get("action", "")
        return _SPEECH.get(f"volet.{action}", "C'est fait.")
    return _SPEECH.get(service, "C'est fait.")


def _action_for(cmd: dict[str, Any]) -> dict[str, Any]:
    service = cmd["service"]
    if service == "script.tab5_volet_action":
        return {"service": service, "data": dict(cmd.get("service_data") or {})}
    action: dict[str, Any] = {"service": service}
    if cmd.get("entity_id"):
        action["target"] = {"entity_id": cmd["entity_id"]}
    if cmd.get("service_data"):
        action["data"] = dict(cmd["service_data"])
    return action


def build_fallback(commands: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retourne (custom_sentences, intent_script) pour le noyau vital."""
    intents_sentences: dict[str, Any] = {}
    intent_script: dict[str, Any] = {}

    for cmd in commands:
        if cmd.get("service") not in _CORE_SERVICES:
            continue
        # Ignorer les phrases avec accents (doublons) : HA gère la casse/accents,
        # on garde les variantes sans accent + une avec, dédupliquées.
        phrases = sorted({str(p).strip() for p in cmd.get("phrases", []) if str(p).strip()})
        if not phrases:
            continue
        name = _intent_name(cmd)
        # Fusion si deux entrées produisent le même intent (ex. alias volet).
        bucket = intents_sentences.setdefault(name, {"data": [{"sentences": []}]})
        existing = bucket["data"][0]["sentences"]
        for p in phrases:
            if p not in existing:
                existing.append(p)
        if name not in intent_script:
            intent_script[name] = {
                "speech": {"text": _speech_for(cmd)},
                "action": [_action_for(cmd)],
            }

    custom_sentences = {"language": "fr", "intents": intents_sentences}
    return custom_sentences, intent_script


def main() -> None:
    commands = json.loads(_HA_COMMANDS.read_text(encoding="utf-8")).get("commands", [])
    custom_sentences, intent_script = build_fallback(commands)

    _SENTENCES_OUT.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GÉNÉRÉ par scripts/gen_ha_local_fallback.py — NE PAS ÉDITER À LA MAIN.\n"
        "# Source de vérité : ha_commands.json (moteur). Régénérer après modif.\n"
        "# Noyau de secours : commandes vitales servies par HA Assist LOCAL quand\n"
        "# le moteur (Steam Deck) est injoignable.\n"
    )
    _SENTENCES_OUT.write_text(
        header + yaml.safe_dump(custom_sentences, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    _INTENTS_OUT.write_text(
        header + yaml.safe_dump(intent_script, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    print(f"✅ {len(intent_script)} intents de secours générés")
    print(f"   → {_SENTENCES_OUT.relative_to(_REPO_ROOT)}")
    print(f"   → {_INTENTS_OUT.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
