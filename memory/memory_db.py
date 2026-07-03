"""
memory_db.py — Base de données SQLite unifiée pour la mémoire active de l'agent.

Unifie le FactStore, l'EpisodeStore, le SkillStore et le Graphe MCP en un seul
point d'accès requêtable avec transactions ACID et mode WAL.

Architecture hybride :
  - Les fichiers Markdown (contexte_ia/) restent la source de vérité Git.
  - Cette base SQLite sert de cache d'accès rapide pour les recherches et l'injection.
  - La synchronisation bidirectionnelle est assurée par seed_memory_db.py (Markdown → DB)
    et par le hook FIN DE SESSION (DB → Markdown).

Auteur : Antigravity IDE + Axel
Dernière mise à jour : 2026-06-16 (Refactoring v12.1.0)
"""

import os
import sqlite3
import time
import logging
import threading
import struct
import asyncio
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

logger = logging.getLogger("memory.memory_db")

# Chemin par défaut de la base de données (à côté de session_history.db)
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "memory.db"
)


@dataclass
class Fact:
    """Représente un fait ou une leçon apprise stockée en base."""
    id: int
    category: str           # ex: "esphome", "moteur", "gcp", "hmi", "infra"
    title: str              # titre court du fait
    content: str            # contenu détaillé (Markdown)
    source_file: str        # fichier Markdown d'origine
    tags: str               # tags séparés par des virgules
    created_at: float       # timestamp de création
    updated_at: float       # timestamp de dernière mise à jour
    relevance_score: float  # score de pertinence (0.0 - 1.0, décroît avec le temps)


@dataclass
class GraphEntity:
    """Représente une entité du graphe de connaissances (ex-MCP Memory)."""
    id: int
    name: str
    entity_type: str        # ex: "software", "agent", "BugFix", "KnowledgeEntry"
    observations: str       # JSON array de strings
    created_at: float
    updated_at: float


@dataclass
class GraphRelation:
    """Représente une relation entre deux entités du graphe."""
    id: int
    from_entity: str
    to_entity: str
    relation_type: str      # ex: "develops", "has_models", "was_fixed_in"
    created_at: float


