"""
api/routes/auth_tickets.py — Émission de tickets d'accès éphémères (SSE/WebSocket).

[HMI v2] Les EventSource et WebSocket du navigateur ne peuvent pas porter de
header Authorization. Plutôt que d'exposer `MOTEUR_API_KEY` en query param
(`?token=`, qui fuit dans les logs), le client détenteur de la clé échange un
Bearer contre un ticket à usage unique et courte durée, puis ouvre le flux avec
`?ticket=<ticket>`.

Le routeur est monté AVEC `require_auth` (cf. gui_server.py) : émettre un ticket
exige donc déjà un Bearer valide. La validation/consommation du ticket côté flux
est gérée par `require_auth` lui-même (qui accepte `?ticket=`).
"""

import logging
from fastapi import APIRouter

from core.auth import issue_ticket, _TICKET_TTL_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Auth"])


@router.post("/api/auth/ticket")
async def create_sse_ticket():
    """
    Émet un ticket éphémère à usage unique pour ouvrir un flux SSE/WS.

    Protégé par `require_auth` au niveau du routeur (Bearer requis). Le client
    utilise ensuite le ticket dans l'URL du flux : `/api/stream?ticket=<ticket>`.
    """
    ticket = issue_ticket()
    return {"ticket": ticket, "expires_in": _TICKET_TTL_SECONDS}
