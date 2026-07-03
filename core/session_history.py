"""
core/session_history.py — Persistance et replay de l'historique des sessions.

Enregistre chaque session du moteur (prompt, résultat, agents invoqués, durée)
dans SQLite pour permettre la consultation historique et le replay depuis l'IHM.

Créé dans le cadre de l'audit V5.5 (Axe U2 — Historique des sessions).
"""

import re
import time
import json
import sqlite3
import logging
import threading
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

from core.runtime_db import get_connection, get_db_path

_DB_PATH = get_db_path()

_db_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Ouvre ou crée la base SQLite unifiée."""
    return get_connection()


def record_session_start(
    session_id: str,
    objective: str,
    starting_agent: str = "planner",
) -> None:
    """Enregistre le début d'une session."""
    try:
        with _db_lock:
            conn = _get_connection()
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                (session_id, objective, status, started_at, starting_agent)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (session_id, objective, time.time(), starting_agent),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur enregistrement start : {e}")


def record_session_end(
    session_id: str,
    status: str,
    agents_invoked: List[str] = None,
    task_count: int = 0,
    error_message: Optional[str] = None,
    result_summary: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Enregistre la fin d'une session avec les résultats."""
    try:
        with _db_lock:
            conn = _get_connection()

            # Récupérer le timestamp de démarrage pour calculer la durée
            row = conn.execute(
                "SELECT started_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            ended_at = time.time()
            duration_ms = (ended_at - row[0]) * 1000 if row else 0

            conn.execute(
                """
                UPDATE sessions SET
                    status = ?,
                    ended_at = ?,
                    duration_ms = ?,
                    agents_invoked = ?,
                    task_count = ?,
                    error_message = ?,
                    result_summary = ?,
                    metadata = ?
                WHERE session_id = ?
                """,
                (
                    status,
                    ended_at,
                    duration_ms,
                    json.dumps(agents_invoked or []),
                    task_count,
                    error_message,
                    result_summary[:1000] if result_summary else None,
                    json.dumps(metadata) if metadata else None,
                    session_id,
                ),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur enregistrement end : {e}")


def get_sessions(
    limit: int = 50,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retourne les dernières sessions (les plus récentes en premier).

    Args:
        limit: Nombre maximum de sessions à retourner
        status_filter: Filtrer par statut ("success", "error", "running")

    Returns:
        Liste de dictionnaires représentant chaque session.
    """
    try:
        conn = _get_connection()

        if status_filter:
            rows = conn.execute(
                """
                SELECT session_id, objective, status, started_at, ended_at,
                       duration_ms, starting_agent, agents_invoked,
                       task_count, error_message, result_summary
                FROM sessions
                WHERE status = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (status_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT session_id, objective, status, started_at, ended_at,
                       duration_ms, starting_agent, agents_invoked,
                       task_count, error_message, result_summary
                FROM sessions
                ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

        conn.close()

        sessions = []
        for row in rows:
            sessions.append({
                "session_id": row[0],
                "objective": row[1],
                "status": row[2],
                "started_at": row[3],
                "ended_at": row[4],
                "duration_ms": row[5],
                "starting_agent": row[6],
                "agents_invoked": json.loads(row[7]) if row[7] else [],
                "task_count": row[8],
                "error_message": row[9],
                "result_summary": row[10],
            })

        return sessions

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur de lecture : {e}")
        return []


def get_session_detail(session_id: str) -> Optional[Dict[str, Any]]:
    """Retourne le détail complet d'une session par son ID."""
    try:
        conn = _get_connection()
        row = conn.execute(
            """
            SELECT session_id, objective, status, started_at, ended_at,
                   duration_ms, starting_agent, agents_invoked,
                   task_count, error_message, result_summary, metadata
            FROM sessions WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "session_id": row[0],
            "objective": row[1],
            "status": row[2],
            "started_at": row[3],
            "ended_at": row[4],
            "duration_ms": row[5],
            "starting_agent": row[6],
            "agents_invoked": json.loads(row[7]) if row[7] else [],
            "task_count": row[8],
            "error_message": row[9],
            "result_summary": row[10],
            "metadata": json.loads(row[11]) if row[11] else {},
        }

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_session_detail : {e}")
        return None


def get_session_stats() -> Dict[str, Any]:
    """Retourne les statistiques agrégées de l'historique des sessions."""
    try:
        conn = _get_connection()

        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'success'"
        ).fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'error'"
        ).fetchone()[0]
        avg_duration = conn.execute(
            "SELECT AVG(duration_ms) FROM sessions WHERE duration_ms IS NOT NULL"
        ).fetchone()[0]

        conn.close()

        return {
            "total_sessions": total,
            "success_count": success,
            "error_count": errors,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "avg_duration_ms": round(avg_duration, 0) if avg_duration else 0,
        }

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_stats : {e}")
        return {"error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Persistance des tokens en BDD SQLite
# ──────────────────────────────────────────────────────────────────

def _ensure_token_table(conn: sqlite3.Connection) -> None:
    """Crée la table token_usage si elle n'existe pas encore."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            channel TEXT,
            agent_name TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_token_ts
        ON token_usage(timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_token_session
        ON token_usage(session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_token_model
        ON token_usage(model)
    """)
    conn.commit()


def record_token_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float = 0.0,
    session_id: Optional[str] = None,
    channel: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> None:
    """
    Enregistre un appel LLM individuel dans la table token_usage.
    
    Appelé en complément du fichier JSON pour double-écriture.
    La BDD permet des requêtes SQL (agrégations, filtres par date/modèle).
    """
    try:
        with _db_lock:
            conn = _get_connection()
            _ensure_token_table(conn)
            conn.execute(
                """
                INSERT INTO token_usage
                (session_id, timestamp, model, prompt_tokens, completion_tokens,
                 total_tokens, cost_usd, channel, agent_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    time.time(),
                    model,
                    prompt_tokens,
                    completion_tokens,
                    prompt_tokens + completion_tokens,
                    cost_usd,
                    channel,
                    agent_name,
                ),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur record_token_usage : {e}")


def get_token_stats(
    since_hours: Optional[int] = None,
    model_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retourne des statistiques agrégées de consommation de tokens.
    
    Args:
        since_hours: Limite temporelle (ex: 24 = dernières 24h). None = tout.
        model_filter: Filtre sur le nom du modèle (ex: 'deepseek-chat').
    """
    try:
        conn = _get_connection()
        _ensure_token_table(conn)

        conditions = []
        params = []

        if since_hours:
            cutoff = time.time() - (since_hours * 3600)
            conditions.append("timestamp >= ?")
            params.append(cutoff)

        if model_filter:
            conditions.append("model LIKE ?")
            params.append(f"%{model_filter}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt,
                COALESCE(SUM(completion_tokens), 0) as total_completion,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cost_usd), 0) as total_cost
            FROM token_usage {where}
            """,
            params,
        ).fetchone()

        # Détail par modèle
        model_rows = conn.execute(
            f"""
            SELECT model,
                   COUNT(*) as calls,
                   SUM(total_tokens) as tokens,
                   SUM(cost_usd) as cost
            FROM token_usage {where}
            GROUP BY model
            ORDER BY tokens DESC
            """,
            params,
        ).fetchall()

        # Détail par canal
        channel_rows = conn.execute(
            f"""
            SELECT channel,
                   COUNT(*) as calls,
                   SUM(total_tokens) as tokens,
                   SUM(cost_usd) as cost
            FROM token_usage {where}
            GROUP BY channel
            ORDER BY tokens DESC
            """,
            params,
        ).fetchall()

        conn.close()

        return {
            "call_count": row[0],
            "total_prompt_tokens": row[1],
            "total_completion_tokens": row[2],
            "total_tokens": row[3],
            "total_cost_usd": round(row[4], 6),
            "by_model": [
                {"model": r[0], "calls": r[1], "tokens": r[2], "cost_usd": round(r[3], 6)}
                for r in model_rows
            ],
            "by_channel": [
                {"channel": r[0] or "unknown", "calls": r[1], "tokens": r[2], "cost_usd": round(r[3], 6)}
                for r in channel_rows
            ],
        }

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_token_stats : {e}")
        return {"error": str(e)}


def get_token_history(
    limit: int = 100,
    session_id: Optional[str] = None,
    model_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retourne l'historique des appels LLM enregistrés en BDD.
    
    Args:
        limit: Nombre max d'entrées
        session_id: Filtrer par session
        model_filter: Filtrer par modèle
    """
    try:
        conn = _get_connection()
        _ensure_token_table(conn)

        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if model_filter:
            conditions.append("model LIKE ?")
            params.append(f"%{model_filter}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        rows = conn.execute(
            f"""
            SELECT session_id, timestamp, model, prompt_tokens,
                   completion_tokens, total_tokens, cost_usd,
                   channel, agent_name
            FROM token_usage {where}
            ORDER BY timestamp DESC LIMIT ?
            """,
            params,
        ).fetchall()

        conn.close()

        return [
            {
                "session_id": r[0],
                "timestamp": r[1],
                "model": r[2],
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "total_tokens": r[5],
                "cost_usd": r[6],
                "channel": r[7],
                "agent_name": r[8],
            }
            for r in rows
        ]

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_token_history : {e}")
        return []


def get_quotas_from_db() -> Dict[str, int]:
    """
    Calcule les quotas glissants depuis SQLite — tab5-engine UNIQUEMENT.

    Sources :
    - table `token_usage` : appels API du tab5-engine Python (timestamps UNIX précis)
      → Gemini Free Flash/Pro (RPM/TPM/RPD), DeepSeek, Claude CLI via le moteur

    NON INCLUS ICI (données trop grossières ou sans timestamp précis) :
    - ide_conversations.total_tokens : c'est la taille CUMULÉE de la conversation,
      pas la consommation dans une fenêtre glissante → ne jamais sommer pour TPH
    - Consommation Claude.ai Web/Code : accessible uniquement via scraping Puppeteer
      (voir endpoint /api/quotas/claude-realtime)

    Returns: dict plat compatible frontend
    """
    import time as _time

    now   = _time.time()
    t_1m  = now - 60
    t_1h  = now - 3_600
    t_24h = now - 86_400
    t_30d = now - 2_592_000

    try:
        conn = _get_connection()
        _ensure_token_table(conn)

        def _q(channel: str, since_ts: float):
            """Requêtes + tokens pour un canal depuis un timestamp UNIX."""
            row = conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(total_tokens), 0)
                   FROM token_usage WHERE channel = ? AND timestamp >= ?""",
                (channel, since_ts),
            ).fetchone()
            return row[0], row[1]

        # ─── Gemini Free Flash ───
        ff_rpm, ff_tpm = _q("gemini-free-flash", t_1m)
        ff_rpd, _      = _q("gemini-free-flash", t_24h)

        # ─── Gemini Free Pro ───
        fp_rpm, fp_tpm = _q("gemini-free-pro", t_1m)
        fp_rpd, _      = _q("gemini-free-pro", t_24h)

        # ─── Claude CLI via moteur (commandes passées par le moteur Python uniquement) ───
        _, cl_moteur_tph = _q("claude-cli-abo", t_1h)
        _, cl_moteur_tpm = _q("claude-cli-abo", t_30d)

        # ─── Gemini CLI via moteur ───
        _, gc_tph = _q("gemini-cli-abo", t_1h)
        _, gc_tpm = _q("gemini-cli-abo", t_30d)

        conn.close()

        return {
            # Gemini Free Flash (API moteur)
            "gemini_free_flash_rpm": ff_rpm,
            "gemini_free_flash_tpm": ff_tpm,
            "gemini_free_flash_rpd": ff_rpd,
            # Gemini Free Pro (API moteur)
            "gemini_free_pro_rpm": fp_rpm,
            "gemini_free_pro_tpm": fp_tpm,
            "gemini_free_pro_rpd": fp_rpd,
            # Claude CLI via moteur SEULEMENT (pas les sessions IDE — voir /api/quotas/claude-realtime)
            "claude_cli_abo_tph": cl_moteur_tph,
            "claude_cli_abo_tpm": cl_moteur_tpm,
            "claude_cli_tph":     cl_moteur_tph,
            "claude_cli_tpm":     cl_moteur_tpm,
            # Gemini CLI via moteur
            "gemini_cli_abo_tph": gc_tph,
            "gemini_cli_abo_tpm": gc_tpm,
            "gemini_cli_tph":     gc_tph,
            "gemini_cli_tpm":     gc_tpm,
        }

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_quotas_from_db : {e}")
        return {}




# ──────────────────────────────────────────────────────────────────
# Persistance des conversations IDE (Antigravity + Claude CLI)
# ──────────────────────────────────────────────────────────────────

def canonical_project(label: str) -> str:
    """Normalise un libellé de projet en retirant le préfixe de lettre de lecteur.

    Unifie le split d'identité Claude (P3) : 'E--AuxFilsDesIdees-moteur-agents' et
    'h--AuxFilsDesIdees-moteur-agents' → 'AuxFilsDesIdees-moteur-agents'. Les libellés
    sans préfixe (ex: 'antigravity-ide') sont renvoyés inchangés.
    """
    if not label:
        return ""
    return re.sub(r'^[A-Za-z]--', '', label)


def _ensure_ide_conversations_table(conn: sqlite3.Connection) -> None:
    """Crée la table ide_conversations si elle n'existe pas, et migre les colonnes P3."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ide_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            objective TEXT,
            first_timestamp TEXT,
            last_timestamp TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0,
            user_messages INTEGER DEFAULT 0,
            model_responses INTEGER DEFAULT 0,
            models_json TEXT,
            is_subscription INTEGER DEFAULT 1,
            project TEXT,
            api_calls INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            estimation_method TEXT DEFAULT 'chars_ratio',
            imported_at REAL NOT NULL,
            canonical_project TEXT,
            transcript_ref TEXT
        )
    """)
    # ─── Migration P3 (auto, idempotente) : colonnes ajoutées sur les bases existantes ───
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(ide_conversations)")}
    if "canonical_project" not in existing_cols:
        conn.execute("ALTER TABLE ide_conversations ADD COLUMN canonical_project TEXT")
    if "transcript_ref" not in existing_cols:
        conn.execute("ALTER TABLE ide_conversations ADD COLUMN transcript_ref TEXT")
    # Backfill INCONDITIONNEL (idempotent) : remplit canonical_project sur les lignes
    # non encore renseignées — robuste même si la colonne a été ajoutée ailleurs (runtime_db).
    for cid, proj, src in conn.execute(
        "SELECT conversation_id, project, source FROM ide_conversations "
        "WHERE canonical_project IS NULL OR canonical_project = ''"
    ).fetchall():
        conn.execute(
            "UPDATE ide_conversations SET canonical_project = ? WHERE conversation_id = ?",
            (canonical_project(proj or src or ""), cid),
        )
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ide_conv_source
        ON ide_conversations(source)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ide_conv_ts
        ON ide_conversations(first_timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ide_conv_canon
        ON ide_conversations(canonical_project)
    """)
    conn.commit()


def upsert_ide_conversation(session_data: Dict[str, Any]) -> bool:
    """
    Insère ou met à jour une conversation IDE dans la BDD.
    
    Utilise INSERT OR REPLACE sur conversation_id pour la déduplication.
    Retourne True si l'opération a réussi, False sinon.
    """
    try:
        conv_id = session_data.get("conversation_id", "")
        if not conv_id:
            return False
        
        with _db_lock:
            conn = _get_connection()
            _ensure_ide_conversations_table(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO ide_conversations
                (conversation_id, source, objective, first_timestamp, last_timestamp,
                 prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd,
                 user_messages, model_responses, models_json, is_subscription,
                 project, api_calls, cache_read_tokens, cache_creation_tokens,
                 estimation_method, imported_at, canonical_project, transcript_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id,
                    session_data.get("channel", "unknown"),
                    session_data.get("objective", ""),
                    session_data.get("timestamp", ""),
                    session_data.get("last_activity", ""),
                    session_data.get("prompt_tokens", 0),
                    session_data.get("completion_tokens", 0),
                    session_data.get("total_tokens", 0),
                    session_data.get("estimated_cost_usd", 0.0),
                    session_data.get("user_messages", 0),
                    session_data.get("model_responses", 0),
                    json.dumps(session_data.get("models", {})),
                    1 if session_data.get("is_subscription", True) else 0,
                    session_data.get("project", ""),
                    session_data.get("api_calls", 0),
                    session_data.get("cache_read_tokens", 0),
                    session_data.get("cache_creation_tokens", 0),
                    session_data.get("estimation_method", "chars_ratio"),
                    time.time(),
                    canonical_project(session_data.get("project") or session_data.get("channel") or ""),
                    session_data.get("transcript_ref", ""),
                ),
            )
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur upsert_ide_conversation : {e}")
        return False


def bulk_upsert_ide_conversations(sessions: List[Dict[str, Any]]) -> int:
    """
    Insère en masse des conversations IDE dans la BDD.
    
    Retourne le nombre de conversations insérées/mises à jour.
    """
    count = 0
    try:
        with _db_lock:
            conn = _get_connection()
            _ensure_ide_conversations_table(conn)
            now = time.time()
            
            for session_data in sessions:
                conv_id = session_data.get("conversation_id", "")
                if not conv_id:
                    continue
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ide_conversations
                        (conversation_id, source, objective, first_timestamp, last_timestamp,
                         prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd,
                         user_messages, model_responses, models_json, is_subscription,
                         project, api_calls, cache_read_tokens, cache_creation_tokens,
                         estimation_method, imported_at, canonical_project, transcript_ref)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            conv_id,
                            session_data.get("channel", "unknown"),
                            session_data.get("objective", ""),
                            session_data.get("timestamp", ""),
                            session_data.get("last_activity", ""),
                            session_data.get("prompt_tokens", 0),
                            session_data.get("completion_tokens", 0),
                            session_data.get("total_tokens", 0),
                            session_data.get("estimated_cost_usd", 0.0),
                            session_data.get("user_messages", 0),
                            session_data.get("model_responses", 0),
                            json.dumps(session_data.get("models", {})),
                            1 if session_data.get("is_subscription", True) else 0,
                            session_data.get("project", ""),
                            session_data.get("api_calls", 0),
                            session_data.get("cache_read_tokens", 0),
                            session_data.get("cache_creation_tokens", 0),
                            session_data.get("estimation_method", "chars_ratio"),
                            now,
                            canonical_project(session_data.get("project") or session_data.get("channel") or ""),
                            session_data.get("transcript_ref", ""),
                        ),
                    )
                    count += 1
                except Exception as inner_e:
                    logger.warning(f"[SESSION HISTORY] Erreur bulk upsert {conv_id[:8]}: {inner_e}")
            
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur bulk_upsert : {e}")
    
    logger.info(f"[SESSION HISTORY] {count}/{len(sessions)} conversations IDE persistées en BDD")
    return count


