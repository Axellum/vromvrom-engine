"""
core/worker_registry.py — Registre de Workers pour le Swarm (V6 Acte 4).

Gère la découverte, le suivi et le dispatch des tâches vers les workers REST distants.

Fonctionnement :
1. L'Engine enregistre les workers au démarrage (config.json ou auto-découverte)
2. Le DAGRunner consulte le registre pour dispatcher les tâches éligibles
3. Un heartbeat périodique vérifie la disponibilité des workers
4. Si un worker est indisponible, la tâche est exécutée localement (fallback)

Critères de dispatch vers un worker distant :
- La tâche n'est PAS de type "compilation" (PC local 10x plus rapide)
- Le worker est "idle" (pas déjà occupé)
- Le worker a répondu au dernier heartbeat
"""

import os
import time
import json
import logging
import asyncio
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Fichier de configuration des workers (optionnel)
_WORKERS_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "workers.json",
)

# Catégories de tâches NON déportables (compilé sur le PC principal)
_LOCAL_ONLY_CATEGORIES = {
    "compilation", "build", "flash", "esphome_compile",
    "git_commit", "git_push", "file_write",
}

# Timeout pour les requêtes HTTP vers les workers (secondes)
WORKER_TIMEOUT = 120
HEARTBEAT_TIMEOUT = 5


@dataclass
class WorkerInfo:
    """Informations sur un worker distant."""
    name: str
    host: str
    port: int
    status: str = "unknown"       # "idle", "busy", "offline", "unknown"
    last_heartbeat: float = 0.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    current_task: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    # Métriques système enrichies via heartbeat
    cpu_percent: float = 0.0          # Charge CPU du worker (%)
    ram_percent: float = 0.0          # RAM utilisée (%)
    lm_studio_online: bool = False    # True si LM Studio répond sur le worker
    lm_studio_models: List[str] = field(default_factory=list)  # Modèles chargés


