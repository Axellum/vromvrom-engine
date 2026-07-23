"""
services/ha_state_query.py — Questions d'état domotiques vocales (mode:ha).

Répond en Zero-LLM aux questions d'état (« la clim est allumée ? », « le volet
est ouvert ? », « il fait combien dans le salon ? ») : détection déterministe
→ lecture REST /api/states → phrase TTS courte. Sans ce module, ces questions
tombaient en « Je n'ai pas compris la commande domotique ».

Anti-hallucination : on ne répond QUE depuis l'état live HA ; si la lecture
échoue, on le dit au lieu d'inventer une valeur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.execute_service import (
    _CLIMATE_KEYWORDS,
    _CLIMATE_MODE_LABELS,
    normalize_ha_command_prompt,
    read_ha_state,
    resolve_climate_entity,
)

logger = logging.getLogger(__name__)

# Premier mot impératif → c'est une commande, pas une question d'état.
_IMPERATIVE_FIRST = frozenset({
    "allume", "allumer", "eteins", "eteindre", "ouvre", "ouvrir", "ferme",
    "fermer", "monte", "baisse", "descend", "descends", "mets", "met", "coupe",
    "active", "desactive", "demarre", "arrete", "stop", "regle", "augmente",
    "diminue", "leve",
})
# Marqueurs interrogatifs / d'état (déclenchent la lecture).
_QUESTION_MARKERS = frozenset({"combien", "quelle", "quel", "ou", "est-ce", "comment"})
_STATE_ADJ = frozenset({
    "allumee", "allumees", "allume", "eteinte", "eteintes", "eteint",
    "ouvert", "ouverte", "ouverts", "ferme", "fermee", "fermes",
    "tourne", "marche", "fonctionne", "statut", "etat",
})
_TEMP_MARKERS = frozenset({"temperature", "combien", "degres", "degre"})
# Infinitifs d'action : « tu peux allumer le salon ? » reste une commande même
# formulée en question (1er mot non impératif). On bascule en commande sauf si un
# adjectif d'état est aussi présent (« le salon est allumé ? »).
_ACTION_INFINITIVES = frozenset({
    "allumer", "eteindre", "ouvrir", "fermer", "mettre", "couper", "activer",
    "desactiver", "demarrer", "regler", "baisser", "monter", "augmenter",
    "diminuer", "lever",
})

# Pièce → (entité, attribut) pour la température (source = capteur clim connu).
_TEMPERATURE_SOURCES: dict[str, tuple[str, str]] = {
    "salon": ("climate.salon_daikinap71273_clim", "current_temperature"),
}
_TEMPERATURE_DEFAULT_ROOM = "salon"

# Pièce → (entité lumière, libellé TTS).
_LIGHT_ENTITIES: dict[str, tuple[str, str]] = {
    "salon": ("light.salon", "du salon"),
    "chambre": ("light.h6008_2", "de la chambre"),
    "chevet": ("light.h6008", "de chevet"),
    "cuisine": ("light.sonoff_1000f18da8", "de la cuisine"),
}

_VOLET_STATE_ENTITY = "input_text.volet_serre_etat"
_VOLET_COVER_ENTITY = "cover.volet_serre_rideau"


@dataclass(frozen=True)
class HAStateQuery:
    """Question d'état reconnue."""
    kind: str          # temperature | climate | volet | light
    entity_id: str
    display: str = ""  # libellé pour la phrase (article inclus)


