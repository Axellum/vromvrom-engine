"""
api/routes/swarm.py — Routeur FastAPI pour la gestion et le monitoring des workers Swarm.

Permet de lister les workers, forcer un ping (heartbeat) et enregistrer/désenregistrer
des workers distants avec persistance dans workers.json.
"""

import os
import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from core.worker_registry import get_worker_registry
from core.safe_io import safe_json_write, file_lock  # [P1-2.3]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Swarm Workers"])

WORKERS_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "workers.json"
)


# ──────────────────────────────────────────────────────────────────
# Modèles Pydantic
# ──────────────────────────────────────────────────────────────────

class WorkerRegisterBody(BaseModel):
    name: str
    host: str
    port: int = 8780
    capabilities: List[str] = []
    description: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Utilitaires de persistance
# ──────────────────────────────────────────────────────────────────

def _load_workers_json() -> list:
    """Charge le fichier workers.json de manière sécurisée."""
    if os.path.exists(WORKERS_CONFIG):
        try:
            with file_lock(WORKERS_CONFIG):  # [P1-2.3] lecture hors écriture concurrente
                with open(WORKERS_CONFIG, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            return data.get("workers", [])
        except Exception as e:
            logger.warning(f"Erreur de lecture de workers.json : {e}")
            return []
    return []


def _save_workers_json(workers: list):
    """Sauvegarde la liste des workers dans workers.json.

    [P1-2.3] Écriture atomique (os.replace) + FileLock inter-process.
    """
    try:
        safe_json_write(WORKERS_CONFIG, {"workers": workers})
    except Exception as e:
        logger.error(f"Erreur d'écriture dans workers.json : {e}")
        raise RuntimeError(f"Impossible de sauvegarder workers.json : {e}")


# ──────────────────────────────────────────────────────────────────
# Routes du Swarm
# ──────────────────────────────────────────────────────────────────

@router.get("/api/swarm/workers")
def route_get_workers():
    """Retourne la liste complète de tous les workers enregistrés avec leur statut."""
    try:
        registry = get_worker_registry()
        return {"status": "ok", "workers": registry.get_all_status()}
    except Exception as e:
        logger.error(f"Erreur get_workers : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/swarm/workers/ping")
async def route_ping_workers():
    """Force immédiatement le heartbeat (ping) de tous les workers."""
    try:
        registry = get_worker_registry()
        await registry.heartbeat_all()
        return {"status": "ok", "message": "Pings Swarm effectués.", "workers": registry.get_all_status()}
    except Exception as e:
        logger.error(f"Erreur ping_workers : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/swarm/workers/register")
def route_register_worker(body: WorkerRegisterBody):
    """Enregistre un nouveau worker Swarm (persistance + mémoire)."""
    try:
        registry = get_worker_registry()
        
        # 1. Enregistrement en mémoire
        registry.register(
            name=body.name,
            host=body.host,
            port=body.port,
            capabilities=body.capabilities
        )
        
        # 2. Persistance dans le fichier workers.json
        workers = _load_workers_json()
        
        # Supprimer le doublon s'il existe déjà dans la liste
        workers = [w for w in workers if w.get("name") != body.name]
        
        # Ajouter le nouveau
        worker_entry = {
            "name": body.name,
            "host": body.host,
            "port": body.port,
            "capabilities": body.capabilities
        }
        if body.description:
            worker_entry["description"] = body.description
            
        workers.append(worker_entry)
        _save_workers_json(workers)

        return {"status": "ok", "message": f"Worker '{body.name}' enregistré avec succès.", "workers": registry.get_all_status()}
    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement du worker {body.name} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/swarm/workers/unregister/{name}")
def route_unregister_worker(name: str):
    """Retire un worker Swarm (persistance + mémoire)."""
    try:
        registry = get_worker_registry()
        
        # 1. Désenregistrement en mémoire
        registry.unregister(name)
        
        # 2. Retrait du fichier workers.json
        workers = _load_workers_json()
        workers = [w for w in workers if w.get("name") != name]
        _save_workers_json(workers)

        return {"status": "ok", "message": f"Worker '{name}' retiré.", "workers": registry.get_all_status()}
    except Exception as e:
        logger.error(f"Erreur lors du retrait du worker {name} : {e}")
        raise HTTPException(status_code=500, detail=str(e))
