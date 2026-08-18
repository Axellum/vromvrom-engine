"""
services/ha_weather_query.py — Météo locale vocale (mode:chat).

Répond en Zero-LLM aux demandes de météo (« quel temps fait-il ? », « est-ce
qu'il va pleuvoir demain ? », « la météo ») à partir de l'entité météo de la
maison Home Assistant, au lieu de partir au spécialiste web qui n'a pas la
localisation et finit par livrer un chiffre inventé sur un autre continent.

Source de vérité : l'entité `weather.*` de la maison (Météo-France), lue via la
session aiohttp partagée et les identifiants HA existants — aucun troisième
chemin HTTP, aucun `requests`.

Anti-hallucination : on ne répond QUE depuis les données HA lues. Si la lecture
échoue, la prévision est absente pour le jour demandé, ou l'entité est
inconnue, on rend None : la cascade repart vers le spécialiste web comme avant.
On ne fabrique jamais de valeur de repli ni de moyenne.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

import aiohttp

from services.execute_service import (
    _get_ha_session,
    _read_ha_credentials,
    read_ha_state,
)

logger = logging.getLogger(__name__)

# Entité météo de la maison — UNE seule constante, surchargeable par variable
# d'environnement. Si la variable désigne une entité absente, la lecture
# échouera et on rendra None : comportement voulu (pas de source → pas de météo).
_WEATHER_ENTITY = os.environ.get("HA_WEATHER_ENTITY") or "weather.home"

# Pièces nommées → question de température INTÉRIEURE, déjà traitée par
# `services/ha_state_query.py`. Si le prompt nomme une pièce, on rend None pour
# ne pas voler une question qui marche aujourd'hui.
_INDOOR_ROOMS = frozenset({"salon", "chambre", "cuisine", "chevet"})


class WeatherHorizon(str, Enum):
    """Horizon temporel d'une demande météo."""

    NOW = "now"            # maintenant → attributs de l'entité
    TODAY = "today"        # aujourd'hui → attributs de l'entité
    TOMORROW = "tomorrow"  # demain → get_forecasts daily
    WEEKEND = "weekend"    # week-end → get_forecasts daily (sam + dim)


@dataclass(frozen=True)
class WeatherQuery:
    """Demande météo reconnue et son horizon."""

    horizon: WeatherHorizon


# Traduction des conditions Home Assistant en français (TTS court).
_CONDITION_LABELS: dict[str, str] = {
    "sunny": "ensoleillé",
    "partlycloudy": "partiellement nuageux",
    "cloudy": "nuageux",
    "rainy": "pluvieux",
    "pouring": "pluie forte",
    "lightning": "orageux",
    "fog": "brumeux",
    "snowy": "neigeux",
    "windy": "venteux",
    "clear-night": "ciel dégagé",
}

# Marqueurs de demande météo (formulations réellement observées en prod).
# « quel temps » couvre « quel temps fait-il / il fait / il fera ».
_WEATHER_PHRASES = (
    "quel temps",
    "la meteo",
    "la météo",
    "il va pleuvoir",
    "il pleuvra",
    "pleuvoir",
)
_WEATHER_WORDS = frozenset({"meteo", "météo", "pluie", "pleut", "pleuvra"})
# « il fait combien » = température ; « dehors » distingue l'extérieur de la
# question intérieure (« il fait combien dans le salon », pièce → None).
_OUTDOOR_TEMP_PHRASE = "il fait combien"
_OUTDOOR_MARKER = "dehors"
# [#T371] « quelle est la température extérieure ? » n'était reconnue par
# personne : l'étage d'état la captait et répondait le capteur du SALON, et même
# une fois relâchée elle n'atteignait pas la météo, faute de marqueur. Les deux
# orthographes sont listées parce que `_normalize` de ce module CONSERVE les
# accents — c'est la cause racine de #T383, on ne la refait pas.
_OUTDOOR_MARKERS = ("dehors", "exterieur", "extérieur", "exterieure", "extérieure")
_TEMP_WORDS = ("temperature", "température", "degres", "degrés", "il fait combien")

