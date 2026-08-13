"""
api/routes/metrics.py — Routes API pour le Dashboard de Métriques V6.

Observabilité Data-Driven.

Expose les données de télémétrie agrégées depuis la base unifiée
moteur_runtime.db (core.runtime_db) :
- token_usage : consommation réelle par appel (tokens, coût facturé) — seule
  source de vérité pour l'argent et les tokens (#T294)
- sessions : sessions moteur (started_at REAL)
- quota_snapshots : quotas par canal (table clé/valeur, sans notion de coût)
- routing_decisions / model_elo_scores : routage et scores Elo

Endpoints :
- GET /api/metrics/telemetry  : Données agrégées pour le dashboard Chart.js
- GET /api/metrics/elo        : Scores Elo par modèle/domaine
- GET /api/metrics/routing     : Statistiques de routage
- GET /api/metrics/agents      : Performance par agent
"""

import asyncio
import contextvars
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


def _safe_query(db_path: str, query: str, params: tuple = ()) -> list | None:
    """
    Requête SQLite sécurisée sur la base unifiée.

    Retour :
    - une liste de lignes en cas de succès (vide si zéro ligne : mesure nulle) ;
    - None en cas de DÉFAUT DE CODE (colonne/table inexistante) : journalisé en
      error et remonté dans la réponse via `query_errors`. Un appelant ne doit
      jamais convertir None en 0 — ce serait exactement l'exception déguisée en
      mesure que cette correction supprime (#T294).
    """
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError as e:
        logger.error(f"[METRICS] Défaut de code : requête invalide — {e}")
        _record_query_error(query, str(e))
        return None
    except Exception as e:
        logger.warning(f"[METRICS] Erreur SQLite unifiée : {e}")
        return []


# Registre des défauts de requête détectés pendant une réponse (ContextVar :
# isolé par requête HTTP, vidé par _reset_query_errors à l'entrée de chaque
# endpoint). Additif : jamais présent dans la réponse si rien n'a échoué.
# default=None (jamais une liste mutable, cf. B039) : initialisation paresseuse
# dans _record_query_error.
_metrics_query_errors: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "metrics_query_errors", default=None
)


def _record_query_error(query: str, error: str) -> None:
    """Enregistre un défaut de requête pour la réponse en cours (liste créée à la demande)."""
    errors = _metrics_query_errors.get()
    if errors is None:
        errors = []
        _metrics_query_errors.set(errors)
    errors.append(f"{query} — {error}")


def _reset_query_errors() -> None:
    """Ouvre un registre d'erreurs de requête vierge pour la réponse en cours."""
    _metrics_query_errors.set([])


def _attach_query_errors(payload: dict) -> dict:
    """Ajoute `query_errors` (additif) si des requêtes ont échoué pendant la réponse."""
    errors = _metrics_query_errors.get() or []
    if errors:
        payload["query_errors"] = errors
    return payload


def _kpi_number(rows: list | None, key: str) -> int | float | None:
    """Extrait un KPI agrégé : nombre si la mesure est connue (0 si zéro ligne),
    None si la requête a échoué — un défaut de code ne doit jamais devenir un 0."""
    if rows is None:
        return None
    return (rows[0][key] or 0) if rows else 0


