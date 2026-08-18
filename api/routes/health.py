"""
api/routes/health.py — Sonde de liveness PUBLIQUE (auto-bascule pipeline vocal).

Route volontairement SANS auth (montée hors _AUTH_DEP) : le simple fait de
répondre 200 prouve que le moteur est vivant. Utilisée par l'automation HA
« engine_health » qui bascule le pipeline Assist du Tab5 sur l'agent LOCAL
quand le moteur (Steam Deck) est injoignable, et le restaure au retour.

N'expose aucune donnée sensible : uniquement l'état d'exécution indicatif.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Santé"])


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness légère et rapide (sans auth). 200 = moteur vivant.

    Sur cette sonde PUBLIQUE, on n'ajoute qu'au maximum le SHA court du commit
    (champ `commit`, absent si indéterminable) — jamais de chemin de fichier, de
    branche ni de nom de machine. L'identité complète vit sur la route authentifiée
    /api/observability/version. Le SHA est lu depuis le cache de core.version_info :
    aucun `git` supplémentaire par requête.
    """
    status = None
    try:
        from core.app_state import get_app_state
        status = get_app_state().execution_state.get("status")
    except Exception as exc:  # pragma: no cover - défensif, ne doit jamais 500
        logger.debug("[HEALTHZ] app_state indisponible : %s", exc)
    payload = {"ok": True, "service": "tab5-engine", "status": status}
    try:
        from core.version_info import get_short_commit
        short = get_short_commit()
        if short:
            payload["commit"] = short
    except Exception as exc:  # pragma: no cover - défensif, ne doit jamais 500
        logger.debug("[HEALTHZ] version indisponible : %s", exc)
    return payload
