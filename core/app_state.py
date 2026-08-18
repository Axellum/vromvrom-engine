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
import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Verrou d'exécution PAR SESSION (#T324) ──────────────────────────────
# Anciennement un unique verrou global : toute exécution (IHM, vocal, DAG)
# était sérialisée et renvoyait 409 aux autres. Désormais la granularité est
# la SESSION : deux sessions différentes peuvent s'exécuter en parallèle,
# une même session ne s'exécute jamais deux fois (garde-fou anti-doublon).
#
# Le plafond de concurrence global reste nécessaire : le Deck est une machine
# modeste qui fait tourner la prod 24/7, « plus de 409 » ne doit pas se payer
# en saturation. Au-delà du plafond, le 409 reste la bonne réponse, avec un
# message qui distingue plafond et doublon de session.
#
# Variable d'environnement : MOTEUR_EXECUTION_MAX_CONCURRENCY (défaut 3).
#   - 1 : revient au comportement historique (une exécution à la fois).
#   - 3 : IHM + vocal + un DAG de fond peuvent tourner en parallèle.
#   - >3 : réservé aux machines plus puissantes que le Deck.
def _max_concurrence() -> int:
    brut = os.environ.get("MOTEUR_EXECUTION_MAX_CONCURRENCY")
    if brut:
        try:
            demande = int(brut)
        except ValueError:
            logger.warning(
                f"[T324] MOTEUR_EXECUTION_MAX_CONCURRENCY illisible ({brut!r}) — défaut 3 retenu."
            )
            return 3
        if demande < 1:
            logger.warning(
                f"[T324] MOTEUR_EXECUTION_MAX_CONCURRENCY={demande} invalide (<1) — défaut 3 retenu."
            )
            return 3
        return demande
    return 3


def _cle_execution(session_id: str | None, source=None) -> str:
    """Clé d'exécution (#T324) : identité de la session dans le registre.

    Ordre de priorité :
      1. `session_id` fourni par le client (champ dédié du corps de requête) ;
      2. à défaut, la source de la requête (device_id, sinon type:mode) —
         c'est ce qui permet au Tab5 de rejouer une commande vocale sur le
         même device et d'être bloqué en doublon.

    Ne PAS confondre avec le `session_id` d'audit/BDD (unique par requête,
    généré en interne) : celui-ci est la CLÉ de concurrence, stable.
    """
    if session_id and str(session_id).strip():
        return str(session_id).strip()
    if source is not None:
        device_id = getattr(source, "device_id", None)
        if device_id:
            return f"src:{getattr(source, 'type', 'unknown')}:{device_id}"
        return f"src:{getattr(source, 'type', 'unknown')}:{getattr(source, 'mode', 'default')}"
    return "src:unknown:default"


