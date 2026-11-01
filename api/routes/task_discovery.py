"""
api/routes/task_discovery.py — Routes FastAPI pour l'Auto-Découverte de Tâches.

Permet de déclencher manuellement ou de consulter l'état de l'agent
de découverte automatique de tâches (TaskDiscoveryAgent & Scheduler).

Auteur : Antigravity IDE + Axel
Créé le : 2026-08-18 (Branche : feat/task-discovery-agent)
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, status

from agents.task_discovery_agent import TaskDiscoveryAgent
from core.task_discovery_scheduler import TaskDiscoveryScheduler

logger = logging.getLogger("api.task_discovery")

router = APIRouter(prefix="/api/tasks/discovery", tags=["Task Discovery"])

_scheduler = TaskDiscoveryScheduler()


@router.get("/scan", response_model=List[Dict[str, Any]])
async def scan_tasks():
    """
    Exécute un scan à la demande et renvoie la liste des tâches découvertes
    avec leur niveau de complexité et leur Tier LLM conseillé.
    """
    try:
        agent = TaskDiscoveryAgent()
        return agent.run_discovery()
    except Exception as exc:
        logger.error("[API_TASK_DISCOVERY] Erreur lors du scan: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du scan: {exc}",
        )


@router.post("/enqueue-all", response_model=Dict[str, Any])
async def enqueue_discovered_tasks():
    """
    Déclenche un scan et injecte automatiquement toutes les nouvelles tâches
    dédupliquées dans le backlog SQLite (moteur_runtime.db).
    """
    try:
        result = await _scheduler.sync_discovered_tasks()
        return {
            "status": "success",
            "result": result,
        }
    except Exception as exc:
        logger.error("[API_TASK_DISCOVERY] Erreur lors de l'injection: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'injection: {exc}",
        )


@router.get("/status", response_model=Dict[str, Any])
async def get_discovery_status():
    """
    Renvoie l'état persistant du planificateur de découverte (dernière exécution, stats).
    """
    try:
        return _scheduler.load_state()
    except Exception as exc:
        logger.error("[API_TASK_DISCOVERY] Erreur lecture état: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lecture état: {exc}",
        )
