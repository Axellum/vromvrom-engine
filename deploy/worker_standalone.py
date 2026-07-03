"""
deploy/worker_standalone.py — Worker Swarm autonome pour Freebox VM.

Version STANDALONE du worker daemon.

Ce fichier est AUTOSUFFISANT — il n'a aucune dépendance au moteur principal.
Il peut être copié seul sur n'importe quelle machine avec Python 3.10+ et
les packages : fastapi, uvicorn, aiohttp.

Prérequis :
  pip install fastapi uvicorn aiohttp python-dotenv
  
Lancement :
  python worker_standalone.py --name worker-freebox --port 8780

Configuration :
  Créer un fichier .env à côté du script avec :
    GEMINI_API_KEY=AIza...votre_clé...

Il appelle directement l'API Gemini REST (pas de LLMGateway, pas de moteur).
"""

import os
import sys
import time
import logging
import argparse
from typing import Optional, Dict, Any

# Chargement du .env si présent
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback manuel si python-dotenv n'est pas installé
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass

# --- Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("worker-standalone")

# Force UTF-8 sous Windows/Linux
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


# ──────────────────────────────────────────────────────────────────
# Client Gemini API minimaliste (aucune dépendance externe)
# ──────────────────────────────────────────────────────────────────

async def call_gemini(
    system_prompt: str,
    user_prompt: str,
    api_key: str = None,
    model: str = None,
    max_tokens: int = 8192,
    temperature: float = 0.7,
) -> str:
    """
    Appel direct à l'API Gemini REST — 100% autonome.
    
    Args:
        system_prompt: Instruction système
        user_prompt: Message utilisateur
        api_key: Clé API (défaut: variable d'environnement)
        model: Nom du modèle (défaut: GEMINI_MODEL)
        max_tokens: Limite de tokens en sortie
        temperature: Créativité (0-2)
        
    Returns:
        Texte de la réponse Gemini
    """
    import aiohttp

    key = api_key or GEMINI_API_KEY
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY manquante. "
            "Créez un fichier .env avec GEMINI_API_KEY=votre_clé"
        )

    mdl = model or GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{mdl}:generateContent?key={key}"
    )

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": user_prompt}]}
        ],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Gemini API erreur {resp.status}: {error_text[:300]}"
                )

            data = await resp.json()

            # Extraire le texte de la réponse
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")

            return "[Réponse vide de Gemini]"


# ──────────────────────────────────────────────────────────────────
# Worker Daemon (autosuffisant)
# ──────────────────────────────────────────────────────────────────

