"""
core/app_state.py — Singleton partagé de l'état global du tab5-engine.

Créé lors du refactoring de gui_server.py (Semaine 3).

Problème résolu : gui_server.py contenait 10+ variables globales non-structurées
partagées entre 72 routes. Tout accès concurrent était non protégé.

Solution : Classe AppState singleton thread-safe centralisant tous les états,
importable depuis n'importe quel module api/routes/*.py sans dépendances circulaires.

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import asyncio
import threading
import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

# Import conditionnel de TTLCache (avec fallback dict)
try:
    from cachetools import TTLCache
    _HAS_CACHETOOLS = True
except ImportError:
    _HAS_CACHETOOLS = False
    TTLCache = None


class AppState:
    """
    Singleton thread-safe de l'état global du serveur FastAPI.

    Centralise toutes les variables globales de gui_server.py :
    - execution_state       : état de l'exécution en cours
    - GLOBAL_CHAT_HISTORY   : historique multi-sessions (TTLCache)
    - GLOBAL_ROUTER         : instance du Router (LLM routing)
    - GLOBAL_MCP_BRIDGE     : pont MCP (servers externes)
    - sse_clients_set       : ensemble des files SSE clients
    - _FAST_PATH_CACHE      : cache TTL pour les prompts fast-path
    - _engine_instance      : singleton Engine (protégé par _engine_lock)

    Usage depuis un module de route :
        from core.app_state import get_app_state
        state = get_app_state()
        async with state.execution_lock:
            state.execution_state["status"] = "running"
    """

    _instance: Optional["AppState"] = None
    _class_lock = threading.Lock()

    def __new__(cls) -> "AppState":
        """Singleton avec double-check locking thread-safe."""
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def initialize(self) -> None:
        """
        Initialise tous les états partagés.
        Idempotent — peut être appelé plusieurs fois sans effet de bord.
        Appelé dans lifespan() de gui_server.py.
        """
        if self._initialized:
            return

        # ── État d'exécution ──
        self.execution_state: Dict[str, Any] = {
            "status": "idle",       # "idle" | "running" | "success" | "error"
            "objective": "",
            "engine_state": None,   # GlobalState sérialisé
            "error_message": None,
        }
        # Lock asyncio pour les écritures concurrentes sur execution_state
        self.execution_lock = asyncio.Lock()

        # ── Engine singleton ──
        self._engine_instance = None
        self.engine_lock = asyncio.Lock()  # double-check lock pour init Engine

        # ── Historique de chat multi-sessions ──
        # TTL 10 min + LRU 500 sessions max (protection OOM sur VM Alpine)
        if _HAS_CACHETOOLS:
            self.chat_history: Any = TTLCache(maxsize=500, ttl=600)
        else:
            self.chat_history: Dict = {}

        # ── Router global ──
        self.global_router = None   # Instance core.router.Router

        # ── [P1-2.1] Assemblage moteur canonique (factory) ──
        # Router/Engine/config construits UNE seule fois et partagés par toutes
        # les surfaces (gui, routes, dreamer, pipeline). Évite les Router nus
        # (sans gateway/RAG/config) qui tuaient le slow-path LLM, le RAG et l'Elo.
        self._shared_engine = None
        self._shared_router = None
        self._shared_config = None
        self._build_lock = threading.Lock()  # build synchrone thread-safe

        # ── MCP Bridge ──
        self.mcp_bridge = None      # Instance core.mcp_bridge.MCPBridge

        # ── SSE Clients ──
        # set() protégé par asyncio.Lock (pas de list.remove() concurrent)
        self.sse_clients: Set[asyncio.Queue] = set()
        self.sse_lock = asyncio.Lock()

        # ── Cache fast-path ──
        # TTL 15s pour les prompts répétés du fast-path conversation
        if _HAS_CACHETOOLS:
            self.fast_path_cache: Any = TTLCache(maxsize=100, ttl=15)
        else:
            self.fast_path_cache: Dict = {}

        self._initialized = True
        logger.info("[AppState] ✅ État partagé initialisé (singleton thread-safe).")

    # ──────────────────────────────────────────────────────────────
    # Propriétés d'accès sécurisé
    # ──────────────────────────────────────────────────────────────

    @property
    def engine(self):
        """Retourne l'instance Engine (peut être None avant initialisation)."""
        return self._engine_instance

    @engine.setter
    def engine(self, value):
        """Définit l'instance Engine."""
        self._engine_instance = value

    # ──────────────────────────────────────────────────────────────
    # [P1-2.1] Assemblage moteur canonique via la factory
    # ──────────────────────────────────────────────────────────────

    def get_shared_assembly(self, session_id: str = "shared_session"):
        """
        [P1-2.1] Construit (une seule fois) et renvoie l'assemblage canonique
        (engine, router, config) via core.factory.create_engine. Thread-safe.

        Toutes les surfaces de production doivent passer par ici plutôt que
        d'instancier un `Router(default_agent="planner")` nu : sans gateway, RAG
        ni config, le slow-path LLM, l'injection RAG et le classement Elo sont
        morts (cf. plan de remédiation 2.1).

        Returns:
            tuple (engine, router, config)
        """
        if self._shared_router is not None:
            return self._shared_engine, self._shared_router, self._shared_config
        with self._build_lock:
            if self._shared_router is None:
                # Import tardif : évite un cycle d'import au chargement du module
                # (factory importe engine/router qui peuvent importer app_state).
                from core.factory import create_engine
                engine, router, config = create_engine(session_id=session_id)
                self._shared_engine = engine
                self._shared_router = router
                self._shared_config = config
                self.global_router = router  # compat legacy
                logger.info(
                    "[AppState] [P1-2.1] Assemblage moteur canonique construit via factory."
                )
        return self._shared_engine, self._shared_router, self._shared_config

    def get_shared_router(self, session_id: str = "shared_session"):
        """[P1-2.1] Raccourci : Router canonique câblé (gateway + RAG + config)."""
        return self.get_shared_assembly(session_id)[1]

    async def broadcast_sse(self, event_type: str, data: Any) -> None:
        """
        Diffuse un événement formaté SSE à tous les clients connectés.
        Thread-safe via sse_lock. Les clients déconnectés sont nettoyés automatiquement.

        Args:
            event_type : Type de l'événement (ex: "task_update", "quotas_updated")
            data       : Données serialisables JSON
        """
        payload = {
            "event": event_type,
            "data": data,
            "engine_state": self.execution_state.get("engine_state"),
            "status": self.execution_state.get("status"),
        }
        # Copie du set pour itération sans lock (safe en asyncio mono-thread)
        for queue in list(self.sse_clients):
            try:
                await queue.put(payload)
            except Exception:
                pass  # Client déconnecté — retiré via sse_lock au cleanup


# ──────────────────────────────────────────────────────────────────
# Instance globale et fonctions d'accès
# ──────────────────────────────────────────────────────────────────

# Singleton accessible partout via : from core.app_state import get_app_state
_app_state_instance = AppState()


def get_app_state() -> AppState:
    """
    Retourne le singleton AppState initialisé.
    Utilisable depuis n'importe quel module de route, service ou agent.

    Returns:
        AppState : Singleton partagé de l'état global du moteur.

    Raises:
        RuntimeError : Si l'état n'a pas encore été initialisé (avant startup).
    """
    if not _app_state_instance._initialized:
        # Auto-init lazy (pour les tests unitaires et les imports directs)
        _app_state_instance.initialize()
    return _app_state_instance


async def broadcast_event(event_type: str, data: Any) -> None:
    """
    Fonction utilitaire globale pour diffuser un événement SSE.
    Alias de get_app_state().broadcast_sse() pour compatibilité avec gui_server.py.

    Args:
        event_type : Type de l'événement
        data       : Données serialisables JSON
    """
    await get_app_state().broadcast_sse(event_type, data)