# Marqueurs d'horizon.
_HORIZON_WEEKEND = ("week-end", "weekend", "ce week end", "ce weekend")
_HORIZON_TOMORROW = "demain"
_HORIZON_NOW = ("maintenant", "tout de suite")
_HORIZON_TODAY = ("aujourd hui", "aujourd'hui")
# Formulations au futur sans « demain » explicite → jour suivant.
_FUTURE_PHRASES = ("il fera", "il va pleuvoir", "il pleuvra", "pleuvra")


def _normalize(text: str) -> str:
    """Normalise un prompt vocal (minuscules, accents, apostrophes)."""
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = re.sub(r"[^\w\sàâäéèêëïîôùûüç'-]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _looks_like_weather(norm: str) -> bool:
    """Vrai si le prompt normalisé est une demande de météo."""
    if any(p in norm for p in _WEATHER_PHRASES):
        return True
    words = set(norm.split())
    if words & _WEATHER_WORDS:
        return True
    # Température extérieure : « il fait combien dehors » (pas de pièce nommée).
    if _OUTDOOR_TEMP_PHRASE in norm and _OUTDOOR_MARKER in norm:
        return True
    # [#T371] Toute formulation « température + extérieur » vaut demande météo.
    if any(m in norm for m in _OUTDOOR_MARKERS) and any(t in norm for t in _TEMP_WORDS):
        return True
    return False


def _detect_horizon(norm: str) -> WeatherHorizon:
    if any(w in norm for w in _HORIZON_WEEKEND):
        return WeatherHorizon.WEEKEND
    if _HORIZON_TOMORROW in norm:
        return WeatherHorizon.TOMORROW
    if any(w in norm for w in _HORIZON_NOW):
        return WeatherHorizon.NOW
    if any(w in norm for w in _HORIZON_TODAY):
        return WeatherHorizon.TODAY
    # Formulation au futur sans horizon explicite → jour suivant.
    if any(f in norm for f in _FUTURE_PHRASES):
        return WeatherHorizon.TOMORROW
    return WeatherHorizon.NOW


def match_weather_query(prompt: str) -> WeatherQuery | None:
    """Détecte une demande météo et son horizon. None si ce n'en est pas une."""
    norm = _normalize(prompt)
    if not norm:
        return None
    # Question de température INTÉRIEURE (pièce nommée) → laisse ha_state_query.
    if any(room in norm for room in _INDOOR_ROOMS):
        return None
    if not _looks_like_weather(norm):
        return None
    return WeatherQuery(horizon=_detect_horizon(norm))


def _round_temp(value: object) -> int | None:
    try:
        return round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _translate_condition(condition: object) -> str:
    cond = str(condition or "").strip().lower()
    return _CONDITION_LABELS.get(cond, cond)


def _format_now(state: dict) -> str:
    """Phrase « maintenant/aujourd'hui » depuis les attributs de l'entité."""
    condition = _translate_condition(state.get("state"))
    attrs = state.get("attributes", {}) or {}
    temp = _round_temp(attrs.get("temperature"))
    if temp is None:
        # Pas de température exploitable → ne pas inventer de valeur.
        return ""
    phrase = f"Il fait {temp} degrés, {condition}."
    apparent = _round_temp(attrs.get("apparent_temperature"))
    if apparent is not None and abs(apparent - temp) > 3:
        phrase += f" Ressenti {apparent} degrés."
    return phrase


def _parse_forecast_date(value: object) -> date | None:
    """Extrait la date d'un `datetime` HA (ex. '2026-08-17T00:00:00+00:00')."""
    text = str(value or "")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                return None
        return None


def _format_forecast_day(entry: dict, label: str) -> str:
    """Phrase TTS pour une prévision journalière."""
    condition = _translate_condition(entry.get("condition"))
    temp = _round_temp(entry.get("temperature"))
    templow = _round_temp(entry.get("templow"))
    phrase = f"{label}, {condition}"
    if temp is not None:
        phrase += f", {temp} degrés"
    if templow is not None:
        phrase += f" au plus bas {templow}"
    phrase += "."
    precipitation = entry.get("precipitation")
    if isinstance(precipitation, (int, float)) and precipitation > 0:
        phrase += f" {round(float(precipitation) * 100)}% de risques de précipitations."
    return phrase


async def _get_daily_forecast() -> list[dict[str, Any]] | None:
    """Appelle `weather.get_forecasts` type daily (?return_response).

    POST sur la MÊME session aiohttp partagée et avec les MÊMES identifiants
    que `read_ha_state`. None si la lecture échoue (HA muet, service en erreur,
    entité absente).
    """
    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        return None
    url = f"{ha_url.rstrip('/')}/api/services/weather/get_forecasts?return_response"
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload = {"entity_id": _WEATHER_ENTITY, "type": "daily"}
    try:
        session = _get_ha_session()
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("[HA WEATHER] get_forecasts skip : %s", exc)
        return None

    service_response = (data or {}).get("service_response")
    if not isinstance(service_response, dict):
        return None
    entity_data = service_response.get(_WEATHER_ENTITY)
    if not isinstance(entity_data, dict):
        return None
    forecast = entity_data.get("forecast")
    if not isinstance(forecast, list):
        return None
    return list(forecast)


async def _forecast_for_date(target: date) -> dict[str, Any] | None:
    """Prévision journalière correspondant à `target`, ou None si absente."""
    forecast = await _get_daily_forecast()
    if not forecast:
        return None
    for entry in forecast:
        entry_date = _parse_forecast_date(entry.get("datetime"))
        if entry_date == target:
            return dict(entry)
    return None


async def _resolve_forecast_day(
    prompt: str,
    label: str,
    day_offset: int,
) -> str | None:
    """Prévision du jour `today + day_offset` (ex. demain). None si absente."""
    target = date.today() + timedelta(days=day_offset)
    entry = await _forecast_for_date(target)
    if entry is None:
        logger.info(
            "[HA WEATHER] Pas de prévision pour %s (%s) — cascade continue",
            target, prompt[:60],
        )
        return None
    return _format_forecast_day(entry, label)


async def _resolve_weekend(prompt: str) -> str | None:
    """Prévision du prochain week-end (samedi + dimanche), ou None si absente."""
    today = date.today()
    # Jours jusqu'au prochain samedi (weekday() : lundi=0 … samedi=5, dimanche=6).
    days_until_saturday = (5 - today.weekday()) % 7
    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)
    sat_entry = await _forecast_for_date(saturday)
    sun_entry = await _forecast_for_date(sunday)
    if sat_entry is None and sun_entry is None:
        logger.info(
            "[HA WEATHER] Pas de prévision week-end (%s) — cascade continue",
            prompt[:60],
        )
        return None
    parts = []
    if sat_entry is not None:
        parts.append(_format_forecast_day(sat_entry, "Samedi"))
    if sun_entry is not None:
        parts.append(_format_forecast_day(sun_entry, "Dimanche"))
    return " ".join(parts)


async def resolve_weather_query(prompt: str) -> str | None:
    """Résout une demande météo en phrase TTS courte, ou None si non résolue.

    None si ce n'est pas une demande météo, si la lecture HA échoue, ou si la
    prévision demandée est absente : la cascade repart alors vers le spécialiste
    web comme avant. Aucune valeur de repli n'est fabriquée.
    """
    query = match_weather_query(prompt)
    if not query:
        return None

    if query.horizon in (WeatherHorizon.NOW, WeatherHorizon.TODAY):
        state = await read_ha_state(_WEATHER_ENTITY)
        if state is None:
            logger.info("[HA WEATHER] Lecture %s indisponible — cascade continue", _WEATHER_ENTITY)
            return None
        return _format_now(state) or None

    if query.horizon == WeatherHorizon.TOMORROW:
        return await _resolve_forecast_day(prompt, "Demain", 1)

    if query.horizon == WeatherHorizon.WEEKEND:
        return await _resolve_weekend(prompt)

    return None
