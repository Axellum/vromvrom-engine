#!/usr/bin/env python3
"""Sync vocal/domotique files from private moteur_agents → OSS avec anonymisation."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PRIVATE = Path(r"H:\AuxFilsDesIdees\moteur_agents")
OSS = Path(r"H:\vromvrom-engine-oss")

IP_REPLACEMENTS = [
    ("192.168.0.16", "${HA_HOST:-192.168.1.x}"),
    ("192.168.0.43", "${DECK_HOST:-192.168.1.x}"),
    ("192.168.0.139", "${DECK_HOST_WIFI:-192.168.1.x}"),
    ("192.168.0.84", "${LM_STUDIO_HOST:-192.168.1.x}"),
    ("192.168.0.155", "${PC_HOST:-192.168.1.x}"),
    ("192.168.0.159", "${PC_HOST_ALT:-192.168.1.x}"),
    ("192.168.0.88", "${TAB5_HOST:-192.168.1.x}"),
    ("192.168.0.74", "${MINITAB_HOST:-192.168.1.x}"),
    ("192.168.0.254", "${GATEWAY_HOST:-192.168.1.x}"),
    ("192.168.0.20", "${ZIGBEE_HOST:-192.168.1.x}"),
]

ENTITY_REPLACEMENTS = [
    ("light.salon", "light.living_room"),
    ("light.h6008_2", "light.bedroom"),
    ("light.h6008", "light.bedside"),
    ("light.h600c", "light.hallway"),
    ("light.sonoff_1000f18da8", "light.kitchen"),
    ("cover.volet_serre_rideau", "cover.living_room_blind"),
    ("input_boolean.volet_serre_mouvement", "input_boolean.blind_moving"),
    ("climate.salon_daikinap71273_clim", "climate.living_room"),
    ("script.tab5_volet_action", "script.blind_action"),
    (
        "switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active",
        "switch.example_wake_word",
    ),
]

PATH_REPLACEMENTS = [
    (r"H:\AuxFilsDesIdees\00ProjetTab\audio_lib", "audio_lib"),
    (r"E:\AuxFilsDesIdees\00ProjetTab\audio_lib", "audio_lib"),
    (r"H:\AuxFilsDesIdees", "."),
]

MISC_REPLACEMENTS = [
    ("popydeck", "remote-host"),
]

# (relative_path, mode) mode: direct | ip | ip+entity | vocal_tts | entity
FILES = [
    ("core/vocal_audit.py", "direct"),
    ("core/vocal_stt_normalize.py", "direct"),
    ("core/vocal_tts_cache.py", "vocal_tts"),
    ("core/ha_fuzzy_matcher.py", "ip"),
    ("core/runtime_db.py", "ip"),
    ("core/router.py", "ip"),
    ("services/execute_service.py", "ip+entity"),
    ("services/ha_vocal_fallback.py", "ip+entity"),
    ("services/pipeline_service.py", "ip"),
    ("api/routes/agents.py", "ip"),
    ("api/routes/streaming.py", "ip"),
    ("tools/validate_tab5_payloads.py", "ip"),
    ("tests/fixtures/tab5_payloads.json", "direct"),
    ("tests/unit/test_vocal_stt_normalize.py", "direct"),
    ("tests/unit/test_vocal_tts_cache.py", "entity"),
    ("tests/unit/test_vocal_execute.py", "direct"),
    ("tests/unit/test_ha_command_match.py", "entity"),
]


def apply_replacements(text: str, *, ips: bool, entities: bool, paths: bool) -> str:
    if ips:
        for old, new in IP_REPLACEMENTS:
            text = text.replace(old, new)
    if entities:
        for old, new in ENTITY_REPLACEMENTS:
            text = text.replace(old, new)
    if paths:
        for old, new in PATH_REPLACEMENTS:
            text = text.replace(old, new)
    for old, new in MISC_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def patch_vocal_tts_cache(text: str) -> str:
    text = apply_replacements(text, ips=True, entities=True, paths=True)
    # Chemins audio : uniquement relatif + override env
    text = text.replace(
        "_REPO_ROOT = Path(__file__).resolve().parents[2]\n"
        "_PHRASES_YAML = _REPO_ROOT / \"scripts\" / \"domotic_phrases.yaml\"\n"
        "_CACHE_DIRS = (\n"
        "    _REPO_ROOT / \"00ProjetTab\" / \"audio_lib\",\n"
        "    Path(r\"H:\\AuxFilsDesIdees\\00ProjetTab\\audio_lib\"),\n"
        "    Path(r\"E:\\AuxFilsDesIdees\\00ProjetTab\\audio_lib\"),\n"
        ")",
        "_REPO_ROOT = Path(__file__).resolve().parents[1]\n"
        "_PHRASES_YAML = _REPO_ROOT / \"scripts\" / \"domotic_phrases.example.yaml\"\n"
        "_CACHE_DIRS = (\n"
        "    _REPO_ROOT / \"audio_lib\",\n"
        ")",
    )
    text = text.replace(
        '"vocal_bonjour_axel": "Bonjour Axel, que veux-tu contrôler ?"',
        '"vocal_bonjour": "Bonjour, que veux-tu contrôler ?"',
    )
    return text


def patch_ha_vocal_fallback(text: str) -> str:
    text = apply_replacements(text, ips=False, entities=True, paths=False)
    text = text.replace(
        '- Si la pièce est « salon » et action allumer → light.salon + light.turn_on.\n'
        '- Si la pièce est « chambre » et action éteindre → light.h6008_2 + light.turn_off.\n',
        '- Si la pièce est « salon » et action allumer → light.living_room + light.turn_on.\n'
        '- Si la pièce est « chambre » et action éteindre → light.bedroom + light.turn_off.\n',
    )
    text = text.replace('{"service":"light.turn_on","entity_id":"light.salon"}', '{"service":"light.turn_on","entity_id":"light.living_room"}')
    return text


def sync_file(rel: str, mode: str) -> None:
    src = PRIVATE / rel
    dst = OSS / rel
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")

    if mode == "direct":
        shutil.copy2(src, dst)
        return
    if mode == "ip":
        text = apply_replacements(text, ips=True, entities=False, paths=False)
    elif mode == "ip+entity":
        text = apply_replacements(text, ips=True, entities=True, paths=False)
    elif mode == "entity":
        text = apply_replacements(text, ips=False, entities=True, paths=False)
    elif mode == "vocal_tts":
        text = patch_vocal_tts_cache(text)
    else:
        raise ValueError(mode)

    if rel == "services/ha_vocal_fallback.py":
        text = patch_ha_vocal_fallback(text)

    dst.write_text(text, encoding="utf-8", newline="\n")
    print(f"  synced {rel} ({mode})")


def main() -> None:
    print("Sync vocal OSS depuis moteur_agents privé...")
    for rel, mode in FILES:
        sync_file(rel, mode)
    print("Done.")


if __name__ == "__main__":
    main()
