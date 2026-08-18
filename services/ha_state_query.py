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
import re
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
# [#T364] « combien » ne suffit PLUS à faire une question de température.
# Mesuré en prod le 17/08 : « Combien fait 17 fois 24 ? » était répondu « Il fait
# 22 degrés dans le salon. » en 38 ms, et 5 questions sur 5 commençant par
# « combien » (prix d'un billet, habitants de la France, temps de cuisson)
# renvoyaient la même température. Le mot est trop courant en question libre.
# Il ne compte désormais que s'il est ancré par une pièce connue ; les marqueurs
# ci-dessous, eux, sont sans ambiguïté et suffisent seuls.
_TEMP_MARKERS = frozenset({"temperature", "degres", "degre"})
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
    "salon": ("climate.living_room", "current_temperature"),
    # [#T371] La chambre porte un thermomètre bien vivant
    # (`sensor.bedroom_temperature`, 24,49 °C relevé sur le HA de
    # prod le 18/08) mais n'était déclarée nulle part. Depuis #T364, « quelle
    # est la température de la chambre ? » ne renvoyait plus le salon : elle ne
    # renvoyait RIEN, et partait au chat, qui n'a pas accès au capteur. Un
    # `sensor.*` porte sa valeur dans l'état et non dans un attribut :
    # `_format_temperature` retombe sur `state` quand l'attribut est absent.
    "chambre": ("sensor.bedroom_temperature", "state"),
}
_TEMPERATURE_DEFAULT_ROOM = "salon"

# [#T371] Une température « dehors » n'est pas un état domotique : c'est la
# météo, étage SUIVANT de la cascade (`ha_weather_query`, #T367). Mesuré le
# 18/08 sur le code de master : « quelle est la température extérieure ? »
# répondait le capteur du SALON — une valeur exacte, donc indiscernable d'une
# bonne réponse, et l'étage météo n'était jamais atteint.
_MARQUEURS_EXTERIEUR = frozenset({"exterieur", "exterieure", "exterieurs", "dehors"})

# [#T371] Lieu explicitement nommé : « la température DE LA buanderie ». Si le
# lieu cité n'est aucune pièce connue, on ne répond pas plutôt que de servir le
# capteur d'une pièce voisine.
_LOCALISATION_RE = re.compile(
    r"\b(?:dans|de|du|au|a)\s+(?:(?:la|le|les|l')\s*)?([a-z]{3,})"
)
# Mots que la préposition attrape sans qu'ils désignent un lieu. « maison » est
# volontairement ici : la maison, pour le moteur, c'est la pièce de vie — donc
# le défaut salon reste bon.
_MOTS_NON_LIEUX = frozenset({
    "temperature", "degre", "degres", "combien", "quelle", "quel", "maison",
    "moment", "matin", "soir", "nuit", "journee", "demain", "aujourd", "hui",
})


def _localisation_inconnue(norm: str) -> bool:
    """Vrai si la phrase nomme un lieu qui n'est aucune pièce connue."""
    lieux = [m for m in _LOCALISATION_RE.findall(norm) if m not in _MOTS_NON_LIEUX]
    if not lieux:
        return False
    return not any(lieu in _PIECES_CONNUES for lieu in lieux)

# Pièce → (entité lumière, libellé TTS).
_LIGHT_ENTITIES: dict[str, tuple[str, str]] = {
    "salon": ("light.living_room", "du salon"),
    "chambre": ("light.bedroom", "de la chambre"),
    "chevet": ("light.bedside", "de chevet"),
    "cuisine": ("light.kitchen", "de la cuisine"),
}

_VOLET_STATE_ENTITY = "input_text.blind_state"
_VOLET_COVER_ENTITY = "cover.living_room_blind"

# [#T364] Pièces réellement connues du moteur : celles qui portent une entité.
# Sert d'ancrage à « combien » et empêche de répondre pour une pièce voisine.
_PIECES_CONNUES = frozenset(_LIGHT_ENTITIES) | frozenset(_TEMPERATURE_SOURCES)
# [#T364] Mot de domaine obligatoire pour la branche lumière : le seul nom de
# pièce ne suffit plus. Mesuré : « Quel est l'attempérature du salon ? » (STT
# abîmé, « attempérature » ne matche pas « temperature ») tombait dans la branche
# lumière et répondait « La lumière du salon est allumée. » — une réponse sur un
# tout autre appareil que celui demandé.
_LUMIERE_MARKERS = frozenset({"lumiere", "lumieres", "lampe", "lampes", "eclairage"})

