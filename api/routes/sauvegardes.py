"""
api/routes/sauvegardes.py — Inventaire et déclenchement des sauvegardes des bases.

Deux routes, montées sous l'authentification Bearer fail-closed comme les autres
routes `/api` :

    GET  /api/sauvegardes   inventaire (dossier, rétention, liste, dernière date)
    POST /api/sauvegardes   crée une sauvegarde maintenant → 201 + l'entrée créée
                            409 si une sauvegarde est déjà en cours

La logique vit dans `core/sauvegarde.py` (aussi pilotée par la CLI et le timer
systemd) : cette route ne fait que l'exposer. La copie lourde (sha256, backup
SQLite) tourne dans un thread pour ne pas figer la boucle asyncio du moteur.

Vérité d'abord : un dossier de sauvegarde absent n'est pas une erreur (« aucune
sauvegarde » est une réponse vraie) ; un dossier inaccessible ou un disque plein
répond 503 avec la raison, jamais un 200 vide.
"""

import logging

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from core import sauvegarde

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sauvegardes", tags=["Sauvegardes"])


@router.get("")
async def lister_les_sauvegardes() -> dict:
    """Inventaire des sauvegardes (lecture seule, rapide : ne lit que les manifestes)."""
    try:
        return await run_in_threadpool(sauvegarde.etat_sauvegardes)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dossier de sauvegarde illisible : {exc}",
        ) from exc


@router.post("", status_code=201)
async def creer_une_sauvegarde() -> dict:
    """Crée une sauvegarde des bases maintenant et renvoie son entrée d'inventaire."""
    try:
        manifeste = await run_in_threadpool(sauvegarde.creer_sauvegarde)
    except sauvegarde.SauvegardeEnCours as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sauvegarde.SauvegardeImpossible as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    entree = sauvegarde.entree_api(manifeste)
    ko = [f["nom"] for f in entree["fichiers"] if f["integrite"] == "ko"]
    if ko:
        # La sauvegarde existe (201) mais elle est marquée : on ne dit pas « ok ».
        logger.error("[SAUVEGARDE] Créée avec anomalie : %s", ", ".join(ko))
    return entree
