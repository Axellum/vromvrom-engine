"""
api/routes/metrics.py — Routes API pour le Dashboard de Métriques V6.

Observabilité Data-Driven.

Expose les données de télémétrie agrégées depuis les sources SQLite et JSON :
- session_history.db : sessions moteur, token_usage, quota_snapshots, billing
- routing_metrics.db : décisions de routage, scores Elo
- token_usage.json : consommation par modèle

Endpoints :
- GET /api/metrics/telemetry  : Données agrégées pour le dashboard Chart.js
- GET /api/metrics/elo        : Scores Elo par modèle/domaine
- GET /api/metrics/routing     : Statistiques de routage
- GET /api/metrics/agents      : Performance par agent
"""

import os
import time
import sqlite3
import logging
from datetime import datetime
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Métriques & Télémétrie"])

from core.runtime_db import get_db_path, get_connection

# Chemins des bases de données
_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RUNTIME_DB = get_db_path()
_SESSION_DB = _RUNTIME_DB
_ROUTING_DB = _RUNTIME_DB



@router.get("/metrics", include_in_schema=False)
async def serve_metrics_dashboard():
    """Redirige le dashboard de métriques historique vers l'onglet de l'IHM principale."""
    return RedirectResponse(url="/#metrics")


def _safe_query(db_path: str, query: str, params: tuple = ()) -> list:
    """Requête SQLite sécurisée sur la base unifiée avec gestion d'erreur."""
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"[METRICS] Erreur SQLite unifiée : {e}")
        return []


@router.get("/api/metrics/telemetry")
async def get_telemetry(
    period: str = Query("24h", description="Période : 1h, 6h, 24h, 7d, 30d")
):
    """
    Point d'entrée principal du dashboard de métriques.
    
    Agrège les données de toutes les sources pour le rendu Chart.js :
    - Séries temporelles (tokens, coûts par heure)
    - Stats par agent (durée moyenne, taux de succès)
    - Stats par modèle (usage, Elo, coût/qualité)
    - Stats de routage (fast-path vs slow-path)
    - Prévisions budgétaires
    """
    # Calculer le timestamp de début selon la période
    period_map = {
        "1h": 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
        "30d": 30 * 24 * 3600,
    }
    seconds = period_map.get(period, 24 * 3600)
    since_ts = time.time() - seconds

    result = {
        "period": period,
        "generated_at": datetime.now().isoformat(),
        "time_series": _get_time_series(since_ts),
        "agent_stats": _get_agent_stats(since_ts),
        "model_stats": _get_model_stats(since_ts),
        "routing_stats": _get_routing_stats(since_ts),
        "kpis": _get_kpis(since_ts),
        "budget_forecast": _get_budget_forecast(),
    }
    return result


@router.get("/api/metrics/elo")
async def get_elo_scores():
    """
    Retourne tous les scores Elo par modèle et domaine.
    Utilisé pour le radar chart et le heatmap du dashboard.
    """
    try:
        from core.elo_scorer import get_all_scores, get_domain_leaderboard
        all_scores = get_all_scores()
        
        # Extraire les domaines uniques
        domains = set()
        for model_domains in all_scores.values():
            domains.update(model_domains.keys())
        
        # Leaderboards par domaine
        leaderboards = {}
        for domain in sorted(domains):
            leaderboards[domain] = get_domain_leaderboard(domain, top_n=10)
        
        return {
            "scores": all_scores,
            "domains": sorted(domains),
            "leaderboards": leaderboards,
        }
    except Exception as e:
        logger.warning(f"[METRICS] Erreur Elo : {e}")
        return {"scores": {}, "domains": [], "leaderboards": {}}


