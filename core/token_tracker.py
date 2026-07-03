import os
import json
import logging
from threading import RLock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Fichier de persistance localisé à la racine du tab5-engine
USAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "token_usage.json")
_lock = RLock()

# Cache mémoire avec TTL pour éviter les relectures disque
_usage_cache = {"data": None, "ts": 0.0}
_CACHE_TTL_S = 15  # Secondes avant expiration du cache

# [P1-2.4] Le barème est désormais centralisé dans core/pricing.py, qui lit la
# source de vérité unique pricing_strategy.json (cf. core.pricing.FALLBACK_PRICING
# pour le repli des alias/legacy).
from core.pricing import get_model_pricing as _get_model_pricing


def _get_pricing_for_model(model: str) -> dict:
    """Retourne la structure de prix d'un modèle (USD/token) via le barème unifié."""
    return _get_model_pricing(model)

def classify_model_channel(model: str) -> str:
    """Classifie le modèle selon son canal de quota glissant.
    
    Logique de classification :
    - Les modèles '-cli' ou '-high-cli'/'-medium-cli'/'-low-cli' → canaux CLI (abonnements)
    - Les modèles '-paid' → API payante GCP (pas de quota glissant limitant)
    - Les modèles '-free' ou les modèles API Gemini standard (sans suffixe -cli/-paid) → Free Tier
    - Les modèles 'pro' → gemini-free-pro, les autres flash/etc → gemini-free-flash
    """
    m = model.lower()
    
    # 1. Canaux CLI (abonnements mensuels) — identifiés par le suffixe '-cli'
    if "-cli" in m:
        if "claude" in m:
            return "claude-cli-abo"
        else:
            return "gemini-cli-abo"
    # Alias direct sans suffixe
    if m == "claude":
        return "claude-cli-abo"
    
    # 2. API payante GCP (pas de quota free tier)
    if "-paid" in m:
        return "gemini-paid-api"
    
    # 3. Modèles Gemini (API Free Tier — la majorité des appels du moteur)
    if "gemini" in m:
        if "pro" in m:
            return "gemini-free-pro"
        else:
            return "gemini-free-flash"
    
    # 4. DeepSeek API payante
    if "deepseek" in m:
        return "deepseek-api-payant"
        
    # 5. MiniMax API payante
    if "minimax" in m:
        return "minimax-api-payant"
    
    return "other"

import sqlite3

