"""
services/ha_climate_control.py — Réglages clim RELATIFS vocaux (mode:ha).

« monte la clim de 2 degrés », « un peu plus chaud », « baisse la climatisation »
→ lecture de la consigne actuelle + delta, en Zero-LLM. Complète
match_ha_climate_command (execute_service) qui, lui, ne gère que l'ABSOLU
(« mets la clim à 22 ») et le mode.

Doit être tenté AVANT le court-circuit d'action : « monte la clim » serait sinon
pris pour un allumage (« monte » = marqueur ON). Anti-hallucination : sans
consigne actuelle lisible, on le dit au lieu d'inventer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from services.execute_service import (
    _CLIMATE_KEYWORDS,
    execute_ha_service,
    normalize_ha_command_prompt,
    parse_climate_temperature,
    read_ha_state,
    resolve_climate_entity,
)

logger = logging.getLogger(__name__)

_MIN_TEMP, _MAX_TEMP = 15, 31

_UP_WORDS = frozenset({"augmente", "augmenter", "monte", "monter", "rehausse", "rechauffe"})
_DOWN_WORDS = frozenset({"baisse", "baisser", "diminue", "diminuer", "descend", "descends"})
_DELTA_RE = re.compile(r"\bde (\d{1,2})\b")


@dataclass(frozen=True)
class ClimateRelative:
    """Réglage relatif reconnu : delta signé (°C)."""
    delta: int  # positif = plus chaud, négatif = plus froid


def match_climate_relative(prompt: str) -> ClimateRelative | None:
    """Détecte « plus chaud / moins chaud / monte/baisse de N ». None sinon."""
    norm = normalize_ha_command_prompt(prompt)
    if not norm:
        return None
    words = set(norm.split())
    if not (words & _CLIMATE_KEYWORDS):
        return None
    # Une température absolue (15-31) → ce n'est pas du relatif : laisser l'absolu gérer.
    if parse_climate_temperature(norm) is not None:
        return None

    up = bool(words & _UP_WORDS) or ("plus" in words and "chaud" in norm)
    down = (
        bool(words & _DOWN_WORDS)
        or ("plus" in words and ("froid" in norm or "frais" in norm))
        or ("moins" in words and "chaud" in norm)
    )
    if up == down:  # ni l'un ni l'autre, ou ambigu (les deux)
        return None

    amount_match = _DELTA_RE.search(norm)
    amount = int(amount_match.group(1)) if amount_match else 1
    amount = max(1, min(amount, 8))  # garde-fou
    return ClimateRelative(delta=amount if up else -amount)


async def resolve_climate_relative(prompt: str) -> str | None:
    """
    Applique un réglage relatif et renvoie la phrase TTS, ou None si la phrase
    n'est pas un réglage relatif (l'appelant poursuit la cascade).
    """
    rel = match_climate_relative(prompt)
    if not rel:
        return None

    norm = normalize_ha_command_prompt(prompt)
    entity = resolve_climate_entity(norm)
    state = await read_ha_state(entity)
    current = None
    if state:
        try:
            current = float(state.get("attributes", {}).get("temperature"))
        except (TypeError, ValueError):
            current = None
    if current is None:
        logger.info("[HA CLIM] Relatif '%s' : consigne actuelle illisible", prompt[:60])
        return "Je n'ai pas la consigne actuelle de la climatisation."

    target = max(_MIN_TEMP, min(round(current) + rel.delta, _MAX_TEMP))
    ok, text = await execute_ha_service(
        "climate.set_temperature", entity, service_data={"temperature": target},
    )
    if ok:
        return text
    return "Je n'ai pas pu régler la climatisation."
