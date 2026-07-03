"""
api/routes/billing.py — Routes API Tokens & Billing du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/tokens, /api/tokens/db, /api/tokens/reset, 
           /api/collect-cli-tokens, /api/ide-conversations/*,
           /api/billing/*, /api/sessions/*

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Tokens & Billing"])


# ──────────────────────────────────────────────────────────────────
# Tokens — Résumé et statistiques
# ──────────────────────────────────────────────────────────────────

@router.get("/api/tokens")
def get_tokens():
    """Retourne les statistiques de consommation de tokens de la session courante."""
    try:
        from core.token_tracker import get_global_summary
        from core.session_history import get_session_stats
        return {
            "global": get_global_summary(),
            "session": get_session_stats(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tokens/db")
def get_tokens_db(
    mode: str = "stats",
    since_hours: int = None,
    model: str = None,
    session_id: str = None,
    limit: int = 100,
):
    """
    Requête les tokens depuis la BDD SQLite.

    Modes :
    - stats   : Agrégations (total, par modèle, par canal)
    - history : Historique des appels LLM individuels

    Params optionnels :
    - since_hours : Filtrer par fenêtre temporelle (ex: 24)
    - model       : Filtrer par nom de modèle (ex: deepseek-chat)
    - session_id  : Filtrer par session
    - limit       : Nombre max d'entrées en mode history (défaut 100)
    """
    from core.session_history import get_token_stats, get_token_history
    if mode == "history":
        return get_token_history(limit=limit, session_id=session_id, model_filter=model)
    return get_token_stats(since_hours=since_hours, model_filter=model)


@router.post("/api/tokens/reset")
def reset_tokens():
    """Réinitialise les compteurs de tokens de la session en cours."""
    try:
        from core.token_tracker import reset_usage
        reset_usage()
        return {"message": "Compteurs de tokens réinitialisés."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/collect-cli-tokens")
async def collect_cli_tokens():
    """Déclenche manuellement la collecte des tokens CLI (Antigravity + Claude)."""
    try:
        from core.cli_token_collector import CLITokenCollector
        collector = CLITokenCollector()
        result = await collector.scan_and_persist()
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"[CLI Tokens] Erreur collecte : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────
# Conversations IDE
# ──────────────────────────────────────────────────────────────────

@router.get("/api/ide-conversations")
def api_get_ide_conversations(limit: int = 200, source: str = None):
    """Retourne les conversations IDE (Antigravity + Claude CLI) persistées en BDD."""
    from core.session_history import get_ide_conversations
    return {"conversations": get_ide_conversations(limit=limit, source_filter=source)}


@router.get("/api/ide-conversations/stats")
def api_get_ide_conversations_stats():
    """Retourne les statistiques agrégées des conversations IDE."""
    from core.session_history import get_ide_conversations_stats
    return get_ide_conversations_stats()


# ──────────────────────────────────────────────────────────────────
# Billing — Sync & historique
# ──────────────────────────────────────────────────────────────────

@router.post("/api/billing/sync")
async def billing_sync():
    """Lance la synchronisation de facturation (Antigravity + LLM Providers)."""
    try:
        from core.billing_sync import run_billing_sync
        result = await run_billing_sync()
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/billing/sync/status")
def billing_sync_status():
    """Retourne le statut de la dernière synchronisation de facturation."""
    try:
        from core.billing_sync import get_sync_status
        return get_sync_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/billing/history")
def billing_history():
    """Retourne l'historique de facturation."""
    try:
        from core.session_history import get_billing_history
        return get_billing_history()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/billing/launch-chrome")
async def billing_launch_chrome():
    """Lance Chrome pour la consultation des factures Cloud."""
    try:
        from core.billing_sync import launch_chrome_billing
        result = await launch_chrome_billing()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────
# Sessions historiques
# ──────────────────────────────────────────────────────────────────

@router.get("/api/sessions", tags=["Historique"])
def get_sessions(limit: int = 50):
    """Retourne les N dernières sessions du moteur (historique)."""
    from core.session_history import get_sessions
    return {"sessions": get_sessions(limit=limit)}


@router.get("/api/sessions/stats", tags=["Historique"])
def get_sessions_stats():
    """Retourne les statistiques agrégées des sessions du moteur."""
    from core.session_history import get_session_stats as get_sessions_stats
    return get_sessions_stats()


@router.get("/api/sessions/{session_id}", tags=["Historique"])
def get_session_detail(session_id: str):
    """Retourne le détail complet d'une session spécifique."""
    from core.session_history import get_session_detail as get_session_by_id
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' introuvable.")
    return session


# ──────────────────────────────────────────────────────────────────
# Télémétrie OpenTelemetry OTLP (Axe 2 — OhMyToken local)
# ──────────────────────────────────────────────────────────────────

# Cache des sessions OTLP cumulatives pour éviter de sur-compter les tokens
# Clé : (start_time_nano, model, token_type) -> valeur cumulée précédente
_otlp_cumulative_cache = {}

