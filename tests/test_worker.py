"""
tests/test_worker.py — Tests unitaires pour le Swarm Workers (V6 Acte 4).

Vérifie :
- Enregistrement/désenregistrement de workers
- Sélection du worker disponible (idle + heartbeat récent)
- Blocage des tâches de compilation (local only)
- Statut du worker daemon
- Format de la réponse d'exécution
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWorkerRegistry(unittest.TestCase):
    """Tests unitaires pour core/worker_registry.py"""

    def test_register_and_list_workers(self):
        """L'enregistrement d'un worker doit être visible dans le statut."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("test-worker", "192.168.1.100", 8780)

        status = registry.get_all_status()
        names = [w["name"] for w in status]
        self.assertIn("test-worker", names)

    def test_unregister_worker(self):
        """Le désenregistrement doit retirer le worker du registre."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("temp-worker", "localhost", 8780)
        registry.unregister("temp-worker")

        status = registry.get_all_status()
        names = [w["name"] for w in status]
        self.assertNotIn("temp-worker", names)

    def test_no_available_worker_when_all_unknown(self):
        """Sans heartbeat récent, aucun worker ne doit être disponible."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("unknown-worker", "localhost", 8780)

        worker = registry.get_available_worker("analysis")
        self.assertIsNone(worker)

    def test_available_worker_with_recent_heartbeat(self):
        """Un worker idle avec heartbeat récent doit être sélectionné."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("live-worker", "localhost", 8780)

        # Simuler un heartbeat récent
        registry._workers["live-worker"].status = "idle"
        registry._workers["live-worker"].last_heartbeat = time.time()

        worker = registry.get_available_worker("analysis")
        self.assertIsNotNone(worker)
        self.assertEqual(worker.name, "live-worker")

    def test_compilation_tasks_are_local_only(self):
        """Les tâches de compilation ne doivent jamais être déportées."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("worker-1", "localhost", 8780)

        # Même avec un worker idle, la compilation reste locale
        registry._workers["worker-1"].status = "idle"
        registry._workers["worker-1"].last_heartbeat = time.time()

        for category in ["compilation", "build", "flash", "esphome_compile"]:
            worker = registry.get_available_worker(category)
            self.assertIsNone(
                worker,
                f"La catégorie '{category}' ne devrait pas être déportée"
            )

    def test_busy_worker_not_selected(self):
        """Un worker occupé ne doit pas être sélectionné."""
        from core.worker_registry import WorkerRegistry
        registry = WorkerRegistry()
        registry.register("busy-worker", "localhost", 8780)
        registry._workers["busy-worker"].status = "busy"
        registry._workers["busy-worker"].last_heartbeat = time.time()

        worker = registry.get_available_worker("analysis")
        self.assertIsNone(worker)


class TestWorkerDaemon(unittest.TestCase):
    """Tests unitaires pour core/worker_daemon.py"""

    def test_worker_status_format(self):
        """Le statut du daemon doit avoir la bonne structure."""
        from core.worker_daemon import WorkerDaemon
        daemon = WorkerDaemon(name="test-daemon", port=8780)
        status = daemon.get_status()

        self.assertEqual(status["name"], "test-daemon")
        self.assertIn("status", status)
        self.assertIn("uptime_seconds", status)
        self.assertIn("tasks_completed", status)
        self.assertEqual(status["status"], "idle")

    def test_execute_task_returns_structured_response(self):
        """L'exécution doit retourner un dict structuré (succès ou erreur)."""
        import asyncio
        from core.worker_daemon import WorkerDaemon
        daemon = WorkerDaemon(name="test-daemon", port=8780)

        result = asyncio.run(daemon.execute_task({
            "task_id": "test-1",
            "task_objective": "Test structurel",
        }))

        # La réponse doit toujours avoir cette structure
        self.assertIn(result["status"], ("success", "error"))
        self.assertEqual(result["task_id"], "test-1")
        self.assertIn("worker_name", result["metadata"])
        self.assertEqual(result["metadata"]["worker_name"], "test-daemon")


if __name__ == "__main__":
    unittest.main()