def _rows_or_empty(rows: list | None) -> list:
    """Normalise le retour de _safe_query pour les usages hors périmètre KPI : un
    échec (None) y vaut liste vide — l'erreur reste journalisée en error et
    remonte via query_errors."""
    return rows if rows is not None else []


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

    Doctrine #T294 : un champ dont la requête a échoué (défaut de code) reste
    null côté API et remonte dans `query_errors` — jamais un 0 menteur.
    """
    # Registre d'erreurs de requête vierge pour cette réponse (ContextVar isolée).
    _reset_query_errors()

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

    agents, agent_unassigned = _get_agent_stats(since_ts)

    result = {
        "period": period,
        "generated_at": datetime.now().isoformat(),
        "time_series": _get_time_series(since_ts),
        "agent_stats": agents,
        "model_stats": _get_model_stats(since_ts),
        "routing_stats": _get_routing_stats(since_ts),
        "kpis": _get_kpis(since_ts),
        "budget_forecast": _get_budget_forecast(),
    }
    if agent_unassigned:
        # agent_name est souvent NULL côté écrivains : distinguer « aucun agent
        # renseigné » d'un tableau vide, sans inventer de ligne « null ».
        result["agent_stats_unassigned_count"] = agent_unassigned
    return _attach_query_errors(result)


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
    [#T116][#T325] Coût USD par tâche réussie, agrégé par session — croise
    `token_usage.session_id` avec `sessions.status` (et non plus
    `model_elo_scores.wins` : cette colonne porte les succès des TIERS,
    `token_usage.model` des ids de modèles, l'intersection est vide).
    Voir `core.elo_scorer.get_cost_per_successful_task`.
    """
    try:
        from core.elo_scorer import get_cost_per_successful_task
        return {"cost_per_success": get_cost_per_successful_task()}
    except Exception as e:
        logger.warning(f"[METRICS] Erreur cost-per-success : {e}")
        return {"cost_per_success": {}}


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
    Performance réellement observée par modèle, croisée depuis deux sources.

    Rien n'est inventé : un champ absent en base reste `None` côté API, et l'IHM
    affiche « — » plutôt qu'un zéro trompeur.

    [#T300] Une TROISIÈME source existait ici — `routing_decisions`, pour la latence
    et le taux de succès. Elle a été retirée, et il ne faut pas la rétablir :

    - elle groupait sur `resolved_model`, colonne **vide sur 68/68 lignes** et vide
      par conception (le routage résout un TIER, pas un modèle — voir la docstring
      de `core/routing_metrics.record_routing_decision`, #T296). La source ne
      rendait donc jamais rien, et c'est la seule raison pour laquelle elle était
      inoffensive ;
    - sa latence est celle du ROUTAGE, pas d'un appel de modèle : mesuré le 11/08,
      de 0,4 ms à 187 659 ms selon que le slow-path LLM de classification est
      invoqué. L'ancien code l'écrasait par-dessus la latence Elo avec le
      commentaire « mesure directe, elle prime » — c'eût été présenter le temps
      passé à CHOISIR un modèle comme le temps mis par ce modèle à répondre ;
    - son taux de succès venait de `routing_decisions.success`, colonne déclarée
      `DEFAULT 1` et **jamais écrite** : `record_routing_decision()` n'a même pas
      ce paramètre. Elle vaut 1 sur 68/68 lignes. Tout taux calculé dessus vaut
      100 % par construction.

    Autrement dit, réparer `resolved_model` sans toucher au reste aurait fait
    apparaître d'un coup des latences fausses et 100 % de succès partout — le
    motif « compteur qui ment » de #T294, en pire, parce que le chiffre aurait
    été plausible. La latence par modèle n'a aujourd'hui AUCUNE source vraie
    (`model_elo_scores.avg_latency_ms` est renseignée sur 0 ligne sur 31) : elle
    reste donc `None`, et l'IHM affiche « — ». C'est un trou assumé, pas un oubli.
    """
    observed: dict[str, dict] = {}

    # 1. Consommation réelle (token_usage) — appels, tokens, coût facturé.
    for row in _rows_or_empty(_safe_query(
        _SESSION_DB,
        "SELECT model, COUNT(*) AS calls, SUM(total_tokens) AS total_tokens, "
        "SUM(cost_usd) AS cost_usd "
        "FROM token_usage WHERE timestamp > ? GROUP BY model",
        (since_ts,),
    )):
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
    for row in _rows_or_empty(_safe_query(
        _SESSION_DB,
        "SELECT model_name, SUM(total_matches) AS matches, SUM(wins) AS wins, "
        "SUM(losses) AS losses, AVG(elo_score) AS elo, AVG(avg_latency_ms) AS latency "
        "FROM model_elo_scores GROUP BY model_name",
    )):
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

    # [#T300] Pas de troisième source : voir la docstring. `routing_decisions` ne
    # mesure ni la latence d'un modèle ni son taux de succès, et l'y chercher a
    # produit deux champs faux pendant tout ce temps — masqués par une colonne de
    # groupement vide. `success_rate` et `avg_latency_ms` restent donc à None tant
    # qu'une source honnête n'existe pas (latence par appel LLM : non persistée).

    return sorted(observed.values(), key=lambda e: (e.get("calls") or 0), reverse=True)


def _load_runs(limit: int) -> list:
    """Charge les derniers comparatifs déclenchés depuis l'IHM, avec leurs résultats."""
    runs = _rows_or_empty(_safe_query(
        _SESSION_DB,
        "SELECT run_id, prompt, system_prompt, created_at, finished_at, status "
        "FROM benchmark_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ))
    for run in runs:
        run["results"] = _rows_or_empty(_safe_query(
            _SESSION_DB,
            "SELECT model, status, latency_ms, response_text, response_chars, "
            "prompt_tokens, completion_tokens, cost_usd, error_message "
            "FROM benchmark_results WHERE run_id = ? ORDER BY latency_ms",
            (run["run_id"],),
        ))
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
    _reset_query_errors()
    return _attach_query_errors({
        "period": period,
        "generated_at": datetime.now().isoformat(),
        "observed": _observed_performance(since_ts),
        "runs": _load_runs(limit),
    })


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
    _reset_query_errors()
    return _attach_query_errors(_get_routing_stats(time.time() - 7 * 24 * 3600))


@router.get("/api/metrics/agents")
async def get_agent_stats_endpoint():
    """Performance détaillée par agent (agent_name ; NULL exclus du tableau et
    comptés via `agent_stats_unassigned_count` dans /api/metrics/telemetry)."""
    _reset_query_errors()
    agents, _ = _get_agent_stats(time.time() - 7 * 24 * 3600)
    return _attach_query_errors(agents if agents is not None else [])


# ──────────────────────────────────────────────────────────────────
# Fonctions d'agrégation internes
# ──────────────────────────────────────────────────────────────────

def _get_time_series(since_ts: float) -> dict | None:
    """
    Séries temporelles pour les graphiques Chart.js.

    Agrège tokens et coûts par tranche horaire depuis token_usage — seule source
    de vérité (timestamp, total_tokens, cost_usd). Retourne None si la requête a
    échoué (défaut de code) : le graphique ne doit pas afficher « aucune donnée
    sur la période » quand la mesure est cassée.
    """
    rows = _safe_query(
        _SESSION_DB,
        "SELECT timestamp, total_tokens, cost_usd "
        "FROM token_usage WHERE timestamp > ? ORDER BY timestamp",
        (since_ts,),
    )
    if rows is None:
        return None

    # Agrégation par heure
    hourly = {}
    for row in rows:
        ts = row.get("timestamp", 0)
        hour_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:00")
        if hour_key not in hourly:
            hourly[hour_key] = {
                "tokens": 0,
                "cost_usd": 0.0,
                "data_points": 0,
            }
        hourly[hour_key]["tokens"] += row.get("total_tokens") or 0
        hourly[hour_key]["cost_usd"] += row.get("cost_usd") or 0
        hourly[hour_key]["data_points"] += 1

    # Formatage pour Chart.js (labels + datasets)
    labels = sorted(hourly.keys())
    return {
        "labels": labels,
        "tokens": [hourly[k]["tokens"] for k in labels],
        "costs": [round(hourly[k]["cost_usd"], 4) for k in labels],
    }


def _get_agent_stats(since_ts: float) -> tuple[list | None, int]:
    """
    Stats par agent depuis token_usage — agrégées sur la colonne agent_name.

    Distingue trois situations, jamais confondues :
    - requête en échec (défaut de code) : liste None, l'erreur remonte via query_errors ;
    - zéro ligne sur la période : liste vide ;
    - appels dont agent_name est NULL (non renseigné par les écrivains) : exclus
      du tableau, mais comptés dans le second élément, exposé additivement via
      `agent_stats_unassigned_count` dans la réponse.
    """
    rows = _safe_query(
        _SESSION_DB,
        "SELECT agent_name as agent, "
        "COUNT(*) as total_calls, "
        "SUM(CASE WHEN prompt_tokens > 0 THEN 1 ELSE 0 END) as successes, "
        "SUM(prompt_tokens) as total_input, "
        "SUM(completion_tokens) as total_output, "
        "AVG(prompt_tokens + completion_tokens) as avg_tokens "
        "FROM token_usage WHERE timestamp > ? "
        "GROUP BY agent_name ORDER BY total_calls DESC",
        (since_ts,),
    )
    if rows is None:
        return None, 0

    agents = []
    unassigned = 0
    for row in rows:
        if not row.get("agent"):
            # agent_name NULL : non renseigné par les écrivains — pas un agent nommé.
            unassigned += row.get("total_calls") or 0
            continue
        total = row.get("total_calls", 1) or 1
        row["success_rate"] = round(
            (row.get("successes", 0) / total) * 100, 1
        )
        agents.append(row)

    return agents, unassigned


def _get_model_stats(since_ts: float) -> dict | None:
    """Stats par modèle avec Elo intégré (token_usage, requête déjà correcte)."""
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
    if rows is None:
        # Défaut de code : le tableau ne doit pas afficher « aucun usage » quand
        # la mesure est cassée — l'erreur remonte via query_errors.
        return None
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

    # Répartition fast-path vs slow-path.
    #
    # [#T321] La colonne qui porte cette information est `fast_path_used` (0/1),
    # PAS `routing_type`. Ce dernier existe bien, mais ne vaut jamais
    # 'fast_path' : ses valeurs réelles en prod sont 'casual_chat', 'ha_direct',
    # 'ha_deterministic', 'default', 'executor_direct', 'sysadmin_direct',
    # 'planner_pour_approbation'. La comparaison rendait donc TOUJOURS 0, et la
    # vue Observabilité affichait 100 % de slow-path — alors que 44 % des
    # décisions empruntent le fast-path (mesuré le 12/08 : 27 sur 61 en 7 jours,
    # 259 sur 501 au total).
    #
    # Défaut cousin de #T294, mais hors de sa portée : la requête est
    # syntaxiquement VALIDE, donc `_safe_query` ne peut rien signaler. Un 0 issu
    # d'une valeur qui n'existe pas est indiscernable d'un vrai 0 — sauf en
    # regardant la donnée.
    #
    # COALESCE : les lignes sans `fast_path_used` comptent comme slow-path, ce
    # qui garantit fast_path + slow_path == total (un `NULL != 1` en SQL rend
    # NULL, donc ni l'un ni l'autre, et les deux compteurs ne bouclaient plus).
    path_stats = _safe_query(
        _ROUTING_DB,
        "SELECT "
        "SUM(CASE WHEN fast_path_used = 1 THEN 1 ELSE 0 END) as fast_path, "
        "SUM(CASE WHEN COALESCE(fast_path_used, 0) != 1 THEN 1 ELSE 0 END) as slow_path, "
        "COUNT(*) as total "
        "FROM routing_decisions WHERE timestamp > ?",
        (since_ts,),
    )

    return {
        "categories": categories,
        "path_distribution": path_stats[0] if path_stats else {},
    }


def _get_kpis(since_ts: float) -> dict:
    """KPIs bannière (coût total, sessions, tokens).

    Source unique : token_usage pour l'argent et les tokens (la même qui alimente
    le tableau « Usage par modèle »), sessions.started_at (REAL) pour les
    sessions. Doctrine du fichier : une requête en échec rend None, jamais 0 —
    un 0 serait une exception déguisée en mesure (#T294).
    """
    # Coût réellement facturé (token_usage) — seule source de vérité de l'argent.
    billing = _safe_query(
        _SESSION_DB,
        "SELECT SUM(cost_usd) as total_cost "
        "FROM token_usage WHERE timestamp > ?",
        (since_ts,),
    )

    # Nombre de sessions : started_at est REAL — comparaison numérique directe.
    # (comparer un ISO-8601 à un REAL rendrait 0 en SQLite : nombre < texte).
    sessions = _safe_query(
        _SESSION_DB,
        "SELECT COUNT(*) as count FROM sessions WHERE started_at > ?",
        (since_ts,),
    )

    # Tokens totaux depuis token_usage (inchangé : déjà correct).
    tokens = _safe_query(
        _SESSION_DB,
        "SELECT SUM(prompt_tokens + completion_tokens) as total "
        "FROM token_usage WHERE timestamp > ?",
        (since_ts,),
    )

    total_cost = _kpi_number(billing, "total_cost")
    return {
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "total_sessions": _kpi_number(sessions, "count"),
        "total_tokens": _kpi_number(tokens, "total"),
    }


def _get_budget_forecast() -> dict | None:
    """
    Projection budgétaire sur 7 jours basée sur la consommation moyenne.

    Agrégation journalière de token_usage (cost_usd) — même source que le KPI
    coût et le tableau « Usage par modèle ». None si la requête a échoué : les
    prévisions ne doivent pas valoir $0 quand la mesure est cassée.
    """
    # Coût moyen des 7 derniers jours
    seven_days_ago = time.time() - 7 * 24 * 3600
    daily_costs = _safe_query(
        _SESSION_DB,
        "SELECT DATE(datetime(timestamp, 'unixepoch')) as day, "
        "SUM(cost_usd) as daily_cost "
        "FROM token_usage WHERE timestamp > ? "
        "GROUP BY day ORDER BY day",
        (seven_days_ago,),
    )
    if daily_costs is None:
        return None

    if not daily_costs:
        return {"avg_daily_cost": 0, "projected_7d": 0, "projected_30d": 0}

    avg_daily = sum(d["daily_cost"] or 0 for d in daily_costs) / len(daily_costs)

    return {
        "avg_daily_cost": round(avg_daily, 4),
        "projected_7d": round(avg_daily * 7, 2),
        "projected_30d": round(avg_daily * 30, 2),
        "daily_breakdown": daily_costs,
    }