def get_ide_conversations(
    limit: int = 100,
    source_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retourne les conversations IDE stockées en BDD.
    
    Args:
        limit: Nombre max d'entrées
        source_filter: Filtrer par source ('antigravity_ide' ou 'claude_cli')
    """
    try:
        conn = _get_connection()
        _ensure_ide_conversations_table(conn)

        conditions = []
        params = []

        if source_filter:
            conditions.append("source = ?")
            params.append(source_filter)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        rows = conn.execute(
            f"""
            SELECT conversation_id, source, objective, first_timestamp,
                   last_timestamp, prompt_tokens, completion_tokens,
                   total_tokens, estimated_cost_usd, user_messages,
                   model_responses, models_json, is_subscription,
                   project, api_calls, estimation_method, imported_at
            FROM ide_conversations {where}
            ORDER BY first_timestamp DESC LIMIT ?
            """,
            params,
        ).fetchall()

        conn.close()

        return [
            {
                "conversation_id": r[0],
                "source": r[1],
                "objective": r[2],
                "timestamp": r[3],
                "last_activity": r[4],
                "prompt_tokens": r[5],
                "completion_tokens": r[6],
                "total_tokens": r[7],
                "estimated_cost_usd": r[8],
                "user_messages": r[9],
                "model_responses": r[10],
                "models": json.loads(r[11]) if r[11] else {},
                "is_subscription": bool(r[12]),
                "project": r[13],
                "api_calls": r[14],
                "estimation_method": r[15],
                "imported_at": r[16],
            }
            for r in rows
        ]

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_ide_conversations : {e}")
        return []


def get_ide_conversations_stats() -> Dict[str, Any]:
    """Retourne les statistiques agrégées des conversations IDE."""
    try:
        conn = _get_connection()
        _ensure_ide_conversations_table(conn)

        total = conn.execute("SELECT COUNT(*) FROM ide_conversations").fetchone()[0]
        by_source = conn.execute(
            """
            SELECT source, COUNT(*), SUM(total_tokens), SUM(estimated_cost_usd)
            FROM ide_conversations GROUP BY source
            """
        ).fetchall()
        total_tokens = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) FROM ide_conversations"
        ).fetchone()[0]
        total_cost = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM ide_conversations"
        ).fetchone()[0]

        conn.close()

        return {
            "total_conversations": total,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "by_source": [
                {
                    "source": r[0],
                    "count": r[1],
                    "tokens": r[2] or 0,
                    "cost_usd": round(r[3] or 0, 6),
                }
                for r in by_source
            ],
        }

    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_ide_stats : {e}")
        return {"error": str(e)}


def get_combined_cost_from_db() -> Dict[str, Any]:
    """Calcule le coût combiné moteur + CLI/IDE depuis la BDD SQLite.
    
    Source de vérité unique — remplace le calcul JSON + cache mémoire.
    Retourne un dict compatible avec le champ `combined_total` de l'API /api/tokens.
    """
    try:
        conn = _get_connection()
        
        # 1. Coût moteur (table token_usage) — tous les appels API du moteur
        moteur = conn.execute("""
            SELECT COALESCE(SUM(total_tokens), 0),
                   COALESCE(SUM(prompt_tokens), 0),
                   COALESCE(SUM(completion_tokens), 0),
                   COALESCE(SUM(cost_usd), 0)
            FROM token_usage
        """).fetchone()
        
        # 2. Coût CLI/IDE (table ide_conversations) — toutes les sessions
        ide_total = conn.execute("""
            SELECT COALESCE(SUM(total_tokens), 0),
                   COALESCE(SUM(estimated_cost_usd), 0),
                   COUNT(*)
            FROM ide_conversations
        """).fetchone()
        
        # 3. Coût CLI/IDE uniquement les APIs payantes (pas abonnements)
        ide_payant = conn.execute("""
            SELECT COALESCE(SUM(estimated_cost_usd), 0)
            FROM ide_conversations
            WHERE is_subscription = 0
        """).fetchone()
        
        # 4. Ventilation par source
        by_source = conn.execute("""
            SELECT source, 
                   COUNT(*) as sessions,
                   COALESCE(SUM(total_tokens), 0),
                   COALESCE(SUM(estimated_cost_usd), 0),
                   SUM(CASE WHEN is_subscription=1 THEN 1 ELSE 0 END) as nb_abo
            FROM ide_conversations
            GROUP BY source
        """).fetchall()
        
        conn.close()
        
        moteur_tokens = moteur[0]
        moteur_cost = moteur[3]
        cli_tokens = ide_total[0]
        cli_cost_abo = ide_total[1]  # Valeur estimée (abonnements inclus)
        cli_cost_payant = ide_payant[0]  # Coût réel APIs payantes uniquement
        
        return {
            "moteur_tokens": moteur_tokens,
            "moteur_prompt": moteur[1],
            "moteur_completion": moteur[2],
            "moteur_cost_usd": round(moteur_cost, 6),
            "cli_tokens": cli_tokens,
            "cli_sessions": ide_total[2],
            "cli_cost_usd": round(cli_cost_payant, 6),        # Coût réel (APIs payantes)
            "cli_cost_estimated_usd": round(cli_cost_abo, 6),  # Valeur estimée (abo inclus)
            "grand_total": moteur_tokens + cli_tokens,
            "grand_cost_usd": round(moteur_cost + cli_cost_payant, 6),  # Total réel
            "by_source": [
                {
                    "source": r[0],
                    "sessions": r[1],
                    "tokens": r[2],
                    "cost_usd": round(r[3], 6),
                    "subscriptions": r[4],
                }
                for r in by_source
            ],
            "source": "sqlite",  # Marqueur pour traçabilité
        }
        
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_combined_cost : {e}")
        return None


# =====================================================================
# HISTORIQUE QUOTAS & BILLING
# =====================================================================

def insert_quota_snapshot(quotas: Dict[str, Any]) -> int:
    """Enregistre un snapshot des quotas actuels en BDD.
    
    Appelé toutes les 60s par le sse_quota_pusher_loop.
    Ne stocke que les métriques non-nulles pour économiser l'espace.
    """
    if not quotas:
        return 0
    
    # Mapping des clés API → (channel, metric, max, window_seconds)
    QUOTA_MAP = {
        'gemini_free_flash_rpm': ('gemini_free_flash', 'rpm', 15, 60),
        'gemini_free_flash_rpd': ('gemini_free_flash', 'rpd', 1500, 86400),
        'gemini_free_flash_tpm': ('gemini_free_flash', 'tpm', 1000000, 60),
        'gemini_free_pro_rpm': ('gemini_free_pro', 'rpm', 2, 60),
        'gemini_free_pro_rpd': ('gemini_free_pro', 'rpd', 50, 86400),
        'gemini_free_pro_tpm': ('gemini_free_pro', 'tpm', 32000, 60),
        'claude_cli_tph': ('claude_cli', 'tph', 1500000, 3600),
        'claude_cli_tpm': ('claude_cli', 'tpm', 35000000, 2592000),
        'gemini_cli_tph': ('gemini_cli', 'tph', 4000000, 3600),
        'gemini_cli_tpm': ('gemini_cli', 'tpm', 100000000, 2592000),
    }
    
    try:
        conn = _get_connection()
        ts = time.time()
        inserted = 0
        for key, (channel, metric, max_val, window) in QUOTA_MAP.items():
            val = quotas.get(key, 0) or 0
            if val > 0:  # Ne stocker que les valeurs non-nulles
                conn.execute(
                    "INSERT INTO quota_snapshots (timestamp, channel, metric, value, max_value, window_seconds) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, channel, metric, val, max_val, window)
                )
                inserted += 1
        conn.commit()
        conn.close()
        return inserted
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur insert_quota_snapshot : {e}")
        return 0


def get_quota_history(hours: int = 24, channel: str = None, metric: str = None) -> List[Dict]:
    """Récupère l'historique des snapshots de quotas.
    
    Args:
        hours: Fenêtre temporelle (défaut 24h)
        channel: Filtrer par canal (ex: 'gemini_free_flash')
        metric: Filtrer par métrique (ex: 'rpd')
    """
    try:
        conn = _get_connection()
        since = time.time() - (hours * 3600)
        
        query = "SELECT timestamp, channel, metric, value, max_value FROM quota_snapshots WHERE timestamp > ?"
        params = [since]
        
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        if metric:
            query += " AND metric = ?"
            params.append(metric)
        
        query += " ORDER BY timestamp ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        
        return [
            {
                "timestamp": r[0],
                "channel": r[1],
                "metric": r[2],
                "value": r[3],
                "max_value": r[4],
                "pct": round(r[3] / r[4] * 100, 1) if r[4] > 0 else 0,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_quota_history : {e}")
        return []


def insert_billing_record(provider: str, metric: str, value: float,
                          currency: str = "USD", sync_source: str = "api") -> bool:
    """Enregistre un point de données de facturation.
    
    Appelé à chaque synchronisation DeepSeek/GCP/Claude.
    """
    try:
        conn = _get_connection()
        conn.execute(
            "INSERT INTO billing_history (timestamp, provider, metric, value, currency, sync_source) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), provider, metric, value, currency, sync_source)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur insert_billing_record : {e}")
        return False


def get_billing_history(days: int = 30, provider: str = None) -> List[Dict]:
    """Récupère l'historique de facturation.
    
    Args:
        days: Fenêtre temporelle (défaut 30 jours)
        provider: Filtrer par provider (ex: 'deepseek', 'gcp')
    """
    try:
        conn = _get_connection()
        since = time.time() - (days * 86400)
        
        query = "SELECT timestamp, provider, metric, value, currency, sync_source FROM billing_history WHERE timestamp > ?"
        params = [since]
        
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        
        query += " ORDER BY timestamp ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        
        return [
            {
                "timestamp": r[0],
                "provider": r[1],
                "metric": r[2],
                "value": r[3],
                "currency": r[4],
                "sync_source": r[5],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur get_billing_history : {e}")
        return []


def cleanup_old_snapshots(retention_days: int = 90):
    """Nettoie les vieux snapshots de quotas pour limiter la taille de la BDD.
    
    Conserve indéfiniment l'historique de consommation réelle (billing_history)
    et de chat (sessions) conformément aux préférences de l'utilisateur.
    Purge les tables techniques d'exécution après 14 jours (TTL runtime)
    et les métriques unitaires de routage après 90 jours (TTL metrics).
    Optimise et défragmente ensuite la base de données (VACUUM).
    """
    try:
        with _db_lock:
            conn = _get_connection()
            
            # 1. Rétention quotas (90 jours par défaut)
            cutoff_quotas = time.time() - (retention_days * 86400)
            deleted_q = conn.execute("DELETE FROM quota_snapshots WHERE timestamp < ?", (cutoff_quotas,)).rowcount
            
            # 2. Rétention métriques unitaires (90 jours par défaut)
            deleted_r = conn.execute("DELETE FROM routing_decisions WHERE timestamp < ?", (cutoff_quotas,)).rowcount
            deleted_t = conn.execute("DELETE FROM token_usage WHERE timestamp < ?", (cutoff_quotas,)).rowcount
            
            # 3. Rétention exécutions techniques (14 jours de TTL)
            cutoff_runtime = time.time() - (14 * 86400)
            deleted_tasks = conn.execute("DELETE FROM dag_tasks WHERE started_at < ? OR (ended_at IS NOT NULL AND ended_at < ?)", (cutoff_runtime, cutoff_runtime)).rowcount
            deleted_edges = conn.execute("DELETE FROM dag_edges WHERE session_id IN (SELECT session_id FROM sessions WHERE started_at < ?)", (cutoff_runtime,)).rowcount
            deleted_memory = conn.execute("DELETE FROM scoped_memory WHERE session_id IN (SELECT session_id FROM sessions WHERE started_at < ?)", (cutoff_runtime,)).rowcount
            deleted_steps = conn.execute("DELETE FROM agent_steps WHERE timestamp < ?", (cutoff_runtime,)).rowcount
            
            # 4. Rétention checkpoints (48 heures)
            deleted_chk = conn.execute("DELETE FROM checkpoints WHERE datetime(updated_at) < datetime('now', '-2 days')").rowcount
            
            conn.commit()
            
            total_deleted = deleted_q + deleted_r + deleted_t + deleted_tasks + deleted_edges + deleted_memory + deleted_steps + deleted_chk
            
            # 5. Optimisation physique de la base de données (defragmentation)
            if total_deleted > 0:
                conn.execute("VACUUM")
                logger.info(
                    f"[SESSION HISTORY] Maintenance DB terminée : {total_deleted} lignes techniques purgées. "
                    f"(snapshots: {deleted_q}, routing: {deleted_r}, tokens: {deleted_t}, "
                    f"tasks: {deleted_tasks}, edges: {deleted_edges}, scope_mem: {deleted_memory}, "
                    f"steps: {deleted_steps}, chk: {deleted_chk}). Base défragmentée via VACUUM."
                )
            conn.close()
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur lors de la maintenance DB : {e}")


# ──────────────────────────────────────────────────────────────────
# [B10-Fix] Nettoyage des sessions zombies (running depuis >1h)
# ──────────────────────────────────────────────────────────────────

def cleanup_zombie_sessions() -> int:
    """
    Marque comme 'error' les sessions restées en statut 'running' depuis plus d'une heure.
    
    Une session zombie est une session dont le statut est 'running' mais qui a démarré
    il y a plus de 3600 secondes (1h). Cela peut arriver si le moteur plante ou si
    une session n'est pas correctement finalisée.
    
    Returns:
        Nombre de sessions zombies nettoyées.
    """
    try:
        with _db_lock:
            conn = _get_connection()
            cursor = conn.execute(
                """
                UPDATE sessions SET
                    status = 'error',
                    ended_at = strftime('%s', 'now'),
                    error_message = 'Session zombie - nettoyée automatiquement (running depuis >1h)'
                WHERE status = 'running'
                  AND started_at < strftime('%s', 'now') - 3600
                """
            )
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            
            if affected > 0:
                logger.warning(f"[SESSION HISTORY] {affected} session(s) zombie(s) nettoyée(s)")
            return affected
    except Exception as e:
        logger.warning(f"[SESSION HISTORY] Erreur cleanup_zombie_sessions : {e}")
        return 0
