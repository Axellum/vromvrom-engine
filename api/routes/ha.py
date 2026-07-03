"""
api/routes/ha.py — Routes API Home Assistant du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient les routes /api/ha/* pour la lecture et le contrôle d'entités HA.

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import os
import logging
from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée
from core.validation import (  # [P0-1.6] validation des identifiants HA
    is_valid_ha_entity_id, is_valid_ha_domain, is_valid_ha_service_name,
    validate_service_data,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Home Assistant"])


# ──────────────────────────────────────────────────────────────────
# Modèles Pydantic
# ──────────────────────────────────────────────────────────────────

class HAControlBody(BaseModel):
    """Corps de la requête pour contrôler une entité HA."""
    entity_id: str
    service: str                          # Ex: "turn_on", "turn_off", "set_temperature"
    domain: Optional[str] = None          # Déduit de entity_id si non fourni
    service_data: Optional[Dict[str, Any]] = None


class HACommandBody(BaseModel):
    """Corps de la requête pour les commandes HA via pipeline domotique."""
    prompt: str
    session_id: Optional[str] = None

def _get_ha_credentials():
    """Retourne (ha_url, ha_token) depuis les variables d'environnement."""
    ha_token = os.environ.get("HASS_TOKEN")
    ha_url = os.environ.get("HASS_URL", "http://${HA_HOST:-192.168.1.x}:8123")
    if not ha_token:
        raise HTTPException(status_code=500, detail="HASS_TOKEN non configuré dans l'environnement.")
    return ha_url, ha_token


# ──────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────

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
