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

    latency = _rows(
        "SELECT AVG(latency_ms) AS avg_ms, MIN(latency_ms) AS min_ms, MAX(latency_ms) AS max_ms "
        "FROM vocal_audit_log WHERE created_at > ? AND phase = 'response' AND latency_ms IS NOT NULL",
        (since_iso,),
    )

    by_mode = _rows(
        "SELECT source_mode, COUNT(*) AS n, AVG(latency_ms) AS avg_ms "
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
    lat = latency[0] if latency else {}

    return {
        "period": period,
        "total_events": t.get("n") or 0,
        "total_exchanges": t.get("responses") or 0,
        "tts_enabled_events": t.get("tts_on") or 0,
        "latency_ms": {
            "avg": round(lat["avg_ms"], 1) if lat.get("avg_ms") is not None else None,
            "min": round(lat["min_ms"], 1) if lat.get("min_ms") is not None else None,
            "max": round(lat["max_ms"], 1) if lat.get("max_ms") is not None else None,
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
            "HASS_TOKEN": bool(os.environ.get("HASS_TOKEN") or os.environ.get("HA_TOKEN")),
            "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY")),
            "GEMINI_PAYANT_API_KEY": bool(os.environ.get("GEMINI_PAYANT_API_KEY")),
        },
        "tts_cloud_available": bool(os.environ.get("GEMINI_PAYANT_API_KEY") or os.environ.get("GEMINI_API_KEY")),
    }
