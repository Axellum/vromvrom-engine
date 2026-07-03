"""
core/worker_daemon.py — Script standalone REST pour les Swarm Workers.

Déport de tâches vers des machines distantes via API REST.

Ce script peut être lancé sur n'importe quelle machine du réseau local :
  python core/worker_daemon.py --port 8780 --name "worker-freebox"

Il expose un endpoint POST /execute qui reçoit un TaskPayload JSON,
exécute la tâche localement (via un agent LLM), et retourne le résultat.

Architecture :
  Engine (PC principal) → worker_registry.py → dispatch → worker_daemon.py (machine N)

L'utilisateur a précisé que la compilation reste sur le PC principal (10x plus rapide),
donc les workers sont utilisés pour les tâches non-compilation (analyse, review, search).

⚠️ STATUT (2026-06-20) : DORMANT — non déployé. Vérifié absent du Deck (host +
conteneur), de Windows et de tout script/service du repo. Le pendant
[[worker_registry]] est branché (4 imports) mais aucun worker ne s'enregistre
via ce daemon. À lancer côté machine-worker (ex: Windows) quand le Swarm sera
réellement activé. Conservé volontairement, pas mort : ne pas supprimer sans
abandonner le concept Swarm.
"""

import os
import sys
import time
import logging
import argparse
import asyncio
from typing import Optional, Dict, Any

# Ajout du répertoire parent au path pour importer les modules du moteur
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("worker_daemon")

# Déférer l'import FastAPI pour éviter les dépendances si non installé
_HAS_FASTAPI = False
try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    import uvicorn
    _HAS_FASTAPI = True
except ImportError:
    pass


class WorkerTaskRequest(BaseModel if _HAS_FASTAPI else object):
    """Payload reçu par le worker pour l'exécution d'une tâche."""
    task_id: str = ""
    task_objective: str = ""
    relevant_context: str = ""
    model_tier: str = "leger"
    metadata: dict = {}
    session_id: str = ""


class WorkerDaemon:
    """
    Daemon REST pour l'exécution de tâches déportées.
    
    Chaque worker possède :
    - Un nom unique (ex: "worker-freebox", "worker-laptop")
    - Un port HTTP dédié
    - Un LLMGateway local pour les appels LLM
    - Un heartbeat périodique pour signaler sa disponibilité
    """

    def __init__(self, name: str, port: int, host: str = "0.0.0.0"):
        self.name = name
        self.port = port
        self.host = host
        self._start_time = time.time()
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._current_task: Optional[str] = None
        self._gateway = None

    def _get_gateway(self):
        """Lazy-init du LLMGateway (évite l'import au démarrage)."""
        if self._gateway is None:
            try:
                from core.llm_gateway import LLMGateway
                self._gateway = LLMGateway()
                logger.info(f"[WORKER:{self.name}] LLMGateway initialisé")
            except Exception as e:
                logger.error(f"[WORKER:{self.name}] Erreur LLMGateway : {e}")
        return self._gateway

    async def execute_task(self, request: dict) -> Dict[str, Any]:
        """
        Exécute une tâche localement et retourne le résultat.
        
        Args:
            request: Dict contenant task_id, task_objective, relevant_context, etc.
            
        Returns:
            Dict avec status, result_data, error_message, metadata.
        """
        task_id = request.get("task_id", "unknown")
        objective = request.get("task_objective", "")
        context = request.get("relevant_context", "")
        model_tier = request.get("model_tier", "leger")
        session_id = request.get("session_id", "")

        self._current_task = task_id
        start_time = time.time()

        logger.info(
            f"[WORKER:{self.name}] ▶ Tâche '{task_id}' reçue : "
            f"{objective[:100]}..."
        )

        try:
            gateway = self._get_gateway()
            if not gateway:
                raise RuntimeError("LLMGateway non disponible")

            from core.llm_gateway import load_config
            config = load_config()
            _, provider = gateway.get_provider_for_tier(model_tier, config)

            # Appel LLM avec le provider résolu
            system_prompt = (
                "Tu es un agent d'exécution sur un worker distant. "
                "Exécute la tâche demandée de manière rigoureuse et concise."
            )
            user_prompt = f"OBJECTIF : {objective}"
            if context:
                user_prompt += f"\n\nCONTEXTE :\n{context[:10000]}"

            response = await asyncio.to_thread(
                provider.generate, system_prompt, user_prompt,
                session_id=session_id,
            )

            elapsed = time.time() - start_time
            self._tasks_completed += 1
            self._current_task = None

            logger.info(
                f"[WORKER:{self.name}] ✅ Tâche '{task_id}' terminée "
                f"en {elapsed:.1f}s"
            )

            return {
                "status": "success",
                "task_id": task_id,
                "result_data": response,
                "error_message": None,
                "metadata": {
                    "worker_name": self.name,
                    "elapsed_seconds": round(elapsed, 2),
                    "model_tier": model_tier,
                },
            }

        except Exception as e:
            elapsed = time.time() - start_time
            self._tasks_failed += 1
            self._current_task = None

            logger.error(
                f"[WORKER:{self.name}] ❌ Tâche '{task_id}' échouée : {e}"
            )

            return {
                "status": "error",
                "task_id": task_id,
                "result_data": None,
                "error_message": str(e),
                "metadata": {
                    "worker_name": self.name,
                    "elapsed_seconds": round(elapsed, 2),
                },
            }

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état actuel du worker (pour heartbeat/monitoring)."""
        return {
            "name": self.name,
            "status": "busy" if self._current_task else "idle",
            "current_task": self._current_task,
            "uptime_seconds": round(time.time() - self._start_time),
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "port": self.port,
        }

    def create_app(self) -> "FastAPI":
        """Crée l'application FastAPI pour le worker."""
        if not _HAS_FASTAPI:
            raise ImportError(
                "FastAPI et uvicorn sont requis pour le worker daemon. "
                "Installez-les avec : pip install fastapi uvicorn"
            )

        app = FastAPI(
            title=f"Moteur — Worker '{self.name}'",
            description="Daemon REST pour l'exécution de tâches déportées.",
            version="6.0.0",
        )

        daemon = self  # Capture pour les closures

        @app.get("/status")
        async def get_status():
            """État du worker (heartbeat)."""
            return daemon.get_status()

        @app.post("/execute")
        async def execute_task(request: dict):
            """Exécute une tâche et retourne le résultat."""
            if daemon._current_task:
                raise HTTPException(
                    status_code=429,
                    detail=f"Worker '{daemon.name}' occupé (tâche: {daemon._current_task})"
                )
            return await daemon.execute_task(request)

        @app.get("/health")
        async def health():
            """Health check simple."""
            return {"status": "ok", "worker": daemon.name}

        return app


def main():
    """Point d'entrée CLI pour lancer le worker daemon."""
    parser = argparse.ArgumentParser(
        description="Moteur — Worker Daemon REST"
    )
    parser.add_argument(
        "--name", default="worker-local",
        help="Nom unique du worker (ex: worker-freebox)"
    )
    parser.add_argument(
        "--port", type=int, default=8780,
        help="Port HTTP (défaut: 8780)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Hôte d'écoute (défaut: 0.0.0.0)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )

    daemon = WorkerDaemon(name=args.name, port=args.port, host=args.host)
    app = daemon.create_app()

    logger.info(
        f"[WORKER] Démarrage du worker '{args.name}' sur {args.host}:{args.port}"
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
