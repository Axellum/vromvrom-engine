"""
core/models_db.py — Registre centralisé des modèles LLM en SQLite.

Source de vérité unique (SSOT) pour les modèles, providers,
clés API, benchmarks, règles de routage et abonnements.
Tables access_channels + quota_realtime pour le tracking temps réel.

Architecture hybride :
  - SQLite models_registry.db = source de vérité technique
  - Exports automatiques vers JSON/Markdown pour la lisibilité humaine
  - config.json reste séparé (assignations agents → tiers)

Tables :
  - providers       : Fournisseurs d'accès LLM (7+ entrées)
  - models          : Catalogue complet des modèles (35+ entrées)
  - api_keys        : Clés API avec quotas par projet
  - benchmarks      : Scores de performance par modèle
  - routing_rules   : Matrice de routage par type de tâche
  - subscriptions   : Abonnements (Claude Pro, Gemini Advanced)
  - access_channels : N-N clé×modèle (quel modèle est accessible par quel canal)
  - quota_realtime  : Quotas temps réel par clé API (RPM/TPM/RPD utilisés)

Usage :
    from core.models_db import get_model, get_active_models, get_routing_score
    from core.models_db import get_access_channels, get_all_quotas_realtime
    model = get_model("gemini-3.5-flash")
    score = get_routing_score("gemini-3.5-flash")  # cascade_priority
    channels = get_access_channels("gemini-3.5-flash")  # tous les canaux d'accès
"""

import asyncio
import concurrent.futures
import os
import json
import time
import sqlite3
import logging
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger("core.models_db")

# Chemin de la BDD — séparée de session_history.db (données statiques vs dynamiques)
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models_registry.db",
)

# Pool de threads dédié aux I/O SQLite (séparé du default executor asyncio)
_DB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix="models_db",
)

# RLock réentrant pour les écritures (évite les deadlocks si A appelle B dans la même transaction)
_db_write_lock = threading.RLock()

# Connexion persistante par thread worker — évite le overhead open/schema/close à chaque appel
_thread_local = threading.local()

# Cache en mémoire pour get_routing_score() — appelé N fois par sort dans le gateway
_routing_score_cache: Dict[str, tuple] = {}
_routing_cache_lock = threading.Lock()
_ROUTING_CACHE_TTL = 60.0  # secondes


