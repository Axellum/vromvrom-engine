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
from core.ha_token import get_ha_token  # [T239] lecture centralisée du token HA
from core.ha_url import get_ha_url  # [T330] lecture centralisée de l'URL HA
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

# [#T363] Formes interrogatives NON AMBIGUËS : elles demandent un état et
# n'ordonnent rien, et aucune n'est une transcription plausible d'un impératif.
# « est-elle » / « est-il » sont volontairement ABSENTS : `normalize_vocal_stt`
# les réécrit en « éteins » (`core/vocal_stt_normalize.py:38`) parce que le STT
# confond réellement les deux — les inclure ici ferait perdre de vraies commandes
# mal transcrites. Pour ces formes-là, seul le point d'interrogation tranche.
# « etat de » figure sans point d'interrogation : « État de la lumière de la
# chambre. » est une demande de rapport, jamais un ordre.
_MARQUEURS_INTERROGATIFS = (
    "est-ce", "est ce", "qu'est-ce", "qu est ce",
    "etat de", "l'etat", "quel est", "quelle est",
    "c'est quoi", "c est quoi", "combien",
)
# Premier mot impératif : l'ordre explicite prime sur la forme interrogative.
_IMPERATIFS_ACTION = frozenset({
    "allume", "eteins", "ouvre", "ferme", "monte", "baisse", "descend", "descends",
    "mets", "met", "coupe", "active", "desactive", "demarre", "arrete", "stop",
    "regle", "augmente", "diminue", "leve", "remonte", "remontre", "releve",
})
# Infinitif d'action : « tu peux allumer la lumière ? » reste une commande polie.
_INFINITIFS_ACTION = frozenset({
    "allumer", "eteindre", "ouvrir", "fermer", "mettre", "couper", "activer",
    "desactiver", "demarrer", "arreter", "stopper", "regler", "baisser", "monter",
    "augmenter", "diminuer", "lever", "remonter", "descendre",
})
# Formes réellement dites (STT) pour ouvrir le volet. « remontre » est une
# transcription fréquente de « remonte » (faster-whisper FR) ; « relève » était
# reconnu par personne le 17/08. Ajoutées sans toucher à la reconnaissance
# existante (« monte », « ouvre », « ouvrir », « leve »).
_VOLET_ON_MARKERS = frozenset({
    "monte", "ouvre", "ouvrir", "leve",
    "remonte", "remontre", "releve",
})
# Écho TTS / phrases d'état — ne pas interpréter comme commande
_VOLET_STATUS_MARKERS = frozenset({"sont", "est", "ete", "etait", "etaient", "seront", "deja", "maintenant"})
_VOLET_COVER_ENTITY = "cover.living_room_blind"
_VOLET_SCRIPT = "script.blind_action"
_VOLET_MOVING_ENTITY = "input_boolean.blind_moving"
_VOLET_STOP_WORDS = frozenset({"stop", "stoppe", "arrete", "arret", "arreter"})
# Table pièce → entité clim (extensible : ajouter une ligne suffit pour une 2e clim).
# La détection reste zéro-LLM et choisit l'entité selon la pièce citée, défaut = salon.
_CLIMATE_ENTITIES: dict[str, str] = {
    "salon": "climate.living_room",
}
_CLIMATE_DEFAULT_ENTITY = _CLIMATE_ENTITIES["salon"]
_CLIMATE_ENTITY = _CLIMATE_DEFAULT_ENTITY  # Rétro-compat (références existantes)
_CLIMATE_KEYWORDS = frozenset({
    "clim", "climatisation", "climatiseur", "climatiser", "climatise", "climatisee",
    # Variantes STT fréquentes de « clim » (faster-whisper FR entend souvent « cline »).
    "cline", "clime",
})
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
_SALON_LIGHT_GROUP = "light.living_room"
_SALON_LIGHT_MEMBERS = (
    "light.hallway",
    "light.hallway",
    "light.bedside",
)


@dataclass(frozen=True)
class HACommandMatch:
    service: str
    entity_id: str = ""
    matched_phrase: str = ""
    service_data: dict[str, Any] | None = None


