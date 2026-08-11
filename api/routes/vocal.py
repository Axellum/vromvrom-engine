"""
api/routes/vocal.py — Routes d'observation de l'assistant vocal.

Complète les routes vocales déjà présentes dans `api/routes/agents.py`
(`/api/vocal/audit`, `/api/vocal/abort`) par ce qui manquait à l'IHM pour
comprendre et régler le pipeline vocal : statistiques agrégées, conversations
et configuration effective.

Tout est en LECTURE SEULE et provient de mesures réelles (`vocal_audit_log`,
`vocal_conversation_turns`, `vocal_jobs`) : aucune valeur n'est estimée.

Auteur : Claude + Axel — 2026-07-28
"""

import logging
import os
import time

from fastapi import APIRouter, Query

from core.runtime_db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vocal", tags=["Assistant vocal"])


def _rows(query: str, params: tuple = ()) -> list[dict]:
    """Requête de lecture tolérante : une table absente n'écroule pas la vue."""
    try:
        import sqlite3
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[VOCAL] Lecture SQLite impossible : {e}")
        return []


def _arrondi(valeur: float | None) -> float | None:
    return round(valeur, 1) if valeur is not None else None


def _percentile(valeurs_triees: list[float], p: int) -> float | None:
    """Percentile par rang le plus proche, sur une liste DÉJÀ triée.

    [#T270] Pas d'interpolation : sur des échantillons de quelques dizaines de
    points, elle inventerait une précision que la donnée n'a pas.
    """
    if not valeurs_triees:
        return None
    rang = min(len(valeurs_triees) - 1, int(len(valeurs_triees) * p / 100))
    return round(valeurs_triees[rang], 1)


@router.get("/stats")
async def vocal_stats(period: str = Query("7d", description="1h, 24h, 7d, 30d")):
    """
    Statistiques réelles du pipeline vocal sur une fenêtre donnée.

    `vocal_audit_log` enregistre deux lignes par échange (phase `request` puis
    `response`) : les latences ne sont donc calculées que sur les lignes
    `response`, seules à porter une mesure.
    """
    period_map = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
    seconds = period_map.get(period, 7 * 86400)
    since_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds))

    totals = _rows(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN phase = 'response' THEN 1 ELSE 0 END) AS responses, "
        "SUM(CASE WHEN tts_enabled = 1 THEN 1 ELSE 0 END) AS tts_on "
        "FROM vocal_audit_log WHERE created_at > ?",
        (since_iso,),
    )

    # [#T270] `latency_ms > 0` et NON `IS NOT NULL` : une latence à 0 signifie
    # « jamais mesurée », pas « instantanée ». Le filtre précédent laissait passer
    # 394 zéros sur 420 réponses en prod et annonçait 123 ms de moyenne là où la
    # vraie valeur est 1991 ms — un facteur 16, dans le sens flatteur. Un chiffre
    # faux qui rassure est pire que pas de chiffre du tout.
    mesures = [
        r["latency_ms"]
        for r in _rows(
            "SELECT latency_ms FROM vocal_audit_log "
            "WHERE created_at > ? AND phase = 'response' AND latency_ms > 0 "
            "ORDER BY latency_ms",
            (since_iso,),
        )
    ]

    by_mode = _rows(
        "SELECT source_mode, COUNT(*) AS n, "
        "SUM(CASE WHEN latency_ms > 0 THEN 1 ELSE 0 END) AS n_mesurees, "
        "AVG(CASE WHEN latency_ms > 0 THEN latency_ms END) AS avg_ms "
        "FROM vocal_audit_log WHERE created_at > ? AND phase = 'response' "
        "GROUP BY source_mode ORDER BY n DESC",
        (since_iso,),
    )

    by_device = _rows(
        "SELECT COALESCE(device_id, 'inconnu') AS device_id, COUNT(*) AS n "
        "FROM vocal_audit_log WHERE created_at > ? GROUP BY device_id ORDER BY n DESC",
        (since_iso,),
    )

    by_routing = _rows(
        "SELECT COALESCE(routing_type, 'inconnu') AS routing_type, COUNT(*) AS n "
        "FROM vocal_audit_log WHERE created_at > ? AND phase = 'response' "
        "GROUP BY routing_type ORDER BY n DESC",
        (since_iso,),
    )

    t = totals[0] if totals else {}
    reponses = t.get("responses") or 0

    return {
        "period": period,
        "total_events": t.get("n") or 0,
        "total_exchanges": reponses,
        "tts_enabled_events": t.get("tts_on") or 0,
        # [#T270] `echantillon` / `non_mesurees` rendent la couverture visible :
        # une moyenne calculée sur 26 échanges de 420 ne veut pas dire la même
        # chose qu'une moyenne calculée sur 420. Sans ce dénominateur, l'IHM
        # affichait un chiffre sans moyen de savoir ce qu'il valait.
        "latency_ms": {
            "avg": _arrondi(sum(mesures) / len(mesures)) if mesures else None,
            "min": _arrondi(mesures[0]) if mesures else None,
            "max": _arrondi(mesures[-1]) if mesures else None,
            "p50": _percentile(mesures, 50),
            "p90": _percentile(mesures, 90),
            "echantillon": len(mesures),
            "non_mesurees": max(0, reponses - len(mesures)),
        },
        "by_mode": by_mode,
        "by_device": by_device,
        "by_routing": by_routing,
    }


@router.get("/conversations")
async def vocal_conversations(limit: int = Query(20, ge=1, le=100)):
    """Derniers tours de conversation vocale (mode Discussion)."""
    turns = _rows(
        "SELECT conversation_id, turn_index, role, content, source_mode, device_id, created_at "
        "FROM vocal_conversation_turns ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"count": len(turns), "turns": turns}


@router.get("/jobs")
async def vocal_jobs(limit: int = Query(20, ge=1, le=100)):
    """Jobs vocaux asynchrones (mode Discussion long)."""
    jobs = _rows(
        "SELECT job_id, intent, status, user_prompt, conversation_id, device_id, "
        "result_text, error_message, created_at, updated_at "
        "FROM vocal_jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"count": len(jobs), "jobs": jobs}


@router.get("/config")
async def vocal_config():
    """
    Configuration effective du pipeline vocal, telle que le moteur la voit.

    Regroupe ce qui est réellement lu à l'exécution : le modèle de l'agent HA
    (config.json) et la présence des clés qui conditionnent la synthèse vocale
    cloud. La présence d'une clé est rapportée, jamais sa valeur.
    """
    config: dict = {}
    try:
        import json
        engine_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(engine_root, "config.json"), encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        logger.warning(f"[VOCAL] config.json illisible : {e}")

    return {
        "ha_model": config.get("ha_model"),
        "executor_model": config.get("executor_model"),
        "semantic_cache": config.get("semantic_cache", {}),
        "secrets_present": {
            # Diagnostic de présence (T239) : on teste si AU MOINS UNE des deux
            # clés existe — pas la valeur du token. Volontairement hors
            # accesseur get_ha_token() (« cette clé précise existe-t-elle »).
            "HASS_TOKEN": bool(os.environ.get("HASS_TOKEN") or os.environ.get("HA_TOKEN")),
            "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY")),
            "GEMINI_PAYANT_API_KEY": bool(os.environ.get("GEMINI_PAYANT_API_KEY")),
        },
        "tts_cloud_available": bool(os.environ.get("GEMINI_PAYANT_API_KEY") or os.environ.get("GEMINI_API_KEY")),
    }