def load_usage() -> dict:
    """Charge les données agrégées depuis SQLite pour simuler l'ancienne structure JSON."""
    try:
        from core.runtime_db import get_connection
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        
        # 1. Charger total
        row_total = conn.execute("""
            SELECT 
                COALESCE(SUM(prompt_tokens), 0) as prompt,
                COALESCE(SUM(completion_tokens), 0) as completion,
                COALESCE(SUM(total_tokens), 0) as total,
                COALESCE(SUM(cost_usd), 0.0) as cost
            FROM token_usage
        """).fetchone()
        
        total = {
            "prompt_tokens": row_total["prompt"],
            "completion_tokens": row_total["completion"],
            "total_tokens": row_total["total"],
            "estimated_cost_usd": row_total["cost"]
        }
        
        # 2. Charger models
        models = {}
        cursor_models = conn.execute("""
            SELECT 
                model,
                SUM(prompt_tokens) as prompt,
                SUM(completion_tokens) as completion,
                SUM(total_tokens) as total,
                SUM(cost_usd) as cost
            FROM token_usage
            GROUP BY model
        """)
        for row in cursor_models:
            models[row["model"]] = {
                "prompt_tokens": row["prompt"],
                "completion_tokens": row["completion"],
                "total_tokens": row["total"],
                "estimated_cost_usd": row["cost"]
            }
            
        # 3. Charger sessions
        sessions = {}
        cursor_sessions = conn.execute("""
            SELECT 
                s.session_id,
                s.objective,
                s.started_at,
                COALESCE(SUM(t.prompt_tokens), 0) as prompt,
                COALESCE(SUM(t.completion_tokens), 0) as completion,
                COALESCE(SUM(t.total_tokens), 0) as total,
                COALESCE(SUM(t.cost_usd), 0.0) as cost
            FROM sessions s
            LEFT JOIN token_usage t ON s.session_id = t.session_id
            GROUP BY s.session_id
        """)
        for row in cursor_sessions:
            started_iso = datetime.fromtimestamp(row["started_at"]).isoformat() if row["started_at"] else datetime.now().isoformat()
            sessions[row["session_id"]] = {
                "timestamp": started_iso,
                "objective": row["objective"] or "Objectif non spécifié",
                "prompt_tokens": row["prompt"],
                "completion_tokens": row["completion"],
                "total_tokens": row["total"],
                "estimated_cost_usd": row["cost"],
                "models": {}
            }
            
        # 4. Charger history (100 derniers appels)
        history = []
        cursor_history = conn.execute("""
            SELECT timestamp, model, prompt_tokens, completion_tokens, cost_usd
            FROM token_usage
            ORDER BY timestamp DESC LIMIT 100
        """)
        for row in cursor_history:
            history.append({
                "timestamp": datetime.fromtimestamp(row["timestamp"]).isoformat() if row["timestamp"] else datetime.now().isoformat(),
                "model": row["model"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "cost_usd": row["cost_usd"]
            })
            
        # 5. Charger real_billing depuis scoped_memory
        real_billing = {
            "gemini_gcp_cost_usd": 0.0,
            "gemini_gcp_last_sync": None,
            "deepseek_balance_usd": 0.0,
            "deepseek_last_sync": None,
            "claude_message_usage_pct": None,
            "claude_summary_text": None,
            "claude_last_sync": None
        }
        row_billing = conn.execute("""
            SELECT value_json FROM scoped_memory 
            WHERE session_id = 'global' AND scope_id = 'global' AND key = 'real_billing'
        """).fetchone()
        if row_billing and row_billing["value_json"]:
            try:
                loaded_billing = json.loads(row_billing["value_json"])
                real_billing.update(loaded_billing)
            except Exception:
                pass
                
        conn.close()
        return {
            "total": total,
            "models": models,
            "sessions": sessions,
            "history": history,
            "real_billing": real_billing
        }
    except Exception as e:
        logger.warning(f"[TOKEN TRACKER] Erreur load_usage SQLite : {e}")
        return {
            "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0},
            "models": {},
            "sessions": {},
            "history": [],
            "real_billing": {
                "gemini_gcp_cost_usd": 0.0,
                "gemini_gcp_last_sync": None,
                "deepseek_balance_usd": 0.0,
                "deepseek_last_sync": None,
                "claude_message_usage_pct": None,
                "claude_summary_text": None,
                "claude_last_sync": None
            }
        }

def save_usage(data: dict):
    """Sauvegarde les données d'utilisation (no-op car stocké en direct dans SQLite)."""
    pass

def update_real_billing(gcp_cost_usd: float = None, deepseek_balance_usd: float = None, claude_message_usage_pct: int = None, claude_summary_text: str = None):
    """Met à jour les informations de facturation réelle dans scoped_memory SQLite."""
    try:
        from core.runtime_db import get_connection
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        
        # Charger la valeur existante
        real_billing = {
            "gemini_gcp_cost_usd": 0.0,
            "gemini_gcp_last_sync": None,
            "deepseek_balance_usd": 0.0,
            "deepseek_last_sync": None,
            "claude_message_usage_pct": None,
            "claude_summary_text": None,
            "claude_last_sync": None
        }
        row_billing = conn.execute("""
            SELECT value_json FROM scoped_memory 
            WHERE session_id = 'global' AND scope_id = 'global' AND key = 'real_billing'
        """).fetchone()
        if row_billing and row_billing["value_json"]:
            try:
                loaded_billing = json.loads(row_billing["value_json"])
                real_billing.update(loaded_billing)
            except Exception:
                pass
                
        # Mettre à jour avec les nouveaux paramètres
        now_str = datetime.now().isoformat()
        if gcp_cost_usd is not None:
            real_billing["gemini_gcp_cost_usd"] = gcp_cost_usd
            real_billing["gemini_gcp_last_sync"] = now_str
        if deepseek_balance_usd is not None:
            real_billing["deepseek_balance_usd"] = deepseek_balance_usd
            real_billing["deepseek_last_sync"] = now_str
        if claude_message_usage_pct is not None:
            real_billing["claude_message_usage_pct"] = claude_message_usage_pct
            real_billing["claude_last_sync"] = now_str
        if claude_summary_text is not None:
            real_billing["claude_summary_text"] = claude_summary_text
            real_billing["claude_last_sync"] = now_str
            
        # Écrire dans scoped_memory
        conn.execute("""
            INSERT OR REPLACE INTO scoped_memory (session_id, scope_id, key, value_json)
            VALUES ('global', 'global', 'real_billing', ?)
        """, (json.dumps(real_billing),))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[TOKEN TRACKER] Erreur update_real_billing SQLite : {e}")

def init_session(session_id: str, objective: str):
    """Initialise une session/conversation dans la base SQLite."""
    if not session_id:
        return
    try:
        from core.session_history import record_session_start
        record_session_start(session_id, objective, "planner")
        logger.info(f"[TOKEN TRACKER] Session '{session_id}' initialisée en BDD : '{objective}'")
    except Exception as e:
        logger.warning(f"[TOKEN TRACKER] Erreur init_session SQLite : {e}")

def record_usage(model: str, prompt_tokens: int, completion_tokens: int, session_id: str = None, cost_usd: float = None):
    """Enregistre la consommation de tokens pour un modèle donné directement dans SQLite."""
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return

    price = _get_pricing_for_model(model)
    if cost_usd is not None:
        cost = cost_usd
    else:
        cost = (prompt_tokens * price["input"]) + (completion_tokens * price["output"])

    try:
        from core.session_history import record_token_usage
        channel = classify_model_channel(model)
        record_token_usage(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            session_id=session_id,
            channel=channel,
        )
    except Exception as e:
        logger.warning(f"[TOKEN TRACKER] Erreur record_usage SQLite : {e}")

    # Envoi facultatif à Langfuse
    try:
        from core.langfuse_bridge import LangfuseBridge
        bridge = LangfuseBridge.get_instance()
        if bridge.enabled and session_id:
            bridge.log_generation(
                session_id=session_id,
                agent_name="unknown",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost
            )
    except Exception:
        pass

def reset_usage():
    """Remet à zéro tous les compteurs de tokens dans SQLite."""
    try:
        from core.runtime_db import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM token_usage")
        conn.commit()
        conn.close()
        logger.info("[TOKEN TRACKER] Réinitialisation complète des compteurs effectuée en BDD.")
    except Exception as e:
        logger.warning(f"[TOKEN TRACKER] Erreur reset_usage SQLite : {e}")

def get_quotas_status() -> dict:
    """Calcule en temps réel l'utilisation des quotas glissants pour chaque canal.
    
    ).
    Fallback sur le JSON si la BDD est indisponible.
    """
    # Tentative via SQLite (source de vérité depuis V5.5)
    try:
        from core.session_history import get_quotas_from_db
        db_result = get_quotas_from_db()
        if db_result:
            return db_result
    except Exception as e:
        logger.warning(f"[QUOTAS] Fallback JSON (SQLite indisponible) : {e}")
    
    # Fallback : calcul depuis le JSON history[] (ancien code)
    with _lock:
        data = load_usage()
        history = data.get("history", [])
        
        now = datetime.now()
        t_1m = now - timedelta(minutes=1)
        t_1h = now - timedelta(hours=1)
        t_24h = now - timedelta(days=1)
        t_30j = now - timedelta(days=30)
        
        calls = {
            "gemini-free-flash": [],
            "gemini-free-pro": [],
            "claude-cli-abo": [],
            "gemini-cli-abo": []
        }
        
        for tx in history:
            try:
                tx_time = datetime.fromisoformat(tx["timestamp"])
            except Exception:
                continue
            channel = classify_model_channel(tx["model"])
            if channel in calls:
                calls[channel].append({
                    "time": tx_time,
                    "tokens": tx.get("prompt_tokens", 0) + tx.get("completion_tokens", 0)
                })
        
        flat = {
            "gemini_free_flash_rpm": 0, "gemini_free_flash_tpm": 0, "gemini_free_flash_rpd": 0,
            "gemini_free_pro_rpm": 0, "gemini_free_pro_tpm": 0, "gemini_free_pro_rpd": 0,
            "claude_cli_tph": 0, "claude_cli_tpm": 0,
            "gemini_cli_tph": 0, "gemini_cli_tpm": 0,
        }
        
        # Gemini Free Flash
        items = calls["gemini-free-flash"]
        flat["gemini_free_flash_rpm"] = len([x for x in items if x["time"] > t_1m])
        flat["gemini_free_flash_tpm"] = sum(x["tokens"] for x in items if x["time"] > t_1m)
        flat["gemini_free_flash_rpd"] = len([x for x in items if x["time"] > t_24h])
        
        # Gemini Free Pro
        items = calls["gemini-free-pro"]
        flat["gemini_free_pro_rpm"] = len([x for x in items if x["time"] > t_1m])
        flat["gemini_free_pro_tpm"] = sum(x["tokens"] for x in items if x["time"] > t_1m)
        flat["gemini_free_pro_rpd"] = len([x for x in items if x["time"] > t_24h])
        
        # Claude CLI
        items = calls["claude-cli-abo"]
        flat["claude_cli_tph"] = sum(x["tokens"] for x in items if x["time"] > t_1h)
        flat["claude_cli_tpm"] = sum(x["tokens"] for x in items if x["time"] > t_30j)
        
        # Gemini CLI
        items = calls["gemini-cli-abo"]
        flat["gemini_cli_tph"] = sum(x["tokens"] for x in items if x["time"] > t_1h)
        flat["gemini_cli_tpm"] = sum(x["tokens"] for x in items if x["time"] > t_30j)
        
        return flat


def get_global_summary() -> dict:
    """
    Retourne un résumé compact de la consommation totale de tokens.
    Fonction requise par l'endpoint MCP get_token_usage.
    """
    try:
        data = load_usage()
        total = data.get("total", {})
        models = data.get("models", {})
        real_billing = data.get("real_billing", {})
        
        return {
            "total_tokens": total.get("total_tokens", 0),
            "total_prompt_tokens": total.get("prompt_tokens", 0),
            "total_completion_tokens": total.get("completion_tokens", 0),
            "estimated_cost_usd": round(total.get("estimated_cost_usd", 0.0), 6),
            "models_used": list(models.keys()),
            "model_count": len(models),
            "models_detail": {
                m: {
                    "total_tokens": v.get("total_tokens", 0),
                    "estimated_cost_usd": round(v.get("estimated_cost_usd", 0.0), 6)
                }
                for m, v in models.items()
            },
            "real_billing": {
                "deepseek_balance_usd": real_billing.get("deepseek_balance_usd", 0.0),
                "gemini_gcp_cost_usd": real_billing.get("gemini_gcp_cost_usd", 0.0),
                "claude_usage_pct": real_billing.get("claude_message_usage_pct"),
            }
        }
    except Exception as e:
        logger.error(f"[TOKEN TRACKER] Erreur dans get_global_summary: {e}")
        return {"error": str(e), "total_tokens": 0, "estimated_cost_usd": 0.0}


def get_session_total_tokens(session_id: str) -> int:
    """Retourne le nombre total de tokens consommés par une session donnée.
    Utilisé par le garde-fou de budget tokens dans engine.py."""
    if not session_id:
        return 0
    with _lock:
        data = load_usage()
        sess = data.get("sessions", {}).get(session_id, {})
        return sess.get("total_tokens", 0)


def get_session_total_cost(session_id: str) -> float:
    """[P2-3.4] Retourne le coût total (USD) estimé d'une session donnée.
    Utilisé par le garde-fou de budget coût (ExecutionBudget)."""
    if not session_id:
        return 0.0
    with _lock:
        data = load_usage()
        sess = data.get("sessions", {}).get(session_id, {})
        return float(sess.get("estimated_cost_usd", 0.0) or 0.0)

