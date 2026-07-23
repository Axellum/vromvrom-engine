"""
services/execute_service.py — Logique partagée de /api/execute (vocal, HA, source_router).

Centralise les fast paths HA, l'application des overrides source_router et les
réponses d'échec mode domotique pour éviter la duplication agents.py / streaming.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

import aiohttp

from core.ha_tls import ha_ssl_context
from core.source_router import ModeType, RequestSource
from core.vocal_stt_normalize import normalize_vocal_stt
from core.vocal_tts_cache import canonical_text_for_ha_action, enrich_response_with_tts_cache

logger = logging.getLogger(__name__)

HA_MODE_FAILURE_RESPONSE = "Je n'ai pas compris la commande domotique."
CHAT_MODE_FAILURE_RESPONSE = "Désolé, je n'ai pas su répondre. Peux-tu reformuler ?"
_HA_COMMANDS_PATH = Path(__file__).resolve().parents[1] / "ha_commands.json"
_HA_COMMAND_FUZZY_THRESHOLD = 0.82

_ACTION_ON_MARKERS = frozenset({
    "allume", "allumer", "allumé", "allumee", "allumée", "mets", "met", "mettre",
    "active", "demarre", "démarre", "ouvre", "ouvrir", "monte",
})
_ACTION_OFF_MARKERS = frozenset({
    "eteins", "éteins", "eteindre", "éteindre", "eteint", "éteint", "coupe",
    "ferme", "fermer", "arrete", "arrête", "desactive", "désactive", "baisse",
    "descend", "descends",
})
_VOLET_OFF_MARKERS = frozenset({"descend", "descends", "baisse", "ferme", "fermer"})
_VOLET_ON_MARKERS = frozenset({"monte", "ouvre", "ouvrir", "leve"})
# Écho TTS / phrases d'état — ne pas interpréter comme commande
_VOLET_STATUS_MARKERS = frozenset({"sont", "est", "ete", "etait", "etaient", "seront", "deja", "maintenant"})
_VOLET_COVER_ENTITY = "cover.volet_serre_rideau"
_VOLET_SCRIPT = "script.tab5_volet_action"
_VOLET_MOVING_ENTITY = "input_boolean.volet_serre_mouvement"
_VOLET_STOP_WORDS = frozenset({"stop", "stoppe", "arrete", "arret", "arreter"})
# Table pièce → entité clim (extensible : ajouter une ligne suffit pour une 2e clim).
# La détection reste zéro-LLM et choisit l'entité selon la pièce citée, défaut = salon.
_CLIMATE_ENTITIES: dict[str, str] = {
    "salon": "climate.salon_daikinap71273_clim",
}
_CLIMATE_DEFAULT_ENTITY = _CLIMATE_ENTITIES["salon"]
_CLIMATE_ENTITY = _CLIMATE_DEFAULT_ENTITY  # Rétro-compat (références existantes)
_CLIMATE_KEYWORDS = frozenset({"clim", "climatisation", "climatiseur", "climatiser"})
_CLIMATE_MODE_KEYWORDS: dict[str, str] = {
    "froid": "cool", "rafraichis": "cool", "rafraichir": "cool",
    "refroidis": "cool", "refroidir": "cool", "climatise": "cool",
    "chaud": "heat", "chauffe": "heat", "chauffer": "heat", "chauffage": "heat",
    "sec": "dry", "deshumidifie": "dry", "deshumidifier": "dry", "deshumidification": "dry",
    "ventilation": "fan_only", "ventile": "fan_only", "ventiler": "fan_only", "brasse": "fan_only",
}
_CLIMATE_MODE_LABELS: dict[str, str] = {
    "cool": "froid", "heat": "chaud", "dry": "sec", "fan_only": "ventilation", "off": "éteint",
}
# Plage Daikin usuelle — évite de capter un nombre sans rapport avec la température
_CLIMATE_TEMP_RE = re.compile(r"\b(1[5-9]|2[0-9]|3[0-1])\b")
# Nombres en lettres 15-31 : le STT écrit parfois « vingt-deux » au lieu de « 22 ».
_CLIMATE_TEMP_WORDS: dict[str, int] = {
    "quinze": 15, "seize": 16, "dix sept": 17, "dix huit": 18, "dix neuf": 19,
    "vingt": 20, "vingt et un": 21, "vingt un": 21, "vingt deux": 22,
    "vingt trois": 23, "vingt quatre": 24, "vingt cinq": 25, "vingt six": 26,
    "vingt sept": 27, "vingt huit": 28, "vingt neuf": 29,
    "trente": 30, "trente et un": 31, "trente un": 31,
}
# Alternation triée par longueur décroissante : « vingt deux » testé avant « vingt ».
_CLIMATE_TEMP_WORDS_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _CLIMATE_TEMP_WORDS), key=len, reverse=True)) + r")\b"
)
# True : « régler + mode » émet set_hvac_mode PUIS set_temperature (2 appels) au
# lieu d'un set_temperature portant hvac_mode — plus compatible (certains Daikin
# rejettent hvac_mode dans set_temperature).
_CLIMATE_SPLIT_HVAC_AND_TEMP = True
_SALON_LIGHT_GROUP = "light.salon"
_SALON_LIGHT_MEMBERS = (
    "light.sonoff_1001601d46",
    "light.h600c",
    "light.h6008",
)


@dataclass(frozen=True)
class HACommandMatch:
    service: str
    entity_id: str = ""
    matched_phrase: str = ""
    service_data: dict[str, Any] | None = None


def _volet_script(action: str, phrase: str) -> HACommandMatch:
    """Toutes les commandes volet passent par script.tab5_volet_action (suivi écran HA)."""
    return HACommandMatch(
        service=_VOLET_SCRIPT,
        entity_id="",
        matched_phrase=phrase,
        service_data={"action": action},
    )


def ensure_volet_via_script(match: HACommandMatch) -> HACommandMatch:
    """Convertit cover.volet_serre_rideau → script avec suivi mouvement."""
    if match.service == _VOLET_SCRIPT:
        return match
    if match.entity_id != _VOLET_COVER_ENTITY:
        return match
    action_map = {
        "cover.open_cover": "open",
        "cover.close_cover": "close",
        "cover.stop_cover": "stop",
    }
    action = action_map.get(match.service)
    if action:
        return _volet_script(action, match.matched_phrase)
    return match


def _strip_accents(text: str) -> str:
    for src, dst in (
        ("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"),
        ("à", "a"), ("â", "a"), ("ä", "a"),
        ("î", "i"), ("ï", "i"),
        ("ô", "o"), ("ö", "o"),
        ("ù", "u"), ("û", "u"), ("ü", "u"),
        ("ç", "c"),
    ):
        text = text.replace(src, dst)
    return text


def normalize_ha_command_prompt(text: str) -> str:
    """Normalise une phrase utilisateur pour ha_commands.json (STT tolérant)."""
    t = normalize_vocal_stt(text)
    t = _strip_accents(t)
    t = re.sub(r"[^\w\s'-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"[?.!,;]+$", "", t).strip()
    replacements = (
        (r"\ballumee\b", "allume"),
        (r"\ballume\b", "allume"),
        (r"\beteint\b", "eteins"),
        (r"\beteins\b", "eteins"),
        (r"\beteindre\b", "eteins"),
        (r"\blumiere\b", "lumiere"),
        (r"\blumieres\b", "lumiere"),
    )
    for pattern, repl in replacements:
        t = re.sub(pattern, repl, t)
    return t


def prompt_has_domotic_action(text: str) -> bool:
    words = set(normalize_ha_command_prompt(text).split())
    return bool(words & (_ACTION_ON_MARKERS | _ACTION_OFF_MARKERS))


@lru_cache(maxsize=1)
def load_ha_commands() -> list[dict[str, Any]]:
    if not _HA_COMMANDS_PATH.is_file():
        return []
    try:
        data = json.loads(_HA_COMMANDS_PATH.read_text(encoding="utf-8"))
        return list(data.get("commands", []))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[HA CMD] Impossible de charger ha_commands.json : %s", exc)
        return []


# Pièce → (entity_id, services on/off)
_ROOM_ENTITIES: dict[str, tuple[str, dict[str, str]]] = {
    "salon": ("light.salon", {"on": "light.turn_on", "off": "light.turn_off"}),
    "chambre": ("light.h6008_2", {"on": "light.turn_on", "off": "light.turn_off"}),
    "chevet": ("light.h6008", {"on": "light.turn_on", "off": "light.turn_off"}),
    "cuisine": ("light.sonoff_1000f18da8", {"on": "light.turn_on", "off": "light.turn_off"}),
    "serre": ("cover.volet_serre_rideau", {"on": "cover.open_cover", "off": "cover.close_cover"}),
    "volet serre": ("cover.volet_serre_rideau", {"on": "cover.open_cover", "off": "cover.close_cover"}),
    "clim salon": ("climate.salon_daikinap71273_clim", {"on": "climate.turn_on", "off": "climate.turn_off"}),
}


def match_ha_volet_stop(prompt: str) -> HACommandMatch | None:
    """Stop volet — « stop », « arrête le volet », etc."""
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None
    words = set(norm.split())
    if not (words & _VOLET_STOP_WORDS):
        return None
    if "volet" in norm or "volets" in norm:
        return _volet_script("stop", norm)
    filler = frozenset({"le", "la", "les", "du", "des", "salon"})
    if words <= (_VOLET_STOP_WORDS | filler):
        return _volet_script("stop", norm)
    return None


def match_ha_volet_keywords(prompt: str) -> HACommandMatch | None:
    """
    Volet (unique cover volet_serre_rideau — alias vocal « volets du salon »).
    Prioritaire sur le match lumière salon quand « volet » est présent.
    """
    norm = normalize_ha_command_prompt(prompt)
    if not norm or "volet" not in norm:
        return None
    words = set(norm.split())
    # « les volets du salon sont fermés » (écho TTS) ≠ commande
    if words & _VOLET_STATUS_MARKERS:
        return None
    is_off = bool(words & (_ACTION_OFF_MARKERS | _VOLET_OFF_MARKERS))
    is_on = bool(words & (_ACTION_ON_MARKERS | _VOLET_ON_MARKERS))
    if not is_off and not is_on:
        return None
    script_action = "close" if is_off and not is_on else "open"
    return _volet_script(script_action, f"volet:{script_action}")


def resolve_climate_entity(norm: str) -> str:
    """Choisit l'entité clim selon la pièce citée (défaut : salon)."""
    for room, entity in _CLIMATE_ENTITIES.items():
        if room in norm:
            return entity
    return _CLIMATE_DEFAULT_ENTITY


