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
from pydantic import BaseModel

from api.services.billing_service import billing_sync_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Tokens & Billing"])


# ──────────────────────────────────────────────────────────────────
# Tokens — Résumé et statistiques
# ──────────────────────────────────────────────────────────────────

@router.get("/api/tokens")
def get_tokens():
    """Retourne les statistiques de consommation de tokens de la session courante."""
    try:
        from core.session_history import get_session_stats
        from core.token_tracker import get_global_summary
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
    from core.session_history import get_token_history, get_token_stats
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
# Comptes — Vue agrégée pour la page "Comptes API" de l'IHM
# ──────────────────────────────────────────────────────────────────

# Métadonnées statiques (libellé, lien admin, type de plan) par provider.
# Pas de donnée live ici — uniquement pour habiller les entrées de
# get_quota_summary() et fournir un lien direct même pour les providers
# non suivis en quota (pas de scraping/API de solde disponible pour eux).
_PROVIDER_DASHBOARDS: dict[str, dict[str, str]] = {
    "ollama": {"label": "Ollama (local)", "dashboard_url": "", "plan": "Local — gratuit"},
    "lmstudio": {"label": "LM Studio (local)", "dashboard_url": "", "plan": "Local — gratuit"},
    "gemini-free": {"label": "Google Gemini (GCP)", "dashboard_url": "https://console.cloud.google.com/billing", "plan": "Free Tier + GCP"},
    "deepseek-free": {"label": "DeepSeek", "dashboard_url": "https://platform.deepseek.com/usage", "plan": "API pay-as-you-go"},
    "anthropic-claude-haiku": {"label": "Anthropic (budget global)", "dashboard_url": "https://console.anthropic.com/settings/cost", "plan": "API pay-as-you-go"},
}

# Providers connus du moteur (core/llm_gateway.py) mais absents de
# get_quota_summary() : pas de suivi de quota/solde en direct aujourd'hui,
# juste un lien vers la console d'admin pour vérification manuelle.
_UNTRACKED_PROVIDER_LINKS: dict[str, dict[str, str]] = {
    "claude-subscription": {"label": "Claude.ai (abonnement)", "dashboard_url": "https://claude.ai/settings/usage", "plan": "Abonnement mensuel"},
    "gemini-subscription": {"label": "Gemini (abonnement Google AI)", "dashboard_url": "https://gemini.google.com/app", "plan": "Abonnement mensuel"},
    "mistral": {"label": "Mistral AI", "dashboard_url": "https://console.mistral.ai/usage", "plan": "API pay-as-you-go — pas d'API de solde documentée, dashboard email/mot de passe standard"},
    "cohere": {"label": "Cohere", "dashboard_url": "https://dashboard.cohere.com/billing", "plan": "Trial Key gratuite (Command R/R+) — jamais utilisée par le moteur à ce jour, pas de coût"},
    "cerebras": {"label": "Cerebras", "dashboard_url": "https://cloud.cerebras.ai/", "plan": "Free Tier"},
    "dashscope": {
        "label": "Alibaba DashScope (Coding Plan)",
        "dashboard_url": "https://modelstudio.console.alibabacloud.com/",
        "plan": "Coding Plan Lite — forfait requêtes (1200/5h, 9000/sem, 18000/mois), pas d'API de solde",
    },
    "zhipu": {"label": "Zhipu AI / Z.ai (GLM)", "dashboard_url": "https://open.bigmodel.cn/usercenter/financial", "plan": "API pay-as-you-go — dashboard en chinois, login téléphone/WeChat uniquement"},
    "openrouter": {"label": "OpenRouter", "dashboard_url": "https://openrouter.ai/credits", "plan": "API pay-as-you-go"},
    "minimax": {"label": "MiniMax", "dashboard_url": "https://platform.minimaxi.com/console", "plan": "API pay-as-you-go — dashboard en chinois, login téléphone/WeChat uniquement"},
    "grok": {"label": "xAI (Grok)", "dashboard_url": "https://console.x.ai/", "plan": "API pay-as-you-go"},
    "github": {"label": "GitHub Models", "dashboard_url": "https://github.com/settings/billing", "plan": "Free Tier"},
}


def _status_for(name: str, q: dict) -> str:
    """Statut lisible : dispo locale pour ollama/lmstudio, quota sinon."""
    if name in ("ollama", "lmstudio"):
        return "active" if q.get("available") else "offline"
    return "active" if q.get("available") else "quota_exceeded"


class ManualBalanceBody(BaseModel):
    provider: str
    balance_usd: float
    note: str = ""


