"""
api/routes/quotas.py — Routes API Quotas & monitoring des providers du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/quotas/*, /api/quotas/sliding, /api/quotas/history,
           /api/quotas/claude-realtime, /api/quotas/claude-update

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Quotas & Billing"])


@router.get("/api/quotas")
def get_quotas():
    """Retourne l'état des quotas de tous les providers LLM configurés."""
    try:
        from core.quota_collector import get_refresh_summary
        return get_refresh_summary()
    except ImportError:
        return {"error": "Module quota_collector non disponible", "quotas": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/quotas/refresh")
async def refresh_quotas():
    """Force le rafraîchissement des quotas depuis les APIs providers."""
    try:
        from core.quota_collector import refresh_all_quotas
        result = refresh_all_quotas(include_claude=True, force_claude=True)
        return {"status": "ok", "result": result}
    except ImportError:
        return {"error": "Module quota_collector non disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/quotas/sliding")
def get_quotas_sliding():
    """Retourne les quotas sur fenêtre glissante (24h, 7j, 30j) pour le dashboard."""
    try:
        from core.session_history import get_sliding_window_stats
        return get_sliding_window_stats()
    except Exception as e:
        logger.debug(f"[QUOTAS SLIDING] Erreur : {e}")
        return {"daily": {}, "weekly": {}, "monthly": {}}


@router.get("/api/quotas/history")
def get_quotas_history(limit: int = 100):
    """Retourne l'historique des appels LLM pour le graphe de consommation."""
    try:
        from core.session_history import get_token_history
        return get_token_history(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/quotas/claude-realtime")
async def get_claude_quotas_realtime():
    """Retourne les quotas Claude en temps réel (via scraping ou API)."""
    try:
        from core.claude_quota_monitor import get_realtime_quotas
        return await get_realtime_quotas()
    except ImportError:
        return {"error": "Module claude_quota_monitor non disponible", "quotas": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/quotas/claude-update")
async def update_claude_quotas(body: dict = None):
    """Met à jour manuellement les quotas Claude (saisie utilisateur ou scraping)."""
    try:
        from core.claude_quota_monitor import update_quotas
        result = await update_quotas(body or {})
        return {"status": "ok", "result": result}
    except ImportError:
        return {"error": "Module claude_quota_monitor non disponible"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/access-map")
def get_access_map():
    """Retourne la carte d'accès à tous les modèles (clés disponibles ou non)."""
    try:
        from core.llm_gateway import LLMGateway
        gateway = LLMGateway()
        return gateway.get_access_map()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/access-map/{model_id}")
def get_access_map_model(model_id: str):
    """Retourne la disponibilité d'accès pour un modèle spécifique."""
    try:
        from core.llm_gateway import LLMGateway
        gateway = LLMGateway()
        access_map = gateway.get_access_map()
        model_info = access_map.get(model_id)
        if model_info is None:
            raise HTTPException(status_code=404, detail=f"Modèle '{model_id}' inconnu.")
        return model_info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/gcp-billing")
async def get_gcp_billing():
    """Retourne la facturation GCP (Gemini API) de la période courante."""
    try:
        from core.gcp_oauth import get_gcp_billing_data
        return await get_gcp_billing_data()
    except ImportError:
        return {"error": "Module gcp_oauth non disponible", "billing": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
