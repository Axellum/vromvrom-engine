"""
api/routes/rendement.py — Compteur de rendement DreamCoder.

Expose l'entonnoir de production autonome (tenté → poussé → récolté → mergé)
et le ratio tokens/coût PAR PR mergée — le seul chiffre qui dise si
l'architecture produit plus qu'elle ne consomme.

Lecture seule : l'alimentation se fait en fin de cycle DreamCoder
(`core.rendement_dreamcoder.enregistrer_cycle`) et à la récolte
(`scripts/recolte_dreamcoder.py`).
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from core.rendement_dreamcoder import get_rendement, synchroniser_merges

logger = logging.getLogger("api.rendement")

router = APIRouter(prefix="/api/dreamcoder", tags=["DreamCoder"])

# Un second drainage pendant que le premier tourne casserait les slots Git.
_drainage_en_cours = asyncio.Lock()


@router.get("/rendement", response_model=dict)
async def rendement():
    """Entonnoir complet tenté → poussé → récolté → mergé + tokens par PR mergée."""
    try:
        return await asyncio.to_thread(get_rendement)
    except Exception as exc:
        logger.error("[API_RENDEMENT] Erreur de lecture du rendement : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de lecture du rendement : {exc}",
        ) from exc


@router.post("/drain", response_model=dict)
async def declencher_drainage() -> dict:
    """
    Lance un drainage DreamCoder tout de suite, sans attendre l'inactivité.

    Le cycle nocturne saute l'étape 3.0 si l'utilisateur a parlé récemment
    (`dreamcoder_min_idle_hours`). Après un déploiement, l'opérateur veut
    drainer le backlog `failed` débloqué par #T432 sans attendre 1 h.
    Auth : Bearer `MOTEUR_API_KEY` (montage fail-closed de `gui_server`).
    """
    from agents.dreamer_agent import (
        _load_persistent_config,
        _run_dreamcoder_drain_loop,
        dreamer_state,
    )

    if dreamer_state.get("running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="un cycle dreamer est déjà en cours",
        )
    if _drainage_en_cours.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="un drainage est déjà en cours",
        )

    async with _drainage_en_cours:
        pa_config = _load_persistent_config()
        if not pa_config.get("dreamcoder_enabled", True):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="dreamcoder_enabled est faux",
            )
        try:
            resultats = await _run_dreamcoder_drain_loop(pa_config)
        except Exception as exc:
            logger.error("[API_DREAMCODER] Drainage en échec : %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Drainage en échec : {exc}",
            ) from exc
        return {"status": "ok", "drain": resultats}


@router.post("/rendement/sync-merges", response_model=dict)
async def sync_merges() -> dict:
    """
    Constate via `gh` les PR de tâches mergées (titre `[auto]` posé par la
    récolte — best effort). Utile surtout côté PC, là où `gh` est
    authentifié ; le Deck rendra 0 sans erreur.
    """
    try:
        nouveaux = await asyncio.to_thread(synchroniser_merges, ".")
        etat = await asyncio.to_thread(get_rendement)
        return {"nouvelles_prs_mergees": nouveaux, "rendement": etat}
    except Exception as exc:
        logger.error("[API_RENDEMENT] Erreur de synchronisation des merges : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de synchronisation des merges : {exc}",
        ) from exc