@router.get("/api/metrics/cost-per-success")
async def get_cost_per_success_endpoint():
    """
    [#T116] Coût USD par tâche réussie, agrégé par provider — croise le coût
    cumulé par modèle (token_usage) avec le nombre de tâches réussies par
    modèle (model_elo_scores.wins). Voir `core.elo_scorer.get_cost_per_successful_task`.
    """
    try:
        from core.elo_scorer import get_cost_per_successful_task
        return {"providers": get_cost_per_successful_task()}
    except Exception as e:
        logger.warning(f"[METRICS] Erreur cost-per-success : {e}")
        return {"providers": {}}


@router.get("/api/metrics/routing")
async def get_routing_stats_endpoint():
    """Statistiques détaillées de routage (catégories, fast/slow path)."""
    return _get_routing_stats(time.time() - 7 * 24 * 3600)


@router.get("/api/metrics/agents")
async def get_agent_stats_endpoint():
    """Performance détaillée par agent."""
    return _get_agent_stats(time.time() - 7 * 24 * 3600)


# ──────────────────────────────────────────────────────────────────
# Fonctions d'agrégation internes
# ──────────────────────────────────────────────────────────────────

def _get_time_series(since_ts: float) -> dict:
    """
    Séries temporelles pour les graphiques Chart.js.
    Agrège tokens et coûts par tranche horaire depuis quota_snapshots.
    """
    # Données depuis les snapshots de quota (enregistrés toutes les 60s)
    snapshots = _safe_query(
        _SESSION_DB,
        "SELECT timestamp, gemini_free_tpm, claude_cli_tph, "
        "gemini_cli_tph, estimated_cost_usd "
        "FROM quota_snapshots WHERE timestamp > ? ORDER BY timestamp",
        (since_ts,),
    )

    # Agrégation par heure
    hourly = {}
    for snap in snapshots:
        ts = snap.get("timestamp", 0)
        hour_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:00")
        if hour_key not in hourly:
            hourly[hour_key] = {
                "tokens": 0,
                "cost_usd": 0.0,
                "data_points": 0,
            }
        hourly[hour_key]["tokens"] += (
            (snap.get("gemini_free_tpm") or 0)
            + (snap.get("claude_cli_tph") or 0)
            + (snap.get("gemini_cli_tph") or 0)
        )
        hourly[hour_key]["cost_usd"] += snap.get("estimated_cost_usd") or 0
        hourly[hour_key]["data_points"] += 1

    # Formatage pour Chart.js (labels + datasets)
    labels = sorted(hourly.keys())
    return {
        "labels": labels,
        "tokens": [hourly[k]["tokens"] for k in labels],
        "costs": [round(hourly[k]["cost_usd"], 4) for k in labels],
    }


def _get_agent_stats(since_ts: float) -> list:
    """Stats par agent depuis les sessions moteur."""
    rows = _safe_query(
        _SESSION_DB,
        "SELECT model_name as agent, "
        "COUNT(*) as total_calls, "
        "SUM(CASE WHEN prompt_tokens > 0 THEN 1 ELSE 0 END) as successes, "
        "SUM(prompt_tokens) as total_input, "
        "SUM(completion_tokens) as total_output, "
        "AVG(prompt_tokens + completion_tokens) as avg_tokens "
        "FROM token_usage WHERE timestamp > ? "
        "GROUP BY model_name ORDER BY total_calls DESC",
        (since_ts,),
    )
    
    for row in rows:
        total = row.get("total_calls", 1) or 1
        row["success_rate"] = round(
            (row.get("successes", 0) / total) * 100, 1
        )
    
    return rows


