"""
core/vocal_tools.py — Mini allowlist d'outils pour Discussion vocal (Cerebras).

Pas le catalogue MCP entier : 6 outils TTS-friendly, max 2 tours tool-calling.
HA écriture bornée (domaines/services whitelist). Lecture calendrier + web + mémoire.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 2

# Conservé pour ha_call_service (tests / éventuel usage interne) — non exposé au LLM Discussion.
_HA_ALLOWED_SERVICES: dict[str, frozenset[str]] = {
    "light": frozenset({"turn_on", "turn_off", "toggle"}),
    "switch": frozenset({"turn_on", "turn_off", "toggle"}),
    "climate": frozenset({
        "set_temperature", "set_hvac_mode", "turn_on", "turn_off", "set_preset_mode",
    }),
    "cover": frozenset({
        "open_cover", "close_cover", "stop_cover", "set_cover_position",
    }),
    "media_player": frozenset({
        "turn_on", "turn_off", "media_play", "media_pause", "media_stop",
        "volume_set", "volume_up", "volume_down", "volume_mute",
    }),
    "fan": frozenset({"turn_on", "turn_off", "toggle", "set_percentage"}),
    "scene": frozenset({"turn_on"}),
    "input_boolean": frozenset({"turn_on", "turn_off", "toggle"}),
}

VOCAL_TOOL_SYSTEM_SUFFIX = (
    "\n\n[OUTILS LECTURE] Tu as UNIQUEMENT : ha_list, ha_get_state, memory_search. "
    "Pour une température/état : appelle ha_list puis ha_get_state. "
    "INTERDIT d'inventer une valeur, une entité ou un service. "
    "Si l'outil échoue ou renvoie unavailable, dis-le clairement. "
    "Réponse finale : 1 phrase TTS, français, sans markdown, sans JSON."
)

# Lecture seule — les commandes passent par Zero-LLM (vocal_host).
VOCAL_TOOLS_OPENAI: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ha_list",
            "description": (
                "Cherche des entités Home Assistant par nom (ex: salon, chambre, clim)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Mot-clé friendly name ou entity_id.",
                    },
                    "domain": {
                        "type": "string",
                        "description": "Filtre domaine optionnel (sensor, climate, light…).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_get_state",
            "description": "Lit l'état et attributs d'une entité HA (entity_id complet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Ex: sensor.xxx_temperature, climate.salon…",
                    },
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Cherche un fait / leçon dans la mémoire projet (lecture seule).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]


def _ha_credentials() -> tuple[str, str]:
    ha_url = os.environ.get("HA_URL") or os.environ.get("HASS_URL") or "http://${HA_HOST:-192.168.1.x}:8123"
    ha_token = os.environ.get("HA_TOKEN") or os.environ.get("HASS_TOKEN") or ""
    return ha_url.rstrip("/"), ha_token


def _ha_requests_verify() -> bool | str:
    """Aligné sur core.ha_tls : False si HA_VERIFY_TLS=false, sinon CA bundle ou True."""
    from core.ha_tls import ha_tls_verification_enabled

    if not ha_tls_verification_enabled():
        return False
    ca_bundle = os.environ.get("HA_CA_BUNDLE", "").strip()
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle
    return True


def _ha_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _tool_ha_list(query: str, domain: str = "") -> str:
    import requests

    ha_url, ha_token = _ha_credentials()
    if not ha_token:
        return "Erreur: HASS_TOKEN absent."
    try:
        resp = requests.get(
            f"{ha_url}/api/states",
            headers=_ha_headers(ha_token),
            timeout=10,
            verify=_ha_requests_verify(),
        )
    except requests.RequestException as exc:
        return f"Erreur HA réseau: {exc}"
    if resp.status_code != 200:
        return f"Erreur HA HTTP {resp.status_code}"

    entities = resp.json()
    if domain:
        prefix = f"{domain.strip().lower()}."
        entities = [e for e in entities if str(e.get("entity_id", "")).startswith(prefix)]

    q = (query or "").strip().lower()
    matched = [
        e for e in entities
        if q in str(e.get("entity_id", "")).lower()
        or q in str(e.get("attributes", {}).get("friendly_name", "")).lower()
    ][:20]

    if not matched:
        return f"Aucune entité pour '{query}'."

    lines = []
    for e in matched:
        eid = e.get("entity_id", "?")
        name = e.get("attributes", {}).get("friendly_name", "")
        state = e.get("state", "?")
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        lines.append(f"{eid} | {name} = {state}{(' ' + unit) if unit else ''}")
    return "\n".join(lines)


def _tool_ha_get_state(entity_id: str) -> str:
    import requests
    from core.validation import is_valid_ha_entity_id

    if not is_valid_ha_entity_id(entity_id or ""):
        return f"entity_id invalide: {entity_id!r}"

    ha_url, ha_token = _ha_credentials()
    if not ha_token:
        return "Erreur: HASS_TOKEN absent."
    try:
        resp = requests.get(
            f"{ha_url}/api/states/{entity_id}",
            headers=_ha_headers(ha_token),
            timeout=10,
            verify=_ha_requests_verify(),
        )
    except requests.RequestException as exc:
        return f"Erreur HA réseau: {exc}"
    if resp.status_code == 404:
        return f"Entité introuvable: {entity_id}"
    if resp.status_code != 200:
        return f"Erreur HA HTTP {resp.status_code}"

    data = resp.json()
    attrs = data.get("attributes") or {}
    keep = {
        k: attrs[k]
        for k in (
            "friendly_name", "brightness", "color_temp", "temperature",
            "current_temperature", "hvac_mode", "hvac_action", "percentage",
            "volume_level", "media_title", "unit_of_measurement",
        )
        if k in attrs
    }
    return json.dumps(
        {"entity_id": data.get("entity_id"), "state": data.get("state"), "attributes": keep},
        ensure_ascii=False,
    )


def _tool_ha_call_service(
    entity_id: str,
    service: str,
    service_data: dict | None = None,
) -> str:
    import requests
    from core.validation import (
        is_valid_ha_domain,
        is_valid_ha_entity_id,
        is_valid_ha_service_name,
        validate_service_data,
    )

    if not is_valid_ha_entity_id(entity_id or ""):
        return f"Refusé: entity_id invalide ({entity_id!r})."

    if "." in (service or ""):
        domain, svc_name = service.split(".", 1)
    else:
        domain = entity_id.split(".", 1)[0]
        svc_name = service or ""

    if not is_valid_ha_domain(domain) or not is_valid_ha_service_name(svc_name):
        return f"Refusé: service invalide ({service!r})."

    allowed = _HA_ALLOWED_SERVICES.get(domain)
    if not allowed or svc_name not in allowed:
        return (
            f"Refusé: `{domain}.{svc_name}` hors allowlist vocale. "
            f"Domaines OK: {', '.join(sorted(_HA_ALLOWED_SERVICES))}."
        )

    svc_data: dict[str, Any] = dict(service_data or {})
    try:
        validate_service_data(svc_data)
    except ValueError as exc:
        return f"Refusé service_data: {exc}"

    if "entity_id" not in svc_data:
        svc_data["entity_id"] = entity_id

    ha_url, ha_token = _ha_credentials()
    if not ha_token:
        return "Erreur: HASS_TOKEN absent."

    try:
        resp = requests.post(
            f"{ha_url}/api/services/{domain}/{svc_name}",
            headers=_ha_headers(ha_token),
            json=svc_data,
            timeout=10,
            verify=_ha_requests_verify(),
        )
    except requests.RequestException as exc:
        return f"Erreur HA réseau: {exc}"

    if resp.status_code not in (200, 201):
        return f"Échec HA HTTP {resp.status_code}: {resp.text[:200]}"
    return f"OK: {domain}.{svc_name} sur {entity_id}"


def _tool_calendar_today(max_results: int = 8) -> str:
    from tools.google_workspace import get_calendar_events

    try:
        max_r = int(max_results)
    except (TypeError, ValueError):
        max_r = 8
    max_r = max(1, min(max_r, 15))
    return get_calendar_events("primary", str(max_r))


async def _tool_web_search(query: str, *, session_id: str) -> str:
    from core.vocal_jobs import _call_gemini_search_grounding

    text = await _call_gemini_search_grounding(query, session_id=session_id)
    if text:
        return text
    return (
        "Recherche web indisponible (clé Gemini payante absente ou erreur). "
        "Dis-le à l'utilisateur sans inventer de faits."
    )


def _tool_memory_search(query: str) -> str:
    try:
        from memory.memory_db import MemoryDB

        db = MemoryDB()
        rows = db.search_facts(query, limit=5) or []
        if not rows:
            return "Aucun fait mémoire trouvé."
        lines = []
        for row in rows[:5]:
            if isinstance(row, dict):
                content = row.get("content") or row.get("fact") or str(row)
            else:
                content = str(row)
            lines.append(f"- {str(content)[:240]}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("[VOCAL_TOOLS] memory_search: %s", exc)
        return f"Mémoire indisponible: {exc}"


async def dispatch_vocal_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session_id: str,
) -> str:
    """Exécute un outil allowlist et renvoie un texte court pour le LLM."""
    args = arguments or {}
    try:
        if name == "ha_list":
            return await asyncio.to_thread(
                _tool_ha_list,
                str(args.get("query", "")),
                str(args.get("domain", "") or ""),
            )
        if name == "ha_get_state":
            return await asyncio.to_thread(_tool_ha_get_state, str(args.get("entity_id", "")))
        if name == "ha_call_service":
            raw_data = args.get("service_data") or {}
            if isinstance(raw_data, str):
                try:
                    raw_data = json.loads(raw_data) if raw_data.strip() else {}
                except json.JSONDecodeError:
                    return "service_data JSON invalide"
            return await asyncio.to_thread(
                _tool_ha_call_service,
                str(args.get("entity_id", "")),
                str(args.get("service", "")),
                raw_data if isinstance(raw_data, dict) else {},
            )
        if name == "calendar_today":
            return await asyncio.to_thread(
                _tool_calendar_today,
                args.get("max_results", 8),
            )
        if name == "web_search":
            return await _tool_web_search(str(args.get("query", "")), session_id=session_id)
        if name == "memory_search":
            return await asyncio.to_thread(_tool_memory_search, str(args.get("query", "")))
        return f"Outil inconnu ou non autorisé: {name}"
    except Exception as exc:
        logger.warning("[VOCAL_TOOLS] %s échec: %s", name, exc)
        return f"Erreur outil {name}: {exc}"


def _unwrap_provider(provider: Any) -> Any:
    """Déroule FallbackProvider / ClaudeInstructionsWrapper jusqu'au provider brut."""
    seen: set[int] = set()
    cur = provider
    while id(cur) not in seen:
        seen.add(id(cur))
        cls = type(cur).__name__
        if cls == "FallbackProvider":
            inners = getattr(cur, "providers", None) or []
            if not inners:
                return cur
            cur = inners[0][1]
            continue
        if cls == "ClaudeInstructionsWrapper":
            inner = getattr(cur, "provider", None)
            if inner is None:
                return cur
            cur = inner
            continue
        return cur
    return cur


