"""
core/runtime_db.py — Base de données relationnelle unifiée moteur_runtime.db.

Centralise l'accès et le schéma de toutes les tables du moteur :
- Historique de sessions et quotas (auparavant dans session_history.db)
- Scores ELO et routage (auparavant dans routing_metrics.db)
- Checkpoints de sessions (auparavant dans checkpoints.db)
- [V8/V9] Tâches DAG, relations, Swarm workers, logs d'étapes ReAct et mémoire cloisonnée.

Sécurité ACID, mode WAL activé, gestion multi-thread sécurisée par verrous.
"""

import logging
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from typing import Any

try:
    import aiosqlite
    _AIOSQLITE_AVAILABLE = True
except ImportError:
    _AIOSQLITE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Chemin unique et centralisé de la base de données (peut être écrasé pour les tests)
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "moteur_runtime.db",
)


def override_db_path(new_path: str) -> None:
    """Écrase le chemin de la base de données (utile pour l'isolation des tests unitaires)."""
    global _DB_PATH
    _DB_PATH = new_path
    logger.info(f"[RUNTIME_DB] Chemin de base de données écrasé : {_DB_PATH}")

# Verrou global pour synchroniser les écritures SQLite sur les threads Python
_db_lock = threading.Lock()


def get_db_path() -> str:
    """Retourne le chemin absolu de la base de données unifiée."""
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """
    Crée une connexion SQLite synchrone vers la base unifiée avec optimisations WAL/Synchronous,
    et initialise automatiquement tout le schéma relationnel s'il n'existe pas.
    
    Usage : `with get_connection() as conn:` (context manager SQLite standard)
    """
    conn = sqlite3.connect(_DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


@asynccontextmanager
async def get_async_connection():
    """
    Connexion SQLite ASYNCHRONE via aiosqlite.
    
    Exécute toutes les opérations SQLite dans un thread séparé via asyncio.to_thread,
    ce qui évite de bloquer la boucle asyncio pendant les écritures DAG concurrentes.
    
    Usage :
        async with get_async_connection() as db:
            await db.execute("UPDATE dag_tasks SET status = ? WHERE ...", (status, ...))
            await db.commit()

    Fallback synchrone si aiosqlite n'est pas disponible.
    """
    # Garantir que le schéma est créé (notamment pour les bases de tests temporaires)
    try:
        conn_init = get_connection()
        conn_init.close()
    except Exception as e:
        logger.error(f"[RUNTIME_DB] Erreur lors de l'initialisation du schéma : {e}")

    if not _AIOSQLITE_AVAILABLE:
        logger.warning("[RUNTIME_DB] aiosqlite non disponible — utilisation du fallback asynchrone simulé")
        conn = get_connection()
        class AsyncConnectionWrapper:
            def __init__(self, c):
                self._c = c
            async def execute(self, sql, parameters=()):
                import asyncio
                return await asyncio.to_thread(self._c.execute, sql, parameters)
            async def commit(self):
                import asyncio
                await asyncio.to_thread(self._c.commit)
            async def close(self):
                import asyncio
                await asyncio.to_thread(self._c.close)
        wrapper = AsyncConnectionWrapper(conn)
        try:
            yield wrapper
        finally:
            conn.close()
        return

    async with aiosqlite.connect(_DB_PATH, timeout=10.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """
    Ajoute les colonnes manquantes à une table existante (migration additive idempotente).

    Permet d'aligner les bases créées avec un ancien schéma sans recréer la table.
    Ne touche pas aux colonnes déjà présentes (ALTER TABLE ... ADD COLUMN uniquement).
    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_def in columns.items():
        if col_name not in existing:
            logger.info(f"[RUNTIME_DB] Migration : ajout de la colonne '{col_name}' à '{table}'.")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


def _init_schema(conn: sqlite3.Connection) -> None:
    """Initialise le schéma global de la base de données unifiée."""
    # ─── PARTIE SESSIONS & HISTORIQUE (Ex-session_history.db) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            objective TEXT NOT NULL,
            status TEXT DEFAULT 'running',
            started_at REAL NOT NULL,
            ended_at REAL,
            duration_ms REAL,
            starting_agent TEXT,
            agents_invoked TEXT,
            task_count INTEGER DEFAULT 0,
            error_message TEXT,
            result_summary TEXT,
            metadata TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS quota_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            channel TEXT NOT NULL,
            metric TEXT NOT NULL,
            value INTEGER DEFAULT 0,
            max_value INTEGER NOT NULL,
            window_seconds INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quota_ts ON quota_snapshots(timestamp DESC)")

    # Schéma canonique unique de billing_history (#T64) : inclut les colonnes
    # historiquement ajoutées à chaud par budget_guard (model, tokens_used,
    # cost_usd, window_type). budget_guard ne définit plus son propre schéma.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            provider TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            sync_source TEXT,
            model TEXT,
            tokens_used INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            window_type TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_billing_ts ON billing_history(timestamp DESC)")
    # Migration additive pour les bases existantes créées avec l'ancien schéma 7 colonnes.
    _ensure_columns(conn, "billing_history", {
        "model": "TEXT",
        "tokens_used": "INTEGER DEFAULT 0",
        "cost_usd": "REAL DEFAULT 0.0",
        "window_type": "TEXT",
    })

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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ts ON token_usage(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage(model)")

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
    # Migration P3 idempotente : ajoute les colonnes sur les bases existantes
    # (CREATE TABLE IF NOT EXISTS ne les ajoute pas si la table préexiste).
    _ic_cols = {r[1] for r in conn.execute("PRAGMA table_info(ide_conversations)")}
    if "canonical_project" not in _ic_cols:
        conn.execute("ALTER TABLE ide_conversations ADD COLUMN canonical_project TEXT")
    if "transcript_ref" not in _ic_cols:
        conn.execute("ALTER TABLE ide_conversations ADD COLUMN transcript_ref TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ide_conv_source ON ide_conversations(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ide_conv_ts ON ide_conversations(first_timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ide_conv_canon ON ide_conversations(canonical_project)")

    # ─── PARTIE ROUTAGE & ELO (Ex-routing_metrics.db) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_elo_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            domain TEXT NOT NULL,
            elo_score REAL NOT NULL DEFAULT 1500.0,
            total_matches INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            avg_latency_ms REAL,
            last_updated REAL NOT NULL,
            UNIQUE(model_name, domain)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elo_domain ON model_elo_scores(domain, elo_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elo_model ON model_elo_scores(model_name)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            user_prompt_hash TEXT NOT NULL,
            prompt_length INTEGER,
            dominant_category TEXT,
            routing_type TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            model_tier TEXT,
            resolved_model TEXT,
            is_complex INTEGER DEFAULT 0,
            fast_path_used INTEGER DEFAULT 1,
            llm_classifier_used INTEGER DEFAULT 0,
            llm_confidence REAL,
            context_categories TEXT,
            latency_ms REAL,
            success INTEGER DEFAULT 1,
            session_id TEXT,
            error_category TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_routing_timestamp ON routing_decisions(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_routing_category ON routing_decisions(dominant_category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_routing_session ON routing_decisions(session_id)")

    # ─── PARTIE CHECKPOINTS (Ex-checkpoints.db) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoints (
            session_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            state_json TEXT NOT NULL,
            history_count INTEGER DEFAULT 0,
            size_bytes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ─── NOUVELLES TABLES DU DAG RÉACTIF ET SWARM (V8/V9) ───

    # dag_tasks : Suivi unitaire de chaque tâche éphémère ou réactive
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dag_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',          -- pending, running, success, error, blocked
            worker_id TEXT,                         -- ID du worker Swarm ou 'local'
            inputs_json TEXT,                       -- Arguments et paramètres de la tâche
            outputs_json TEXT,                      -- Résultats de la tâche
            depends_on_json TEXT,                   -- Liste des task_id parents attendus
            started_at REAL,
            ended_at REAL,
            error_message TEXT,
            UNIQUE(session_id, task_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dag_tasks_session ON dag_tasks(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dag_tasks_status ON dag_tasks(status)")

    # dag_edges : Dépendances explicites parent-enfant
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dag_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            parent_task_id TEXT NOT NULL,
            child_task_id TEXT NOT NULL,
            UNIQUE(session_id, parent_task_id, child_task_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dag_edges_session ON dag_edges(session_id)")

    # swarm_workers : Registre temps réel des instances distributives
    conn.execute("""
        CREATE TABLE IF NOT EXISTS swarm_workers (
            worker_id TEXT PRIMARY KEY,
            ip TEXT NOT NULL,
            port INTEGER NOT NULL,
            status TEXT DEFAULT 'offline',          -- online, busy, offline
            capabilities_json TEXT,                 -- Liste des tools / agents supportés
            avg_latency_ms REAL DEFAULT 0.0,
            active_tasks INTEGER DEFAULT 0,
            last_seen REAL NOT NULL
        )
    """)

    # agent_steps : Logs pas-à-pas de l'exécution ReAct (Auditabilité fine)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            task_id TEXT,
            step_index INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            phase TEXT NOT NULL,                    -- planning, execution, review, correction
            thought TEXT,
            tool_calls_json TEXT,                   -- Outil appelé + args
            observations_json TEXT,                 -- Retour de l'outil
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            timestamp REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_session_task ON agent_steps(session_id, task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_timestamp ON agent_steps(timestamp DESC)")

    # scoped_memory : Espaces mémoires hiérarchisés du Swarm (MapReduce contextuel)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scoped_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,                 -- 'global', 'swarm_X', 'task_Y'
            parent_scope_id TEXT,
            key TEXT NOT NULL,
            value_json TEXT,
            UNIQUE(session_id, scope_id, key)
        )
    """)

    # ─── PERSISTANCE DES CONVERSATIONS CHAT IHM (T83) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_session_id TEXT NOT NULL,   -- UUID généré par le frontend (localStorage)
            role TEXT NOT NULL,              -- 'user' | 'assistant'
            content TEXT NOT NULL,
            agents_used TEXT,                -- JSON array de strings
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(chat_session_id, created_at ASC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            chat_session_id TEXT PRIMARY KEY,
            title TEXT,                      -- résumé court (premier message utilisateur tronqué)
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0
        )
    """)
    _ensure_columns(conn, "chat_messages", {
        "agents_used": "TEXT",
    })

    # ─── AUDIT VOCAL Tab5/Assist (diagnostic STT → moteur) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vocal_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT NOT NULL,
            user_prompt TEXT NOT NULL,
            source_type TEXT,
            source_mode TEXT,
            tts_enabled INTEGER DEFAULT 0,
            device_id TEXT,
            routing_type TEXT,
            agents_used TEXT,
            response_text TEXT,
            latency_ms REAL,
            phase TEXT DEFAULT 'response'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vocal_audit_created ON vocal_audit_log(created_at DESC)"
    )

    # ─── Historique vocal Discussion multi-tour (Sprint A3) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vocal_conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            source_mode TEXT DEFAULT 'chat',
            device_id TEXT,
            created_at REAL NOT NULL,
            UNIQUE(conversation_id, turn_index)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vocal_conv_turns ON vocal_conversation_turns(conversation_id, turn_index)"
    )

    # ─── Jobs vocaux Discussion async (Sprint B) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vocal_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            intent TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            user_prompt TEXT NOT NULL,
            conversation_id TEXT,
            device_id TEXT,
            session_id TEXT,
            result_text TEXT,
            error_message TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vocal_jobs_status ON vocal_jobs(status, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vocal_jobs_conv ON vocal_jobs(conversation_id, created_at DESC)"
    )

    conn.commit()


def write_scoped_var(session_id: str, scope_id: str, parent_scope_id: str | None, key: str, value: Any) -> None:
    """
    Enregistre ou met à jour une variable dans la mémoire d'un scope donné.
    """
    import json

    val_json = json.dumps(value)
    # Remplacer la connexion pour utiliser get_connection() avec gestion du lock si multi-thread,
    # mais get_connection() est thread-safe sous SQLite WAL.
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO scoped_memory 
            (session_id, scope_id, parent_scope_id, key, value_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, scope_id, parent_scope_id, key, val_json)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_scoped_vars(session_id: str, scope_id: str) -> dict:
    """
    Récupère toutes les variables visibles depuis un scope donné,
    en remontant la hiérarchie des scopes parents jusqu'à la racine (ex: 'global').
    Les clés des scopes enfants surchargent celles des scopes parents.
    """
    import json

    # 1. Résoudre la hiérarchie des scopes
    scopes_chain = []
    current_scope = scope_id
    visited = set()

    while current_scope and current_scope not in visited:
        visited.add(current_scope)
        scopes_chain.append(current_scope)

        # Récupérer le parent du scope courant
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT DISTINCT parent_scope_id FROM scoped_memory WHERE session_id = ? AND scope_id = ?",
                (session_id, current_scope)
            ).fetchone()
        finally:
            conn.close()

        if row and row[0]:
            current_scope = row[0]
        else:
            if current_scope != 'global':
                current_scope = 'global'
            else:
                current_scope = None

    if 'global' not in visited:
        scopes_chain.append('global')

    # Inverser pour appliquer du parent éloigné au plus proche (surcharge)
    scopes_chain.reverse()

    # 2. Charger les variables de chaque scope
    result = {}
    conn = get_connection()
    try:
        for s in scopes_chain:
            cursor = conn.execute(
                "SELECT key, value_json FROM scoped_memory WHERE session_id = ? AND scope_id = ?",
                (session_id, s)
            )
            for row in cursor.fetchall():
                key, val_json = row
                try:
                    result[key] = json.loads(val_json) if val_json else None
                except Exception:
                    result[key] = val_json
    finally:
        conn.close()

    return result
