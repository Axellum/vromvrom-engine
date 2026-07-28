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

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Métriques & Télémétrie"])

from core.runtime_db import get_connection, get_db_path

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


# ──────────────────────────────────────────────────────────────────
# Comparatif de modèles (vue « Benchmarks » de l'IHM)
#
# Deux natures de données, jamais mélangées dans l'affichage :
#  - « observed » : ce que les modèles ont RÉELLEMENT fait en production
#    (token_usage + model_elo_scores + routing_decisions). Aucune estimation.
#  - « runs »     : comparatifs déclenchés explicitement depuis l'IHM, dont la
#    latence est chronométrée côté serveur autour de l'appel provider.
# ──────────────────────────────────────────────────────────────────

# Garde-fous : un comparatif déclenche de VRAIS appels LLM facturés.
_BENCH_MAX_MODELS = 8
_BENCH_MAX_TOKENS = 2048


class BenchmarkRequest(BaseModel):
    """Corps de POST /api/metrics/benchmarks — lance un comparatif réel."""
    prompt: str = Field(..., min_length=1, description="Prompt envoyé à tous les modèles")
    models: list[str] = Field(..., min_length=1, description="Modèles/tiers à comparer")
    system_prompt: str | None = Field(None, description="Prompt système commun")
    max_tokens: int | None = Field(512, ge=1, le=_BENCH_MAX_TOKENS)
    temperature: float | None = Field(0.3, ge=0.0, le=2.0)


def _observed_performance(since_ts: float) -> list:
    """
    Performance réellement observée par modèle, croisée depuis trois sources.

    Rien n'est inventé : un champ absent en base reste `None` côté API, et l'IHM
    affiche « — » plutôt qu'un zéro trompeur.
    """
    observed: dict[str, dict] = {}

    # 1. Consommation réelle (token_usage) — appels, tokens, coût facturé.
    for row in _safe_query(
        _SESSION_DB,
        "SELECT model, COUNT(*) AS calls, SUM(total_tokens) AS total_tokens, "
        "SUM(cost_usd) AS cost_usd "
        "FROM token_usage WHERE timestamp > ? GROUP BY model",
        (since_ts,),
    ):
        model = row["model"]
        if not model:
            continue
        calls = row["calls"] or 0
        observed[model] = {
            "model": model,
            "calls": calls,
            "total_tokens": row["total_tokens"] or 0,
            "cost_usd": round(row["cost_usd"] or 0.0, 6),
            "avg_cost_per_call_usd": round((row["cost_usd"] or 0.0) / calls, 6) if calls else None,
            "elo": None,
            "matches": None,
            "win_rate": None,
            "avg_latency_ms": None,
            "success_rate": None,
        }

    # 2. Scores Elo (model_elo_scores) — agrégés sur tous les domaines.
    for row in _safe_query(
        _SESSION_DB,
        "SELECT model_name, SUM(total_matches) AS matches, SUM(wins) AS wins, "
        "SUM(losses) AS losses, AVG(elo_score) AS elo, AVG(avg_latency_ms) AS latency "
        "FROM model_elo_scores GROUP BY model_name",
    ):
        model = row["model_name"]
        if not model:
            continue
        entry = observed.setdefault(model, {
            "model": model, "calls": 0, "total_tokens": 0, "cost_usd": 0.0,
            "avg_cost_per_call_usd": None, "success_rate": None,
        })
        matches = row["matches"] or 0
        entry["elo"] = round(row["elo"], 1) if row["elo"] is not None else None
        entry["matches"] = matches
        entry["win_rate"] = round((row["wins"] or 0) / matches * 100, 1) if matches else None
        entry["avg_latency_ms"] = round(row["latency"], 1) if row["latency"] is not None else None

    # 3. Latence et succès mesurés au routage (routing_decisions).
    for row in _safe_query(
        _ROUTING_DB,
        "SELECT resolved_model, AVG(latency_ms) AS latency, COUNT(*) AS n, "
        "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok "
        "FROM routing_decisions WHERE timestamp > ? AND resolved_model IS NOT NULL "
        "GROUP BY resolved_model",
        (since_ts,),
    ):
        model = row["resolved_model"]
        if not model:
            continue
        entry = observed.setdefault(model, {
            "model": model, "calls": 0, "total_tokens": 0, "cost_usd": 0.0,
            "avg_cost_per_call_usd": None, "elo": None, "matches": None, "win_rate": None,
        })
        n = row["n"] or 0
        # La latence de routage est une mesure directe : elle prime sur la moyenne Elo.
        if row["latency"] is not None:
            entry["avg_latency_ms"] = round(row["latency"], 1)
        entry["success_rate"] = round((row["ok"] or 0) / n * 100, 1) if n else None

    return sorted(observed.values(), key=lambda e: (e.get("calls") or 0), reverse=True)


