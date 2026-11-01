"""
api/routes/vocal_simulation.py — POST /api/vocal/simulate : « que ferait le moteur de cette phrase ? ».

Sous l'authentification Bearer fail-closed comme les autres routes `/api`. La logique et
ses garanties (aucun appel HA, aucun LLM, aucune écriture) vivent dans
`services/vocal_simulation.py` ; cette route ne fait que valider l'entrée et exposer le
résultat. Elle n'écrit PAS dans le journal vocal : une simulation n'est pas un échange.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from services.vocal_simulation import simuler

router = APIRouter(prefix="/api/vocal", tags=["Vocal"])


class SimulationVocale(BaseModel):
    """Corps de POST /api/vocal/simulate."""

    texte: str = Field(..., min_length=1, max_length=500, description="La phrase à analyser.")
    mode: Literal["ha", "chat"] = Field("ha", description="« ha » = domotique, « chat » = discussion.")

    @field_validator("texte")
    @classmethod
    def _texte_non_vide(cls, valeur: str) -> str:
        valeur = valeur.strip()
        if not valeur:
            raise ValueError("Le texte ne peut pas être vide.")
        return valeur


@router.post("/simulate")
async def simuler_une_phrase(corps: SimulationVocale) -> dict:
    """Chemin que suivrait la phrase dans le vocal, sans rien exécuter (voir le module)."""
    return await run_in_threadpool(simuler, corps.texte, corps.mode)
