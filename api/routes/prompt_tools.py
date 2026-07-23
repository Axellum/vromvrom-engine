"""
api/routes/prompt_tools.py — Branche le PromptEngineerAgent sur l'IHM (#T192).

L'agent existait depuis le 07/07 mais n'était importé nulle part (code mort
confirmé par grep). Décision Phase 3 : le BRANCHER plutôt que le supprimer —
il devient le backend du bouton "Optimiser mon prompt" de la page Prompt de
l'IHM v2, sa raison d'être d'origine (demande d'Axel : agent de reformulation).

POST /api/prompt/engineer : {prompt, context?, tier?} → {optimized_prompt}.
Le tier par défaut vient de config.json["prompt_engineer_model"] (clé gérée
par le CRUD /api/agents), repli "fort" (choix historique de l'agent).
"""

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Prompt Tools"])


class PromptEngineerBody(BaseModel):
    prompt: str = Field(..., min_length=1, description="Demande brute à transformer en prompt expert")
    context: str = Field("", description="Contexte optionnel à intégrer au prompt généré")
    tier: str | None = Field(None, description="Tier (leger/moyen/fort/automatique) ou id de modèle")


@router.post("/api/prompt/engineer")
async def engineer_prompt(body: PromptEngineerBody):
    """Transforme une demande brouillonne en prompt expert structuré (PromptEngineerAgent)."""
    from agents.prompt_engineer_agent import PromptEngineerAgent
    from core.llm_gateway import LLMGateway, load_config
    from core.state import TaskPayload

    config = load_config()
    provider_name = body.tier or config.get("prompt_engineer_model", "fort")

    agent = PromptEngineerAgent(llm_gateway=LLMGateway(), provider_name=provider_name)
    payload = TaskPayload(
        task_objective=body.prompt,
        relevant_context=body.context,
        metadata={"session_id": f"prompt_studio_{int(time.time())}"},
    )

    try:
        # L'agent gère déjà son propre timeout 60s ; filet global à 90s.
        update = await asyncio.wait_for(agent.invoke(payload), timeout=90.0)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="PromptEngineer : délai dépassé (90s).")

    if update.status != "success" or not update.result_data:
        raise HTTPException(
            status_code=502,
            detail=f"PromptEngineer en échec : {update.error_message or 'réponse vide'}",
        )

    return {
        "optimized_prompt": str(update.result_data).strip(),
        "provider_used": provider_name,
    }
