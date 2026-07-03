"""
tests/test_checkpoint.py — Tests unitaires pour core/checkpoint.py.

Vérifie les opérations d'écriture, lecture, suppression et nettoyage de checkpoints.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

# Ajouter le répertoire parent au path pour importer les modules du moteur
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.state import GlobalState, ExecutionPhase


class TestCheckpoint:
    """Tests unitaires pour CheckpointManager."""

    def setUp(self):
        """Rediriger la base de données unifiée vers une base de test isolée."""
        import core.runtime_db as db_mod
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)

    def tearDown(self):
        """Restaurer le chemin original et nettoyer le fichier temporaire."""
        import core.runtime_db as db_mod
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_save_and_load(self):
        self.setUp()
        try:
            from core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            
            # Création d'un état de test
            state = GlobalState(session_id="session_test_save_load")
            state.current_phase = ExecutionPhase.PLANNING
            state.working_memory["cle_test"] = "valeur_test"
            
            # Sauvegarde
            saved_id = cm.save(state)
            assert saved_id == "session_test_save_load"
            
            # Vérification de l'existence
            assert cm.exists("session_test_save_load") is True
            
            # Chargement
            loaded = cm.load("session_test_save_load")
            assert loaded is not None
            assert loaded.session_id == "session_test_save_load"
            assert loaded.current_phase == ExecutionPhase.PLANNING
            assert loaded.working_memory.get("cle_test") == "valeur_test"
        finally:
            self.tearDown()

    def test_delete(self):
        self.setUp()
        try:
            from core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            
            state = GlobalState(session_id="session_test_delete")
            cm.save(state)
            assert cm.exists("session_test_delete") is True
            
            # Suppression
            deleted = cm.delete("session_test_delete")
            assert deleted is True
            assert cm.exists("session_test_delete") is False
            
            # Supprimer une session inexistante
            assert cm.delete("inexistant") is False
        finally:
            self.tearDown()

    def test_list_checkpoints(self):
        self.setUp()
        try:
            from core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            
            state1 = GlobalState(session_id="session_1")
            state2 = GlobalState(session_id="session_2")
            cm.save(state1)
            cm.save(state2)
            
            checkpoints = cm.list_checkpoints()
            assert len(checkpoints) >= 2
            ids = {cp["session_id"] for cp in checkpoints}
            assert "session_1" in ids
            assert "session_2" in ids
        finally:
            self.tearDown()

    def test_cleanup(self):
        self.setUp()
        try:
            from core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            
            state = GlobalState(session_id="session_cleanup_test")
            cm.save(state)
            assert cm.exists("session_cleanup_test") is True
            
            # Forcer une date de mise à jour très ancienne pour la session
            conn = cm._get_conn()
            conn.execute(
                "UPDATE checkpoints SET updated_at = ? WHERE session_id = ?",
                ((datetime.now() - timedelta(hours=5)).isoformat(), "session_cleanup_test")
            )
            conn.commit()
            conn.close()
            
            # Nettoyer les checkpoints de plus de 2 heures
            cleaned = cm.cleanup(max_age_hours=2)
            assert cleaned == 1
            assert cm.exists("session_cleanup_test") is False
        finally:
            self.tearDown()
