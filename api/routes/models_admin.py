"""
api/routes/models_admin.py — Routeur FastAPI pour l'administration de models_registry.db.

Permet à l'HMI de modifier à chaud les données de tarification, quotas, abonnements
et de configuration des modèles directement en base de données.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.models_db import (
    get_all_api_keys,
    get_model,
    get_provider,
    get_subscriptions,
    upsert_api_key,
    upsert_model,
    upsert_provider,
    upsert_subscription,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Administration Modèles"])


# ──────────────────────────────────────────────────────────────────
# Modèles de validation Pydantic
# ──────────────────────────────────────────────────────────────────

class ModelUpdateBody(BaseModel):
    id: str
    provider_id: str
    display_name: str | None = None
    status: str | None = "active"
    tier: str | None = None
    routing_tier: str | None = None
    context_input: int | None = None
    context_output: int | None = None
    cost_input_per_m: float | None = None
    cost_output_per_m: float | None = None
    cost_cached_per_m: float | None = None
    currency: str | None = "USD"
    supports_thinking: int | None = 0
    supports_tools: int | None = 0
    supports_vision: int | None = 0
    supports_audio: int | None = 0
    supports_json_mode: int | None = 0
    supports_streaming: int | None = 0
    supports_search_grounding: int | None = 0
    speciality: str | None = None
    recommended_use: str | None = None
    notes: str | None = None


class ProviderUpdateBody(BaseModel):
    id: str
    name: str
    type: str | None = "unknown"
    api_endpoint: str | None = None
    auth_method: str | None = None
    confidentiality: str | None = "non confidentiel"
    cascade_priority: float | None = 5.0
    notes: str | None = None


class ApiKeyUpdateBody(BaseModel):
    id: str
    provider_id: str
    env_var: str
    project_name: str | None = None
    key_type: str | None = "free"
    quota_rpm: int | None = None
    quota_rpd: int | None = None
    quota_tpm: int | None = None
    status: str | None = "active"


class SubscriptionUpdateBody(BaseModel):
    id: str
    name: str
    cost_monthly_usd: float | None = 0.0
    rolling_window_hours: int | None = 24
    hourly_token_limit: int | None = None
    monthly_token_limit: int | None = None
    estimated_messages_limit: int | None = None
    advantages: str | None = None
    recommended_use: str | None = None


# ──────────────────────────────────────────────────────────────────
# Routes d'administration
# ──────────────────────────────────────────────────────────────────

@router.post("/api/models/update")
def route_update_model(body: ModelUpdateBody):
    """
    Met à jour ou crée un modèle dans models_registry.db.

    Sémantique merge-patch : les champs non fournis (None) sont repris du modèle
    existant plutôt que remis à leur valeur par défaut — l'INSERT OR REPLACE
    d'upsert_model effaçait sinon routing_tier (et ttft_ms, notes…) à chaque
    édition partielle depuis l'IHM (bug de la même famille que #T160).
    """
    try:
        # Base = ligne existante complète (préserve aussi les colonnes hors body
        # comme ttft_ms/throughput_tps), surchargée par les champs réellement fournis.
        provided = {k: v for k, v in body.dict().items() if v is not None}
        existing = get_model(body.id) or {}
        merged = {**existing, **provided}
        success = upsert_model(body.id, **merged)
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