def provider_supports_openai_tools(provider: Any) -> bool:
    """True si le provider (après unwrap) accepte tools= OpenAI-compat."""
    raw = _unwrap_provider(provider)
    cls = type(raw).__name__
    return "OpenAICompatible" in cls


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw) if raw.strip() else {}
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _assistant_message_for_history(result: dict[str, Any]) -> dict[str, Any]:
    """Ne garde que les champs OpenAI-compat (évite `reasoning` Cerebras rejeté)."""
    msg: dict[str, Any] = {"role": "assistant"}
    content = result.get("content")
    if content:
        msg["content"] = content
    tool_calls = result.get("tool_calls")
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


async def run_vocal_tool_loop(
    provider: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    session_id: str,
    temperature: float = 0.0,
) -> str | None:
    """
    Boucle tool-calling (max MAX_TOOL_ROUNDS) puis réponse texte.
    Retourne None si le provider ne gère pas les outils.
    """
    if not provider_supports_openai_tools(provider):
        return None

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt + VOCAL_TOOL_SYSTEM_SUFFIX},
        {"role": "user", "content": user_prompt},
    ]

    for round_idx in range(MAX_TOOL_ROUNDS + 1):
        result = await asyncio.to_thread(
            lambda msgs=list(messages): provider.generate(
                "",
                "",
                messages=msgs,
                tools=VOCAL_TOOLS_OPENAI,
                temperature=temperature,
                max_tokens=256,
                session_id=session_id,
            )
        )

        if isinstance(result, str):
            return result.strip() or None

        if not isinstance(result, dict):
            return str(result).strip() or None

        tool_calls = result.get("tool_calls") or []
        content = (result.get("content") or "").strip()

        if not tool_calls:
            return content or None

        if round_idx >= MAX_TOOL_ROUNDS:
            logger.info("[VOCAL_TOOLS] plafond tours atteint — force réponse texte")
            messages.append(_assistant_message_for_history(result))
            messages.append({
                "role": "user",
                "content": (
                    "Tu as assez d'infos. Réponds maintenant en 1-3 phrases TTS, "
                    "sans appeler d'outil."
                ),
            })
            final = await asyncio.to_thread(
                lambda: provider.generate(
                    "",
                    "",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=256,
                    session_id=session_id,
                )
            )
            if isinstance(final, dict):
                return (final.get("content") or "").strip() or None
            return str(final).strip() or None

        messages.append(_assistant_message_for_history(result))

        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = _parse_tool_args(fn.get("arguments"))
            tc_id = tc.get("id") or f"call_{name}_{round_idx}"
            logger.info("[VOCAL_TOOLS] round=%s → %s(%s)", round_idx, name, list(args.keys()))
            tool_result = await dispatch_vocal_tool(name, args, session_id=session_id)
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_result[:4000],
            })

    return None