def parse_climate_temperature(norm: str) -> int | None:
    """Température cible 15-31 depuis chiffres OU nombres en lettres."""
    digit = _CLIMATE_TEMP_RE.search(norm)
    if digit:
        return int(digit.group(1))
    word = _CLIMATE_TEMP_WORDS_RE.search(norm.replace("-", " "))
    if word:
        return _CLIMATE_TEMP_WORDS[word.group(1)]
    return None


def match_ha_climate_command(prompt: str) -> HACommandMatch | None:
    """
    Réglage clim (température et/ou mode) — Zero-LLM.

    Le on/off simple ("allume/éteins la clim du salon") reste couvert par
    match_ha_room_keywords via _ROOM_ENTITIES["clim salon"] ; cette fonction
    ne gère que le cas absent du fuzzy matcher : température cible et/ou
    mode HVAC (froid/chaud/sec/ventilation), qui partaient auparavant en LLM
    (lent, peu fiable — cause du « les LLM ont du mal à régler la clim »).
    """
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None
    words = set(norm.split())
    if not (words & _CLIMATE_KEYWORDS):
        return None

    temperature = parse_climate_temperature(norm)

    mode = None
    for word in words:
        if word in _CLIMATE_MODE_KEYWORDS:
            mode = _CLIMATE_MODE_KEYWORDS[word]
            break

    if temperature is None and mode is None:
        return None  # Pas de température ni de mode : laisser le on/off existant gérer

    service_data: dict[str, Any] = {}
    if temperature is not None:
        service = "climate.set_temperature"
        service_data["temperature"] = temperature
        if mode:
            service_data["hvac_mode"] = mode
    else:
        service = "climate.set_hvac_mode"
        service_data["hvac_mode"] = mode

    return HACommandMatch(
        service=service,
        entity_id=resolve_climate_entity(norm),
        matched_phrase=f"climate:{service}:{service_data}",
        service_data=service_data,
    )


