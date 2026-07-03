"""
api/routes/events.py — Routes FastAPI pour exposer l'EventStore (audit trail append-only).
Intégration dans le moteur pour l'audit et la visualisation.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException

from core.event_store import get_event_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Events & Audit"])


@router.get("/api/events")
async def get_events(limit: int = 100, type: Optional[str] = None):
    """
    Récupère la liste des événements récents de l'EventStore.
    Filtre optionnel par type d'événement.
    """
    try:
        store = get_event_store()
        events = await store.get_recent_events(limit=limit, event_type=type)
        return {"status": "success", "count": len(events), "events": events}
    except Exception as e:
        logger.error(f"[EVENTS API] Erreur lors de la récupération des événements: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/events/session/{session_id}")
async def get_session_events(session_id: str):
    """
    Récupère tous les événements associés à une session spécifique.
    """
    try:
        store = get_event_store()
        events = await store.get_session_events(session_id=session_id)
        return {
            "status": "success",
            "session_id": session_id,
            "count": len(events),
            "events": events
        }
    except Exception as e:
        logger.error(f"[EVENTS API] Erreur lors de la récupération de la session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/events/session/{session_id}/replay")
async def replay_session_events(session_id: str):
    """
    Retourne l'historique textuel formaté (replay) d'une session.
    """
    try:
        store = get_event_store()
        lines = await store.replay_session(session_id=session_id)
        return {
            "status": "success",
            "session_id": session_id,
            "lines": lines
        }
    except Exception as e:
        logger.error(f"[EVENTS API] Erreur lors du replay de la session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/events/stats")
async def get_event_stats():
    """
    Retourne les statistiques de l'EventStore.
    """
    try:
        store = get_event_store()
        stats = await store.get_stats()
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"[EVENTS API] Erreur lors de la récupération des statistiques: {e}")
        raise HTTPException(status_code=500, detail=str(e))