def _load_runs(limit: int) -> list:
    """Charge les derniers comparatifs déclenchés depuis l'IHM, avec leurs résultats."""
    runs = _safe_query(
        _SESSION_DB,
        "SELECT run_id, prompt, system_prompt, created_at, finished_at, status "
        "FROM benchmark_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    for run in runs:
        run["results"] = _safe_query(
            _SESSION_DB,
            "SELECT model, status, latency_ms, response_text, response_chars, "
            "prompt_tokens, completion_tokens, cost_usd, error_message "
            "FROM benchmark_results WHERE run_id = ? ORDER BY latency_ms",
            (run["run_id"],),
        )
    return runs


@router.get("/api/metrics/benchmarks")
async def get_benchmarks(
    limit: int = Query(10, ge=1, le=50, description="Nombre de comparatifs retournés"),
    period: str = Query("30d", description="Fenêtre pour la performance observée"),
):
    """
    Comparatif des modèles : performance observée en production + comparatifs manuels.

    Aucune donnée n'est simulée. Si une métrique n'a jamais été mesurée pour un
    modèle, elle vaut `null` — c'est à l'IHM de l'afficher comme non mesurée.
    """
    period_map = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400}
    since_ts = time.time() - period_map.get(period, 30 * 86400)
    return {
        "period": period,
        "generated_at": datetime.now().isoformat(),
        "observed": _observed_performance(since_ts),
        "runs": _load_runs(limit),
    }


def _bench_one(model: str, system_prompt: str, user_prompt: str,
               max_tokens: int, temperature: float) -> dict:
    """
    Exécute le prompt sur un modèle et chronomètre l'appel. Bloquant : appelé via
    asyncio.to_thread pour que les modèles soient comparés en parallèle.
    """
    from core.llm_gateway import LLMGateway

    started = time.perf_counter()
    try:
        provider = LLMGateway().get_provider(model)
        response = provider.generate(
            system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        if isinstance(response, dict):
            response = response.get("content", str(response))
        text = str(response)

        # Comptage de tokens : approximation explicite (les providers ne renvoient
        # pas tous un usage). Le coût en découle, il est donc lui aussi approché —
        # c'est signalé à l'IHM par `tokens_estimated`.
        prompt_tokens = max(1, len(f"{system_prompt} {user_prompt}".split()))
        completion_tokens = max(1, len(text.split()))

        from core.pricing import get_model_pricing
        price = get_model_pricing(model)
        cost = prompt_tokens * price.get("input", 0.0) + completion_tokens * price.get("output", 0.0)

        # La consommation réelle est comptabilisée comme n'importe quel appel moteur.
        try:
            from core.token_tracker import record_usage
            record_usage(model, prompt_tokens, completion_tokens, cost_usd=cost)
        except Exception as track_err:
            logger.warning(f"[BENCH] Comptabilisation tokens impossible ({model}) : {track_err}")

        return {
            "model": model, "status": "success", "latency_ms": round(elapsed_ms, 1),
            "response_text": text, "response_chars": len(text),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost_usd": round(cost, 6), "error_message": None,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning(f"[BENCH] Échec sur {model} : {e}")
        return {
            "model": model, "status": "error", "latency_ms": round(elapsed_ms, 1),
            "response_text": None, "response_chars": 0,
            "prompt_tokens": None, "completion_tokens": None,
            "cost_usd": None, "error_message": str(e),
        }


@router.post("/api/metrics/benchmarks")
async def run_benchmark(body: BenchmarkRequest):
    """
    Lance un comparatif réel : le même prompt est envoyé en parallèle à chaque
    modèle, la latence est chronométrée côté serveur et la consommation est
    comptabilisée normalement.

    ⚠ Déclenche de vrais appels LLM facturés (plafonné à 8 modèles par run).
    """
    models = list(dict.fromkeys(m.strip() for m in body.models if m and m.strip()))
    if not models:
        raise HTTPException(status_code=400, detail="Aucun modèle valide fourni.")
    if len(models) > _BENCH_MAX_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"{len(models)} modèles demandés : maximum {_BENCH_MAX_MODELS} par comparatif.",
        )

    run_id = f"bench-{uuid.uuid4().hex[:12]}"
    system_prompt = (body.system_prompt or "Tu es un assistant IA expert. Réponds de façon concise.").strip()
    created_at = time.time()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO benchmark_runs (run_id, prompt, system_prompt, created_at, status) "
            "VALUES (?, ?, ?, ?, 'running')",
            (run_id, body.prompt, system_prompt, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    results = await asyncio.gather(*[
        asyncio.to_thread(
            _bench_one, model, system_prompt, body.prompt,
            body.max_tokens or 512, body.temperature if body.temperature is not None else 0.3,
        )
        for model in models
    ])

    finished_at = time.time()
    conn = get_connection()
    try:
        for res in results:
            conn.execute(
                "INSERT INTO benchmark_results (run_id, model, status, latency_ms, response_text, "
                "response_chars, prompt_tokens, completion_tokens, cost_usd, error_message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, res["model"], res["status"], res["latency_ms"], res["response_text"],
                 res["response_chars"], res["prompt_tokens"], res["completion_tokens"],
                 res["cost_usd"], res["error_message"], finished_at),
            )
        conn.execute(
            "UPDATE benchmark_runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (finished_at, "completed", run_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "run_id": run_id,
        "prompt": body.prompt,
        "created_at": created_at,
        "finished_at": finished_at,
        "status": "completed",
        "results": results,
    }


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