# ══════════════════════════════════════════════════════════════════
# Connexion et création des tables
# ══════════════════════════════════════════════════════════════════

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée toutes les tables et index si absents. Appelé une seule fois par connexion thread-locale."""
    # ── Table providers ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            api_endpoint TEXT,
            auth_method TEXT,
            confidentiality TEXT,
            cascade_priority REAL DEFAULT 5.0,
            notes TEXT
        )
    """)

    # ── Table models ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL REFERENCES providers(id),
            display_name TEXT,
            status TEXT DEFAULT 'active',
            tier TEXT,
            context_input INTEGER,
            context_output INTEGER,
            cost_input_per_m REAL,
            cost_output_per_m REAL,
            cost_cached_per_m REAL,
            currency TEXT DEFAULT 'USD',
            ttft_ms INTEGER,
            throughput_tps REAL,
            supports_thinking INTEGER DEFAULT 0,
            supports_tools INTEGER DEFAULT 0,
            supports_vision INTEGER DEFAULT 0,
            supports_audio INTEGER DEFAULT 0,
            supports_json_mode INTEGER DEFAULT 0,
            supports_streaming INTEGER DEFAULT 0,
            supports_search_grounding INTEGER DEFAULT 0,
            speciality TEXT,
            recommended_use TEXT,
            last_tested TEXT,
            notes TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_models_provider
        ON models(provider_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_models_status
        ON models(status)
    """)

    # ── Table api_keys ───────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            provider_id TEXT REFERENCES providers(id),
            env_var TEXT NOT NULL,
            project_name TEXT,
            key_type TEXT,
            quota_rpm INTEGER,
            quota_rpd INTEGER,
            quota_tpm INTEGER,
            status TEXT DEFAULT 'active',
            last_tested TEXT
        )
    """)

    # ── Table benchmarks ─────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS benchmarks (
            model_id TEXT REFERENCES models(id),
            benchmark_name TEXT,
            score REAL,
            unit TEXT DEFAULT '%',
            PRIMARY KEY (model_id, benchmark_name)
        )
    """)

    # ── Table routing_rules ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routing_rules (
            task_type TEXT PRIMARY KEY,
            recommended_model TEXT REFERENCES models(id),
            provider_id TEXT,
            justification TEXT,
            effective_cost TEXT
        )
    """)

    # ── Table subscriptions ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            cost_monthly_usd REAL,
            rolling_window_hours INTEGER,
            hourly_token_limit INTEGER,
            monthly_token_limit INTEGER,
            estimated_messages_limit INTEGER,
            models_json TEXT,
            advantages TEXT,
            recommended_use TEXT
        )
    """)

    # ── Table access_channels  — N-N clé×modèle ─────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS access_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL REFERENCES models(id),
            api_key_id TEXT REFERENCES api_keys(id),
            access_method TEXT NOT NULL,
            provider_alias TEXT,
            speed_tier TEXT,
            latency_ttft_ms INTEGER,
            throughput_tps REAL,
            is_default INTEGER DEFAULT 0,
            notes TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ac_model
        ON access_channels(model_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ac_key
        ON access_channels(api_key_id)
    """)
    # Contrainte d'unicité (pas de doublon modèle+clé+alias)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ac_unique
        ON access_channels(model_id, api_key_id, provider_alias)
    """)

    # ── Table quota_realtime  — quotas temps réel par clé ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quota_realtime (
            api_key_id TEXT PRIMARY KEY REFERENCES api_keys(id),
            limit_rpm INTEGER,
            limit_rpd INTEGER,
            limit_tpm INTEGER,
            limit_tph INTEGER,
            limit_monthly INTEGER,
            used_rpm INTEGER DEFAULT 0,
            used_tpm INTEGER DEFAULT 0,
            used_rpd INTEGER DEFAULT 0,
            used_tph INTEGER DEFAULT 0,
            used_monthly INTEGER DEFAULT 0,
            saturation_pct REAL DEFAULT 0.0,
            external_balance_usd REAL,
            external_usage_pct REAL,
            external_status TEXT DEFAULT 'unknown',
            updated_at REAL NOT NULL,
            source TEXT DEFAULT 'calculated'
        )
    """)

    conn.commit()


def _get_connection() -> sqlite3.Connection:
    """Retourne la connexion SQLite persistante du thread courant (thread-local pool).

    La connexion est créée une seule fois par worker thread et réutilisée à chaque appel.
    Le schéma est initialisé lors de la première création. Un health-check rapide détecte
    les connexions fermées ou corrompues et les recrée à la demande.
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _thread_local.conn = None

    conn = sqlite3.connect(_DB_PATH, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # busy_timeout en ms — SQLite attend ce délai avant de lever SQLITE_BUSY
    conn.execute("PRAGMA busy_timeout=30000")
    # 8 MB de page cache par connexion worker
    conn.execute("PRAGMA cache_size=-8000")
    conn.row_factory = sqlite3.Row
    _thread_local.conn = conn
    _ensure_schema(conn)
    return conn


@contextmanager
def _write_transaction():
    """Context manager pour les écritures atomiques.

    Acquiert le RLock d'écriture, yield la connexion thread-locale, commit si tout
    se passe bien, rollback + re-raise sinon. La connexion n'est jamais fermée.
    Le RLock réentrant sérialise les écritures côté Python (en complément de WAL +
    busy_timeout=30000) et protège les transactions imbriquées dans un même thread.
    """
    with _db_write_lock:
        conn = _get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ══════════════════════════════════════════════════════════════════
# LECTURE — Fonctions de requête ciblée (utilisées par le moteur)
# ══════════════════════════════════════════════════════════════════

def get_model(model_id: str) -> Optional[Dict[str, Any]]:
    """Retourne les détails d'un modèle par son ID."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM models WHERE id = ?", (model_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_model({model_id}): {e}")
        return None


