"""
api/routes/approval.py — Routes HITL (Human-In-The-Loop) pour le moteur.

Endpoints REST pour approuver/rejeter les demandes d'approbation du moteur
quand il détecte des tâches à risque dans le plan.

Créé dans le cadre de l'audit V5.5 (Axe HL1-bis).
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Approbation HITL"])


class ApprovalPayload(BaseModel):
    """Payload de la requête d'approbation/rejet."""
    request_id: str
    feedback: Optional[str] = None
    modified_data: Optional[dict] = None


@router.get("/api/approval/pending")
def get_pending_approvals():
    """
    Retourne la liste des demandes d'approbation en attente.
    
    Returns:
        Liste des demandes avec request_id, description, risk_level et elapsed.
    """
    try:
        from gui_server import _engine_instance
        if _engine_instance is None or not hasattr(_engine_instance, 'hitl'):
            return {"pending": [], "count": 0}
        
        pending = _engine_instance.hitl.get_pending_requests()
        return {"pending": pending, "count": len(pending)}
    except Exception as e:
        logger.error(f"[HITL API] Erreur get_pending: {e}")
        return {"pending": [], "count": 0, "error": str(e)}


@router.post("/api/approval/approve")
async def approve_request(payload: ApprovalPayload):
    """
    Approuve une demande d'approbation HITL en attente.

    Le flux d'exécution du moteur reprend automatiquement après approbation.
    
    Args:
        payload: ApprovalPayload avec request_id et feedback optionnel.
    """
    try:
        from gui_server import _engine_instance, broadcast_event

        if _engine_instance is None or not hasattr(_engine_instance, 'hitl'):
            raise HTTPException(status_code=400, detail="Moteur non initialisé.")

        success = _engine_instance.hitl.approve(
            request_id=payload.request_id,
            feedback=payload.feedback,
            modified_data=payload.modified_data,
        )

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Demande '{payload.request_id}' introuvable ou déjà traitée.",
            )

        # Notification SSE pour l'IHM
        await broadcast_event("approval_user_action", {
            "request_id": payload.request_id,
            "action": "approved",
            "feedback": payload.feedback,
        })

        logger.info(f"[HITL API] Demande {payload.request_id} approuvée.")
        return {
            "message": f"Demande '{payload.request_id}' approuvée avec succès.",
            "request_id": payload.request_id,
            "status": "approved",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HITL API] Erreur approve: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/approval/reject")
async def reject_request(payload: ApprovalPayload):
    """
    Rejette une demande d'approbation HITL en attente.

    Le flux d'exécution du moteur est annulé pour cette tâche.
    
    Args:
        payload: ApprovalPayload avec request_id et feedback optionnel.
    """
    try:
        from gui_server import _engine_instance, broadcast_event

        if _engine_instance is None or not hasattr(_engine_instance, 'hitl'):
            raise HTTPException(status_code=400, detail="Moteur non initialisé.")

        success = _engine_instance.hitl.reject(
            request_id=payload.request_id,
            feedback=payload.feedback,
        )

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Demande '{payload.request_id}' introuvable ou déjà traitée.",
            )

        # Notification SSE pour l'IHM
        await broadcast_event("approval_user_action", {
            "request_id": payload.request_id,
            "action": "rejected",
            "feedback": payload.feedback,
        })

        logger.info(f"[HITL API] Demande {payload.request_id} rejetée.")
        return {
            "message": f"Demande '{payload.request_id}' rejetée.",
            "request_id": payload.request_id,
            "status": "rejected",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HITL API] Erreur reject: {e}")
        raise HTTPException(status_code=500, detail=str(e))