# ── [#T358] Questions GÉNÉRALES : elles parlent du monde, pas de l'installation ──
#
# Mesuré en prod le 17/08 (deux fois, 58 ms) : « en une phrase, c'est quoi un
# volet roulant ? » était répondu « Le volet est partiellement ouvert. » — la
# détection se contentait du mot « volet » dans une phrase interrogative, sans
# vérifier que la question porte sur CET appareil-ci. Le mode `chat` porte 98 %
# du trafic vocal réel, donc toute question de culture générale contenant un nom
# d'appareil était détournée vers une lecture d'état.
_MARQUEURS_EXPLICATION = (
    "explique", "expliquer", "explication",
    "comment marche", "comment fonctionne", "comment ca marche",
    "comment ca fonctionne", "comment fabrique", "comment installer",
    "comment on installe", "comment choisir",
    "a quoi sert", "a quoi servent", "definition",
    "difference entre", "quelle difference", "quelles differences",
)
# Nom d'appareil précédé d'un article INDÉFINI (« un volet », « une ampoule
# LED ») : marque l'objet générique. Une question d'état réelle emploie un
# déterminant défini ou possessif (« le volet », « ma clim »).
_MOTS_APPAREIL = (
    "volet", "volets", "lumiere", "lumieres", "lampe", "lampes", "ampoule",
    "ampoules", "clim", "climatisation", "climatiseur", "chauffage",
    "temperature", "thermostat", "store", "stores",
)
_RE_APPAREIL_GENERIQUE = re.compile(
    r"\b(?:un|une|des|d'un|d'une)\s+(?:\w+\s+)?(?:" + "|".join(_MOTS_APPAREIL) + r")\b"
)


@dataclass(frozen=True)
class HAStateQuery:
    """Question d'état reconnue."""
    kind: str          # temperature | climate | volet | light
    entity_id: str
    display: str = ""  # libellé pour la phrase (article inclus)


def _est_question_generale(norm: str, word_set: set[str]) -> bool:
    """
    [#T358] La question porte-t-elle sur le monde plutôt que sur l'installation ?

    Ordre imposé par le vocabulaire : une demande d'explication l'emporte sur
    l'adjectif d'état, car les deux partagent les mêmes verbes — « comment
    fonctionne une climatisation ? » contient « fonctionne », qui est aussi le
    marqueur d'état de « la clim fonctionne ? ». Sans cette priorité, la moitié
    des définitions repasserait en lecture d'état.

    Vient ensuite l'adjectif d'état, qui tranche en faveur de l'installation :
    « est-ce qu'une lumière est allumée ? » reste une question d'état malgré
    l'article indéfini. En dernier, un nom d'appareil générique (« un volet
    roulant ») signe la question de culture générale, qui doit poursuivre la
    cascade jusqu'au chat au lieu de déclencher une lecture HA.
    """
    if any(marqueur in norm for marqueur in _MARQUEURS_EXPLICATION):
        return True
    if word_set & _STATE_ADJ:
        return False
    return bool(_RE_APPAREIL_GENERIQUE.search(norm))


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

    # [#T358] Question générale (définition, explication, objet générique) : ne
    # pas la détourner vers une lecture d'état. On rend None pour que la cascade
    # poursuive jusqu'au chat, qui sait répondre « un volet roulant, c'est… ».
    if _est_question_generale(norm, word_set):
        logger.debug("[HA STATE] '%s' : question générale, pas une lecture d'état", prompt[:60])
        return None

    # Priorité : clim → volet → température → lumière.
    if word_set & _CLIMATE_KEYWORDS:
        return HAStateQuery("climate", resolve_climate_entity(norm))

    if "volet" in norm or "volets" in norm:
        return HAStateQuery("volet", _VOLET_STATE_ENTITY)

    # [#T364] La pièce citée prime toujours sur le défaut : une question sur la
    # chambre ne doit jamais être répondue avec le capteur du salon.
    piece_citee = next((p for p in _PIECES_CONNUES if p in norm), None)
    demande_thermique = bool(word_set & _TEMP_MARKERS) or (
        "combien" in word_set and piece_citee is not None
    )
    if demande_thermique:
        # [#T371] Extérieur : ce n'est pas une lecture d'état. On rend None pour
        # que l'étage météo réponde ; sinon le salon est servi comme
        # température du dehors.
        if word_set & _MARQUEURS_EXTERIEUR:
            logger.debug("[HA STATE] '%s' : température extérieure → météo", norm[:60])
            return None
        # [#T371] Lieu nommé mais inconnu (« la buanderie ») : jamais le capteur
        # d'une autre pièce. Le défaut salon ne vaut que si AUCUN lieu n'est
        # nommé (« il fait combien ? »).
        if piece_citee is None and _localisation_inconnue(norm):
            logger.debug("[HA STATE] '%s' : lieu inconnu, pas de repli salon", norm[:60])
            return None
        room = piece_citee or _TEMPERATURE_DEFAULT_ROOM
        entity, attr = _TEMPERATURE_SOURCES.get(room, (None, None))
        if not entity:
            # Pièce sans capteur connu → ne pas répondre celle d'à côté.
            return None
        return HAStateQuery("temperature", entity, display=f"{attr}|{room}")

    # [#T364] La branche lumière exige le mot de domaine ET la pièce. Sans le
    # mot, elle servait de fourre-tout à toute question nommant une pièce.
    if word_set & _LUMIERE_MARKERS:
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