def match_ha_room_keywords(prompt: str) -> HACommandMatch | None:
    """
    Match pièce + action quand STT est trop bruité pour ha_commands exact/fuzzy.
    Ex: « et tel les lumières du salon » → éteindre light.salon
    """
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None
    # Volet géré à part (évite « baisse le volet salon » → lumière éteinte)
    if "volet" in norm:
        return match_ha_volet_keywords(prompt)
    words = set(norm.split())
    is_off = bool(words & _ACTION_OFF_MARKERS)
    is_on = bool(words & _ACTION_ON_MARKERS)
    if not is_off and not is_on:
        if "lumiere" in norm:
            is_on = True
        else:
            return None
    action = "off" if is_off and not is_on else "on"

    for room in sorted(_ROOM_ENTITIES, key=len, reverse=True):
        if room in norm:
            entity_id, services = _ROOM_ENTITIES[room]
            service = services.get(action)
            if service:
                return HACommandMatch(
                    service=service,
                    entity_id=entity_id,
                    matched_phrase=f"room:{room}:{action}",
                )
    return None


def match_ha_command(prompt: str) -> HACommandMatch | None:
    """
    Match déterministe ha_commands.json avec tolérance STT (exact puis fuzzy).
    """
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None

    best: HACommandMatch | None = None
    best_score = 0.0

    for cmd in load_ha_commands():
        service = cmd.get("service", "")
        if not service:
            continue
        entity_id = str(cmd.get("entity_id") or "")
        raw_data = cmd.get("service_data")
        service_data = dict(raw_data) if raw_data else None
        if not entity_id and not service_data:
            continue
        for phrase in cmd.get("phrases", []):
            phrase_norm = normalize_ha_command_prompt(str(phrase))
            if not phrase_norm:
                continue
            hit = HACommandMatch(
                service=service,
                entity_id=entity_id,
                matched_phrase=phrase_norm,
                service_data=service_data,
            )
            if norm == phrase_norm or phrase_norm in norm or norm in phrase_norm:
                return ensure_volet_via_script(hit)
            score = SequenceMatcher(None, norm, phrase_norm).ratio()
            if score > best_score:
                best_score = score
                best = hit

    if best and best_score >= _HA_COMMAND_FUZZY_THRESHOLD:
        logger.info(
            "[HA CMD] Match fuzzy %.2f : '%s' ≈ '%s' → %s",
            best_score, norm, best.matched_phrase, best.entity_id or best.service_data,
        )
        return ensure_volet_via_script(best)

    stop = match_ha_volet_stop(prompt)
    if stop:
        return stop
    volet = match_ha_volet_keywords(prompt)
    if volet:
        return volet
    climate = match_ha_climate_command(prompt)
    if climate:
        return climate
    room = match_ha_room_keywords(prompt)
    return ensure_volet_via_script(room) if room else None


