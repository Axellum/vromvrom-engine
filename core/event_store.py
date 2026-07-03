"""
core/event_store.py — Event Sourcing SQLite append-only pour le tab5-engine.

Table 'events' en append-only (jamais de UPDATE/DELETE).
WAL mode pour lectures concurrentes sans blocage.
Toutes les opérations SQL via asyncio.to_thread.

Types d'événements :
  - request_received  : requête entrante (source Tab5/API)
  - agent_started     : début d'exécution d'un agent
  - tool_called       : appel d'outil (ToolRegistry)
  - response_sent     : réponse envoyée au client
  - error             : erreur non fatale

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Chemin par défaut relatif au répertoire moteur
_ENGINE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB  = str(_ENGINE_DIR / "moteur_runtime.db")

# SQL append-only
_SQL_CREATE = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    source     TEXT    DEFAULT '',
    agent      TEXT    DEFAULT '',
    session_id TEXT    DEFAULT '',
    payload    TEXT    DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type    ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""


class EventStore:
    """
    Stockage d'événements append-only pour l'Event Sourcing du moteur.

    - INSERT uniquement (jamais UPDATE/DELETE)
    - WAL mode pour lectures concurrentes sans lock
    - Toutes les I/O SQL via asyncio.to_thread (non bloquant)
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Args:
            db_path: Chemin vers le fichier SQLite (défaut: moteur_runtime.db)
        """
        self.db_path = db_path or DEFAULT_DB
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Ouvre la connexion SQLite, active WAL et crée la table events."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SQL_CREATE)
        self._conn.commit()
        logger.debug(f"[EVENT STORE] Initialisé : {self.db_path}")

    # ──────────────────────────────────────────────────────────────
    # Écriture (append-only)
    # ──────────────────────────────────────────────────────────────

    async def log(
        self,
        type: str,
        source: str = "",
        agent: str = "",
        session_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Enregistre un événement (INSERT uniquement).

        Args:
            type:       Type d'événement (request_received|agent_started|tool_called|response_sent|error)
            source:     Source de la requête (tab5|api|dreamer|scheduler...)
            agent:      Nom de l'agent concerné
            session_id: ID de session
            payload:    Données additionnelles JSON-sérialisables

        Returns:
            id de l'événement inséré
        """
        ts           = datetime.utcnow().isoformat()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)

        def _insert() -> int:
            cur = self._conn.execute(
                "INSERT INTO events (ts, type, source, agent, session_id, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, type, source, agent, session_id, payload_json),
            )
            self._conn.commit()
            return cur.lastrowid

        event_id = await asyncio.to_thread(_insert)
        logger.debug(
            f"[EVENT STORE] #{event_id} type={type} source={source} agent={agent}"
        )
        return event_id

    # ──────────────────────────────────────────────────────────────
    # Lecture
    # ──────────────────────────────────────────────────────────────

    async def get_session_events(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Récupère tous les événements d'une session (tri chronologique).

        Returns:
            Liste de {id, ts, type, source, agent, session_id, payload: dict}
        """
        def _fetch():
            cur = self._conn.execute(
                "SELECT id, ts, type, source, agent, session_id, payload "
                "FROM events WHERE session_id = ? ORDER BY ts ASC",
                (session_id,),
            )
            return [
                {**dict(r), "payload": json.loads(r["payload"])}
                for r in cur.fetchall()
            ]
        return await asyncio.to_thread(_fetch)

    async def get_recent_events(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les N événements les plus récents, avec filtre type optionnel.

        Returns:
            Liste triée du plus récent au plus ancien.
        """
        def _fetch():
            if event_type:
                cur = self._conn.execute(
                    "SELECT id, ts, type, source, agent, session_id, payload "
                    "FROM events WHERE type = ? ORDER BY ts DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT id, ts, type, source, agent, session_id, payload "
                    "FROM events ORDER BY ts DESC LIMIT ?",
                    (limit,),
                )
            return [
                {**dict(r), "payload": json.loads(r["payload"])}
                for r in cur.fetchall()
            ]
        return await asyncio.to_thread(_fetch)

    async def replay_session(self, session_id: str) -> List[str]:
        """
        Rejoue les événements d'une session sous forme textuelle (audit).

        Returns:
            Lignes format : "[ts] type / agent : payload_preview"
        """
        events = await self.get_session_events(session_id)
        lines  = []
        for ev in events:
            preview = json.dumps(ev["payload"], ensure_ascii=False)[:80]
            lines.append(
                f"[{ev['ts']}] {ev['type']} / {ev.get('agent', '-')} : {preview}"
            )
        return lines

    async def get_stats(self) -> Dict[str, Any]:
        """
        Retourne les statistiques globales sur les événements.

        Returns:
            {total_events, first_event_ts, last_event_ts, by_type: {type: count}}
        """
        def _fetch():
            total    = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            first_ts = self._conn.execute("SELECT MIN(ts) FROM events").fetchone()[0]
            last_ts  = self._conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
            by_type  = {
                row[0]: row[1]
                for row in self._conn.execute(
                    "SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY COUNT(*) DESC"
                ).fetchall()
            }
            return {
                "total_events":   total,
                "first_event_ts": first_ts,
                "last_event_ts":  last_ts,
                "by_type":        by_type,
            }
        return await asyncio.to_thread(_fetch)

    def close(self) -> None:
        """Ferme la connexion SQLite."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("[EVENT STORE] Connexion fermée")


# ──────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────

_event_store_instance: Optional[EventStore] = None


def get_event_store(db_path: str = None) -> EventStore:
    """Retourne le singleton EventStore (chargement lazy)."""
    global _event_store_instance
    if _event_store_instance is None:
        _event_store_instance = EventStore(db_path)
    return _event_store_instance