def _get_model_stats(since_ts: float) -> dict:
    """Stats par modèle avec Elo intégré."""
    # Charger depuis token_usage SQLite
    usage = {}
    rows = _safe_query(
        _SESSION_DB,
        """
        SELECT model, 
               SUM(total_tokens) as total_tokens, 
               COUNT(*) as total_calls, 
               SUM(cost_usd) as cost_usd 
        FROM token_usage 
        WHERE timestamp > ?
        GROUP BY model
        """,
        (since_ts,),
    )
    for row in rows:
        usage[row["model"]] = {
            "total_tokens": row["total_tokens"] or 0,
            "total_calls": row["total_calls"] or 0,
            "cost_usd": row["cost_usd"] or 0.0,
        }

    # Fusionner avec les scores Elo
    try:
        from core.elo_scorer import get_all_scores
        elo_scores = get_all_scores()
        for model, domains in elo_scores.items():
            if model not in usage:
                usage[model] = {"total_tokens": 0, "total_calls": 0, "cost_usd": 0}
            usage[model]["elo_domains"] = domains
            # Score Elo moyen
            if domains:
                avg_elo = sum(d["elo"] for d in domains.values()) / len(domains)
                usage[model]["avg_elo"] = round(avg_elo, 1)
    except Exception:
        pass

    return usage


def _get_routing_stats(since_ts: float) -> dict:
    """Statistiques de routage depuis routing_metrics.db."""
    # Répartition par catégorie
    categories = _safe_query(
        _ROUTING_DB,
        "SELECT dominant_category, COUNT(*) as count, "
        "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes "
        "FROM routing_decisions WHERE timestamp > ? "
        "GROUP BY dominant_category ORDER BY count DESC",
        (since_ts,),
    )

    # Répartition fast-path vs slow-path
    path_stats = _safe_query(
        _ROUTING_DB,
        "SELECT "
        "SUM(CASE WHEN routing_type = 'fast_path' THEN 1 ELSE 0 END) as fast_path, "
        "SUM(CASE WHEN routing_type != 'fast_path' THEN 1 ELSE 0 END) as slow_path, "
        "COUNT(*) as total "
        "FROM routing_decisions WHERE timestamp > ?",
        (since_ts,),
    )

    return {
        "categories": categories,
        "path_distribution": path_stats[0] if path_stats else {},
    }


def _get_kpis(since_ts: float) -> dict:
    """KPIs bannière (coût total, sessions, taux de succès, tokens)."""
    # Coût estimé total
    billing = _safe_query(
        _SESSION_DB,
        "SELECT SUM(estimated_cost_usd) as total_cost "
        "FROM quota_snapshots WHERE timestamp > ?",
        (since_ts,),
    )

    # Nombre de sessions
    sessions = _safe_query(
        _SESSION_DB,
        "SELECT COUNT(*) as count FROM sessions WHERE start_time > ?",
        (datetime.fromtimestamp(since_ts).isoformat(),),
    )

    # Tokens totaux depuis token_usage
    tokens = _safe_query(
        _SESSION_DB,
        "SELECT SUM(prompt_tokens + completion_tokens) as total "
        "FROM token_usage WHERE timestamp > ?",
        (since_ts,),
    )

    return {
        "total_cost_usd": round(
            (billing[0]["total_cost"] or 0) if billing else 0, 4
        ),
        "total_sessions": (sessions[0]["count"] or 0) if sessions else 0,
        "total_tokens": (tokens[0]["total"] or 0) if tokens else 0,
    }


def _get_budget_forecast() -> dict:
    """
    Projection budgétaire sur 7 jours basée sur la consommation moyenne.
    """
    # Coût moyen des 7 derniers jours
    seven_days_ago = time.time() - 7 * 24 * 3600
    daily_costs = _safe_query(
        _SESSION_DB,
        "SELECT DATE(datetime(timestamp, 'unixepoch')) as day, "
        "SUM(estimated_cost_usd) as daily_cost "
        "FROM quota_snapshots WHERE timestamp > ? "
        "GROUP BY day ORDER BY day",
        (seven_days_ago,),
    )

    if not daily_costs:
        return {"avg_daily_cost": 0, "projected_7d": 0, "projected_30d": 0}

    avg_daily = sum(d["daily_cost"] or 0 for d in daily_costs) / len(daily_costs)

    return {
        "avg_daily_cost": round(avg_daily, 4),
        "projected_7d": round(avg_daily * 7, 2),
        "projected_30d": round(avg_daily * 30, 2),
        "daily_breakdown": daily_costs,
    }