async def is_volet_moving() -> bool:
    """État live HA — volet en mouvement (chrono 26 s)."""
    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        return False
    url = f"{ha_url.rstrip('/')}/api/states/{_VOLET_MOVING_ENTITY}"
    headers = {"Authorization": f"Bearer {ha_token}"}
    try:
        session = _get_ha_session()
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            return data.get("state") == "on"
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("[HA CMD] is_volet_moving skip : %s", exc)
        return False


async def resolve_ha_command_for_execute(prompt: str) -> HACommandMatch | None:
    """
    Match HA + règles async (ex. « stop » seul uniquement si volet en mouvement).
    """
    cmd = match_ha_command(prompt)
    if not cmd:
        return None
    cmd = ensure_volet_via_script(cmd)
    if (
        cmd.service == _VOLET_SCRIPT
        and (cmd.service_data or {}).get("action") == "stop"
        and "volet" not in normalize_ha_command_prompt(prompt)
    ):
        if not await is_volet_moving():
            return None
    return cmd


def build_natural_ha_response(
    entity_id: str,
    ha_service: str,
    friendly_name: str | None = None,
    service_data: dict[str, Any] | None = None,
) -> str:
    """Phrase TTS naturelle sans identifiant technique HA."""
    if ha_service == _VOLET_SCRIPT and service_data:
        from core.vocal_tts_cache import load_phrase_catalog
        action = str(service_data.get("action", ""))
        pid = {"open": "vol_salon_ouvert", "close": "vol_salon_ferme", "stop": "vol_salon_stop"}.get(action)
        if pid:
            cached = load_phrase_catalog().get(pid)
            if cached:
                return cached

    cached = canonical_text_for_ha_action(entity_id, ha_service)
    if cached:
        return cached

    domain = entity_id.split(".", 1)[0] if entity_id else ""
    action = ha_service.split(".", 1)[-1] if ha_service else ""
    name = (friendly_name or "").strip()

    if domain == "light":
        if "turn_on" in action or action == "on":
            if name:
                return f"Lumière {name.lower()} allumée."
            return "Lumière allumée."
        if "turn_off" in action or action == "off":
            if name:
                return f"Lumière {name.lower()} éteinte."
            return "Lumière éteinte."
    if domain == "climate":
        if "turn_on" in action:
            return f"Climatisation {name.lower()} allumée." if name else "Climatisation allumée."
        if "turn_off" in action:
            return f"Climatisation {name.lower()} éteinte." if name else "Climatisation éteinte."
        if action in ("set_temperature", "set_hvac_mode"):
            temp = (service_data or {}).get("temperature")
            hvac = (service_data or {}).get("hvac_mode")
            mode_txt = _CLIMATE_MODE_LABELS.get(hvac, "") if hvac else ""
            if temp is not None and mode_txt:
                return f"Climatisation réglée sur {temp} degrés, mode {mode_txt}."
            if temp is not None:
                return f"Climatisation réglée sur {temp} degrés."
            if mode_txt:
                return f"Climatisation en mode {mode_txt}."
    if domain == "cover":
        if "open" in action:
            return f"{name} ouvert." if name else "Volet ouvert."
        if "close" in action:
            return f"{name} fermé." if name else "Volet fermé."
    if domain == "switch":
        if "turn_on" in action:
            return f"{name} activé." if name else "C'est activé."
        if "turn_off" in action:
            return f"{name} désactivé." if name else "C'est désactivé."

    if name:
        return f"{name}, c'est fait."
    return "Commande exécutée."


