"""
api/routes/approval.py — Routes HITL (Human-In-The-Loop) pour le moteur.

Endpoints REST pour approuver/rejeter les demandes d'approbation du moteur
quand il détecte des tâches à risque dans le plan.

Créé dans le cadre de l'audit V5.5 (Axe HL1-bis).
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Approbation HITL"])


class ApprovalPayload(BaseModel):
    """Payload de la requête d'approbation/rejet."""
    request_id: str
    feedback: str | None = None
    modified_data: dict | None = None


@router.get("/api/approval/pending")
def get_pending_approvals():
    """
    Retourne la liste des demandes d'approbation en attente.

    [#T267] Source de vérité = le store DURABLE (`hitl_pending_approvals`), et
    non plus la mémoire du moteur : une demande devait survivre à la session qui
    l'a créée — c'est tout l'objet du HITL non bloquant. Les demandes encore en
    mémoire (mode bloquant conservé derrière `MOTEUR_HITL_NON_BLOQUANT=0`) sont
    ajoutées ensuite, sans doublon.

    Returns:
        Liste des demandes avec request_id, description, risk_level et elapsed.
    """
    import time

    from core import hitl_store

    pending: list[dict] = []
    vus: set[str] = set()
    try:
        for d in hitl_store.lister_en_attente():
            vus.add(d["request_id"])
            pending.append({
                "request_id": d["request_id"],
                "session_id": d["session_id"],
                "description": d["description"],
                "plan_summary": d["plan_summary"],
                "risk_level": d["risk_level"],
                "created_at": d["created_at"],
                "elapsed": time.time() - (d["created_at"] or time.time()),
                "task_count": len(d.get("dag_tasks") or []),
                "durable": True,
            })
    except Exception as e:
        logger.error(f"[HITL API] Lecture du store durable impossible : {e}")

    try:
        from gui_server import _engine_instance
        if _engine_instance is not None and hasattr(_engine_instance, "hitl"):
            for r in _engine_instance.hitl.get_pending_requests():
                if r.get("request_id") not in vus:
                    r["durable"] = False
                    pending.append(r)
    except Exception as e:
        logger.debug(f"[HITL API] Demandes en mémoire non lues : {e}")

    return {"pending": pending, "count": len(pending)}


@router.post("/api/approval/approve")
async def approve_request(payload: ApprovalPayload):
    """
    Approuve une demande d'approbation HITL en attente.

    Le flux d'exécution du moteur reprend automatiquement après approbation.
    
    Args:
        payload: ApprovalPayload avec request_id et feedback optionnel.
    """
    try:
        # [#T267] Deux chemins, dans cet ordre :
        #  1. store durable → la session a été SUSPENDUE, il faut rejouer le DAG stocké ;
        #  2. mémoire du moteur → mode bloquant (kill-switch), il faut débloquer l'attente.
        # `marquer_decision` n'accepte la transition que depuis `pending`, donc un
        # double clic sur « Approuver » ne peut pas relancer deux fois le plan.
        from core import hitl_store
        from gui_server import _engine_instance, broadcast_event

        decision_durable = hitl_store.marquer_decision(
            payload.request_id, approuve=True, feedback=payload.feedback
        )

        success = decision_durable
        if _engine_instance is not None and hasattr(_engine_instance, "hitl"):
            success = _engine_instance.hitl.approve(
                request_id=payload.request_id,
                feedback=payload.feedback,
                modified_data=payload.modified_data,
            ) or success

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Demande '{payload.request_id}' introuvable ou déjà traitée.",
            )

        if decision_durable:
            # La reprise peut durer plusieurs minutes : elle part en tâche de fond,
            # la route répond immédiatement. Le suivi se fait par SSE et par le
            # statut de la demande (`resumed` / `failed`).
            import asyncio

            from services.approval_resume_service import reprendre_apres_approbation
            asyncio.create_task(reprendre_apres_approbation(
                payload.request_id, on_event_callback=None,
            ))
            logger.info(
                f"[HITL API] [T267] Reprise lancée en tâche de fond pour {payload.request_id}."
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
        # [#T267] Même double chemin que l'approbation.
        from core import hitl_store
        from gui_server import _engine_instance, broadcast_event

        decision_durable = hitl_store.marquer_decision(
            payload.request_id, approuve=False, feedback=payload.feedback
        )

        success = decision_durable
        if _engine_instance is not None and hasattr(_engine_instance, "hitl"):
            success = _engine_instance.hitl.reject(
                request_id=payload.request_id,
                feedback=payload.feedback,
            ) or success

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Demande '{payload.request_id}' introuvable ou déjà traitée.",
            )

        if decision_durable:
            # Le plan est abandonné : la branche éphémère laissée en place par la
            # session suspendue doit être annulée, sinon elle reste pendante.
            import asyncio

            from services.approval_resume_service import annuler_apres_rejet
            asyncio.create_task(asyncio.to_thread(annuler_apres_rejet, payload.request_id))

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
