"""
api/routes/ha.py — Routes API Home Assistant du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient les routes /api/ha/* pour la lecture et le contrôle d'entités HA.

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.ha_tls import ha_ssl_context, ha_tls_pinned_hostname  # [P0-1.5] politique TLS HA centralisée
from core.ha_token import get_ha_token  # [T239] lecture centralisée du token HA
from core.validation import (  # [P0-1.6] validation des identifiants HA
    is_valid_ha_domain,
    is_valid_ha_entity_id,
    is_valid_ha_service_name,
    validate_service_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Home Assistant"])


# ──────────────────────────────────────────────────────────────────
# Modèles Pydantic
# ──────────────────────────────────────────────────────────────────

class HAControlBody(BaseModel):
    """Corps de la requête pour contrôler une entité HA."""
    entity_id: str
    service: str                          # Ex: "turn_on", "turn_off", "set_temperature"
    domain: str | None = None          # Déduit de entity_id si non fourni
    service_data: dict[str, Any] | None = None


class HACommandBody(BaseModel):
    """Corps de la requête pour les commandes HA via pipeline domotique."""
    prompt: str
    session_id: str | None = None

def _get_ha_credentials():
    """
    Retourne (ha_url, ha_token) depuis les variables d'environnement.

    Le token passe par l'accesseur centralisé `core.ha_token.get_ha_token()`
    (HASS_TOKEN prioritaire, repli HA_TOKEN — T239).
    """
    ha_token = get_ha_token()
    ha_url = os.environ.get("HASS_URL") or os.environ.get("HA_URL") or "http://${HA_HOST:-192.168.1.x}:8123"
    if not ha_token:
        raise HTTPException(status_code=500, detail="Token Home Assistant non configuré (HASS_TOKEN/HA_TOKEN).")
    return ha_url, ha_token


# ──────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────

@router.get("/api/ha/health")
async def ha_health():
    """
    Diagnostic réel de la liaison Home Assistant (vue « Domotique » de l'IHM).

    Ne renvoie JAMAIS un état « connecté » supposé : la joignabilité est établie
    par un appel effectif à `GET /api/` de Home Assistant, chronométré. En cas
    d'échec, la cause exacte est remontée telle quelle.
    """
    import time

    ha_token = get_ha_token()
    ha_url = os.environ.get("HASS_URL") or os.environ.get("HA_URL") or "http://${HA_HOST:-192.168.1.x}:8123"
    verify_tls = os.environ.get("HA_VERIFY_TLS", "true").lower() not in ("false", "0", "no")

    result: dict[str, Any] = {
        "url": ha_url,
        "token_configured": bool(ha_token),
        "verify_tls": verify_tls,
        "ca_bundle": os.environ.get("HA_CA_BUNDLE") or None,
        "tls_server_hostname": ha_tls_pinned_hostname() or None,
        "reachable": False,
        "latency_ms": None,
        "version": None,
        "entity_count": None,
        "domains": {},
        "error": None,
    }

    if not ha_token:
        result["error"] = "Token Home Assistant absent du .env du moteur (HASS_TOKEN/HA_TOKEN) — aucune requête tentée."
        return result

    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}

    import aiohttp
    try:
        started = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            # 1. Joignabilité + version.
            async with session.get(
                f"{ha_url}/api/",
                headers=headers,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                if resp.status == 401:
                    result["error"] = "Token refusé par Home Assistant (401)."
                    return result
                if resp.status != 200:
                    result["error"] = f"Home Assistant a répondu {resp.status}."
                    return result
                result["reachable"] = True

            # 2. Inventaire des entités, pour situer le périmètre exposé au moteur.
            async with session.get(
                f"{ha_url}/api/states",
                headers=headers,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    states = await resp.json()
                    result["entity_count"] = len(states)
                    domains: dict[str, int] = {}
                    for entity in states:
                        domain = entity.get("entity_id", "").split(".", 1)[0]
                        if domain:
                            domains[domain] = domains.get(domain, 0) + 1
                    result["domains"] = dict(sorted(domains.items(), key=lambda kv: -kv[1]))

        # 3. Version : exposée par l'entité de configuration si disponible.
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ha_url}/api/config",
                headers=headers,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                if resp.status == 200:
                    cfg = await resp.json()
                    result["version"] = cfg.get("version")
                    result["location_name"] = cfg.get("location_name")

        return result

    except aiohttp.ClientConnectorError as e:
        result["error"] = f"Connexion impossible : {e}"
        return result
    except TimeoutError:
        result["error"] = "Délai dépassé — Home Assistant n'a pas répondu à temps."
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


@router.get("/api/ha/entities")
async def ha_entities(
    search: str = "",
    domain: str = "",
    limit: int = 200,
):
    """
    Liste les entités Home Assistant, filtrables par domaine et par texte.

    Sert l'explorateur d'entités de l'IHM : c'est le périmètre exact sur lequel
    le moteur et l'assistant vocal peuvent agir.
    """
    ha_url, ha_token = _get_ha_credentials()
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    safe_limit = max(1, min(limit, 1000))

    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ha_url}/api/states",
                headers=headers,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"Erreur Home Assistant : {resp.status}")
                states = await resp.json()
    except aiohttp.ClientConnectorError as e:
        raise HTTPException(status_code=503, detail=f"Impossible de se connecter à Home Assistant : {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    needle = search.strip().lower()
    matched = []
    for entity in states:
        entity_id = entity.get("entity_id", "")
        if domain and not entity_id.startswith(f"{domain}."):
            continue
        friendly = entity.get("attributes", {}).get("friendly_name", "") or ""
        if needle and needle not in entity_id.lower() and needle not in friendly.lower():
            continue
        matched.append({
            "entity_id": entity_id,
            "friendly_name": friendly,
            "state": entity.get("state"),
            "unit": entity.get("attributes", {}).get("unit_of_measurement"),
            "device_class": entity.get("attributes", {}).get("device_class"),
            "last_changed": entity.get("last_changed"),
        })

    matched.sort(key=lambda e: e["entity_id"])
    return {
        "total_matched": len(matched),
        "returned": min(len(matched), safe_limit),
        "entities": matched[:safe_limit],
    }


@router.get("/api/ha/state/{entity_id}")
async def get_ha_state(entity_id: str):
    """Récupère l'état d'une entité de Home Assistant."""
    if not is_valid_ha_entity_id(entity_id):
        raise HTTPException(status_code=400, detail=f"entity_id invalide : {entity_id!r}")
    ha_url, ha_token = _get_ha_credentials()
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ha_url}/api/states/{entity_id}",
                headers=headers,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    raise HTTPException(status_code=404, detail=f"Entité '{entity_id}' introuvable dans Home Assistant.")
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"Erreur Home Assistant: {resp.status}")
                return await resp.json()
    except aiohttp.ClientConnectorError as e:
        raise HTTPException(status_code=503, detail=f"Impossible de se connecter à Home Assistant: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/ha/control")
async def control_ha_entity(body: HAControlBody):
    """Contrôle une entité Home Assistant en appelant un service HA."""
    ha_url, ha_token = _get_ha_credentials()

    # Déduction du domaine si non fourni
    domain = body.domain
    if not domain and "." in body.entity_id:
        domain = body.entity_id.split(".", 1)[0]
    if not domain:
        raise HTTPException(status_code=400, detail="Impossible de déterminer le domaine de l'entité (ex: light, switch).")

    # [P0-1.6] Valider les identifiants avant injection dans l'URL de l'API HA.
    if not is_valid_ha_entity_id(body.entity_id):
        raise HTTPException(status_code=400, detail=f"entity_id invalide : {body.entity_id!r}")
    if not is_valid_ha_domain(domain):
        raise HTTPException(status_code=400, detail=f"domaine HA invalide : {domain!r}")
    if not is_valid_ha_service_name(body.service):
        raise HTTPException(status_code=400, detail=f"service HA invalide : {body.service!r}")

    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload = {"entity_id": body.entity_id}
    if body.service_data:
        try:
            validate_service_data(body.service_data)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        payload.update(body.service_data)

    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ha_url}/api/services/{domain}/{body.service}",
                headers=headers,
                json=payload,
                ssl=ha_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    detail_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"Erreur HA: {detail_text}")
                return await resp.json()
    except aiohttp.ClientConnectorError as e:
        raise HTTPException(status_code=503, detail=f"Impossible de se connecter à Home Assistant: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