@router.post("/api/otlp/v1/metrics", tags=["Télémétrie OTLP"])
async def receive_otlp_metrics(payload: dict):
    """
    Micro-collecteur OTLP (OpenTelemetry Protocol) JSON/HTTP temps réel.
    Reçoit les métriques de tokens poussées par Claude Code CLI ou d'autres agents.
    """
    global _otlp_cumulative_cache
    try:
        from core.session_history import record_token_usage
        import time

        # Guardrail anti fuite mémoire
        if len(_otlp_cumulative_cache) > 2000:
            _otlp_cumulative_cache.clear()

        resource_metrics = payload.get("resourceMetrics", [])
        records_added = 0
        service_name = "unknown_service"
        
        for rm in resource_metrics:
            resource_attrs = {
                attr.get("key"): attr.get("value", {}).get("stringValue")
                for attr in rm.get("resource", {}).get("attributes", [])
            }
            service_name = resource_attrs.get("service.name", service_name)

            scope_metrics = rm.get("scopeMetrics", [])
            for sm in scope_metrics:
                metrics = sm.get("metrics", [])
                for metric in metrics:
                    metric_name = metric.get("name", "")
                    
                    # Claude Code pousse des métriques de type claude_code.token.usage
                    if "token" in metric_name.lower():
                        sum_data = metric.get("sum", {}) or metric.get("gauge", {})
                        data_points = sum_data.get("dataPoints", [])
                        
                        # Accumulateurs temporaires par modèle pour cette requête
                        model_tokens = {}
                        
                        for dp in data_points:
                            attrs = {
                                attr.get("key"): attr.get("value", {}).get("stringValue")
                                for attr in dp.get("attributes", [])
                            }
                            
                            model = attrs.get("model") or attrs.get("model_name") or "claude-code-unknown"
                            token_type = attrs.get("token_type") or attrs.get("type") or "prompt"
                            
                            val = int(dp.get("asInt", 0) or dp.get("asDouble", 0))
                            if val <= 0:
                                continue
                                
                            start_time = dp.get("startTimeUnixNano") or "0"
                            cache_key = (start_time, model, token_type)
                            
                            # Calcul du delta en cas de métriques cumulatives (monotoniques)
                            if start_time != "0" and cache_key in _otlp_cumulative_cache:
                                prev_val = _otlp_cumulative_cache[cache_key]
                                delta = val - prev_val
                                _otlp_cumulative_cache[cache_key] = val
                                if delta <= 0:
                                    continue
                                actual_val = delta
                            else:
                                if start_time != "0":
                                    _otlp_cumulative_cache[cache_key] = val
                                actual_val = val
                                
                            if model not in model_tokens:
                                model_tokens[model] = {"input": 0, "output": 0}
                                
                            if token_type in ("input", "prompt", "cache_read", "cache_creation"):
                                model_tokens[model]["input"] += actual_val
                            elif token_type in ("output", "completion"):
                                model_tokens[model]["output"] += actual_val
                        
                        # Persister les deltas calculés
                        for model, counts in model_tokens.items():
                            if counts["input"] > 0 or counts["output"] > 0:
                                channel = "claude_cli" if "claude" in service_name.lower() or "claude" in model.lower() else "antigravity_ide"
                                
                                # Claude 3.5 Sonnet: $3/M input, $15/M output (estimations)
                                input_rate = 3.0 / 1_000_000 if "claude" in model.lower() else 0.075 / 1_000_000
                                output_rate = 15.0 / 1_000_000 if "claude" in model.lower() else 0.30 / 1_000_000
                                cost = (counts["input"] * input_rate) + (counts["output"] * output_rate)
                                
                                from core.async_db_serializer import AsyncDBSerializer
                                serializer = AsyncDBSerializer.get_instance()
                                await serializer.execute(lambda: record_token_usage(
                                    model=model,
                                    prompt_tokens=counts["input"],
                                    completion_tokens=counts["output"],
                                    cost_usd=round(cost, 6),
                                    session_id=f"otlp_{service_name}_{int(time.time())}",
                                    channel=channel,
                                    agent_name=service_name
                                ))
                                records_added += 1
                                
        if records_added > 0:
            logger.info(f"[OTLP] Reçu et enregistré {records_added} métriques de tokens depuis {service_name}")
            
        return {"status": "success", "processed_records": records_added}
        
    except Exception as e:
        logger.error(f"[OTLP] Erreur traitement metrics : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/otlp/v1/traces", tags=["Télémétrie OTLP"])
async def receive_otlp_traces(payload: dict):
    """Endpoint passif (no-op) pour éviter les erreurs HTTP 404 sur les traces OTel."""
    return {"status": "ignored"}

@router.post("/api/otlp/v1/logs", tags=["Télémétrie OTLP"])
async def receive_otlp_logs(payload: dict):
    """Endpoint passif (no-op) pour éviter les erreurs HTTP 404 sur les logs OTel."""
    return {"status": "ignored"}

