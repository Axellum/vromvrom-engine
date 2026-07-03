"""
api/routes/models_admin.py — Routeur FastAPI pour l'administration de models_registry.db.

Permet à l'HMI de modifier à chaud les données de tarification, quotas, abonnements
et de configuration des modèles directement en base de données.
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.models_db import (
    upsert_model,
    upsert_provider,
    upsert_api_key,
    upsert_subscription,
    get_model,
    get_provider,
    get_all_api_keys,
    get_subscriptions
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Administration Modèles"])


# ──────────────────────────────────────────────────────────────────
# Modèles de validation Pydantic
# ──────────────────────────────────────────────────────────────────

class ModelUpdateBody(BaseModel):
    id: str
    provider_id: str
    display_name: Optional[str] = None
    status: Optional[str] = "active"
    tier: Optional[str] = None
    context_input: Optional[int] = None
    context_output: Optional[int] = None
    cost_input_per_m: Optional[float] = None
    cost_output_per_m: Optional[float] = None
    cost_cached_per_m: Optional[float] = None
    currency: Optional[str] = "USD"
    supports_thinking: Optional[int] = 0
    supports_tools: Optional[int] = 0
    supports_vision: Optional[int] = 0
    supports_audio: Optional[int] = 0
    supports_json_mode: Optional[int] = 0
    supports_streaming: Optional[int] = 0
    supports_search_grounding: Optional[int] = 0
    speciality: Optional[str] = None
    recommended_use: Optional[str] = None
    notes: Optional[str] = None


class ProviderUpdateBody(BaseModel):
    id: str
    name: str
    type: Optional[str] = "unknown"
    api_endpoint: Optional[str] = None
    auth_method: Optional[str] = None
    confidentiality: Optional[str] = "non confidentiel"
    cascade_priority: Optional[float] = 5.0
    notes: Optional[str] = None


class ApiKeyUpdateBody(BaseModel):
    id: str
    provider_id: str
    env_var: str
    project_name: Optional[str] = None
    key_type: Optional[str] = "free"
    quota_rpm: Optional[int] = None
    quota_rpd: Optional[int] = None
    quota_tpm: Optional[int] = None
    status: Optional[str] = "active"


class SubscriptionUpdateBody(BaseModel):
    id: str
    name: str
    cost_monthly_usd: Optional[float] = 0.0
    rolling_window_hours: Optional[int] = 24
    hourly_token_limit: Optional[int] = None
    monthly_token_limit: Optional[int] = None
    estimated_messages_limit: Optional[int] = None
    advantages: Optional[str] = None
    recommended_use: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Routes d'administration
# ──────────────────────────────────────────────────────────────────

@router.post("/api/models/update")
def route_update_model(body: ModelUpdateBody):
    """Met à jour ou crée un modèle dans models_registry.db."""
    try:
        success = upsert_model(body.id, **body.dict())
        if not success:
            raise HTTPException(status_code=500, detail="Échec de l'écriture en base de données.")
        
        # Mettre à jour l'export passif pricing_strategy.json (rétrocompatibilité)
        try:
            from core.models_db import export_to_pricing_json
            export_to_pricing_json()
        except Exception as e:
            logger.warning(f"Impossible d'exporter pricing_strategy.json : {e}")

        return {"status": "ok", "message": f"Modèle '{body.id}' mis à jour avec succès.", "model": get_model(body.id)}
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour du modèle {body.id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/providers/update")
def route_update_provider(body: ProviderUpdateBody):
    """Met à jour ou crée un provider dans models_registry.db."""
    try:
        success = upsert_provider(body.id, **body.dict())
        if not success:
            raise HTTPException(status_code=500, detail="Échec de l'écriture en base de données.")
        return {"status": "ok", "message": f"Provider '{body.id}' mis à jour avec succès.", "provider": get_provider(body.id)}
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour du provider {body.id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/keys/update")
def route_update_key(body: ApiKeyUpdateBody):
    """Met à jour ou crée une clé API dans models_registry.db."""
    try:
        success = upsert_api_key(body.id, **body.dict())
        if not success:
            raise HTTPException(status_code=500, detail="Échec de l'écriture en base de données.")
        
        # Rafraîchir les quotas temps réel pour cette clé
        try:
            from core.quota_collector import refresh_all_quotas
            refresh_all_quotas(include_claude=False, force_claude=False)
        except Exception as q_err:
            logger.warning(f"Impossible de rafraîchir les quotas immédiatement : {q_err}")

        return {"status": "ok", "message": f"Clé API '{body.id}' mise à jour.", "keys": get_all_api_keys(hide_values=True)}
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de la clé {body.id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/subscriptions/update")
def route_update_subscription(body: SubscriptionUpdateBody):
    """Met à jour ou crée un abonnement dans models_registry.db."""
    try:
        success = upsert_subscription(body.id, **body.dict())
        if not success:
            raise HTTPException(status_code=500, detail="Échec de l'écriture en base de données.")
        
        # Mettre à jour l'export passif pricing_strategy.json (rétrocompatibilité)
        try:
            from core.models_db import export_to_pricing_json
            export_to_pricing_json()
        except Exception as e:
            logger.warning(f"Impossible d'exporter pricing_strategy.json : {e}")

        return {"status": "ok", "message": f"Abonnement '{body.id}' mis à jour.", "subscriptions": get_subscriptions()}
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de l'abonnement {body.id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))