def _volet_script(action: str, phrase: str) -> HACommandMatch:
    """Toutes les commandes volet passent par script.blind_action (suivi écran HA)."""
    return HACommandMatch(
        service=_VOLET_SCRIPT,
        entity_id="",
        matched_phrase=phrase,
        service_data={"action": action},
    )


def ensure_volet_via_script(match: HACommandMatch) -> HACommandMatch:
    """Convertit cover.living_room_blind → script avec suivi mouvement."""
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
    "salon": ("light.living_room", {"on": "light.turn_on", "off": "light.turn_off"}),
    "chambre": ("light.bedroom", {"on": "light.turn_on", "off": "light.turn_off"}),
    "chevet": ("light.bedside", {"on": "light.turn_on", "off": "light.turn_off"}),
    "cuisine": ("light.kitchen", {"on": "light.turn_on", "off": "light.turn_off"}),
    "serre": ("cover.living_room_blind", {"on": "cover.open_cover", "off": "cover.close_cover"}),
    "volet serre": ("cover.living_room_blind", {"on": "cover.open_cover", "off": "cover.close_cover"}),
    "clim salon": ("climate.living_room", {"on": "climate.turn_on", "off": "climate.turn_off"}),
}

# [T356] Libellé TTS humain pour les entités pilotées connues : la phrase
# « <libellé> ne répond pas » doit nommer l'appareil pour que l'utilisateur
# agisse sur la bonne cause (« le volet de la serre ne répond pas », pas « je
# n'ai pas pu exécuter la commande »). Repli : friendly_name HA de l'état.
_ENTITE_LIBELLE: dict[str, str] = {
    _VOLET_COVER_ENTITY: "le volet de la serre",
    "light.living_room": "la lumière du salon",
    "light.bedroom": "la lumière de la chambre",
    "light.bedside": "la lumière de chevet",
    "light.kitchen": "la lumière de la cuisine",
    _CLIMATE_DEFAULT_ENTITY: "la climatisation du salon",
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
    Volet (unique cover.living_room_blind — alias vocal « volets du salon »).
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
    Ex: « et tel les lumières du salon » → éteindre light.living_room
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


def _est_question_sans_ordre(prompt: str, norm: str) -> bool:
    """
    [#T363] La phrase est-elle une QUESTION qui ne porte aucun ordre ?

    Mesuré le 17/08 : « est-ce que la lumière du salon est allumée ? » produisait
    `light.turn_on` sur `light.living_room`, et « État de la lumière de la chambre. »
    produisait `light.turn_off` sur `light.bedroom` (fuzzy 0,82 contre « eteins la
    lumiere de la chambre »). Interroger la maison l'actionnait — et la réponse
    « Lumière de la chambre éteinte. » est indiscernable d'un rapport d'état.
    Six formulations de ce type sur 30 jours dans `vocal_audit_log`.

    Deux mécanismes y menaient : le repli de `match_ha_room_keywords` qui force
    `is_on = True` sur le seul mot « lumière », et le fuzzy qui rapproche une
    question d'une phrase impérative du catalogue. Le garde-fou est posé ici, au
    point d'entrée unique, plutôt que dans chacun d'eux.

    L'ordre explicite l'emporte toujours sur la forme interrogative : « tu peux
    allumer la lumière ? » et « allume la lumière ? » restent des commandes. Ce
    n'est que la question SANS verbe d'action qui rend la main à la cascade, qui
    la traitera en lecture d'état.

    ⚠️ Tout se juge sur le prompt BRUT (minuscules, accents retirés), jamais sur
    `norm` : `normalize_ha_command_prompt` réécrit « allumée » en « allume » et,
    via `normalize_vocal_stt`, « est-elle » en « éteins »
    (`core/vocal_stt_normalize.py:38`). Après normalisation, « Est-elle la
    lumière de la chambre ? » est littéralement devenue un ordre d'extinction :
    juger la forme interrogative sur ce texte-là reviendrait à ne jamais la voir.
    """
    brut = _strip_accents(prompt.strip().lower())
    est_question = brut.endswith("?") or any(
        marqueur in brut for marqueur in _MARQUEURS_INTERROGATIFS
    )
    if not est_question:
        return False
    mots = re.findall(r"[\w'-]+", brut)
    if mots and mots[0] in _IMPERATIFS_ACTION:
        return False
    if set(mots) & _INFINITIFS_ACTION:
        return False
    return True


def match_ha_command(prompt: str) -> HACommandMatch | None:
    """
    Match déterministe ha_commands.json avec tolérance STT (exact puis fuzzy).
    """
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None

    # [#T363] Une question n'est pas un ordre : rendre la main à la cascade, qui
    # dispose d'un chemin de lecture d'état. Ne jamais actionner la maison sur un
    # doute — le coût d'une lecture manquée est nul, celui d'une lampe allumée à
    # 3 h du matin ne l'est pas.
    if _est_question_sans_ordre(prompt, norm):
        logger.debug("[HA CMD] '%s' : question sans ordre, pas une commande", prompt[:60])
        return None

    # Volet : la détection déterministe prime sur le fuzzy ha_commands. Un
    # marker explicite (« remonte », « descend ») est plus fiable que la
    # similarité floue — mesuré le 17/08, le fuzzy confondait « remonte le
    # volet » avec la phrase stop « arrete le volet » (score 0.867) et produisait
    # `stop` au lieu de `open`. L'écho TTS (markers de statut) ou une phrase
    # bruitée sans marker retombent ensuite sur le fuzzy, sans régression.
    stop = match_ha_volet_stop(prompt)
    if stop:
        return stop
    volet = match_ha_volet_keywords(prompt)
    if volet:
        return volet

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
    ha_token = get_ha_token()
    ha_url = get_ha_url()
    if ha_token:
        return ha_token, ha_url
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
    # Secours : lecture brute du fichier .env (T239) — volontairement hors
    # accesseur get_ha_token(), qui ne lit que os.environ ; le .env conserve
    # les deux clés HASS_TOKEN et HA_TOKEN (nettoyage manuel séparé).
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


# ── [T356] Vérification de l'état AVANT d'exécuter une commande domotique ─────
#
# Le moteur n'inspectait jamais l'état de l'entité visée : il envoyait le
# service, lisait le code HTTP et annonçait le succès. Or HA répond HTTP 200 à
# un appel de service visant une entité `unavailable` : l'appareil n'exécute
# jamais la commande, et le moteur disait « Volet fermé » alors que rien ne
# bougeait. Mesuré en prod le 17/08 : 146 entités `unavailable`/`unknown`, dont
# 17 pilotables (cover.living_room_blind, 6 light.sonoff_*, 7 switch.sonoff_*).
#
# On réutilise `read_ha_state` (déjà présent — pas de troisième chemin). La
# lecture d'état est un GET /api/states/<id> sur la session keep-alive partagée
# (~10-40 ms LAN vs 271 ms pour la commande) : un aller-retour léger, nécessaire
# car on doit savoir AVANT l'appel si l'entité est morte pour ne pas annoncer un
# succès. Une lecture indisponible (HA muet, erreur réseau) n'est PAS « l'appareil
# est mort » : on exécute quand même, comme avant.

def _entite_physique_a_verifier(ha_service: str, ha_entity: str) -> str | None:
    """
    Détermine l'entité dont il faut lire l'état avant d'exécuter `ha_service`.

    Les commandes de volet passent par `script.blind_action` (entity_id
    vide) : l'entité physique à vérifier est la cover `cover.living_room_blind`.
    Pour tout autre service, on vérifie l'entité passée en paramètre. Retourne
    None si aucune entité n'est identifiable (script générique sans cible) — on
    exécute alors sans vérification.
    """
    if ha_service == _VOLET_SCRIPT:
        return _VOLET_COVER_ENTITY
    return ha_entity or None


def _cover_sans_retour_de_position(entity_id: str, state: dict) -> bool:
    """
    [#T385] Un volet piloté sans capteur de position reste en `unknown` : c'est
    son état NORMAL, pas une panne.

    Mesuré sur le vrai HA le 17/08 : `cover.living_room_blind` — le volet
    UNIQUE de l'installation, et la cible de la commande vocale la plus
    utilisée — est en `unknown` alors qu'il fonctionne (script relancé avec
    succès à 13:53). #T356 traitait `unknown` comme `unavailable` : sans ce
    garde-fou, chaque commande de volet aurait répondu « le volet de la serre
    ne répond pas » pendant que le volet bouge.

    Le critère reste étroit et vérifiable : domaine `cover`, et aucun attribut
    `current_position`. Un volet QUI sait rendre sa position et tombe en
    `unknown` continue d'être traité comme suspect. `unavailable`, lui, bloque
    toujours — c'est l'état par lequel HA dit qu'un appareil est injoignable.
    """
    if not entity_id.startswith("cover."):
        return False
    return "current_position" not in (state.get("attributes") or {})


def _phrase_entite_hors_service(entity_id: str, state: dict) -> str:
    """
    Phrase TTS qui nomme l'appareil qui ne répond pas, pour que l'utilisateur
    agisse sur la bonne cause (« le volet de la serre ne répond pas », pas « je
    n'ai pas pu exécuter la commande »).
    """
    libelle = _ENTITE_LIBELLE.get(entity_id)
    if not libelle:
        friendly = (state.get("attributes") or {}).get("friendly_name")
        if friendly:
            libelle = str(friendly).lower()
        else:
            libelle = entity_id
    return f"{libelle[0].upper()}{libelle[1:]} ne répond pas."


async def _verifier_etat_entite(ha_service: str, ha_entity: str) -> str | None:
    """
    Lit l'état de l'entité visée avant exécution.

    Retourne une phrase de blocage nommant l'appareil si l'entité est
    `unavailable`/`unknown` (ne pas annoncer un succès). Retourne None si
    l'entité est vivante OU si l'état est invérifiable (lecture en échec) : dans
    les deux cas la commande peut partir.

    [#T385] Une exception étroite : un `cover` SANS attribut `current_position`
    en `unknown` est dans son état normal (volet piloté sans capteur), pas en
    panne — voir `_cover_sans_retour_de_position`.
    """
    entity_id = _entite_physique_a_verifier(ha_service, ha_entity)
    if not entity_id:
        return None
    state = await read_ha_state(entity_id)
    if state is None:
        # État invérifiable ≠ appareil mort : ne pas bloquer une commande dont
        # on ne connaît pas l'état. Le moteur doit rester utilisable sous le doute.
        logger.debug("[T356] Lecture d'état indisponible pour %s — on exécute", entity_id)
        return None
    etat = (state.get("state") or "").lower()
    if etat == "unknown" and _cover_sans_retour_de_position(entity_id, state):
        # [#T385] État normal d'un volet sans capteur de position, pas une panne.
        logger.debug(
            "[T385] %s en 'unknown' sans retour de position — on exécute", entity_id
        )
        return None
    if etat in ("unavailable", "unknown"):
        logger.warning(
            "[T356] Entité %s hors service (état=%s) — commande %s non exécutée",
            entity_id, etat, ha_service,
        )
        return _phrase_entite_hors_service(entity_id, state)
    return None


def _est_action_rejouable(ha_service: str) -> bool:
    """
    [#T323] L'action peut-elle être rejouée sans effet de bord observable ?

    Sert à décider si une requête perdue sur une connexion fermée par le serveur
    peut être retentée. Le critère est l'IDEMPOTENCE de l'effet physique :
    « ferme le volet » rejoué laisse le volet fermé, « inverse la lumière »
    rejoué la rallume. Liste blanche stricte — tout service inconnu est traité
    comme non rejouable, parce que le coût d'une double commande physique chez
    l'utilisateur est bien supérieur à celui d'un échec annoncé.

    Le script volet n'est PLUS rejouable depuis #T350 : il est passé par
    `script.turn_on` et son script HA est en `mode: restart`. Rejouer
    `turn_on` ANNULE l'exécution lancée par la première tentative (la seconde
    commande redémarre le script), donc une reprise interrompt au lieu de
    réessayer. On échoue franchement plutôt que de laisser le volet en cours de
    course sans suivi.
    """
    if ha_service == _VOLET_SCRIPT:
        return False
    action = ha_service.split(".", 1)[-1]
    return action in {
        "turn_on", "turn_off",
        "open_cover", "close_cover", "stop_cover", "set_cover_position",
        "set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode",
        "set_value", "select_option",
    }


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
        logger.warning("[HA EXEC] script.blind_action sans action — refus")
        return False, "Je n'ai pas pu exécuter la commande volet."

    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        return False, "Token HA non configuré. Commande non exécutée."

    # [T356] Vérifier l'état de l'entité visée AVANT d'envoyer le service : si
    # elle est `unavailable`/`unknown`, ne pas annoncer un succès — HA répondrait
    # HTTP 200 mais l'appareil n'exécuterait rien. La phrase rendue nomme
    # l'appareil qui ne répond pas. Une lecture d'état indisponible n'est pas un
    # blocage : la commande part quand même (voir _verifier_etat_entite). Un
    # garde-fou : toute erreur inattendue de la VÉRIFICATION ne doit pas bloquer
    # la commande — le doute ne doit jamais devenir un refus.
    try:
        blocage = await _verifier_etat_entite(ha_service, ha_entity)
    except Exception as exc:  # pragma: no cover - filet de sécurité
        logger.warning("[T356] Vérification d'état en échec pour %s : %s — on exécute", ha_entity, exc)
        blocage = None
    if blocage:
        return False, blocage

    domain, action = ha_service.split(".", 1)
    if domain == "script":
        # script.<id> → script.turn_on : rend la main immédiatement sans
        # attendre la fin du script. L'appel direct
        # POST /api/services/script/<id> ne répond qu'une fois le script
        # terminé (ex. volet 26 s) — inutilisable pour une commande vocale
        # (timeout 10 s, #T350). Les paramètres du script passent par
        # `variables` : à plat, script.turn_on les perdrait et le script
        # démarrerait sans branche correspondante.
        api_url = f"{ha_url.rstrip('/')}/api/services/script/turn_on"
        payload: dict[str, Any] = {
            "entity_id": ha_service,  # script.<id>
            "variables": dict(service_data) if service_data else {},
        }
    else:
        api_url = f"{ha_url.rstrip('/')}/api/services/{domain}/{action}"
        payload: dict[str, Any] = dict(service_data) if service_data else {}
        if ha_entity and "entity_id" not in payload:
            payload["entity_id"] = ha_entity
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
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

    # [#T323] Une seule reprise, et seulement sur ServerDisconnectedError.
    #
    # La session HTTP est partagée et garde ses connexions ouvertes. Quand HA (ou
    # son proxy) ferme une connexion inactive pile au moment où on la reprend au
    # pool, aiohttp lève `ServerDisconnectedError` — ce qui frappe exactement les
    # appels ESPACÉS, donc les commandes vocales. Mesuré en prod : 2 échecs sur 5
    # appels zero-LLM en 7 jours, dont « descend le volet du salon » le 12/08,
    # correctement reconnu (fuzzy 0.88) mais jamais exécuté.
    #
    # La reprise est réservée aux actions REJOUABLES : rejouer `close_cover` est
    # sans effet de bord, rejouer `toggle` inverserait deux fois. Toute action
    # hors de cette liste échoue franchement plutôt que de risquer une double
    # commande physique chez l'utilisateur. Le script volet (désormais via
    # `script.turn_on`, `mode: restart`) n'est pas rejouable : une seconde
    # commande annulerait la première (#T350).
    tentatives = 2 if _est_action_rejouable(ha_service) else 1
    resp_status = None
    resp_text = ""
    for tentative in range(tentatives):
        try:
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
                resp_status = resp.status
                resp_text = await resp.text()
            break
        except aiohttp.ServerDisconnectedError as exc:
            if tentative + 1 >= tentatives:
                logger.warning(
                    "[HA EXEC] %s : connexion fermée par le serveur, abandon "
                    "après %d tentative(s) : %s", ha_service, tentatives, exc,
                )
                raise
            logger.info(
                "[HA EXEC] [#T323] %s : connexion du pool fermée par le serveur, "
                "nouvelle tentative sur une connexion neuve.", ha_service,
            )

    if resp_status is not None:
        logger.warning(
            "[HA EXEC] Échec %s payload=%s : HTTP %s %s",
            ha_service, payload, resp_status, resp_text[:120],
        )

    # Fallback : groupe light.living_room → membres individuels
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