class WorkerRegistry:
    """
    Registre centralisé des workers Swarm.
    
    Utilisé par le DAGRunner pour dispatcher des tâches
    vers les machines distantes du réseau local.
    """

    def __init__(self):
        self._workers: Dict[str, WorkerInfo] = {}
        self._load_config()

    def _load_config(self):
        """Charge les workers depuis workers.json si le fichier existe."""
        if os.path.exists(_WORKERS_CONFIG):
            try:
                with open(_WORKERS_CONFIG, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry in data.get("workers", []):
                    name = entry.get("name", "")
                    if name:
                        self._workers[name] = WorkerInfo(
                            name=name,
                            host=entry.get("host", "localhost"),
                            port=entry.get("port", 8780),
                            capabilities=entry.get("capabilities", []),
                        )
                logger.info(
                    f"[SWARM] {len(self._workers)} worker(s) chargé(s) depuis {_WORKERS_CONFIG}"
                )
            except Exception as e:
                logger.warning(f"[SWARM] Erreur de chargement workers.json : {e}")

    def register(self, name: str, host: str, port: int, capabilities: list = None):
        """Enregistre un worker manuellement."""
        self._workers[name] = WorkerInfo(
            name=name,
            host=host,
            port=port,
            capabilities=capabilities or [],
        )
        logger.info(f"[SWARM] Worker '{name}' enregistré ({host}:{port})")

    def unregister(self, name: str):
        """Désenregistre un worker."""
        if name in self._workers:
            del self._workers[name]
            logger.info(f"[SWARM] Worker '{name}' désenregistré")

    def get_available_worker(
        self, task_category: str = "general"
    ) -> Optional[WorkerInfo]:
        """
        Retourne un worker disponible pour le type de tâche donné.
        
        Critères :
        1. Le worker est "idle"
        2. La tâche n'est pas dans _LOCAL_ONLY_CATEGORIES
        3. Le heartbeat est récent (< 60s)
        
        Args:
            task_category: Catégorie de la tâche (ex: "analysis", "compilation")
            
        Returns:
            WorkerInfo si un worker est disponible, None sinon.
        """
        # Les tâches locales ne sont jamais déportées
        if task_category.lower() in _LOCAL_ONLY_CATEGORIES:
            return None

        # Exclure les workers surchargés (CPU > 85%)
        for worker in self._workers.values():
            if (
                worker.status == "idle"
                and (time.time() - worker.last_heartbeat) < 60
                and worker.cpu_percent <= 85.0
            ):
                return worker

        return None

    def get_lmstudio_worker(self) -> Optional[WorkerInfo]:
        """
        Retourne le premier worker avec LM Studio actif et non surchargé.
        Utilisé par MLRouter / ha_fuzzy_matcher pour les embeddings distants.
        """
        for worker in self._workers.values():
            if (
                worker.lm_studio_online
                and worker.status == "idle"
                and worker.cpu_percent <= 85.0
                and (time.time() - worker.last_heartbeat) < 60
            ):
                logger.debug(f"[SWARM] Worker LM Studio disponible : {worker.name}")
                return worker
        return None

    async def dispatch_task(
        self,
        worker: WorkerInfo,
        task_payload: dict,
    ) -> Dict[str, Any]:
        """
        Envoie une tâche à un worker distant via HTTP POST.
        
        Args:
            worker: WorkerInfo du worker cible
            task_payload: Dict avec task_id, task_objective, relevant_context, etc.
            
        Returns:
            Dict avec status, result_data, error_message.
        """
        import aiohttp

        url = f"http://{worker.host}:{worker.port}/execute"
        worker.status = "busy"
        worker.current_task = task_payload.get("task_id", "?")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=task_payload,
                    timeout=aiohttp.ClientTimeout(total=WORKER_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        worker.status = "idle"
                        worker.current_task = None
                        worker.tasks_completed += 1
                        logger.info(
                            f"[SWARM] ✅ Tâche '{task_payload.get('task_id')}' "
                            f"exécutée par worker '{worker.name}'"
                        )
                        return result
                    elif resp.status == 429:
                        worker.status = "busy"
                        return {
                            "status": "error",
                            "error_message": f"Worker '{worker.name}' occupé",
                        }
                    else:
                        text = await resp.text()
                        raise RuntimeError(
                            f"Worker '{worker.name}' erreur {resp.status}: {text[:200]}"
                        )

        except asyncio.TimeoutError:
            worker.status = "offline"
            worker.current_task = None
            worker.tasks_failed += 1
            return {
                "status": "error",
                "error_message": f"Timeout de {WORKER_TIMEOUT}s pour worker '{worker.name}'",
            }
        except Exception as e:
            worker.status = "offline"
            worker.current_task = None
            worker.tasks_failed += 1
            logger.error(f"[SWARM] ❌ Dispatch vers '{worker.name}' échoué : {e}")
            return {
                "status": "error",
                "error_message": f"Erreur de dispatch : {str(e)}",
            }

    async def heartbeat_all(self):
        """
        Vérifie la disponibilité de tous les workers via GET /status.
        Parse maintenant : cpu_percent, ram_percent, lm_studio_online, lm_studio_models.
        Appeler périodiquement (toutes les 30s par exemple).
        """
        import aiohttp

        for worker in self._workers.values():
            url = f"http://{worker.host}:{worker.port}/status"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=HEARTBEAT_TIMEOUT),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            worker.status           = data.get("status", "idle")
                            worker.current_task     = data.get("current_task")
                            worker.tasks_completed  = data.get("tasks_completed", worker.tasks_completed)
                            worker.tasks_failed     = data.get("tasks_failed", worker.tasks_failed)
                            worker.last_heartbeat   = time.time()
                            # Métriques système
                            worker.cpu_percent      = float(data.get("cpu_percent", 0.0))
                            worker.ram_percent      = float(data.get("ram_percent", 0.0))
                            worker.lm_studio_online = bool(data.get("lm_studio_online", False))
                            worker.lm_studio_models = data.get("lm_studio_models", [])
                            logger.debug(
                                f"[SWARM] Heartbeat {worker.name} : "
                                f"status={worker.status}, CPU={worker.cpu_percent:.0f}%, "
                                f"RAM={worker.ram_percent:.0f}%, "
                                f"LM={'online' if worker.lm_studio_online else 'offline'}"
                            )
                        else:
                            worker.status = "offline"
            except Exception:
                worker.status = "offline"

    def get_all_status(self) -> List[Dict[str, Any]]:
        """Retourne l'état enrichi de tous les workers enregistrés."""
        return [
            {
                "name":              w.name,
                "host":              w.host,
                "port":              w.port,
                "status":            w.status,
                "current_task":      w.current_task,
                "tasks_completed":   w.tasks_completed,
                "tasks_failed":      w.tasks_failed,
                "last_heartbeat":    w.last_heartbeat,
                "online":            (time.time() - w.last_heartbeat) < 60 if w.last_heartbeat else False,
                # Métriques système
                "cpu_percent":       w.cpu_percent,
                "ram_percent":       w.ram_percent,
                "lm_studio_online":  w.lm_studio_online,
                "lm_studio_models":  w.lm_studio_models,
            }
            for w in self._workers.values()
        ]


# Singleton global pour le Swarm de Workers 
_global_registry_instance: Optional[WorkerRegistry] = None


def get_worker_registry() -> WorkerRegistry:
    """Retourne l'instance unique globale du registre de workers."""
    global _global_registry_instance
    if _global_registry_instance is None:
        _global_registry_instance = WorkerRegistry()
    return _global_registry_instance

