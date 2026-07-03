"""
core/hitl.py — Human-In-The-Loop (HITL) Manager pour le tab5-engine.

Implémente un vrai mécanisme de pause/resume dans le flux d'exécution via
asyncio.Event(). Quand une approbation humaine est requise (avant un DAG
destructeur, ou quand la confiance du plan est faible), le moteur se met
en pause et attend une réponse via l'API REST ou le WebSocket.

Architecture :
- L'Engine ou le DAGRunner appelle `await hitl.request_approval(...)`
- Le flux est bloqué sur un `asyncio.Event.wait()`
- L'API REST POST /api/approval appelle `hitl.approve()` ou `hitl.reject()`
- Le flux reprend avec la décision humaine

Créé dans le cadre de l'audit V5.5 (Axe HL1 — score HITL 55% → cible 85%).
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Timeout par défaut avant auto-approbation (5 minutes)
DEFAULT_APPROVAL_TIMEOUT = 300


@dataclass
class ApprovalRequest:
    """Représente une demande d'approbation humaine en attente."""
    request_id: str
    description: str
    plan_summary: Optional[str] = None
    risk_level: str = "medium"  # "low", "medium", "high", "critical"
    created_at: float = field(default_factory=time.time)
    timeout: float = DEFAULT_APPROVAL_TIMEOUT
    # Résultat (rempli par approve/reject)
    approved: Optional[bool] = None
    feedback: Optional[str] = None
    # Données modifiables par l'humain avant approbation
    modified_data: Optional[Dict[str, Any]] = None