def get_active_models(provider_id: str = None) -> List[Dict[str, Any]]:
    """Retourne tous les modèles actifs, optionnellement filtrés par provider."""
    try:
        conn = _get_connection()
        if provider_id:
            rows = conn.execute(
                "SELECT * FROM models WHERE status = 'active' AND provider_id = ? ORDER BY id",
                (provider_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM models WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_active_models: {e}")
        return []


def get_models_for_tier(tier: str) -> List[Dict[str, Any]]:
    """Retourne les modèles disponibles pour un tier donné."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            """SELECT m.*, p.cascade_priority, p.confidentiality, p.type as provider_type
               FROM models m JOIN providers p ON m.provider_id = p.id
               WHERE m.status = 'active' AND m.tier = ?
               ORDER BY p.cascade_priority ASC""",
            (tier,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_models_for_tier({tier}): {e}")
        return []


def get_model_cost(model_id: str) -> Dict[str, Any]:
    """Retourne les tarifs d'un modèle (input, output, cached, currency)."""
    try:
        conn = _get_connection()
        row = conn.execute(
            """SELECT cost_input_per_m, cost_output_per_m, cost_cached_per_m, currency
               FROM models WHERE id = ?""",
            (model_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {"cost_input_per_m": 0, "cost_output_per_m": 0, "cost_cached_per_m": None, "currency": "USD"}
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_model_cost({model_id}): {e}")
        return {}


def get_routing_score(model_id: str) -> float:
    """Retourne le cascade_priority du provider d'un modèle (score de routing).

    Score plus bas = plus prioritaire :
      1.0 = local gratuit
      2.0 = API gratuit
      3.0 = CLI abonnement
      4.0 = API pay-as-you-go pas cher
      5.0+ = API pay-as-you-go cher

    Résultat mis en cache 60 s — appelé N fois par sort dans le gateway.
    """
    now = time.time()
    with _routing_cache_lock:
        cached = _routing_score_cache.get(model_id)
        if cached and (now - cached[1]) < _ROUTING_CACHE_TTL:
            return cached[0]

    score = _fetch_routing_score_db(model_id)

    with _routing_cache_lock:
        _routing_score_cache[model_id] = (score, now)
    return score


def _fetch_routing_score_db(model_id: str) -> float:
    """Lecture directe BDD sans cache."""
    try:
        conn = _get_connection()
        row = conn.execute(
            """SELECT p.cascade_priority
               FROM models m JOIN providers p ON m.provider_id = p.id
               WHERE m.id = ?""",
            (model_id,)
        ).fetchone()
        return row["cascade_priority"] if row else 5.0
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_routing_score({model_id}): {e}")
        return 5.0


def get_bulk_routing_scores(model_ids: List[str]) -> Dict[str, float]:
    """Retourne les cascade_priority pour une liste de modèles en une seule requête.

    Utilisé par le gateway pour éviter N appels DB lors du tri des modèles d'un tier.
    Les IDs non trouvés en BDD reçoivent 5.0 (défaut heuristique).
    """
    if not model_ids:
        return {}
    now = time.time()
    result: Dict[str, float] = {}
    missing: List[str] = []

    # Lire d'abord le cache
    with _routing_cache_lock:
        for mid in model_ids:
            cached = _routing_score_cache.get(mid)
            if cached and (now - cached[1]) < _ROUTING_CACHE_TTL:
                result[mid] = cached[0]
            else:
                missing.append(mid)

    if missing:
        try:
            conn = _get_connection()
            placeholders = ",".join("?" * len(missing))
            rows = conn.execute(
                f"""SELECT m.id, p.cascade_priority
                   FROM models m JOIN providers p ON m.provider_id = p.id
                   WHERE m.id IN ({placeholders})""",
                missing,
            ).fetchall()
            fetched = {row["id"]: row["cascade_priority"] for row in rows}
        except Exception as e:
            logger.warning(f"[ModelsDB] Erreur get_bulk_routing_scores: {e}")
            fetched = {}

        with _routing_cache_lock:
            for mid in missing:
                score = fetched.get(mid, 5.0)
                result[mid] = score
                _routing_score_cache[mid] = (score, now)

    return result


def get_provider(provider_id: str) -> Optional[Dict[str, Any]]:
    """Retourne les détails d'un provider."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_provider({provider_id}): {e}")
        return None


def get_all_providers() -> List[Dict[str, Any]]:
    """Retourne tous les providers."""
    try:
        conn = _get_connection()
        rows = conn.execute("SELECT * FROM providers ORDER BY cascade_priority").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_all_providers: {e}")
        return []


def get_all_api_keys(hide_values: bool = True) -> List[Dict[str, Any]]:
    """Retourne toutes les clés API (sans les valeurs sensibles par défaut)."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM api_keys ORDER BY provider_id, id"
        ).fetchall()
        result = [dict(r) for r in rows]
        if hide_values:
            for r in result:
                # Masquer la clé si elle existe en tant que champ
                if "key_value" in r:
                    r["key_value"] = "***"
        return result
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_all_api_keys: {e}")
        return []


def get_benchmarks(model_id: str) -> List[Dict[str, Any]]:
    """Retourne les benchmarks d'un modèle."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM benchmarks WHERE model_id = ? ORDER BY benchmark_name",
            (model_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_benchmarks({model_id}): {e}")
        return []


def get_subscriptions() -> List[Dict[str, Any]]:
    """Retourne tous les abonnements."""
    try:
        conn = _get_connection()
        rows = conn.execute("SELECT * FROM subscriptions").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Décoder le JSON des modèles inclus
            if d.get("models_json"):
                try:
                    d["models"] = json.loads(d["models_json"])
                except json.JSONDecodeError:
                    d["models"] = []
            result.append(d)
        return result
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_subscriptions: {e}")
        return []


def get_routing_rules() -> List[Dict[str, Any]]:
    """Retourne toutes les règles de routage."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM routing_rules ORDER BY task_type"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_routing_rules: {e}")
        return []


# ══════════════════════════════════════════════════════════════════
# LECTURE — Access Channels & Quotas temps réel 
# ══════════════════════════════════════════════════════════════════

def get_access_channels(model_id: str) -> List[Dict[str, Any]]:
    """Retourne tous les canaux d'accès pour un modèle (clé, méthode, vitesse)."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            """SELECT ac.*, ak.env_var, ak.project_name, ak.key_type,
                      ak.quota_rpm, ak.quota_rpd, ak.quota_tpm
               FROM access_channels ac
               LEFT JOIN api_keys ak ON ac.api_key_id = ak.id
               WHERE ac.model_id = ?
               ORDER BY ac.is_default DESC, ac.latency_ttft_ms ASC""",
            (model_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_access_channels({model_id}): {e}")
        return []


def get_models_for_key(api_key_id: str) -> List[Dict[str, Any]]:
    """Retourne tous les modèles accessibles par une clé API donnée."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            """SELECT m.*, ac.access_method, ac.provider_alias, ac.speed_tier,
                      ac.latency_ttft_ms, ac.throughput_tps
               FROM access_channels ac
               JOIN models m ON ac.model_id = m.id
               WHERE ac.api_key_id = ?
               ORDER BY m.id""",
            (api_key_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_models_for_key({api_key_id}): {e}")
        return []


def get_access_map() -> Dict[str, Any]:
    """Carte complète : pour chaque clé API, les modèles accessibles + quotas temps réel."""
    try:
        conn = _get_connection()
        # Toutes les clés avec leurs quotas temps réel
        keys = conn.execute(
            """SELECT ak.*, qr.used_rpm, qr.used_tpm, qr.used_rpd,
                      qr.used_tph, qr.used_monthly,
                      qr.saturation_pct, qr.external_balance_usd,
                      qr.external_usage_pct, qr.external_status,
                      qr.updated_at as quota_updated_at
               FROM api_keys ak
               LEFT JOIN quota_realtime qr ON ak.id = qr.api_key_id
               ORDER BY ak.provider_id, ak.id"""
        ).fetchall()

        result = {}
        for k in keys:
            kd = dict(k)
            key_id = kd["id"]
            # Modèles accessibles par cette clé
            models = conn.execute(
                """SELECT ac.model_id, ac.access_method, ac.provider_alias,
                          ac.speed_tier, ac.latency_ttft_ms, ac.throughput_tps,
                          ac.is_default
                   FROM access_channels ac
                   WHERE ac.api_key_id = ?
                   ORDER BY ac.is_default DESC, ac.model_id""",
                (key_id,)
            ).fetchall()
            kd["models"] = [dict(m) for m in models]
            kd["models_count"] = len(models)
            result[key_id] = kd

        # Ajouter les canaux locaux et CLI (sans clé API)
        cli_local = conn.execute(
            """SELECT ac.*, m.display_name, m.provider_id
               FROM access_channels ac
               JOIN models m ON ac.model_id = m.id
               WHERE ac.api_key_id IS NULL
               ORDER BY ac.access_method, ac.model_id"""
        ).fetchall()
        if cli_local:
            result["_local_cli"] = {
                "id": "_local_cli",
                "description": "Accès locaux et CLI (sans clé API)",
                "models": [dict(m) for m in cli_local],
                "models_count": len(cli_local),
            }

        return result
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_access_map: {e}")
        return {}


def get_quota_realtime(api_key_id: str) -> Optional[Dict[str, Any]]:
    """Retourne le snapshot de quota temps réel pour une clé API."""
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM quota_realtime WHERE api_key_id = ?",
            (api_key_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_quota_realtime({api_key_id}): {e}")
        return None


def get_all_quotas_realtime() -> List[Dict[str, Any]]:
    """Retourne tous les quotas temps réel avec infos clé API."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            """SELECT qr.*, ak.env_var, ak.project_name, ak.provider_id, ak.key_type
               FROM quota_realtime qr
               JOIN api_keys ak ON qr.api_key_id = ak.id
               ORDER BY ak.provider_id, ak.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_all_quotas_realtime: {e}")
        return []


def get_quota_summary() -> Dict[str, Any]:
    """Résumé compact des quotas pour le dashboard HMI."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            """SELECT qr.api_key_id, ak.provider_id, ak.project_name,
                      qr.saturation_pct, qr.external_status,
                      qr.external_balance_usd, qr.external_usage_pct,
                      qr.updated_at
               FROM quota_realtime qr
               JOIN api_keys ak ON qr.api_key_id = ak.id
               ORDER BY qr.saturation_pct DESC"""
        ).fetchall()

        result = [dict(r) for r in rows]
        # Status global : la plus haute saturation
        max_sat = max((r["saturation_pct"] or 0 for r in result), default=0)
        global_status = "ok"
        if max_sat >= 95:
            global_status = "critical"
        elif max_sat >= 80:
            global_status = "warning"
        elif max_sat >= 70:
            global_status = "elevated"

        return {
            "global_status": global_status,
            "max_saturation_pct": round(max_sat, 1),
            "keys": result,
            "keys_count": len(result),
        }
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_quota_summary: {e}")
        return {"global_status": "error", "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# ÉCRITURE — Fonctions CRUD (utilisées par le seed et l'HMI admin)
# ══════════════════════════════════════════════════════════════════

def upsert_provider(provider_id: str, **kwargs) -> bool:
    """Insère ou met à jour un provider."""
    try:
        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO providers
                   (id, name, type, api_endpoint, auth_method,
                    confidentiality, cascade_priority, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider_id,
                    kwargs.get("name", provider_id),
                    kwargs.get("type", "unknown"),
                    kwargs.get("api_endpoint"),
                    kwargs.get("auth_method"),
                    kwargs.get("confidentiality"),
                    kwargs.get("cascade_priority", 5.0),
                    kwargs.get("notes"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_provider({provider_id}): {e}")
        return False


def upsert_model(model_id: str, **kwargs) -> bool:
    """Insère ou met à jour un modèle."""
    try:
        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO models
                   (id, provider_id, display_name, status, tier,
                    context_input, context_output,
                    cost_input_per_m, cost_output_per_m, cost_cached_per_m, currency,
                    ttft_ms, throughput_tps,
                    supports_thinking, supports_tools, supports_vision,
                    supports_audio, supports_json_mode, supports_streaming,
                    supports_search_grounding,
                    speciality, recommended_use, last_tested, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_id,
                    kwargs.get("provider_id", "unknown"),
                    kwargs.get("display_name", model_id),
                    kwargs.get("status", "active"),
                    kwargs.get("tier", "free"),
                    kwargs.get("context_input"),
                    kwargs.get("context_output"),
                    kwargs.get("cost_input_per_m"),
                    kwargs.get("cost_output_per_m"),
                    kwargs.get("cost_cached_per_m"),
                    kwargs.get("currency", "USD"),
                    kwargs.get("ttft_ms"),
                    kwargs.get("throughput_tps"),
                    kwargs.get("supports_thinking", 0),
                    kwargs.get("supports_tools", 0),
                    kwargs.get("supports_vision", 0),
                    kwargs.get("supports_audio", 0),
                    kwargs.get("supports_json_mode", 0),
                    kwargs.get("supports_streaming", 0),
                    kwargs.get("supports_search_grounding", 0),
                    kwargs.get("speciality"),
                    kwargs.get("recommended_use"),
                    kwargs.get("last_tested"),
                    kwargs.get("notes"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_model({model_id}): {e}")
        return False


def upsert_api_key(key_id: str, **kwargs) -> bool:
    """Insère ou met à jour une clé API."""
    try:
        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (id, provider_id, env_var, project_name, key_type,
                    quota_rpm, quota_rpd, quota_tpm, status, last_tested)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key_id,
                    kwargs.get("provider_id"),
                    kwargs.get("env_var", key_id),
                    kwargs.get("project_name"),
                    kwargs.get("key_type", "free"),
                    kwargs.get("quota_rpm"),
                    kwargs.get("quota_rpd"),
                    kwargs.get("quota_tpm"),
                    kwargs.get("status", "active"),
                    kwargs.get("last_tested"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_api_key({key_id}): {e}")
        return False


def upsert_benchmark(model_id: str, benchmark_name: str, score: float, unit: str = "%") -> bool:
    """Insère ou met à jour un benchmark pour un modèle."""
    try:
        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO benchmarks
                   (model_id, benchmark_name, score, unit)
                   VALUES (?, ?, ?, ?)""",
                (model_id, benchmark_name, score, unit),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_benchmark({model_id}, {benchmark_name}): {e}")
        return False


def upsert_routing_rule(task_type: str, **kwargs) -> bool:
    """Insère ou met à jour une règle de routage."""
    try:
        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO routing_rules
                   (task_type, recommended_model, provider_id, justification, effective_cost)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    task_type,
                    kwargs.get("recommended_model"),
                    kwargs.get("provider_id"),
                    kwargs.get("justification"),
                    kwargs.get("effective_cost"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_routing_rule({task_type}): {e}")
        return False


def upsert_subscription(sub_id: str, **kwargs) -> bool:
    """Insère ou met à jour un abonnement."""
    try:
        models = kwargs.get("models")
        models_json = json.dumps(models) if isinstance(models, (list, dict)) else kwargs.get("models_json")

        with _write_transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO subscriptions
                   (id, name, cost_monthly_usd, rolling_window_hours,
                    hourly_token_limit, monthly_token_limit,
                    estimated_messages_limit, models_json,
                    advantages, recommended_use)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sub_id,
                    kwargs.get("name", sub_id),
                    kwargs.get("cost_monthly_usd"),
                    kwargs.get("rolling_window_hours"),
                    kwargs.get("hourly_token_limit"),
                    kwargs.get("monthly_token_limit"),
                    kwargs.get("estimated_messages_limit"),
                    models_json,
                    kwargs.get("advantages"),
                    kwargs.get("recommended_use"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_subscription({sub_id}): {e}")
        return False


def upsert_access_channel(model_id: str, api_key_id: str = None, **kwargs) -> bool:
    """Insère ou met à jour un canal d'accès."""
    try:
        alias = kwargs.get("provider_alias", model_id)
        with _write_transaction() as conn:
            # Vérifie si le canal existe déjà
            existing = conn.execute(
                """SELECT id FROM access_channels
                   WHERE model_id = ? AND api_key_id IS ? AND provider_alias = ?""",
                (model_id, api_key_id, alias)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE access_channels SET
                       access_method = ?, speed_tier = ?, latency_ttft_ms = ?,
                       throughput_tps = ?, is_default = ?, notes = ?
                       WHERE id = ?""",
                    (
                        kwargs.get("access_method", "api_rest"),
                        kwargs.get("speed_tier"),
                        kwargs.get("latency_ttft_ms"),
                        kwargs.get("throughput_tps"),
                        kwargs.get("is_default", 0),
                        kwargs.get("notes"),
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO access_channels
                       (model_id, api_key_id, access_method, provider_alias,
                        speed_tier, latency_ttft_ms, throughput_tps, is_default, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        model_id,
                        api_key_id,
                        kwargs.get("access_method", "api_rest"),
                        alias,
                        kwargs.get("speed_tier"),
                        kwargs.get("latency_ttft_ms"),
                        kwargs.get("throughput_tps"),
                        kwargs.get("is_default", 0),
                        kwargs.get("notes"),
                    ),
                )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur upsert_access_channel({model_id}): {e}")
        return False


def update_quota_realtime(api_key_id: str, **kwargs) -> bool:
    """Met à jour le snapshot de quota temps réel pour une clé API."""
    try:
        with _write_transaction() as conn:
            # Calculer saturation_pct
            sat_values = []
            limit_rpm = kwargs.get("limit_rpm", 0)
            limit_rpd = kwargs.get("limit_rpd", 0)
            limit_tpm = kwargs.get("limit_tpm", 0)
            limit_tph = kwargs.get("limit_tph", 0)
            limit_monthly = kwargs.get("limit_monthly", 0)

            used_rpm = kwargs.get("used_rpm", 0)
            used_rpd = kwargs.get("used_rpd", 0)
            used_tpm = kwargs.get("used_tpm", 0)
            used_tph = kwargs.get("used_tph", 0)
            used_monthly = kwargs.get("used_monthly", 0)

            if limit_rpm and limit_rpm > 0:
                sat_values.append(used_rpm / limit_rpm * 100)
            if limit_rpd and limit_rpd > 0:
                sat_values.append(used_rpd / limit_rpd * 100)
            if limit_tpm and limit_tpm > 0:
                sat_values.append(used_tpm / limit_tpm * 100)
            if limit_tph and limit_tph > 0:
                sat_values.append(used_tph / limit_tph * 100)
            if limit_monthly and limit_monthly > 0:
                sat_values.append(used_monthly / limit_monthly * 100)

            saturation_pct = max(sat_values) if sat_values else 0.0

            # Déterminer le status
            ext_status = kwargs.get("external_status")
            if not ext_status:
                if saturation_pct >= 95:
                    ext_status = "exhausted"
                elif saturation_pct >= 80:
                    ext_status = "critical"
                elif saturation_pct >= 70:
                    ext_status = "warning"
                else:
                    ext_status = "ok"

            conn.execute(
                """INSERT OR REPLACE INTO quota_realtime
                   (api_key_id, limit_rpm, limit_rpd, limit_tpm, limit_tph, limit_monthly,
                    used_rpm, used_tpm, used_rpd, used_tph, used_monthly,
                    saturation_pct, external_balance_usd, external_usage_pct,
                    external_status, updated_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    api_key_id,
                    limit_rpm, limit_rpd, limit_tpm, limit_tph, limit_monthly,
                    used_rpm, used_tpm, used_rpd, used_tph, used_monthly,
                    round(saturation_pct, 2),
                    kwargs.get("external_balance_usd"),
                    kwargs.get("external_usage_pct"),
                    ext_status,
                    time.time(),
                    kwargs.get("source", "calculated"),
                ),
            )
        return True
    except Exception as e:
        logger.error(f"[ModelsDB] Erreur update_quota_realtime({api_key_id}): {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# EXPORT — Génération de fichiers lisibles depuis la BDD
# ══════════════════════════════════════════════════════════════════

def export_to_pricing_json() -> dict:
    """Génère un dict compatible avec l'ancien format pricing_strategy.json."""
    try:
        conn = _get_connection()

        # Abonnements
        subs = conn.execute("SELECT * FROM subscriptions").fetchall()
        subscriptions_out = []
        for s in subs:
            d = dict(s)
            models = json.loads(d.get("models_json") or "[]")
            subscriptions_out.append({
                "name": d["name"],
                "cost_monthly_usd": d["cost_monthly_usd"],
                "billing_period": "Mensuel",
                "rolling_window_hours": d["rolling_window_hours"],
                "estimated_messages_limit": d["estimated_messages_limit"],
                "hourly_token_limit": d["hourly_token_limit"],
                "monthly_token_limit": d["monthly_token_limit"],
                "models": models,
                "advantages": d.get("advantages", ""),
                "recommended_use": d.get("recommended_use", ""),
            })

        # APIs pay-as-you-go — regrouper les modèles par provider
        providers = conn.execute(
            "SELECT * FROM providers WHERE type IN ('pay_as_you_go', 'free') ORDER BY cascade_priority"
        ).fetchall()

        apis_out = []
        for p in providers:
            p_dict = dict(p)
            models = conn.execute(
                "SELECT * FROM models WHERE provider_id = ? ORDER BY id",
                (p_dict["id"],)
            ).fetchall()

            rates = {}
            for m in models:
                md = dict(m)
                rate_entry = {}
                if md.get("cost_input_per_m") is not None:
                    # Adapter la clé selon la devise
                    currency = md.get("currency", "USD").upper()
                    if currency == "EUR":
                        rate_entry["input_cost_per_m_eur"] = md["cost_input_per_m"]
                        rate_entry["output_cost_per_m_eur"] = md.get("cost_output_per_m")
                        if md.get("cost_cached_per_m"):
                            rate_entry["cached_input_per_m_eur"] = md["cost_cached_per_m"]
                    else:
                        rate_entry["input_cost_per_m"] = md["cost_input_per_m"]
                        rate_entry["output_cost_per_m"] = md.get("cost_output_per_m")
                        if md.get("cost_cached_per_m"):
                            rate_entry["input_cache_hit_cost_per_m"] = md["cost_cached_per_m"]
                    if md.get("notes"):
                        rate_entry["note"] = md["notes"]
                    rates[md["id"]] = rate_entry

            if rates:
                api_entry = {
                    "name": p_dict["name"],
                    "type": "Pay-as-you-go" if p_dict["type"] == "pay_as_you_go" else "Free Tier",
                    "rates": rates,
                    "advantages": p_dict.get("notes", ""),
                }
                apis_out.append(api_entry)

        # Local
        local_provider = conn.execute(
            "SELECT * FROM providers WHERE id = 'local'"
        ).fetchone()
        local_out = {}
        if local_provider:
            lp = dict(local_provider)
            local_models = conn.execute(
                "SELECT id FROM models WHERE provider_id = 'local' ORDER BY id"
            ).fetchall()
            local_out = {
                "name": lp["name"],
                "cost_monthly_usd": 0.0,
                "endpoints": [lp.get("api_endpoint", "http://127.0.0.1:1234/v1")],
                "status": "ACTIF",
                "models": [dict(m)["id"] for m in local_models],
                "advantages": lp.get("notes", ""),
            }

        return {
            "subscriptions": subscriptions_out,
            "apis": apis_out,
            "local": local_out,
        }

    except Exception as e:
        logger.error(f"[ModelsDB] Erreur export_to_pricing_json: {e}")
        return {}


def get_all_data() -> Dict[str, Any]:
    """Dump complet de la BDD pour la route /api/models."""
    return {
        "providers": get_all_providers(),
        "models": get_active_models(),
        "api_keys": get_all_api_keys(hide_values=True),
        "subscriptions": get_subscriptions(),
        "routing_rules": get_routing_rules(),
        "quotas": get_all_quotas_realtime(),
        "db_path": _DB_PATH,
        "db_exists": os.path.exists(_DB_PATH),
    }


def get_db_stats() -> Dict[str, Any]:
    """Retourne des statistiques sur le contenu de la BDD."""
    try:
        conn = _get_connection()
        stats = {}
        for table in ["providers", "models", "api_keys", "benchmarks",
                      "routing_rules", "subscriptions", "access_channels", "quota_realtime"]:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            stats[table] = row[0]
        # Nombre de modèles actifs
        stats["active_models"] = conn.execute(
            "SELECT COUNT(*) FROM models WHERE status = 'active'"
        ).fetchone()[0]
        stats["db_path"] = _DB_PATH
        stats["db_size_kb"] = round(os.path.getsize(_DB_PATH) / 1024, 1) if os.path.exists(_DB_PATH) else 0
        return stats
    except Exception as e:
        logger.warning(f"[ModelsDB] Erreur get_db_stats: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# API ASYNC — Wrappers non bloquants pour les coroutines asyncio
#
# Toutes les fonctions SQLite sont synchrones/bloquantes. Ces wrappers
# les délèguent au _DB_EXECUTOR (ThreadPoolExecutor dédié) pour ne pas
# bloquer la boucle asyncio principale du moteur.
#
# Usage depuis une coroutine :
#   from core.models_db import async_update_quota_realtime
#   await async_update_quota_realtime("key_id", used_rpm=5, ...)
# ══════════════════════════════════════════════════════════════════

async def async_get_model(model_id: str) -> Optional[Dict[str, Any]]:
    """Version async de get_model() — ne bloque pas l'event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_model, model_id)


async def async_get_active_models(provider_id: str = None) -> List[Dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_active_models, provider_id)


async def async_get_routing_score(model_id: str) -> float:
    """Version async de get_routing_score() — utilise le cache en mémoire."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_routing_score, model_id)


async def async_get_bulk_routing_scores(model_ids: List[str]) -> Dict[str, float]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_bulk_routing_scores, model_ids)


async def async_get_all_quotas_realtime() -> List[Dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_all_quotas_realtime)


async def async_get_quota_summary() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_quota_summary)


async def async_update_quota_realtime(api_key_id: str, **kwargs) -> bool:
    """Version async de update_quota_realtime() — n'écrit pas dans le thread asyncio."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _DB_EXECUTOR,
        lambda: update_quota_realtime(api_key_id, **kwargs),
    )


async def async_get_db_stats() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_DB_EXECUTOR, get_db_stats)