@router.post("/api/billing/manual")
def set_manual_provider_balance(body: ManualBalanceBody):
    """
    Enregistre un solde saisi à la main pour un provider sans API de solde
    connue (Mistral, Cohere, Cerebras, Zhipu, MiniMax, xAI, GitHub...). Le
    solde est réaffiché dans /api/billing avec un statut "manuel" et
    l'horodatage de la saisie, tant qu'une vraie API n'existe pas pour ce
    provider.
    """
    from core.token_tracker import set_manual_balance
    set_manual_balance(body.provider, body.balance_usd, body.note)
    return {"status": "ok", "provider": body.provider, "balance_usd": body.balance_usd}


@router.get("/api/billing")
async def get_billing_accounts():
    """
    Vue agrégée "Comptes API" consommée par ihm-v2/src/api/accounts.ts::fetchAccounts.

    Croise les quotas gratuits temps réel (core.budget_guard.get_quota_summary,
    déjà exposés séparément sous /api/backlog/quota) avec les soldes réels
    scrapés (core.token_tracker.get_global_summary()["real_billing"], alimentés
    par la sync GCP/Claude) et une table statique de liens d'admin par provider.
    Les providers non suivis en quota apparaissent quand même, avec un lien
    d'admin mais sans donnée de conso (status "non_suivi").
    """
    try:
        import asyncio as _asyncio
        from datetime import datetime

        from core.budget_guard import BudgetGuard
        from core.gcp_oauth_client import get_gcp_client
        from core.provider_balances import (
            fetch_deepseek_balance_usd,
            fetch_minimax_token_plan_status,
            fetch_openrouter_credits,
            fetch_openrouter_key_info,
            fetch_xai_balance_usd,
        )
        from core.session_history import get_token_stats
        from core.token_tracker import get_global_summary, get_manual_balances

        guard = BudgetGuard()
        gcp_client = get_gcp_client()
        gcp_available = gcp_client.available
        (
            quota_summary, deepseek_live, openrouter_live, openrouter_key,
            gcp_month_cost, gcp_credits, minimax_token_plan, xai_balance,
        ) = await _asyncio.gather(
            guard.get_quota_summary(),
            fetch_deepseek_balance_usd(),
            fetch_openrouter_credits(),
            fetch_openrouter_key_info(),
            _asyncio.to_thread(gcp_client.get_bigquery_month_cost_eur) if gcp_available else _asyncio.sleep(0, result=None),
            _asyncio.to_thread(gcp_client.get_credit_grants_status) if gcp_available else _asyncio.sleep(0, result=None),
            fetch_minimax_token_plan_status(),
            fetch_xai_balance_usd(),
        )
        real_billing = get_global_summary().get("real_billing", {})
        manual_balances = get_manual_balances()
        last_sync = billing_sync_state.get("last_sync")

        # Dépense réelle du mois calendaire courant (pas un solde de compte,
        # cf. bug signalé : le total de la bannière sommait les soldes).
        now_dt = datetime.now()
        month_start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        hours_since_month_start = max((now_dt - month_start).total_seconds() / 3600, 1)
        total_cost_usd_month = round(
            get_token_stats(since_hours=hours_since_month_start).get("total_cost", 0.0), 4
        )

        providers: list[dict] = []

        for name, q in quota_summary.get("providers", {}).items():
            meta = _PROVIDER_DASHBOARDS.get(name, {})
            limit = q.get("limit") or 0
            used = q.get("used") or 0
            is_usd = q.get("unit") == "USD"
            balance_usd = None
            balance_eur = None
            plan = meta.get("plan", q.get("metric", ""))
            if name == "deepseek-free":
                # API officielle DeepSeek en priorité, fallback sur le solde scrapé
                balance_usd = deepseek_live if deepseek_live is not None else real_billing.get("deepseek_balance_usd")
            elif name == "gemini-free":
                # Export de facturation BigQuery (format FOCUS, déjà configuré sur
                # ha-delta) en priorité — dépense RÉELLE du mois en EUR (devise
                # vérifiée du compte, PAS USD), pas un solde (GCP est du
                # pay-as-you-go). Fallback sur le scraper Chrome si l'export
                # n'est pas dispo. + crédits promo restants (relevés le 07/07/2026,
                # recalculés en direct depuis la conso réelle).
                if gcp_month_cost is not None:
                    balance_eur = gcp_month_cost
                    plan = f"{plan} — dépense du mois (BigQuery)"
                else:
                    balance_usd = real_billing.get("gemini_gcp_cost_usd")
                if gcp_credits:
                    remaining_total = round(sum(c["remaining_eur"] for c in gcp_credits if not c["expired"]), 2)
                    if remaining_total > 0:
                        plan = f"{plan} — {remaining_total}€ de crédits promo restants"
            elif name == "anthropic-claude-haiku":
                # Coût réel scrapé sur console.anthropic.com/settings/cost (pas de
                # solde : API pay-as-you-go). Dépense, pas budget alloué.
                anthropic_cost = real_billing.get("anthropic_api_cost_usd")
                if anthropic_cost is not None:
                    balance_usd = anthropic_cost
                    plan = f"{plan} — dépense du mois (console.anthropic.com)"

            providers.append({
                "name": meta.get("label", name),
                "balance_usd": balance_usd,
                "balance_eur": balance_eur,
                "quota_rpm": None,
                "quota_rpd": None if is_usd else limit,
                "used_rpm": None,
                "used_rpd": None if is_usd else used,
                "used_pct": round((used / limit) * 100, 1) if limit else 0.0,
                "last_sync": last_sync,
                "dashboard_url": meta.get("dashboard_url", ""),
                "status": _status_for(name, q),
                "plan": plan,
            })

        claude_pct = real_billing.get("claude_usage_pct")
        untracked = dict(_UNTRACKED_PROVIDER_LINKS)
        for name, meta in untracked.items():
            used_pct = claude_pct if name == "claude-subscription" and claude_pct is not None else 0.0
            balance_usd = None
            quota_rpd = None
            used_rpd = None
            plan = meta["plan"]
            status = "non_suivi"
            if name == "openrouter" and openrouter_live is not None:
                # API officielle OpenRouter : GET /api/v1/credits (solde global)
                # + GET /api/v1/key (plafond configuré sur la clé + conso
                # réelle jour/semaine/mois) — plus besoin de se contenter d'un lien.
                balance_usd = openrouter_live["balance"]
                status = "active"
                if openrouter_key:
                    key_limit = openrouter_key.get("limit")
                    if key_limit:
                        quota_rpd = key_limit
                        used_rpd = openrouter_key.get("usage_monthly", 0)
                        used_pct = round((used_rpd / key_limit) * 100, 1) if key_limit else 0.0
                    else:
                        plan = f"{meta['plan']} — pas de plafond configuré sur la clé"
            elif name == "gemini-subscription":
                # Best-effort scraper (gemini.google.com > Réglages > Usage limits) :
                # pas de % fiable comme Claude, juste le texte brut trouvé sur la page.
                gemini_summary = real_billing.get("gemini_subscription_summary")
                if gemini_summary:
                    status = "active"
                    plan = f"{meta['plan']} — {gemini_summary}"
            elif name == "minimax" and minimax_token_plan is not None:
                # API officielle (GET /v1/token_plan/remains, testée en direct 07/07/2026) :
                # pas de solde ici (MiniMax n'a pas d'API pour le pay-as-you-go), juste
                # de quoi confirmer quel système de facturation est actif.
                if minimax_token_plan["has_token_plan"]:
                    plan = f"{meta['plan']} — forfait Token Plan actif : {minimax_token_plan['model_remains']} restants"
                else:
                    plan = f"{meta['plan']} — pas de forfait Token Plan, facturé au solde (pas d'API pour ce mode)"
            elif name == "grok" and xai_balance is not None:
                # Management API xAI (GET /v1/billing/teams/{team_id}/prepaid/balance,
                # clé XAI_MANAGEMENT_API_KEY séparée) — testée en direct 07/07/2026.
                balance_usd = xai_balance
                status = "active"

            entry_last_sync = last_sync if name == "claude-subscription" else None
            if name == "gemini-subscription":
                entry_last_sync = real_billing.get("gemini_subscription_last_sync")
            manual = manual_balances.get(name)
            if status == "non_suivi" and manual:
                # Pas d'API pour ce provider : solde saisi à la main dans l'IHM
                # (POST /api/billing/manual), affiché avec son propre horodatage.
                balance_usd = manual["balance_usd"]
                status = "manuel"
                entry_last_sync = manual["updated_at"]
                if manual.get("note"):
                    plan = f"{plan} — {manual['note']}"

            providers.append({
                "name": meta["label"],
                "balance_usd": balance_usd,
                "balance_eur": None,
                "quota_rpm": None,
                "quota_rpd": quota_rpd,
                "used_rpm": None,
                "used_rpd": used_rpd,
                "used_pct": used_pct,
                "last_sync": entry_last_sync,
                "dashboard_url": meta["dashboard_url"],
                "status": status,
                "plan": plan,
                # Clé technique stable pour POST /api/billing/manual (le "name"
                # affiché est un libellé humain, pas un identifiant fiable).
                "provider_key": name,
            })

        return {"providers": providers, "total_cost_usd_month": total_cost_usd_month}
    except Exception as e:
        logger.error(f"[BILLING] Erreur agrégation /api/billing : {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        import time

        from core.session_history import record_token_usage

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

