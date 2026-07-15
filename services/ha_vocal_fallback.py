"""
ha_vocal_fallback.py — Repli LLM léger quand ha_commands + fuzzy échouent (vocal Tab5).

Objectif : ~3–8 s max, 1 appel tier léger, JSON strict → execute_ha_service.
Ne lance PAS le pipeline Planner/DAG complet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_HA_COMMANDS_PATH = Path(__file__).resolve().parents[1] / "ha_commands.json"
_LLM_TIMEOUT_S = 8.0

_SYSTEM = """Tu es un extracteur d'intentions domotiques Home Assistant.
Entrée : phrase utilisateur (transcription vocale imparfaite, en français).
Sortie : UNIQUEMENT un objet JSON valide sur une ligne, sans markdown :
{"service":"light.turn_on","entity_id":"light.salon"}
ou {"service":null,"entity_id":null} si la commande est incompréhensible.

Règles :
- Choisis UNIQUEMENT une entité de la liste ci-dessous.
- service = domaine.action HA (ex. light.turn_on, light.turn_off, cover.open_cover).
- Interprète les fautes STT (« est-elle » = éteindre, « al lumière » = la lumière).
- Si la pièce est « salon » et action allumer → light.salon + light.turn_on.
- Si la pièce est « chambre » et action éteindre → light.h6008_2 + light.turn_off.
"""


@dataclass(frozen=True)
class LLMIntentResult:
    service: str
    entity_id: str


def _known_entities_block() -> str:
    seen: dict[str, set[str]] = {}
    if _HA_COMMANDS_PATH.is_file():
        data = json.loads(_HA_COMMANDS_PATH.read_text(encoding="utf-8"))
        for cmd in data.get("commands", []):
            eid = cmd.get("entity_id", "")
            svc = cmd.get("service", "")
            if eid and svc:
                seen.setdefault(eid, set()).add(svc)
    lines = ["Entités autorisées :"]
    for eid, services in sorted(seen.items()):
        lines.append(f"- {eid} : {', '.join(sorted(services))}")
    return "\n".join(lines)


def _parse_llm_json(raw: str) -> LLMIntentResult | None:
    if not raw:
        return None
    text = raw.strip()
    # Extraire le premier bloc JSON
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    service = obj.get("service")
    entity_id = obj.get("entity_id")
    if not service or not entity_id:
        return None
    if not isinstance(service, str) or not isinstance(entity_id, str):
        return None
    if "." not in service or "." not in entity_id:
        return None
    return LLMIntentResult(service=service, entity_id=entity_id)


async def resolve_ha_via_llm(user_prompt: str, session_id: str) -> LLMIntentResult | None:
    """
    Un seul appel LLM tier léger. Retourne None si timeout/échec/incompris.
    """
    from core.llm_gateway import LLMGateway, load_config

    gateway = LLMGateway()
    config = load_config()
    system = f"{_SYSTEM}\n\n{_known_entities_block()}"
    user = f"Phrase utilisateur : {user_prompt}"

    try:
        _, provider = gateway.get_provider_for_tier("leger", config)
    except Exception as exc:
        logger.warning("[HA LLM FALLBACK] Tier leger indisponible : %s", exc)
        return None

    loop = asyncio.get_event_loop()
    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: provider.generate(system, user, session_id=session_id),
            ),
            timeout=_LLM_TIMEOUT_S,
        )
    except TimeoutError:
        logger.warning("[HA LLM FALLBACK] Timeout %.0fs", _LLM_TIMEOUT_S)
        return None
    except Exception as exc:
        logger.warning("[HA LLM FALLBACK] Erreur provider : %s", exc)
        return None

    parsed = _parse_llm_json(str(raw))
    if parsed:
        logger.info(
            "[HA LLM FALLBACK] Intent → %s(%s) depuis %r",
            parsed.service, parsed.entity_id, user_prompt[:60],
        )
    return parsed