class WorkerStandalone:
    """
    Worker Swarm autonome — ne dépend d'aucun module du moteur.
    
    Reçoit des tâches via POST /execute, appelle Gemini directement,
    retourne le résultat au maître.
    """

    def __init__(self, name: str, port: int, host: str = "0.0.0.0"):
        self.name = name
        self.port = port
        self.host = host
        self._start_time = time.time()
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._current_task: Optional[str] = None
        self._task_history: list = []  # Historique des 50 dernières tâches

    async def execute_task(self, request: dict) -> Dict[str, Any]:
        """Exécute une tâche via l'API Gemini et retourne le résultat."""
        task_id = request.get("task_id", f"task_{int(time.time())}")
        objective = request.get("task_objective", "")
        context = request.get("relevant_context", "")
        model_tier = request.get("model_tier", "leger")

        self._current_task = task_id
        start_time = time.time()

        logger.info(
            f"[{self.name}] ▶ Tâche '{task_id}' reçue : {objective[:80]}..."
        )

        try:
            # Construction du prompt
            system_prompt = (
                "Tu es un agent d'exécution spécialisé déployé sur un worker distant. "
                "Exécute la tâche demandée de manière rigoureuse, concise et en français. "
                "Retourne un résultat structuré et actionnable."
            )
            user_prompt = f"## OBJECTIF\n{objective}"
            if context:
                user_prompt += f"\n\n## CONTEXTE\n{context[:8000]}"

            # Appel Gemini direct
            response = await call_gemini(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            elapsed = time.time() - start_time
            self._tasks_completed += 1
            self._current_task = None

            # Historique (FIFO 50 entrées)
            self._task_history.append({
                "task_id": task_id,
                "objective": objective[:100],
                "status": "success",
                "elapsed": round(elapsed, 2),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            if len(self._task_history) > 50:
                self._task_history.pop(0)

            logger.info(
                f"[{self.name}] ✅ Tâche '{task_id}' terminée en {elapsed:.1f}s "
                f"({len(response)} chars)"
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
                    "model_used": GEMINI_MODEL,
                    "response_length": len(response),
                },
            }

        except Exception as e:
            elapsed = time.time() - start_time
            self._tasks_failed += 1
            self._current_task = None

            self._task_history.append({
                "task_id": task_id,
                "objective": objective[:100],
                "status": "error",
                "error": str(e)[:200],
                "elapsed": round(elapsed, 2),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            if len(self._task_history) > 50:
                self._task_history.pop(0)

            logger.error(f"[{self.name}] ❌ Tâche '{task_id}' échouée : {e}")

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
        """État du worker (heartbeat + métriques)."""
        return {
            "name": self.name,
            "version": "6.0.0-standalone",
            "status": "busy" if self._current_task else "idle",
            "current_task": self._current_task,
            "uptime_seconds": round(time.time() - self._start_time),
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "port": self.port,
            "model": GEMINI_MODEL,
            "has_api_key": bool(GEMINI_API_KEY),
        }

    def create_app(self):
        """Crée l'application FastAPI."""
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI(
            title=f"Moteur — Worker Standalone '{self.name}'",
            description=(
                "Worker Swarm autonome — exécute des tâches via Gemini API.\n\n"
                "**Endpoints :**\n"
                "- `GET /health` : Health check\n"
                "- `GET /status` : État détaillé + métriques\n"
                "- `GET /history` : Historique des 50 dernières tâches\n"
                "- `POST /execute` : Exécuter une tâche"
            ),
            version="6.0.0",
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        daemon = self

        @app.get("/health")
        async def health():
            """Health check simple pour le monitoring HA."""
            return {
                "status": "ok",
                "worker": daemon.name,
                "uptime": round(time.time() - daemon._start_time),
            }

        @app.get("/status")
        async def status():
            """État complet du worker (heartbeat)."""
            return daemon.get_status()

        @app.get("/history")
        async def history():
            """Historique des 50 dernières tâches (pour debug/monitoring)."""
            return {
                "worker": daemon.name,
                "count": len(daemon._task_history),
                "tasks": list(reversed(daemon._task_history)),
            }

        @app.post("/execute")
        async def execute(request: dict):
            """Exécute une tâche et retourne le résultat."""
            if daemon._current_task:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": f"Worker '{daemon.name}' occupé",
                        "current_task": daemon._current_task,
                    },
                )
            return await daemon.execute_task(request)

        return app


# ──────────────────────────────────────────────────────────────────
# Point d'entrée CLI
# ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Moteur — Worker Standalone Swarm"
    )
    parser.add_argument(
        "--name", default="worker-freebox",
        help="Nom unique du worker (défaut: worker-freebox)"
    )
    parser.add_argument(
        "--port", type=int, default=8780,
        help="Port HTTP (défaut: 8780)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Hôte d'écoute (défaut: 0.0.0.0)"
    )
    parser.add_argument(
        "--model", default=None,
        help="Modèle Gemini (défaut: gemini-2.5-flash)"
    )
    args = parser.parse_args()

    if args.model:
        global GEMINI_MODEL, GEMINI_API_URL
        GEMINI_MODEL = args.model

    # Vérification de la clé API
    if not GEMINI_API_KEY:
        logger.error(
            "❌ GEMINI_API_KEY manquante !\n"
            "   Créez un fichier .env à côté de ce script avec :\n"
            "   GEMINI_API_KEY=votre_clé_api"
        )
        sys.exit(1)

    logger.info(f"╔══════════════════════════════════════════════╗")
    logger.info(f"║  Moteur — Worker Standalone               ║")
    logger.info(f"║  Nom    : {args.name:<35}║")
    logger.info(f"║  Port   : {args.port:<35}║")
    logger.info(f"║  Modèle : {GEMINI_MODEL:<35}║")
    logger.info(f"║  API Key: {'✅ OK':<35}║")
    logger.info(f"╚══════════════════════════════════════════════╝")

    daemon = WorkerStandalone(name=args.name, port=args.port, host=args.host)
    app = daemon.create_app()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