def apply_source_config_overrides(
    config: dict,
    request_source: RequestSource,
    tier_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """
    Applique tier/modèle recommandés par source_router + override explicite requête.
    """
    from services.pipeline_service import WORKLOAD_TIERS, apply_workload_override

    updated = dict(config)
    if not model_override and not tier_override:
        source_tier = request_source.get_model_tier()
        if source_tier in WORKLOAD_TIERS:
            updated = apply_workload_override(updated, tier=source_tier)
    return apply_workload_override(updated, tier=tier_override, model=model_override)


def get_execute_timeout(request_source: RequestSource, routing_type: str) -> float:
    """Timeout effectif pour /api/execute selon le mode source."""
    if request_source.mode == ModeType.HA:
        return request_source.get_timeout()
    if routing_type == "casual_chat":
        return request_source.get_timeout() if request_source.mode == ModeType.CHAT else 15.0
    return request_source.get_timeout()


def should_block_full_pipeline(request_source: RequestSource) -> bool:
    """Modes vocaux Tab5 : jamais Planner/DAG (~30-120s)."""
    return request_source.mode in (ModeType.HA, ModeType.CHAT)


def build_ha_mode_failure_response(session_id: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "session_id": session_id,
        "response": HA_MODE_FAILURE_RESPONSE,
        "history": [{
            "agent_name": "ha_mode_blocked",
            "status": "success",
            "result_data": HA_MODE_FAILURE_RESPONSE,
            "next_agent": "END",
            "error_message": None,
            "new_tasks": [],
            "metadata": {"routing_type": "ha_mode_failure"},
        }],
        "agents_used": ["ha_mode_blocked"],
    }


def build_chat_mode_failure_response(session_id: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "session_id": session_id,
        "response": CHAT_MODE_FAILURE_RESPONSE,
        "history": [{
            "agent_name": "discussion_chat",
            "status": "success",
            "result_data": CHAT_MODE_FAILURE_RESPONSE,
            "next_agent": "END",
            "error_message": None,
            "new_tasks": [],
            "metadata": {"routing_type": "discussion_chat_failure"},
        }],
        "agents_used": ["discussion_chat"],
    }


def _read_ha_credentials() -> tuple[str, str]:
    ha_token = os.environ.get("HASS_TOKEN", "")
    ha_url = os.environ.get("HASS_URL", "https://${HA_HOST:-192.168.1.x}:8123")
    if ha_token:
        return ha_token, ha_url
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as ef:
            for line in ef:
                line = line.strip()
                if line.startswith("HASS_TOKEN="):
                    ha_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("HASS_URL="):
                    ha_url = line.split("=", 1)[1].strip().strip('"').strip("'")
    return ha_token, ha_url


# ── Session aiohttp partagée (keep-alive) pour les appels HA du chemin vocal ──
# Chaque commande domotique ouvrait auparavant une ClientSession + un handshake
# TLS complet (~50-150 ms sur le chemin critique). On réutilise une session
# unique par boucle asyncio, recréée si fermée ou rattachée à une autre boucle
# (redémarrage serveur / tests). Voir close_ha_session() pour l'arrêt propre.
_ha_session: aiohttp.ClientSession | None = None
_ha_session_loop: asyncio.AbstractEventLoop | None = None


def _get_ha_session() -> aiohttp.ClientSession:
    """Retourne la session HA partagée, en la (re)créant au besoin."""
    global _ha_session, _ha_session_loop
    loop = asyncio.get_running_loop()
    if _ha_session is None or _ha_session.closed or _ha_session_loop is not loop:
        connector = aiohttp.TCPConnector(ssl=ha_ssl_context(), limit=8, ttl_dns_cache=300)
        _ha_session = aiohttp.ClientSession(connector=connector)
        _ha_session_loop = loop
    return _ha_session


async def close_ha_session() -> None:
    """Ferme proprement la session HA partagée (à appeler au shutdown FastAPI)."""
    global _ha_session
    if _ha_session is not None and not _ha_session.closed:
        await _ha_session.close()
    _ha_session = None


async def read_ha_state(entity_id: str) -> dict[str, Any] | None:
    """Lit l'état live d'une entité HA (/api/states/<id>). None si indisponible."""
    if not entity_id:
        return None
    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        return None
    url = f"{ha_url.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {ha_token}"}
    try:
        session = _get_ha_session()
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("[HA STATE] read %s skip : %s", entity_id, exc)
        return None