class MemoryDB:
    """
    Singleton thread-safe pour la base de données mémoire unifiée.
    
    Usage :
        db = MemoryDB.get_instance()
        facts = db.search_facts("audio Tab5")
        db.upsert_fact(category="esphome", title="Bug DAC", content="...", ...)
    """
    
    _instance: Optional['MemoryDB'] = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(cls, db_path: str = DEFAULT_DB_PATH) -> 'MemoryDB':
        """Retourne le singleton de la base de données mémoire."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        self._write_lock = threading.RLock()
        self._write_lock_async = asyncio.Lock()
        self._init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Crée une connexion SQLite avec mode WAL et row_factory."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    async def _get_conn_async(self):
        """Crée une connexion SQLite asynchrone avec mode WAL et row_factory."""
        import aiosqlite
        conn = await aiosqlite.connect(self._db_path, timeout=10)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = aiosqlite.Row
        return conn
    
    def _init_db(self):
        """Initialise le schéma de la base de données."""
        conn = self._get_conn()
        try:
            # Table des faits (leçons apprises / RAG)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_file TEXT,
                    tags TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    relevance_score REAL DEFAULT 1.0,
                    quality_score REAL DEFAULT 1.0, -- V9 : score de qualité de la leçon
                    commit_hash TEXT,               -- V9 : hash git de la leçon
                    severity TEXT DEFAULT 'minor',  -- V9 : minor, major, critical
                    UNIQUE(title, category)
                )
                """
            )
            
            # Index sur titre et catégorie
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_cat ON facts(category)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_relevance ON facts(relevance_score)"
            )
            
            # Table FTS5 pour recherche plein texte ultra-rapide (BM25)
            # FTS5 requiert SQLite 3.9+ (présent dans Python 3.6+)
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts_facts USING fts5(
                        fact_id UNINDEXED,
                        title,
                        content,
                        tags,
                        tokenize="unicode61"
                    )
                    """
                )
                
                # Triggers pour maintenir la table FTS synchrone
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_facts_insert AFTER INSERT ON facts BEGIN
                        INSERT INTO fts_facts (fact_id, title, content, tags)
                        VALUES (new.id, new.title, new.content, new.tags);
                    END
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_facts_update AFTER UPDATE ON facts BEGIN
                        UPDATE fts_facts SET 
                            title = new.title,
                            content = new.content,
                            tags = new.tags
                        WHERE fact_id = old.id;
                    END
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_facts_delete AFTER DELETE ON facts BEGIN
                        DELETE FROM fts_facts WHERE fact_id = old.id;
                    END
                    """
                )
            except sqlite3.OperationalError as e:
                logger.warning(f"[MEMORY DB] Impossible d'initialiser FTS5 : {e}. Fallback LIKE disponible.")

            # Table des épisodes de sessions (résumés de sessions passées)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT NOT NULL,
                    session_folder TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    tags TEXT,
                    source_file TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_date ON episodes(session_date)"
            )

            # Table des entités du graphe (Memory relationnelle)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    entity_type TEXT NOT NULL, -- software, hardware, rule, lecon_apprise...
                    observations TEXT NOT NULL, -- JSON array de strings
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            
            # Table des relations du graphe
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_entity TEXT NOT NULL,
                    to_entity TEXT NOT NULL,
                    relation_type TEXT NOT NULL, -- ex: was_fixed_in, linked_to...
                    created_at REAL NOT NULL,
                    UNIQUE(from_entity, to_entity, relation_type),
                    FOREIGN KEY(from_entity) REFERENCES graph_entities(name) ON DELETE CASCADE,
                    FOREIGN KEY(to_entity) REFERENCES graph_entities(name) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rels_from ON graph_relations(from_entity)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rels_to ON graph_relations(to_entity)"
            )
            
            # Table des leçons apprises (skills / optimisations)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    trigger_pattern TEXT NOT NULL, -- regex ou pattern de trigger
                    optimized_code TEXT NOT NULL,
                    explanation TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(category, trigger_pattern)
                )
                """
            )

            # Table de cache d'embeddings des requêtes utilisateur 
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS query_embeddings_cache (
                    query_hash TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

            # Table d'embeddings des faits et chunks (RAG sémantique triple, V8)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL, -- 'fact' ou 'episode'
                    source_id TEXT NOT NULL,   -- id du fait ou nom du dossier session
                    chunk_text TEXT NOT NULL,  -- texte du chunk indexé
                    embedding BLOB NOT NULL,   -- vecteur sérialisé en float32
                    model_name TEXT NOT NULL,  -- nom du modèle d'embedding (ex: gemini-embedding-2)
                    dimension INTEGER NOT NULL, -- ex: 3072
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emb_source ON embeddings(source_type, source_id)"
            )

            # Table de liaison fait-entité pour Graph-RAG relationnel 
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_entity_links (
                    fact_id INTEGER,
                    entity_name TEXT,
                    PRIMARY KEY (fact_id, entity_name),
                    FOREIGN KEY(fact_id) REFERENCES facts(id) ON DELETE CASCADE,
                    FOREIGN KEY(entity_name) REFERENCES graph_entities(name) ON DELETE CASCADE
                )
                """
            )

            # Table de métadonnées de synchronisation
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            
            conn.commit()
            logger.info(f"[MEMORY DB] Base initialisée : {self._db_path}")
        finally:
            conn.close()

        # Migration additive après fermeture (T78) : colonnes de tracking d'accès
        from .facts import _ensure_facts_columns
        _ensure_facts_columns(self)

    # ──────────────────────────────────────────────────────────────
    # EMBEDDINGS
    # ──────────────────────────────────────────────────────────────
    
    def store_embedding(self, source_type: str, source_id: str,
                        chunk_text: str, embedding: bytes,
                        model_name: str = "gemini-embedding-2",
                        dimension: int = 3072) -> int:
        """Stocke un embedding pré-calculé."""
        now = time.time()
        with self._write_lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "INSERT OR REPLACE INTO embeddings "
                    "(source_type, source_id, chunk_text, embedding, "
                    "model_name, dimension, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_type, source_id, chunk_text, embedding, model_name, dimension, now)
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
    
    def get_embeddings_by_source(self, source_type: str,
                                 source_id: str = None) -> List[Dict]:
        """Récupère les embeddings d'une source."""
        conn = self._get_conn()
        try:
            if source_id:
                rows = conn.execute(
                    "SELECT * FROM embeddings WHERE source_type = ? AND source_id = ?",
                    (source_type, source_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM embeddings WHERE source_type = ?",
                    (source_type,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
            
    def get_cached_query_embedding(self, query_hash: str) -> Optional[List[float]]:
        """Récupère l'embedding d'une requête depuis le cache SQLite s'il existe ."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT embedding FROM query_embeddings_cache WHERE query_hash = ?",
                (query_hash,)
            ).fetchone()
            if row and row["embedding"]:
                emb_bytes = row["embedding"]
                n = len(emb_bytes) // 4
                return list(struct.unpack(f'{n}f', emb_bytes))
            return None
        except Exception as e:
            logger.warning(f"[MEMORY DB] Erreur de lecture du cache d'embeddings : {e}")
            return None
        finally:
            conn.close()

    def store_query_embedding_cache(self, query_hash: str, query_text: str,
                                    embedding: List[float]) -> None:
        """Enregistre l'embedding d'une requête dans le cache SQLite ."""
        now = time.time()
        try:
            emb_bytes = struct.pack(f'{len(embedding)}f', *embedding)
            with self._write_lock:
                conn = self._get_conn()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO query_embeddings_cache "
                        "(query_hash, query_text, embedding, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (query_hash, query_text, emb_bytes, now)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"[MEMORY DB] Erreur d'écriture dans le cache d'embeddings : {e}")

    def get_all_cached_query_embeddings(self) -> List[Dict[str, Any]]:
        """Récupère l'intégralité du cache des requêtes avec leurs embeddings ."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT query_hash, query_text, embedding FROM query_embeddings_cache"
            ).fetchall()
            results = []
            for row in rows:
                emb_bytes = row["embedding"]
                if emb_bytes:
                    n = len(emb_bytes) // 4
                    emb_list = list(struct.unpack(f'{n}f', emb_bytes))
                    results.append({
                        "query_hash": row["query_hash"],
                        "query_text": row["query_text"],
                        "embedding": emb_list
                    })
            return results
        except Exception as e:
            logger.warning(f"[MEMORY DB] Erreur de lecture du cache d'embeddings complet : {e}")
            return []
        finally:
            conn.close()
    
    # ──────────────────────────────────────────────────────────────
    # SYNC & METADATA
    # ──────────────────────────────────────────────────────────────
    
    def set_sync_metadata(self, key: str, value: str):
        """Enregistre une métadonnée de synchronisation."""
        now = time.time()
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, value, now)
                )
                conn.commit()
            finally:
                conn.close()
    
    def get_sync_metadata(self, key: str) -> Optional[str]:
        """Récupère une métadonnée de synchronisation."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM sync_metadata WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques globales de la base."""
        conn = self._get_conn()
        try:
            facts_count = conn.execute("SELECT COUNT(*) as c FROM facts").fetchone()["c"]
            episodes_count = conn.execute("SELECT COUNT(*) as c FROM episodes").fetchone()["c"]
            entities_count = conn.execute("SELECT COUNT(*) as c FROM graph_entities").fetchone()["c"]
            relations_count = conn.execute("SELECT COUNT(*) as c FROM graph_relations").fetchone()["c"]
            skills_count = conn.execute("SELECT COUNT(*) as c FROM skills").fetchone()["c"]
            embeddings_count = conn.execute("SELECT COUNT(*) as c FROM embeddings").fetchone()["c"]
            
            # Taille du fichier
            db_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
            
            return {
                "db_path": self._db_path,
                "db_size_kb": round(db_size / 1024, 1),
                "facts": facts_count,
                "episodes": episodes_count,
                "graph_entities": entities_count,
                "graph_relations": relations_count,
                "skills": skills_count,
                "embeddings": embeddings_count,
                "last_sync": self.get_sync_metadata("last_full_sync"),
            }
        finally:
            conn.close()

    # ──────────────────────────────────────────────────────────────
    # DELEGATIONS DE METHODES EXTERNALISEES (v12.1.0 Refactoring SRP)
    # ──────────────────────────────────────────────────────────────

    # --- Facts (memory/facts.py) ---
    def upsert_fact(self, category: str, title: str, content: str,
                    source_file: str = "", tags: str = "",
                    commit_hash: str = None, severity: str = "minor") -> int:
        from .facts import upsert_fact
        return upsert_fact(self, category, title, content, source_file, tags, commit_hash, severity)

    def search_facts(self, query: str, category: str = None,
                     limit: int = 10) -> List[Dict]:
        from .facts import search_facts
        return search_facts(self, query, category, limit)

    def get_facts_by_category(self, category: str) -> List[Dict]:
        from .facts import get_facts_by_category
        return get_facts_by_category(self, category)

    def get_all_facts_count(self) -> Dict[str, int]:
        from .facts import get_all_facts_count
        return get_all_facts_count(self)

    def decay_relevance(self, decay_rate: float = 0.05,
                        min_score: float = 0.1) -> int:
        from .facts import decay_relevance
        return decay_relevance(self, decay_rate, min_score)

    def touch_fact(self, fact_id: int) -> bool:
        from .facts import touch_fact
        return touch_fact(self, fact_id)

    def search_facts_weighted(self, query: str, limit: int = 10) -> List[Dict]:
        from .facts import search_facts_weighted
        return search_facts_weighted(self, query, limit)

    def ensure_facts_columns(self) -> None:
        from .facts import _ensure_facts_columns
        _ensure_facts_columns(self)

    def get_stale_facts(self, threshold: float = 0.3) -> List[Dict]:
        from .facts import get_stale_facts
        return get_stale_facts(self, threshold)

    async def upsert_fact_async(self, category: str, title: str, content: str,
                                 source_file: str = "", tags: str = "",
                                 commit_hash: str = None, severity: str = "minor") -> int:
        from .facts import upsert_fact_async
        return await upsert_fact_async(self, category, title, content, source_file, tags, commit_hash, severity)

    async def decay_relevance_async(self, decay_rate: float = 0.05,
                                    min_score: float = 0.1) -> int:
        from .facts import decay_relevance_async
        return await decay_relevance_async(self, decay_rate, min_score)

    # --- Episodes (memory/episodes.py) ---
    def upsert_episode(self, session_date: str, session_folder: str,
                       summary: str, category: str = "general",
                       tags: str = "", source_file: str = "") -> int:
        from .episodes import upsert_episode
        return upsert_episode(self, session_date, session_folder, summary, category, tags, source_file)

    def search_episodes(self, query: str, limit: int = 10) -> List[Dict]:
        from .episodes import search_episodes
        return search_episodes(self, query, limit)

    async def upsert_episode_async(self, session_date: str, session_folder: str,
                                    summary: str, category: str = "general",
                                    tags: str = "", source_file: str = "") -> int:
        from .episodes import upsert_episode_async
        return await upsert_episode_async(self, session_date, session_folder, summary, category, tags, source_file)

    # --- Graph (memory/graph.py) ---
    def upsert_graph_entity(self, name: str, entity_type: str,
                            observations: List[str] = None) -> int:
        from .graph import upsert_graph_entity
        return upsert_graph_entity(self, name, entity_type, observations)

    def upsert_graph_relation(self, from_entity: str, to_entity: str,
                              relation_type: str) -> int:
        from .graph import upsert_graph_relation
        return upsert_graph_relation(self, from_entity, to_entity, relation_type)

    def search_graph(self, query: str, limit: int = 10) -> Dict[str, Any]:
        from .graph import search_graph
        return search_graph(self, query, limit)

    def get_full_graph(self) -> Dict[str, Any]:
        from .graph import get_full_graph
        return get_full_graph(self)

    def gc_graph_entities(self, max_observations: int = 15,
                          max_age_days: int = 30) -> Dict[str, int]:
        from .graph import gc_graph_entities
        return gc_graph_entities(self, max_observations, max_age_days)

    async def gc_graph_entities_async(self, max_observations: int = 15,
                                      max_age_days: int = 30) -> Dict[str, int]:
        from .graph import gc_graph_entities_async
        return await gc_graph_entities_async(self, max_observations, max_age_days)

    async def upsert_graph_entity_async(self, name: str, entity_type: str,
                                         observations: List[str] = None) -> int:
        from .graph import upsert_graph_entity_async
        return await upsert_graph_entity_async(self, name, entity_type, observations)

    def link_fact_to_entity(self, fact_id: int, entity_name: str) -> bool:
        from .graph import link_fact_to_entity
        return link_fact_to_entity(self, fact_id, entity_name)

    def get_connected_facts_for_entity(self, entity_name: str, limit: int = 5) -> List[Dict]:
        from .graph import get_connected_facts_for_entity
        return get_connected_facts_for_entity(self, entity_name, limit)

    def get_connected_entities_for_fact(self, fact_id: int) -> List[Dict]:
        from .graph import get_connected_entities_for_fact
        return get_connected_entities_for_fact(self, fact_id)

    async def link_fact_to_entity_async(self, fact_id: int, entity_name: str) -> bool:
        from .graph import link_fact_to_entity_async
        return await link_fact_to_entity_async(self, fact_id, entity_name)

    async def get_connected_facts_for_entity_async(self, entity_name: str, limit: int = 5) -> List[Dict]:
        from .graph import get_connected_facts_for_entity_async
        return await get_connected_facts_for_entity_async(self, entity_name, limit)

    # --- Skills (memory/skills.py) ---
    def record_learned_lesson(self, category: str, title: str,
                              content: str, source_file: str = "",
                              tags: str = "",
                              severity: str = "minor") -> Dict[str, Any]:
        from .skills import record_learned_lesson
        return record_learned_lesson(self, category, title, content, source_file, tags, severity)

    def _get_lecon_md_path(self, category: str) -> Optional[str]:
        from .skills import _get_lecon_md_path
        return _get_lecon_md_path(self, category)

    def _compute_quality_score(self, title: str, content: str,
                               category: str) -> float:
        from .skills import _compute_quality_score
        return _compute_quality_score(self, title, content, category)

    async def record_learned_lesson_async(self, category: str, title: str,
                                           content: str, source_file: str = "",
                                           tags: str = "",
                                           severity: str = "minor") -> Dict[str, Any]:
        from .skills import record_learned_lesson_async
        return await record_learned_lesson_async(self, category, title, content, source_file, tags, severity)