class HITLManager:
    """
    Gestionnaire Human-In-The-Loop thread-safe basé sur asyncio.Event.

    Usage dans l'Engine ou le DAGRunner :
        hitl = HITLManager()
        decision = await hitl.request_approval(
            request_id="plan_123",
            description="Exécuter 5 modifications de fichiers YAML",
            risk_level="high",
        )
        if decision.approved:
            # Continuer l'exécution
        else:
            # Annuler ou adapter
    """

    def __init__(self):
        # Événements en attente : request_id -> asyncio.Event
        self._pending_events: Dict[str, asyncio.Event] = {}
        # Requêtes en attente : request_id -> ApprovalRequest
        self._pending_requests: Dict[str, ApprovalRequest] = {}
        # Callback SSE pour notifier l'IHM
        self._on_event = None

    def set_event_callback(self, callback) -> None:
        """Configure le callback SSE pour les notifications IHM."""
        self._on_event = callback

    async def request_approval(
        self,
        request_id: str,
        description: str,
        plan_summary: Optional[str] = None,
        risk_level: str = "medium",
        timeout: float = DEFAULT_APPROVAL_TIMEOUT,
        on_event=None,
    ) -> ApprovalRequest:
        """
        Demande une approbation humaine et bloque jusqu'à réception.

        Le flux d'exécution est suspendu via asyncio.Event.wait() jusqu'à
        ce que approve() ou reject() soit appelé, ou que le timeout expire.

        Args:
            request_id: Identifiant unique de la demande
            description: Description lisible de ce qui nécessite l'approbation
            plan_summary: Résumé du plan (affiché dans l'IHM)
            risk_level: Niveau de risque ("low", "medium", "high", "critical")
            timeout: Délai avant auto-approbation en secondes
            on_event: Callback SSE optionnel (override du callback global)

        Returns:
            ApprovalRequest avec le champ `approved` rempli (True/False)
        """
        event_callback = on_event or self._on_event

        # Créer la requête et l'événement de synchronisation
        request = ApprovalRequest(
            request_id=request_id,
            description=description,
            plan_summary=plan_summary,
            risk_level=risk_level,
            timeout=timeout,
        )
        event = asyncio.Event()

        self._pending_requests[request_id] = request
        self._pending_events[request_id] = event

        logger.info(
            f"[HITL] ⏸️  Approbation demandée : {request_id} "
            f"(risque: {risk_level}, timeout: {timeout}s)"
        )

        # Notifier l'IHM via SSE
        if event_callback:
            await event_callback("approval_required", {
                "request_id": request_id,
                "description": description,
                "plan_summary": plan_summary,
                "risk_level": risk_level,
                "timeout": timeout,
                "created_at": request.created_at,
            })

        # Attente bloquante avec timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            logger.info(
                f"[HITL] ✅ Réponse reçue pour {request_id} : "
                f"{'approuvé' if request.approved else 'rejeté'}"
            )
        except asyncio.TimeoutError:
            # Politique fail-safe : pour les plans à risque élevé/critique, le
            # timeout REJETTE par défaut (ne jamais exécuter une action sensible
            # sans validation explicite). Pour low/medium, auto-approbation.
            fail_safe = str(risk_level).lower() in ("high", "critical")
            request.approved = not fail_safe
            if fail_safe:
                logger.warning(
                    f"[HITL] ⏰ Timeout ({timeout}s) pour {request_id} "
                    f"(risque={risk_level}). REJET fail-safe."
                )
                request.feedback = f"Rejet automatique (fail-safe) après timeout de {timeout}s"
            else:
                logger.warning(
                    f"[HITL] ⏰ Timeout ({timeout}s) pour {request_id} "
                    f"(risque={risk_level}). Auto-approbation."
                )
                request.feedback = f"Auto-approbation après timeout de {timeout}s"

            if event_callback:
                await event_callback("approval_timeout", {
                    "request_id": request_id,
                    "auto_approved": request.approved,
                    "risk_level": risk_level,
                })

        # Nettoyage
        self._pending_events.pop(request_id, None)
        self._pending_requests.pop(request_id, None)

        return request

    def approve(
        self,
        request_id: str,
        feedback: Optional[str] = None,
        modified_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Approuve une demande en attente (appelé par l'API REST).

        Args:
            request_id: L'identifiant de la demande à approuver
            feedback: Commentaire optionnel de l'utilisateur
            modified_data: Données modifiées par l'utilisateur (ex: plan édité)

        Returns:
            True si la demande existait et a été approuvée, False sinon.
        """
        request = self._pending_requests.get(request_id)
        event = self._pending_events.get(request_id)

        if not request or not event:
            logger.warning(f"[HITL] Demande {request_id} introuvable ou expirée.")
            return False

        request.approved = True
        request.feedback = feedback
        request.modified_data = modified_data
        event.set()  # Débloque le await dans request_approval()

        logger.info(f"[HITL] Demande {request_id} approuvée par l'utilisateur.")
        return True

    def reject(
        self,
        request_id: str,
        feedback: Optional[str] = None,
    ) -> bool:
        """
        Rejette une demande en attente (appelé par l'API REST).

        Args:
            request_id: L'identifiant de la demande à rejeter
            feedback: Raison du rejet

        Returns:
            True si la demande existait et a été rejetée, False sinon.
        """
        request = self._pending_requests.get(request_id)
        event = self._pending_events.get(request_id)

        if not request or not event:
            logger.warning(f"[HITL] Demande {request_id} introuvable ou expirée.")
            return False

        request.approved = False
        request.feedback = feedback
        event.set()  # Débloque le await dans request_approval()

        logger.info(f"[HITL] Demande {request_id} rejetée par l'utilisateur.")
        return True

    def get_pending_requests(self) -> list:
        """Retourne la liste des demandes d'approbation en attente."""
        return [
            {
                "request_id": r.request_id,
                "description": r.description,
                "plan_summary": r.plan_summary,
                "risk_level": r.risk_level,
                "created_at": r.created_at,
                "timeout": r.timeout,
                "elapsed": time.time() - r.created_at,
            }
            for r in self._pending_requests.values()
        ]

    @property
    def has_pending(self) -> bool:
        """Indique s'il y a des demandes en attente."""
        return len(self._pending_requests) > 0