async def execute_ha_service(
    ha_service: str,
    ha_entity: str,
    response_text: str | None = None,
    friendly_name: str | None = None,
    service_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Appelle un service HA REST. Retourne (succès, message TTS)."""
    volet_match = ensure_volet_via_script(
        HACommandMatch(service=ha_service, entity_id=ha_entity, service_data=service_data),
    )
    ha_service = volet_match.service
    ha_entity = volet_match.entity_id
    service_data = volet_match.service_data

    if ha_service == _VOLET_SCRIPT and not (service_data or {}).get("action"):
        logger.warning("[HA EXEC] script.tab5_volet_action sans action — refus")
        return False, "Je n'ai pas pu exécuter la commande volet."

    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        return False, "Token HA non configuré. Commande non exécutée."

    domain, action = ha_service.split(".", 1)
    api_url = f"{ha_url.rstrip('/')}/api/services/{domain}/{action}"
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = dict(service_data) if service_data else {}
    if ha_entity and "entity_id" not in payload:
        payload["entity_id"] = ha_entity
    http_session = _get_ha_session()

    # Compatibilité Daikin : régler le mode AVANT la température, en 2 appels
    # (certains backends rejettent hvac_mode dans set_temperature). On garde
    # service_data intact pour la phrase TTS ; seul le payload POST est allégé.
    if (
        _CLIMATE_SPLIT_HVAC_AND_TEMP
        and ha_service == "climate.set_temperature"
        and payload.get("hvac_mode")
    ):
        mode_payload = {"entity_id": ha_entity, "hvac_mode": payload.pop("hvac_mode")}
        try:
            async with http_session.post(
                f"{ha_url.rstrip('/')}/api/services/climate/set_hvac_mode",
                json=mode_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as mode_resp:
                if mode_resp.status != 200:
                    logger.warning("[HA EXEC] set_hvac_mode préalable échoué : HTTP %s", mode_resp.status)
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("[HA EXEC] set_hvac_mode préalable erreur : %s", exc)

    async with http_session.post(
        api_url,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status == 200:
            if response_text:
                return True, response_text
            return True, build_natural_ha_response(
                ha_entity, ha_service, friendly_name, service_data=service_data,
            )
        resp_text = await resp.text()
        logger.warning(
            "[HA EXEC] Échec %s payload=%s : HTTP %s %s",
            ha_service, payload, resp.status, resp_text[:120],
        )

    # Fallback : groupe light.salon → membres individuels
    if ha_entity == _SALON_LIGHT_GROUP and "turn_" in ha_service:
        action = ha_service.split(".", 1)[-1]
        any_ok = False
        for member in _SALON_LIGHT_MEMBERS:
            async with http_session.post(
                f"{ha_url.rstrip('/')}/api/services/light/{action}",
                json={"entity_id": member},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    any_ok = True
                else:
                    logger.debug(
                        "[HA EXEC] Fallback membre %s : HTTP %s",
                        member, resp.status,
                    )
        if any_ok:
            return True, build_natural_ha_response(_SALON_LIGHT_GROUP, ha_service, friendly_name)
        return False, "Je n'ai pas pu éteindre les lumières du salon."

    return False, "Je n'ai pas pu exécuter la commande domotique."


def build_ha_fast_path_response(
    session_id: str,
    response_text: str,
    agent_name: str,
    metadata: dict,
) -> dict[str, Any]:
    payload = {
        "status": "completed",
        "session_id": session_id,
        "response": response_text,
        "history": [{
            "agent_name": agent_name,
            "status": "success",
            "result_data": response_text,
            "next_agent": "END",
            "error_message": None,
            "new_tasks": [],
            "metadata": metadata,
        }],
        "agents_used": [agent_name],
    }
    return enrich_response_with_tts_cache(payload)


async def get_casual_chat_context(user_prompt: str, max_chars: int = 1500) -> str:
    """
    RAG léger pour fast-path discussion (#T173) : 3 faits mémoire max.
    """
    snippets: list[str] = []
    try:
        from memory.memory_db import MemoryDB

        db = MemoryDB()
        facts = db.search_facts_weighted(user_prompt, limit=3)
        for fact in facts:
            title = fact.get("title") or fact.get("key") or ""
            content = (fact.get("content") or fact.get("value") or "")[:400]
            if title or content:
                snippets.append(f"- {title}: {content}".strip(": "))
    except Exception as exc:
        logger.debug("[FAST_PATH] RAG facts skip : %s", exc)

    if not snippets:
        return ""
    block = "\n".join(snippets)
    if len(block) > max_chars:
        block = block[:max_chars] + "…"
    return (
        "\n\n[CONTEXTE PROJET — faits utiles, ne pas inventer au-delà]\n"
        f"{block}"
    )