# Messages 409 distincts : doublon de session ≠ plafond de concurrence.
MSG_DOUBLON_SESSION = (
    "Une exécution est déjà en cours pour cette session. "
    "Attendez sa fin ou utilisez /api/stop."
)
MSG_PLAFOND_CONCURRENCE = (
    "Plafond de concurrence atteint (MOTEUR_EXECUTION_MAX_CONCURRENCY). "
    "Réessayez dans quelques instants."
)

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
        # Vue AGRÉGÉE (#T324) : `status == "running"` dès qu'au moins une
        # exécution tourne. Garde exactement sa forme historique (4 clés) pour
        # ne casser aucun consommateur (GET /api/status, stop, abort, Dreamer…).
        self.execution_state: dict[str, Any] = {
            "status": "idle",       # "idle" | "running" | "success" | "error"
            "objective": "",
            "engine_state": None,   # GlobalState sérialisé
            "error_message": None,
        }
        # Registre des exécutions EN COURS, indexé par clé de session (#T324).
        # Une entrée est créée par begin_execution() et TOUJOURS retirée par
        # end_execution() (try/finally) : aucune fuite d'entrée, sinon la
        # session serait bloquée pour toujours.
        self.execution_registry: dict[str, dict[str, Any]] = {}
        # Compteur de REQUÊTES EN VOL, distinct du registre de concurrence
        # (#T344). Le plafond de concurrence ne protège que run_full_pipeline,
        # mais le dreamer doit rester silencieux pendant TOUTE activité
        # utilisateur (chemins légers compris). Ce compteur maintient
        # execution_state["status"] == "running" tant qu'une requête tourne,
        # sans jamais influencer le plafond de concurrence.
        self.active_requests: int = 0
        # Plafond de concurrence global (variable d'environnement).
        self.max_concurrency: int = _max_concurrence()
        # Lock asyncio pour les écritures concurrentes sur execution_state et
        # execution_registry (l'état est manipulé depuis la boucle d'événements).
        self.execution_lock = asyncio.Lock()

        # ── Engine singleton ──
        self._engine_instance = None
        self.engine_lock = asyncio.Lock()  # double-check lock pour init Engine

        # ── Historique de chat multi-sessions ──
        # TTL 10 min + LRU 500 sessions max (protection OOM sur VM Alpine)
        if _HAS_CACHETOOLS:
            self.chat_history: Any = TTLCache(maxsize=500, ttl=600)
        else:
            self.chat_history: dict = {}

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
        self.sse_clients: set[asyncio.Queue] = set()
        self.sse_lock = asyncio.Lock()

        # ── Cache fast-path ──
        # TTL 15s pour les prompts répétés du fast-path conversation
        if _HAS_CACHETOOLS:
            self.fast_path_cache: Any = TTLCache(maxsize=100, ttl=15)
        else:
            self.fast_path_cache: dict = {}

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

    # ──────────────────────────────────────────────────────────────
    # Cycle de vie d'une exécution PAR SESSION (#T324)
    # ──────────────────────────────────────────────────────────────

    def _refresh_aggregate_locked(self, status: str = "success", error_message: str | None = None) -> None:
        """Reconstruit la vue agrégée de execution_state (appelé sous execution_lock).

        Règle (#T324) : `status == "running"` dès qu'au moins une exécution
        tourne dans le registre. Quand la dernière se termine, on applique le
        statut terminal (success/error) et le message d'erreur éventuel.

        Règle (#T344) : `status == "running"` aussi tant que le compteur de
        requêtes en vol (`active_requests`) est positif — les chemins légers
        (casual_chat, HA déterministe, fuzzy, repli LLM, small-talk) ne prennent
        aucun créneau du plafond mais doivent quand même maintenir le dreamer
        silencieux pendant l'activité utilisateur.
        """
        if self.active_requests > 0 or self.execution_registry:
            self.execution_state["status"] = "running"
            return
        self.execution_state["status"] = status
        self.execution_state["error_message"] = error_message

    async def check_duplicate(self, session_key: str) -> dict | None:
        """Vérifie le doublon de session SANS consommer de créneau (#T344).

        Lecture seule du registre : renvoie le dict d'erreur 409 si la clé est
        déjà en vol, sinon None. Ne vérifie PAS le plafond de concurrence : un
        chemin léger ne doit jamais être refusé parce que le plafond est saturé
        par des exécutions lourdes. Utilisée en amont d'`analyze_request` pour
        ne pas payer le routeur (LLM) pour une requête qui sera de toute façon
        refusée en doublon. La protection réelle reste `begin_execution`, qui
        re-vérifie atomiquement sous lock juste avant le pipeline.
        """
        async with self.execution_lock:
            if session_key in self.execution_registry:
                return {"status_code": 409, "detail": MSG_DOUBLON_SESSION}
            return None

    async def begin_execution(self, session_key: str, objective: str) -> dict | None:
        """Tente de démarrer une exécution pour la session.

        Args:
            session_key : Clé de session (#T324) — identité de concurrence.
            objective   : Objectif affiché dans la vue agrégée.

        Returns:
            None si l'exécution est acceptée (entrée enregistrée dans le
            registre), sinon un dict d'erreur HTTP : {"status_code": 409,
            "detail": ...} — doublon de session ou plafond atteint.
        """
        async with self.execution_lock:
            if session_key in self.execution_registry:
                return {"status_code": 409, "detail": MSG_DOUBLON_SESSION}
            if len(self.execution_registry) >= self.max_concurrency:
                return {"status_code": 409, "detail": MSG_PLAFOND_CONCURRENCE}
            self.execution_registry[session_key] = {
                "status": "running",
                "objective": objective,
                "engine_state": None,
                "error_message": None,
            }
            self.execution_state.update({
                "status": "running",
                "objective": objective,
                "engine_state": None,
                "error_message": None,
            })
            return None

    async def end_execution(
        self,
        session_key: str,
        status: str = "success",
        engine_state=None,
        error_message: str | None = None,
    ) -> None:
        """Libère l'entrée de la session (idempotent, à appeler en try/finally).

        Retire l'entrée du registre puis rafraîchit la vue agrégée. Si une
        autre exécution tourne encore, la vue reste "running" ; sinon elle
        prend le statut terminal — celui fourni par défaut, ou celui déjà
        enregistré sur l'entrée si un chemin (SSE erreur/déconnexion) l'a posé.
        """
        async with self.execution_lock:
            entry = self.execution_registry.pop(session_key, None)
            if entry is not None and entry.get("status") in ("success", "error"):
                status = entry["status"]
                if entry.get("error_message"):
                    error_message = entry.get("error_message")
            if not self.execution_registry:
                self.execution_state["engine_state"] = engine_state
            self._refresh_aggregate_locked(status=status, error_message=error_message)

    async def set_execution_status(self, session_key: str, status: str) -> None:
        """Met à jour le statut d'une exécution en cours (fin d'étape, etc.).

        Ne change la vue agrégée que si c'est la dernière exécution active et
        que le registre se vide (sinon "running" doit rester affiché).
        """
        async with self.execution_lock:
            entry = self.execution_registry.get(session_key)
            if entry is not None:
                entry["status"] = status
            self._refresh_aggregate_locked(status=status)

    async def begin_request(self, objective: str = "") -> None:
        """Compte une requête utilisateur en vol (#T344).

        Distinct de `begin_execution` : ce compteur ne participe PAS au plafond
        de concurrence (ne refuse jamais une requête) et ne crée aucune entrée
        dans `execution_registry`. Il sert uniquement de contre-pression au
        dreamer : tant qu'une requête tourne — chemin léger ou pipeline complet —
        `execution_state["status"]` reste "running" et le dreamer se tait.

        Doit être apparié à un appel `end_request` (try/finally).
        """
        async with self.execution_lock:
            self.active_requests += 1
            self.execution_state["status"] = "running"
            if objective:
                self.execution_state["objective"] = objective

    async def end_request(self) -> None:
        """Décrémente le compteur de requêtes en vol et rafraîchit la vue.

        Idempotent : ne passe jamais en négatif. Si plus aucune requête ni
        exécution ne tourne, la vue agrégée retombe sur "idle".
        """
        async with self.execution_lock:
            if self.active_requests > 0:
                self.active_requests -= 1
            self._refresh_aggregate_locked(status="idle")

    async def clear_executions(self, status: str = "error", error_message: str | None = None) -> None:
        """Libère TOUTES les exécutions en cours (arrêt global, abort).

        Utilisé par /api/stop et /api/vocal/abort pour débloquer l'état sans
        laisser d'entrée orpheline dans le registre (garde-fou anti-fuite).
        """
        async with self.execution_lock:
            self.execution_registry.clear()
            self.execution_state["status"] = status
            if error_message is not None:
                self.execution_state["error_message"] = error_message


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