def match_ha_state_query(prompt: str) -> HAStateQuery | None:
    """Détecte une question d'état domotique. None si c'est une commande ou hors-sujet."""
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None
    words = norm.split()
    word_set = set(words)
    # Premier mot impératif → commande (« ferme le volet » ≠ « le volet est fermé »).
    if words[0] in _IMPERATIVE_FIRST:
        return None
    # Infinitif d'action sans adjectif d'état → commande polie (« peux-tu ouvrir… »).
    if (word_set & _ACTION_INFINITIVES) and not (word_set & _STATE_ADJ):
        return None

    is_question = (
        prompt.strip().endswith("?")
        or bool(word_set & _QUESTION_MARKERS)
        or bool(word_set & _STATE_ADJ)
        or "est-ce" in norm
    )
    if not is_question:
        return None

    # Priorité : clim → volet → température → lumière.
    if word_set & _CLIMATE_KEYWORDS:
        return HAStateQuery("climate", resolve_climate_entity(norm))

    if "volet" in norm or "volets" in norm:
        return HAStateQuery("volet", _VOLET_STATE_ENTITY)

    if word_set & _TEMP_MARKERS:
        room = next((r for r in _TEMPERATURE_SOURCES if r in norm), _TEMPERATURE_DEFAULT_ROOM)
        entity, attr = _TEMPERATURE_SOURCES.get(room, (None, None))
        if not entity:
            return None  # Pas de source fiable pour cette pièce → ne pas inventer.
        return HAStateQuery("temperature", entity, display=f"{attr}|{room}")

    for room, (entity, label) in _LIGHT_ENTITIES.items():
        if room in norm:
            return HAStateQuery("light", entity, display=label)

    return None


def _round_temp(value: object) -> int | None:
    try:
        return round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _format_climate(state: dict) -> str:
    st = (state.get("state") or "").lower()
    attrs = state.get("attributes", {})
    if st in ("off", "unavailable", "unknown", ""):
        return "La climatisation est éteinte."
    mode_label = _CLIMATE_MODE_LABELS.get(st, st)
    phrase = f"La climatisation est allumée en mode {mode_label}"
    target = _round_temp(attrs.get("temperature"))
    if target is not None:
        phrase += f", réglée sur {target} degrés"
    phrase += "."
    current = _round_temp(attrs.get("current_temperature"))
    if current is not None:
        phrase += f" Il fait {current} degrés dans la pièce."
    return phrase


def _format_temperature(state: dict, display: str) -> str:
    attr, room = (display.split("|", 1) + [""])[:2] if "|" in display else ("current_temperature", display)
    attrs = state.get("attributes", {})
    value = _round_temp(attrs.get(attr))
    if value is None:
        value = _round_temp(state.get("state"))
    if value is None:
        return "Je n'ai pas la température pour l'instant."
    article = "dans la" if room in ("cuisine", "chambre", "salle") else "dans le"
    return f"Il fait {value} degrés {article} {room}." if room else f"Il fait {value} degrés."


def _format_volet(state: dict) -> str:
    val = (state.get("state") or "").strip().lower()
    mapping = {
        "ouvert": "Le volet est ouvert.",
        "ferme": "Le volet est fermé.",
        "fermé": "Le volet est fermé.",
        "en_mouvement": "Le volet est en mouvement.",
        "partiel": "Le volet est partiellement ouvert.",
        "open": "Le volet est ouvert.",
        "closed": "Le volet est fermé.",
        "opening": "Le volet est en train de s'ouvrir.",
        "closing": "Le volet est en train de se fermer.",
    }
    if val in mapping:
        return mapping[val]
    return "Je n'ai pas l'état du volet pour l'instant."


def _format_light(state: dict, label: str) -> str:
    st = (state.get("state") or "").lower()
    if st == "on":
        return f"La lumière {label} est allumée."
    if st == "off":
        return f"La lumière {label} est éteinte."
    return f"Je n'ai pas l'état de la lumière {label}."


async def resolve_ha_state_query(prompt: str) -> str | None:
    """
    Reconnaît une question d'état et renvoie la phrase TTS, ou None si ce n'en
    est pas une (l'appelant poursuit alors la cascade normale).
    """
    query = match_ha_state_query(prompt)
    if not query:
        return None

    # Le volet expose son état de préférence via l'input_text de suivi ; repli cover.
    state = await read_ha_state(query.entity_id)
    if state is None and query.kind == "volet":
        state = await read_ha_state(_VOLET_COVER_ENTITY)
    if state is None:
        logger.info("[HA STATE] Question '%s' : lecture indisponible", prompt[:60])
        return "Je n'ai pas pu lire l'état pour l'instant."

    if query.kind == "climate":
        return _format_climate(state)
    if query.kind == "temperature":
        return _format_temperature(state, query.display)
    if query.kind == "volet":
        return _format_volet(state)
    if query.kind == "light":
        return _format_light(state, query.display)
    return None
