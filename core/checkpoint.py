"""
core/checkpoint.py — Gestionnaire de checkpoints avec persistance SQLite.

Migration depuis fichiers JSON sur disque vers SQLite pour :
- Transactions ACID (pas de fichiers tronqués en cas de crash)
- Requêtabilité (SELECT * FROM checkpoints WHERE session_id = ?)
- Performance (pas de parsing JSON à chaque load)

L'interface publique reste identique (save, load, exists, delete, cleanup, list_checkpoints)
pour garantir la rétrocompatibilité avec engine.py, gui_server.py, etc.
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional

from core.state import GlobalState
from core.runtime_db import get_connection, get_db_path

logger = logging.getLogger(__name__)

# Base de données SQLite pour les checkpoints
_DEFAULT_CHECKPOINT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "checkpoints"
)
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_CHECKPOINT_DIR, "checkpoints.db")


class CheckpointManager:
    """
    Sérialise/désérialise GlobalState dans SQLite pour permettre :
    - La reprise après crash (le moteur recharge le dernier état sauvegardé)
    - Le HITL (l'état est sauvegardé pendant l'attente d'approbation humaine)
    - Le debugging post-mortem (inspection de l'état final d'une session échouée)
    
    Migration JSON → SQLite.
    """

    def __init__(self, checkpoint_dir: str = _DEFAULT_CHECKPOINT_DIR, db_path: str = None):
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        
        self.db_path = db_path or get_db_path()
        logger.info(f"[CHECKPOINT] Point d'accès configuré sur la base de données unifiée : {self.db_path}")

    def _init_db(self):
        """No-op car l'initialisation est centralisée dans runtime_db."""
        pass

    def _get_conn(self) -> sqlite3.Connection:
        """Retourne une connexion SQLite vers la base de données unifiée."""
        return get_connection()

    def save(self, state: GlobalState) -> str:
        """
        Écrit l'état complet dans la base SQLite (UPSERT).
        Retourne le session_id sauvegardé.
        """
        try:
            json_data = state.model_dump_json(indent=2)
            conn = self._get_conn()
            try:
                conn.execute("""
                    INSERT INTO checkpoints (session_id, phase, state_json, history_count, size_bytes, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        phase = excluded.phase,
                        state_json = excluded.state_json,
                        history_count = excluded.history_count,
                        size_bytes = excluded.size_bytes,
                        updated_at = excluded.updated_at
                """, (
                    state.session_id,
                    state.current_phase.value,
                    json_data,
                    len(state.history),
                    len(json_data),
                    datetime.now().isoformat()
                ))
                conn.commit()
            finally:
                conn.close()

            logger.info(
                f"[CHECKPOINT] État sauvegardé pour session '{state.session_id}' "
                f"(phase: {state.current_phase.value}, "
                f"historique: {len(state.history)} entrées, "
                f"taille: {len(json_data):,} bytes)"
            )
            return state.session_id
        except Exception as e:
            logger.error(f"[CHECKPOINT] Échec de la sauvegarde pour '{state.session_id}' : {e}")
            return ""

    def load(self, session_id: str) -> Optional[GlobalState]:
        """
        Recharge un état sauvegardé pour reprise après crash.
        Retourne None si la session n'existe pas ou est corrompue.
        """
        try:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "SELECT state_json, phase, history_count FROM checkpoints WHERE session_id = ?",
                    (session_id,)
                )
                row = cursor.fetchone()
            finally:
                conn.close()

            if not row:
                logger.info(f"[CHECKPOINT] Aucun checkpoint trouvé pour '{session_id}'")
                return None

            state = GlobalState.model_validate_json(row[0])
            logger.info(
                f"[CHECKPOINT] État rechargé pour session '{session_id}' "
                f"(phase: {row[1]}, historique: {row[2]} entrées)"
            )
            return state
        except Exception as e:
            logger.error(f"[CHECKPOINT] Échec du chargement pour '{session_id}' : {e}")
            return None

    def exists(self, session_id: str) -> bool:
        """Vérifie si un checkpoint existe pour une session donnée."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "SELECT 1 FROM checkpoints WHERE session_id = ?", (session_id,)
                )
                return cursor.fetchone() is not None
            finally:
                conn.close()
        except Exception:
            return False

    def delete(self, session_id: str) -> bool:
        """Supprime le checkpoint d'une session."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM checkpoints WHERE session_id = ?", (session_id,)
                )
                conn.commit()
                deleted = cursor.rowcount > 0
            finally:
                conn.close()
            if deleted:
                logger.info(f"[CHECKPOINT] Checkpoint supprimé pour '{session_id}'")
            return deleted
        except Exception as e:
            logger.warning(f"[CHECKPOINT] Impossible de supprimer '{session_id}' : {e}")
            return False

    def cleanup(self, max_age_hours: int = 24) -> int:
        """Supprime les checkpoints plus anciens que max_age_hours."""
        try:
            cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM checkpoints WHERE updated_at < ?", (cutoff,)
                )
                conn.commit()
                deleted = cursor.rowcount
            finally:
                conn.close()
            if deleted > 0:
                logger.info(f"[CHECKPOINT] Nettoyage terminé : {deleted} checkpoint(s) supprimé(s)")
            return deleted
        except Exception as e:
            logger.error(f"[CHECKPOINT] Erreur lors du nettoyage : {e}")
            return 0

    def list_checkpoints(self) -> list[dict]:
        """Liste tous les checkpoints disponibles avec leurs métadonnées."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.execute("""
                    SELECT session_id, phase, history_count, size_bytes, updated_at
                    FROM checkpoints
                    ORDER BY updated_at DESC
                """)
                rows = cursor.fetchall()
            finally:
                conn.close()

            return [
                {
                    "session_id": row[0],
                    "phase": row[1],
                    "history_count": row[2],
                    "size_bytes": row[3],
                    "last_modified": row[4]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"[CHECKPOINT] Erreur de listage : {e}")
            return []
