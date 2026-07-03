"""
api/routes/context.py — Routes API Contexte IA & Configuration du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/context-status, /api/context-reload, /api/context-ha-ingest,
           /api/config (GET + POST), /api/pricing (GET + POST),
           /api/models, /api/models/*, /api/providers, /api/keys,
           /api/pricing/auto-update

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import os
import json
import logging
from fastapi import APIRouter, HTTPException

from core.safe_io import safe_json_write, file_lock  # [P2-3.1] écritures atomiques

logger = logging.getLogger(__name__)

router = APIRouter()

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
PRICING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pricing_strategy.json")


@router.get("/api/config", tags=["Configuration"])
def get_config():
    """Retourne la configuration complète du moteur (config.json)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="config.json introuvable.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/config", tags=["Configuration"])
def update_config(body: dict):
    """Met à jour la configuration du moteur (merge partiel)."""
    try:
        # [P2-3.1] Read-modify-write atomique sous FileLock (aligné sur llm_gateway).
        with file_lock(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config = json.load(f)
            config.update(body)
            safe_json_write(CONFIG_FILE, config, lock=False)
        return {"message": "Configuration mise à jour.", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/context-status")
def get_context_status():
    """Retourne le statut du chargement du contexte IA 3-Layers."""
    try:
        from memory.context_loader import get_context_status
        return get_context_status()
    except ImportError:
        return {"status": "unavailable", "files": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/context-reload")
async def context_reload():
    """Force le rechargement du contexte IA depuis les fichiers Markdown."""
    try:
        from memory.context_loader import reload_context
        result = await reload_context()
        return {"status": "ok", "result": result}
    except ImportError:
        return {"status": "unavailable"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/context-ha-ingest")
async def context_ha_ingest():
    """Ingère les entités et états HA dans le contexte RAG."""
    try:
        from core.ha_context_ingestor import ingest_ha_context
        result = await ingest_ha_context()
        return {"status": "ok", "entities_ingested": result}
    except ImportError:
        return {"status": "unavailable", "entities_ingested": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models")
def get_models(provider: str = None, status: str = "active"):
    """Liste tous les modèles du registre."""
    try:
        from core.models_db import get_active_models, get_all_data
        if provider:
            return get_active_models(provider_id=provider)
        return get_all_data()
    except ImportError:
        return {"error": "Module models_db non disponible", "models": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models/stats")
def get_models_stats():
    """Retourne les statistiques d'utilisation des modèles."""
    try:
        from core.models_db import get_model_stats
        return get_model_stats()
    except ImportError:
        return {"error": "Module models_db non disponible", "stats": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models/{model_id}")
def get_model_detail(model_id: str):
    """Retourne les détails d'un modèle spécifique."""
    try:
        from core.models_db import get_model_by_id
        model = get_model_by_id(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail=f"Modèle '{model_id}' introuvable.")
        return model
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/providers")
def get_providers():
    """Liste tous les providers LLM configurés avec leur disponibilité."""
    try:
        from core.llm_gateway import LLMGateway
        gateway = LLMGateway()
        return gateway.get_providers_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/keys")
def get_keys_status():
    """Retourne le statut des clés API (présence, non valeur)."""
    api_keys = {
        "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY")),
        "DEEPSEEK_API_KEY": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")),
        "MISTRAL_API_KEY": bool(os.environ.get("MISTRAL_API_KEY")),
        "COHERE_API_KEY": bool(os.environ.get("COHERE_API_KEY")),
        "HASS_TOKEN": bool(os.environ.get("HASS_TOKEN")),
    }
    return {"keys": api_keys, "configured_count": sum(api_keys.values())}


@router.get("/api/pricing")
def get_pricing():
    """Retourne la stratégie de pricing des modèles LLM."""
    try:
        with open(PRICING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "pricing_strategy.json introuvable", "models": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/pricing")
def update_pricing(body: dict):
    """Met à jour la stratégie de pricing."""
    try:
        # [P2-3.1] Read-modify-write atomique sous FileLock.
        with file_lock(PRICING_FILE):
            pricing = {}
            try:
                with open(PRICING_FILE, encoding="utf-8") as f:
                    pricing = json.load(f)
            except FileNotFoundError:
                pass
            pricing.update(body)
            safe_json_write(PRICING_FILE, pricing, lock=False)
        return {"message": "Pricing mis à jour.", "pricing": pricing}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/pricing/auto-update")
async def pricing_auto_update():
    """Met à jour automatiquement les pricing depuis les APIs providers."""
    try:
        from core.pricing_updater import auto_update_pricing
        result = await auto_update_pricing()
        return {"status": "ok", "updated": result}
    except ImportError:
        return {"status": "unavailable", "updated": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
