"""
tests/test_runtime_db.py — Tests unitaires pour core/runtime_db.py.

Vérifie l'initialisation de la base de données unifiée, l'existence des tables
et la validité des requêtes d'écriture/lecture.
"""

import os
import sys
import sqlite3

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRuntimeDB:
    """Tests du module core/runtime_db.py."""

    def test_import(self):
        """Le module core.runtime_db s'importe correctement."""
        from core.runtime_db import get_connection, get_db_path
        assert callable(get_connection)
        assert callable(get_db_path)

    def test_db_path(self):
        """Le chemin de la base est correct et pointe vers moteur_runtime.db."""
        from core.runtime_db import get_db_path
        path = get_db_path()
        assert path is not None
        assert path.endswith("moteur_runtime.db")

    def test_get_connection_and_schema(self):
        """La connexion est établie avec succès et toutes les tables requises sont créées."""
        from core.runtime_db import get_connection
        conn = get_connection()
        assert isinstance(conn, sqlite3.Connection)
        
        # Récupérer la liste des tables créées
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        
        expected_tables = {
            "sessions",
            "quota_snapshots",
            "billing_history",
            "token_usage",
            "ide_conversations",
            "model_elo_scores",
            "routing_decisions",
            "checkpoints",
            "dag_tasks",
            "dag_edges",
            "swarm_workers",
            "agent_steps",
            "scoped_memory"
        }
        
        for table in expected_tables:
            assert table in tables, f"La table '{table}' est manquante dans la base unifiée."
            
        conn.close()

    def test_db_concurrency_mode(self):
        """Le mode WAL et le mode synchrone NORMAL sont bien configurés."""
        from core.runtime_db import get_connection
        conn = get_connection()
        
        # Vérification du mode journal (WAL)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal", f"Attendu mode WAL, reçu : {journal_mode}"
        
        # Vérification du mode synchrone (NORMAL ou 1)
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        # SQLite retourne 1 pour NORMAL, 2 pour FULL, 0 pour OFF
        assert synchronous in (1, "NORMAL"), f"Attendu synchro NORMAL (1), reçu : {synchronous}"
        
        conn.close()

    def test_write_read_session(self):
        """On peut écrire et lire des données dans la table sessions sans conflit."""
        from core.runtime_db import get_connection
        conn = get_connection()
        
        test_session_id = "test_session_12345"
        test_objective = "Tester la base unifiée"
        
        # Nettoyage préalable au cas où
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (test_session_id,))
        conn.commit()
        
        # Écriture
        conn.execute(
            "INSERT INTO sessions (session_id, objective, started_at) VALUES (?, ?, ?)",
            (test_session_id, test_objective, 1234567.89)
        )
        conn.commit()
        
        # Lecture
        row = conn.execute(
            "SELECT objective, started_at FROM sessions WHERE session_id = ?",
            (test_session_id,)
        ).fetchone()
        
        assert row is not None
        assert row[0] == test_objective
        assert row[1] == 1234567.89
        
        # Nettoyage
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (test_session_id,))
        conn.commit()
        
        conn.close()
