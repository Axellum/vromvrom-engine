"""
gui_server.py — Serveur FastAPI HMI (port 8000), point d'entrée PRODUCTION.

Assemble le moteur via core/factory.create_engine() (partagé avec main.py et
mcp_server.py) et l'expose en HTTP/SSE : chat streaming, statut, config,
métriques Elo/routing/coûts, contrôle du DreamerAgent. Le lifespan FastAPI
démarre/arrête les tâches de fond 24/7 (WatchdogDaemon MQTT, DreamerAgent,
cleanup des sessions zombies) et l'AsyncDBSerializer (core/async_db_serializer.py).

Voir aussi : contexte_ia/03_Software/CARTOGRAPHIE_MOTEUR.md §3.6 (points d'entrée)
et §2 (diagramme des groupes fonctionnels).
"""
import json
import logging
import os
import sys

try:
    import google.cloud.logging
except ImportError:
    pass
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# TTLCache pour GLOBAL_CHAT_HISTORY (protection OOM sur VM Alpine)
try:
    from cachetools import TTLCache
except ImportError:
    # Fallback si cachetools non installé
    TTLCache = None
    logger_temp = logging.getLogger("gui_server")
    logger_temp.warning("[QW-4] cachetools non disponible — GLOBAL_CHAT_HISTORY en dict classique (sans TTL).")

# Chargement des variables d'environnement (.env)
load_dotenv()

# Forcer UTF-8 sur stdout/stderr sous Windows pour éviter les crash
# 'charmap' codec quand print() rencontre des emojis/symboles unicode (→, ✅, ⛔)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass  # Python < 3.7 ou stream non-reconfigurable


from core import token_tracker
from core.cli_token_collector import collect_all_cli_tokens

# Initialisation du logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("gui_server")

try:
    _key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha_logger_key.json")
    if os.path.exists(_key_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _key_path
        _gcp_client = google.cloud.logging.Client()
        _gcp_client.setup_logging()
        logger.info("☁️  Google Cloud Logging intégré avec succès au root logger.")
except Exception as e:
    logger.warning(f"⚠️  Google Cloud Logging non initialisé : {e}")

# Lifespan handler (remplace les 2 @app.on_event('startup') dépréciés)
# Défini ici mais complété après les helpers (sse_quota_pusher_loop, swarm_heartbeat_loop)
# → voir la définition complète lifespan() juste avant app = FastAPI(lifespan=lifespan)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handler de cycle de vie FastAPI (remplace les 2 @app.on_event).
    Exécuté une seule fois au démarrage et à l'arrêt du serveur.
    Fusion de startup_event() (L258) + on_startup() (L3180).
    """
    global GLOBAL_ROUTER

    # ── Initialisation du sérialiseur SQLite  ──
    from core.async_db_serializer import AsyncDBSerializer
    db_serializer = AsyncDBSerializer.get_instance()
    await db_serializer.start()

    # ── Initialisation du Router global ──
    # [P1-2.1] Construit via l'assemblage canonique (factory) : gateway + RAG +
    # config câblés, partagé par toutes les routes. Un Router nu tuait le
    # slow-path LLM, le RAG et l'Elo.
    try:
        from core.app_state import get_app_state as _get_app_state
        GLOBAL_ROUTER = _get_app_state().get_shared_router(session_id="gui_shared")
        logger.info("[STARTUP] 🧭 GLOBAL_ROUTER (factory, câblé RAG/gateway/config) initialisé.")

        # Nettoyage des sessions zombies au démarrage
        try:
            from core.session_history import cleanup_zombie_sessions
            _zombies = cleanup_zombie_sessions()
            if _zombies > 0:
                logger.warning(f"[STARTUP] 🧹 {_zombies} session(s) zombie(s) nettoyée(s).")
            else:
                logger.info("[STARTUP] ✅ Aucune session zombie détectée.")
        except Exception as _ze:
            logger.warning(f"[STARTUP] cleanup_zombie_sessions non disponible : {_ze}")
    except Exception as e:
        logger.error(f"[STARTUP] ❌ Échec de l'initialisation de GLOBAL_ROUTER : {e}")

    # ── Initialisation du HAFuzzyMatcher (matching entités HA sans LLM) ──
    try:
        from core.ha_fuzzy_matcher import init_fuzzy_matcher
        _ha_url = os.environ.get("HASS_URL", "https://${HA_HOST:-192.168.1.x}:8123")
        _ha_token = os.environ.get("HASS_TOKEN", "")
        if not _ha_token:
            _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            if os.path.exists(_env_path):
                with open(_env_path, encoding="utf-8") as _ef:
                    for _line in _ef:
                        _line = _line.strip()
                        if _line.startswith("HASS_TOKEN="):
                            _ha_token = _line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif _line.startswith("HASS_URL="):
                            _ha_url = _line.split("=", 1)[1].strip().strip('"').strip("'")
        init_fuzzy_matcher(ha_url=_ha_url, ha_token=_ha_token)
        logger.info(f"[STARTUP] 🔍 HAFuzzyMatcher initialisé → {_ha_url}")
    except Exception as e:
        logger.warning(f"[STARTUP] HAFuzzyMatcher non initialisé : {e}")

    # ── Initialisation de la table backlog_tasks AVANT le dreamer ──
    # Sans cet appel, dreamer_main_loop() interroge get_next_task() sur une table
    # inexistante → "no such table: backlog_tasks" à chaque cycle DreamCoder.
    try:
        from core.backlog_db import init_backlog_db
        await init_backlog_db()
        logger.info("[STARTUP] 🗂️ Table backlog_tasks initialisée.")
    except Exception as e:
        logger.error(f"[STARTUP] ❌ Échec init backlog_tasks : {e}")

    # ── Lancement des tâches de fond ──
    asyncio.create_task(sse_quota_pusher_loop())
    logger.info("[STARTUP] sse_quota_pusher_loop enregistré et lancé.")

    from core.daemon_loop import daemon_main_loop
    asyncio.create_task(daemon_main_loop())
    logger.info("[STARTUP] 🔄 Démon Sentinelle 24/7 lancé.")

    from agents.dreamer_agent import dreamer_main_loop
    asyncio.create_task(dreamer_main_loop())
    logger.info("[STARTUP] 🌙 autoDream (consolidation nocturne) lancé.")

    from core.auditor_agent import auditor_main_loop
    asyncio.create_task(auditor_main_loop())
    logger.info("[STARTUP] 🕵️ Auditeur autonome (backlog auto-alimenté, opt-in via config) lancé.")

    asyncio.create_task(swarm_heartbeat_loop())
    logger.info("[STARTUP] 📡 Heartbeat Swarm Workers lancé.")

    # ── Scan CLI initial dans un thread (ne bloque pas le démarrage) ──
    import concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _startup_cli_scan)
    asyncio.create_task(_periodic_cli_scan())
    logger.info("[STARTUP] 🔍 Scan CLI initial terminé + tâche périodique lancée.")

    # ── Initialisation du singleton AppState (partage état entre modules) ──
    # Doit être fait APRÈS l'initialisation du GLOBAL_ROUTER pour synchroniser les refs
    try:
        from core.app_state import get_app_state
        _app_state = get_app_state()
        _app_state.initialize()
        # Synchroniser les références globales de gui_server vers AppState
        _app_state.global_router = GLOBAL_ROUTER
        # Connecter les sse_clients pour que les nouveaux modules de routes les partagent
        # Note : sse_clients_set et _sse_lock dans gui_server restent pour le legacy
        # _app_state.sse_clients et _app_state.sse_lock sont utilisés par api/routes/streaming.py
        logger.info("[STARTUP] 🗂️  AppState singleton initialisé et synchronisé.")
    except Exception as _e:
        logger.warning(f"[STARTUP] AppState non initialisé : {_e}")

    # ── Warm-up pool HTTP (connexion TLS pré-établie avec le provider par défaut) ──
    # Initialise le SharedHTTPPool au démarrage afin que le premier appel LLM
    # n'ait pas à payer le coût du handshake TLS.
    try:
        from core.openai_compat_provider import SharedHTTPPool
        SharedHTTPPool.get_session()  # Crée la session + pool, sans faire d'appel réel
        logger.info("[STARTUP] ⚡ SharedHTTPPool initialisé (keep-alive TLS prét).")
    except Exception as _e:
        logger.warning(f"[STARTUP] Pool HTTP non initialisé : {_e}")

    # ── Watchdog MQTT 24/7 (T80b) ──
    _watchdog = None
    if os.getenv("MQTT_HOST"):
        try:
            from core.watchdog import create_watchdog_daemon
            _watchdog = create_watchdog_daemon({
                "mqtt_host": os.getenv("MQTT_HOST", "${HA_HOST:-192.168.1.x}"),
                "mqtt_port": int(os.getenv("MQTT_PORT", "1883")),
                "mqtt_username": os.getenv("MQTT_USERNAME"),
                "mqtt_password": os.getenv("MQTT_PASSWORD"),
                "moteur_url": f"http://localhost:{os.getenv('PORT', '8000')}",
            })
            asyncio.create_task(_watchdog.start())
            logger.info(f"[STARTUP] 🐕 Watchdog MQTT lancé → {os.getenv('MQTT_HOST')}:{os.getenv('MQTT_PORT', '1883')}")
        except Exception as _we:
            logger.warning(f"[STARTUP] Watchdog MQTT non lancé : {_we}")

    logger.info("[STARTUP] ✅ tab5-engine — Démarrage complet.")
    yield  # ← Le serveur tourne ici

    # ── Arrêt propre ──
    if _watchdog:
        try:
            await _watchdog.stop()
        except Exception:
            pass
    logger.info("[SHUTDOWN] 🛑 Arrêt propre du tab5-engine.")
    # Fermeture propre du sérialiseur SQLite
    try:
        await db_serializer.stop()
    except Exception:
        pass
    # Fermeture propre du pool HTTP
    try:
        from core.openai_compat_provider import SharedHTTPPool
        SharedHTTPPool.close()
    except Exception:
        pass
    # Fermeture propre de la session HA partagée (keep-alive vocal)
    try:
        from services.execute_service import close_ha_session
        await close_ha_session()
    except Exception:
        pass


# Titre et version mis à jour
app = FastAPI(
    title="tab5-engine — IHM Dashboard",
    description="""
API REST du moteur d'orchestration multi-agents IA.

## Fonctionnalités principales
- **Exécution** : Lancement et suivi de tâches orchestrées (DAG parallèle)
- **SSE** : Streaming temps réel des événements du moteur
- **Quotas** : Monitoring des quotas API par fournisseur
- **Workflows** : Gestion des workflows (CRUD + application au moteur)
- **Configuration** : Gestion de la config du moteur (tiers, modèles, pricing)
- **Billing** : Synchronisation et collecte des tokens depuis les CLIs

## Architecture
Requête → Router → Planner (DAG) → Executor/Antigravity/HA Agent → Reviewer → Résultat
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Exécution", "description": "Lancement et contrôle des tâches"},
        {"name": "Streaming", "description": "SSE (Server-Sent Events) temps réel"},
        {"name": "Quotas & Billing", "description": "Monitoring des quotas et facturation"},
        {"name": "Configuration", "description": "Gestion de la configuration du moteur"},
        {"name": "Workflows", "description": "Gestion des workflows (CRUD)"},
        {"name": "Contexte", "description": "Chargement des fichiers de contexte 3-Layers"},
        {"name": "APIs", "description": "Status et disponibilité des APIs externes"},
    ]
)

# Configuration CORS pilotée par l'environnement.
# MOTEUR_CORS_ORIGINS : liste d'origines séparées par des virgules
#   (ex. "http://192.168.1.x:8000,http://localhost:8000").
# Sécurité : la combinaison allow_origins=["*"] + allow_credentials=True est
# invalide/dangereuse (CSRF cross-origin authentifié). Si aucune origine n'est
# définie, on retombe sur "*" SANS credentials.
_cors_env = os.environ.get("MOTEUR_CORS_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    _cors_allow_credentials = True
else:
    _cors_origins = ["*"]
    _cors_allow_credentials = False
    logger.warning(
        "[CORS] ⚠️ MOTEUR_CORS_ORIGINS non défini : CORS ouvert (*) SANS credentials. "
        "Définissez les origines autorisées pour activer les credentials."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [P0-1.1] Authentification fail-closed (require_auth) appliquée au MONTAGE de
# tous les routers sensibles. ⚠️ DÉPLOIEMENT : définir MOTEUR_API_KEY dans le
# .env, sinon ces routes renvoient 503 (jamais d'accès libre). Restent publics :
# les webhooks WhatsApp (vérification de signature/token propre) et les assets
# statiques (montés en fin de fichier).
from core.auth import require_auth, require_websocket_auth

_AUTH_DEP = [Depends(require_auth)]

# [HMI v2] Émission de tickets éphémères pour SSE/WS (Bearer requis pour émettre,
# le ticket sert ensuite à ouvrir le flux sans exposer MOTEUR_API_KEY en query param).
from api.routes.auth_tickets import router as auth_tickets_router

app.include_router(auth_tickets_router, dependencies=_AUTH_DEP)

# Routes API modulaires (HITL)
from api.routes.approval import router as approval_router

app.include_router(approval_router, dependencies=_AUTH_DEP)

# Routes API de métriques et télémétrie (Dashboard in-house)
from api.routes.metrics import router as metrics_router

app.include_router(metrics_router, dependencies=_AUTH_DEP)

# Routes API d'administration des modèles (SQLite models_registry.db)
from api.routes.models_admin import router as models_admin_router

app.include_router(models_admin_router, dependencies=_AUTH_DEP)

# Routes API de gestion et monitoring du Swarm Workers
from api.routes.swarm import router as swarm_router

app.include_router(swarm_router, dependencies=_AUTH_DEP)

# [WHATSAPP] Webhooks PUBLICS — auth propre par signature/token (cf. api/routes/whatsapp.py).
# Ne PAS protéger par require_auth : Meta/Twilio ne portent pas de Bearer MOTEUR_API_KEY.
from api.routes.whatsapp import router as whatsapp_router

app.include_router(whatsapp_router)

# [HEALTHZ] Sonde de liveness PUBLIQUE (auto-bascule pipeline vocal HA).
# Sans auth volontairement : répondre 200 suffit à prouver que le moteur est vivant.
from api.routes.health import router as health_router

app.include_router(health_router)

# Routes Google Workspace (Calendar, Drive, Gmail, Sheets, Tasks, YouTube, Contacts)
from api.routes.google_workspace import router as google_workspace_router

app.include_router(google_workspace_router, dependencies=_AUTH_DEP)

# Routes Workflows CRUD (sauvegarde, chargement, suppression, application)
from api.routes.workflows import router as workflows_router

app.include_router(workflows_router, dependencies=_AUTH_DEP)

# Nouveaux modules extraits de gui_server.py (Semaine 3 — Refactoring)
from api.routes.daemon import router as daemon_router

app.include_router(daemon_router, dependencies=_AUTH_DEP)

# [v12.1.0] Routes API d'exécution d'agents
from api.routes.agents import router as agents_router

app.include_router(agents_router, dependencies=_AUTH_DEP)

# [#T159] CRUD /api/agents (vue AgentsManager de l'IHM v2)
from api.routes.agents_crud import router as agents_crud_router

app.include_router(agents_crud_router, dependencies=_AUTH_DEP)

# [#T192] Outils prompt (PromptEngineerAgent branché pour la page Prompt IHM)
from api.routes.prompt_tools import router as prompt_tools_router

app.include_router(prompt_tools_router, dependencies=_AUTH_DEP)


from api.routes.streaming import router as streaming_router

app.include_router(streaming_router, dependencies=_AUTH_DEP)

from api.routes.ha import router as ha_router

app.include_router(ha_router, dependencies=_AUTH_DEP)

from api.routes.execution import router as execution_router

app.include_router(execution_router, dependencies=_AUTH_DEP)

# Modules Batch 2 — Billing, Quotas, APIs, Contexte
from api.routes.billing import router as billing_router

app.include_router(billing_router, dependencies=_AUTH_DEP)

from api.routes.quotas import router as quotas_router

app.include_router(quotas_router, dependencies=_AUTH_DEP)

from api.routes.apis_external import router as apis_external_router

app.include_router(apis_external_router, dependencies=_AUTH_DEP)

from api.routes.context import router as context_router

app.include_router(context_router, dependencies=_AUTH_DEP)

from api.routes.events import router as events_router

app.include_router(events_router, dependencies=_AUTH_DEP)

# Proxy OpenAI-compatible — expose /v1/chat/completions, /v1/models, /v1/providers
# Permet à Cline, Continue.dev, Aider et tout IDE OpenAI-compatible de pointer sur le moteur.
# Protégé : les clients OpenAI-compatibles envoient leur Bearer → y mettre MOTEUR_API_KEY.
from api.routes.openai_proxy import router as openai_proxy_router

app.include_router(openai_proxy_router, dependencies=_AUTH_DEP)

# [DREAMCODER] Routes API du Backlog de tâches DreamCoder
from api.routes.backlog import router as backlog_router

app.include_router(backlog_router, dependencies=_AUTH_DEP)

# Persistance des conversations Chat IHM (T83)
from api.routes.chat_messages import router as chat_messages_router

app.include_router(chat_messages_router, dependencies=_AUTH_DEP)

# [OBSERVABILITÉ] Santé Circuit Breakers — JSON, Prometheus, dashboard HTML
from api.routes.observability import router as observability_router

app.include_router(observability_router, dependencies=_AUTH_DEP)



CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Variables globales de suivi de l'exécution (redirection unifiée vers AppState)
from core.app_state import get_app_state

execution_state = get_app_state().execution_state

# Lock asyncio pour protéger les écritures concurrentes sur execution_state
_execution_state_lock = asyncio.Lock()

# Lock asyncio pour l'initialisation du singleton Engine (double-check)
_engine_lock = asyncio.Lock()
_engine_instance = None

# GLOBAL_CHAT_HISTORY avec TTL 10 min + LRU 500 sessions max (protection OOM)
# Chaque session garde 15 messages max (géré dans le code), + expiration auto après 10 min d'inactivité.
if TTLCache is not None:
    GLOBAL_CHAT_HISTORY = TTLCache(maxsize=500, ttl=600)  # 600s = 10 minutes
else:
    GLOBAL_CHAT_HISTORY = {}  # Fallback dict classique si cachetools manquant

GLOBAL_ROUTER = None
GLOBAL_MCP_BRIDGE = None

# Cache TTL pour le fast-path conversation (protection contre les prompts répétés)
# TTL court (15s) : suffisant pour les double-envois/spam sans biaiser les réponses contextuelles.
# clé = hash(system_prompt + user_prompt), valeur = réponse déjà calculée.
if TTLCache is not None:
    _FAST_PATH_CACHE = TTLCache(maxsize=100, ttl=15)  # 15s TTL, 100 prompts max
else:
    _FAST_PATH_CACHE = {}  # Fallback sans TTL si cachetools manquant

# Lock asyncio + set pour sse_clients (protection race condition list.remove)
_sse_lock = asyncio.Lock()
sse_clients_set: set = set()
# sse_clients (list) déclaré après 'from typing import List' ci-dessous



from typing import Any

# Liste de compatibilité pour les routes SSE (protégée via sse_clients_set)
sse_clients: list[asyncio.Queue] = []

class ConfigBody(BaseModel):
    planner_model: str
    executor_model: str
    antigravity_model: str
    ha_model: str = "moyen"
    tiers: dict[str, list[str]]
    persistent_agents: dict | None = None


# load_config est importé depuis core.llm_gateway

async def broadcast_event(event_type: str, data: Any):
    """
    Diffuse un évènement formaté en temps réel à tous les clients HMI connectés.
    Redirigé vers AppState pour la centralisation du flux SSE.
    """
    from core.app_state import broadcast_event as _broadcast
    await _broadcast(event_type, data)

async def sse_quota_pusher_loop():
    """Tâche de fond : push SSE + snapshot BDD toutes les 30-60s."""
    logger.info("[SSE_PUSHER] Démarrage de la boucle de push en arrière-plan.")
    from api.routes.apis_external import get_apis_status
    from api.routes.billing import get_tokens
    from api.routes.quotas import get_quotas

    _snapshot_counter = 0  # Compteur pour snapshot BDD toutes les 60s (1 cycle sur 2)
    _cleanup_counter = 0   # Compteur pour nettoyage quotidien
    while True:
        await asyncio.sleep(30)
        try:
            # Récupérer les données (nécessaire pour BDD ET SSE)
            quotas_data = get_quotas()

            # Broadcast SSE uniquement s'il y a des clients connectés
            from core.app_state import get_app_state
            app_state = get_app_state()
            if app_state.sse_clients:
                apis_data = get_apis_status()
                tokens_data = get_tokens()
                await broadcast_event("quotas_updated", quotas_data)
                await broadcast_event("apis_status_updated", apis_data)
                await broadcast_event("tokens_updated", tokens_data)
            else:
                # Sans clients SSE : sync billing toutes les 5 min (10 cycles)
                if _snapshot_counter % 10 == 0:
                    try:
                        get_apis_status()  # Déclenche insert_billing_record si DeepSeek répond
                    except Exception:
                        pass

            # Snapshot quotas en BDD toutes les 60s (INDÉPENDANT des clients SSE) via le sérialiseur
            _snapshot_counter += 1
            if _snapshot_counter % 2 == 0:  # Toutes les 60s (30s * 2)
                try:
                    from core.async_db_serializer import AsyncDBSerializer
                    from core.session_history import insert_quota_snapshot
                    serializer = AsyncDBSerializer.get_instance()
                    inserted = await serializer.execute(lambda: insert_quota_snapshot(quotas_data))
                    if inserted > 0:
                        logger.debug(f"[SSE_PUSHER] Snapshot quotas : {inserted} métriques enregistrées (async)")
                except Exception as snap_err:
                    logger.warning(f"[SSE_PUSHER] Erreur snapshot quotas : {snap_err}")

            # Nettoyage des vieux snapshots toutes les ~24h (2880 cycles de 30s)
            _cleanup_counter += 1
            if _cleanup_counter >= 2880:
                _cleanup_counter = 0
                try:
                    from core.async_db_serializer import AsyncDBSerializer
                    from core.session_history import cleanup_old_snapshots
                    serializer = AsyncDBSerializer.get_instance()
                    await serializer.execute(lambda: cleanup_old_snapshots(retention_days=30))
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"[SSE_PUSHER] Erreur lors de la diffusion périodique : {e}")

async def swarm_heartbeat_loop():
    """Tâche de fond : effectue un heartbeat de tous les workers Swarm toutes les 30s."""
    logger.info("[SWARM_HEARTBEAT] Démarrage de la boucle de heartbeat Swarm.")
    try:
        from core.worker_registry import get_worker_registry
        registry = get_worker_registry()
    except Exception as e:
        logger.error(f"[SWARM_HEARTBEAT] Impossible de charger le registre : {e}")
        return

    while True:
        try:
            await registry.heartbeat_all()
            logger.debug("[SWARM_HEARTBEAT] Heartbeat Swarm effectué.")
        except Exception as e:
            logger.warning(f"[SWARM_HEARTBEAT] Erreur heartbeat Swarm : {e}")
        await asyncio.sleep(30)

# Les 2 anciens @app.on_event('startup') sont fusionnés dans lifespan()
# défini juste avant la création de l'app FastAPI (ligne ~44).
# Ce bloc est intentionnellement vide — NE PAS supprimer ce commentaire.


# ──────────────────────────────────────────────────────────────────
# Routes API Agents Persistants (Daemon & Dreamer)
# ──────────────────────────────────────────────────────────────────

# [T136] run_engine_flow (orchestrateur legacy [DÉPRÉCIÉ V9 AR-4], ~200 lignes,
# sans appelant) supprimé — remplacé depuis par services.pipeline_service.run_engine_background().

# Cache local du dernier scan CLI (évite de rescanner à chaque refresh 5s)
_cli_cache = {"data": None, "timestamp": None}






# ──────────────────────────────────────────────────────────────────
# Routes Home Assistant pour les cartes HMI
# ──────────────────────────────────────────────────────────────────

class HAControlBody(BaseModel):
    entity_id: str
    service: str
    domain: str | None = None
    service_data: dict[str, Any] | None = None



PRICING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing_strategy.json")

# ──────────────────────────────────────────────────────────────────
# Routes Models Registry (models_registry.db)
# ──────────────────────────────────────────────────────────────────











# ── Routes Quotas temps réel  ────────────────────────────












# Variables pour le suivi de la synchronisation de facturation
billing_sync_state = {
    "status": "idle",
    "message": "",
    "cost": 0.0,
    "currency": "USD",
    "last_sync": None
}

async def handle_scraper_success(stdout_str: str):
    global billing_sync_state
    try:
        result_json = None
        for line in stdout_str.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    result_json = json.loads(line)
                    break
                except Exception:
                    pass

        if result_json and result_json.get("status") == "success":
            gcp = result_json.get("gcp")
            claude = result_json.get("claude")

            gcp_cost_usd = None
            cost_raw = 0.0
            currency = "USD"

            if gcp:
                gcp_cost_usd = gcp.get("cost_usd", 0.0)
                cost_raw = gcp.get("cost_raw", 0.0)
                currency = gcp.get("currency", "USD")
                token_tracker.update_real_billing(gcp_cost_usd=gcp_cost_usd)

            claude_usage = None
            claude_text = None
            if claude:
                claude_usage = claude.get("message_usage_pct")
                claude_text = claude.get("summary_text")
                token_tracker.update_real_billing(claude_message_usage_pct=claude_usage, claude_summary_text=claude_text)

            billing_sync_state["status"] = "success"
            billing_sync_state["message"] = "Synchronisation réussie !"
            billing_sync_state["cost"] = cost_raw
            billing_sync_state["currency"] = currency
            from datetime import datetime
            billing_sync_state["last_sync"] = datetime.now().isoformat()
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = result_json.get("message") if result_json else "Format de sortie du scraper invalide."
    except Exception as e:
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = f"Erreur de décodage des résultats : {str(e)}"

async def run_billing_sync_flow():
    global billing_sync_state

    node_cmd = ["node", "tools/billing_scraper.js", "--headless=true"]
    cwd = os.path.dirname(os.path.abspath(__file__))

    try:
        billing_sync_state["status"] = "running"
        billing_sync_state["message"] = "Vérification de la session en arrière-plan (mode headless)..."

        proc = await asyncio.create_subprocess_exec(
            *node_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )

        stdout, stderr = await proc.communicate()
        exit_code = proc.returncode

        stdout_str = stdout.decode("utf-8", errors="ignore").strip()
        stderr_str = stderr.decode("utf-8", errors="ignore").strip()

        logger.info(f"Headless scraper exit code: {exit_code}")
        logger.info(f"Headless scraper stderr: {stderr_str}")

        # Détecter si login requis : exit_code == 2 ou "Authentification requise" dans la sortie
        needs_login = (exit_code == 2) or ("Authentification requise" in stderr_str) or ("signin" in stdout_str)

        if needs_login:
            billing_sync_state["status"] = "needs_login"
            billing_sync_state["message"] = "Connexion requise. Veuillez vous connecter dans la fenêtre Google Chrome qui vient de s'ouvrir."

            node_cmd_headed = ["node", "tools/billing_scraper.js", "--headless=false"]
            proc_headed = await asyncio.create_subprocess_exec(
                *node_cmd_headed,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )

            stdout_headed, stderr_headed = await proc_headed.communicate()
            exit_code_headed = proc_headed.returncode

            stdout_str_headed = stdout_headed.decode("utf-8", errors="ignore").strip()
            stderr_str_headed = stderr_headed.decode("utf-8", errors="ignore").strip()

            logger.info(f"Headed scraper exit code: {exit_code_headed}")

            if exit_code_headed == 0:
                await handle_scraper_success(stdout_str_headed)
            else:
                billing_sync_state["status"] = "error"
                billing_sync_state["message"] = f"Échec de l'authentification : {stderr_str_headed}"
        elif exit_code == 0:
            await handle_scraper_success(stdout_str)
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = f"Erreur de facturation : {stderr_str}"

    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation de facturation: {e}")
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = str(e)











# Routes Google Workspace extraites vers api/routes/google_workspace.py
# (Calendar, Drive, Gmail, Sheets, Tasks, YouTube, Contacts — 13 routes)




# ─── [P6] Route API Imagen 4 (Génération d'images) ────────────────────

class ImagenRequestBody(BaseModel):
    prompt: str
    model_variant: str = "fast"
    aspect_ratio: str = "1:1"
    save_to_disk: bool = False
    output_path: str = ""




# ─── [P8] Route API TTS Gemini ─────────────────────────────────────────

class TTSRequestBody(BaseModel):
    text: str
    voice_name: str = "Kore"
    output_path: str = ""



# ─── [Phase 1] Route API KeyPool (monitoring rotation clés) ────────────

# Routes Workspace (Gmail, Sheets, Tasks, YouTube, Contacts) extraites
# vers api/routes/google_workspace.py



# ─── [Phase 3] Routes API Cloud TTS v1, Translation, Vision ───────────





# ─── [P10] Routes API Batch Gemini ─────────────────────────────────────

class BatchRequestBody(BaseModel):
    requests: list  # Liste de {"prompt": str, "system": str}
    model: str = "gemini-3.5-flash"
    temperature: float = 0.2
    max_output_tokens: int = 2048










# ─── [P11] Route API AQA (Question-Answering avec citations) ──────────

class AQARequestBody(BaseModel):
    question: str
    passages: list = []  # Si vide, utilise le RAGEngine automatiquement
    model: str = "gemini-3.5-flash"
    language: str = "fr"
    top_n: int = 5








# Cache 60s pour éviter de re-scraper à chaque rafraîchissement IHM
_claude_realtime_cache: dict = {"data": None, "ts": 0.0}
_CLAUDE_CACHE_TTL = 60.0  # secondes





















# ──────────────────────────────────────────────────────────────────
# Endpoints des conversations IDE
# ──────────────────────────────────────────────────────────────────




# ─── Flag d'arrêt demandé par l'utilisateur ───
_stop_requested = False


# ─── B3 — Endpoints Sandbox (pending writes preview) ───



# ─── D4 — WebSocket Bidirectionnel ───
ws_clients: list = []


async def broadcast_ws(event_type: str, data: dict):
    """Broadcast un message à tous les clients WebSocket connectés."""
    message = json.dumps({"type": event_type, "data": data})
    disconnected = []
    for ws in ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        ws_clients.remove(ws)

# ─── ContextLoader : état du chargement des fichiers de contexte 3-Layers ───
from memory.context_loader import ContextLoader

# Instance globale du ContextLoader (chargée une seule fois au démarrage)
_context_loader = ContextLoader()
_context_loader.load_all()







# Routes Workflows extraites vers api/routes/workflows.py
# (7 routes CRUD + variables WORKFLOWS_FILE/DIR + init Default.json)













# ─── Scan CLI automatique au démarrage + tâche périodique ────
def _startup_cli_scan():
    """Scan initial des tokens CLI et persistance en BDD au démarrage du serveur."""
    try:
        logger.info("[STARTUP] Lancement du scan CLI initial (toutes conversations)...")
        result = collect_all_cli_tokens(since_date=None, persist_to_db=True)
        _cli_cache["data"] = result
        _cli_cache["timestamp"] = result.get("scan_timestamp")
        logger.info(
            f"[STARTUP] Scan CLI terminé : {result['total_sessions']} sessions, "
            f"{result['total_cli_tokens']:,} tokens, "
            f"{result.get('persisted_count', 0)} persistées en BDD"
        )
    except Exception as e:
        logger.warning(f"[STARTUP] Erreur scan CLI initial : {e}")


async def _periodic_cli_scan():
    """Tâche périodique (toutes les 10 min) pour scanner les nouvelles conversations CLI."""
    await asyncio.sleep(60)  # Attendre 1 min avant le premier rescan (laisser le startup finir)
    while True:
        try:
            logger.info("[PERIODIC] Rescan CLI (conversations modifiées depuis 6h)...")
            from datetime import datetime, timedelta
            since = (datetime.now() - timedelta(hours=6)).isoformat()
            result = collect_all_cli_tokens(since_date=since, persist_to_db=True)
            _cli_cache["data"] = result
            _cli_cache["timestamp"] = result.get("scan_timestamp")
            logger.info(
                f"[PERIODIC] Rescan CLI terminé : {result['total_sessions']} sessions récentes, "
                f"{result.get('persisted_count', 0)} mises à jour en BDD"
            )
        except Exception as e:
            logger.warning(f"[PERIODIC] Erreur rescan CLI : {e}")
        await asyncio.sleep(600)  # 10 minutes


# L'ancien on_startup() a été fusionné dans lifespan() (ligne ~44).
# Ce bloc est intentionnellement vide — NE PAS supprimer ce commentaire.
# Le scan CLI initial et _periodic_cli_scan() sont désormais dans lifespan().

# Montage automatique du répertoire static pour l'IHM Web
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

# Middleware anti-cache pour le développement (force le rechargement des JS/CSS modifiés)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Désactive le cache navigateur pour les fichiers statiques JS/CSS en développement."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith(('.js', '.css', '.html')):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheStaticMiddleware)

# ──────────────────────────────────────────────────────────────────
# Endpoints d'historique des sessions
# ──────────────────────────────────────────────────────────────────




# ──────────────────────────────────────────────────────────────────
# Endpoint de streaming token-par-token
# ──────────────────────────────────────────────────────────────────




# ──────────────────────────────────────────────────────────────────
# Endpoint public de version (T82) — pas d'auth requise
# ──────────────────────────────────────────────────────────────────

@app.get("/version", tags=["Configuration"])
async def get_version():
    """Retourne la version du moteur, le hash git et la date de build."""
    import sys as _sys

    from core import __version__
    git_hash = "unknown"
    build_date = "unknown"
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--short", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            git_hash = out.decode().strip()
        proc2 = await asyncio.create_subprocess_exec(
            "git", "show", "-s", "--format=%cI", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        out2, _ = await proc2.communicate()
        if proc2.returncode == 0:
            build_date = out2.decode().strip()
    except Exception:
        pass
    return {
        "version": __version__,
        "git_hash": git_hash,
        "build_date": build_date,
        "engine": "moteur_agents",
        "python": _sys.version.split()[0],
    }

from fastapi import WebSocket, WebSocketDisconnect


# ──────────────────────────────────────────────────────────────────
# T105 - Endpoint de tuning dynamique (WebSocket)
# ──────────────────────────────────────────────────────────────────
@app.websocket("/ws/tuning")
async def websocket_tuning(websocket: WebSocket, token: str = Depends(require_websocket_auth)):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if "timeout" in data:
                os.environ["ENGINE_TIMEOUT"] = str(data["timeout"])
                logger.info(f"[TUNING] Timeout mis à jour : {data['timeout']}s")
            if "concurrency" in data:
                os.environ["ENGINE_CONCURRENCY"] = str(data["concurrency"])
                logger.info(f"[TUNING] Concurrence mise à jour : {data['concurrency']}")
            await websocket.send_json({
                "status": "updated",
                "config": {
                    "timeout": os.environ.get("ENGINE_TIMEOUT", "120"),
                    "concurrency": os.environ.get("ENGINE_CONCURRENCY", "4")
                }
            })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[TUNING] Erreur WebSocket: {e}")

# ──────────────────────────────────────────────────────────────────
# T107 - Endpoint d'État Moteur Live (WebSocket)
# ──────────────────────────────────────────────────────────────────
_live_engine_clients = set()
@app.websocket("/ws/engine_state")
async def websocket_engine_state(websocket: WebSocket, token: str = Depends(require_websocket_auth)):
    await websocket.accept()
    _live_engine_clients.add(websocket)
    try:
        await websocket.send_json({"type": "init", "message": "Connecté au flux d'état moteur."})
        while True:
            await websocket.receive_text() # keep-alive ping
    except WebSocketDisconnect:
        _live_engine_clients.discard(websocket)
    except Exception:
        _live_engine_clients.discard(websocket)

async def broadcast_engine_state(event_type: str, data: dict):
    # Diffuse les erreurs fatales en live à l'HMI
    dead_clients = set()
    for client in _live_engine_clients:
        try:
            await client.send_json({"type": event_type, "data": data})
        except Exception:
            dead_clients.add(client)
    for c in dead_clients:
        _live_engine_clients.discard(c)

# ──────────────────────────────────────────────────────────────────
# T106 - Feedback des APIs & OAuth
# ──────────────────────────────────────────────────────────────────
@app.get("/api/system/tokens-status", tags=["Configuration"], dependencies=_AUTH_DEP)
async def get_tokens_status():
    """Remonte le statut des tokens (GCP, API météo, HA) pour l'IHM."""
    return {
        "ha_token": {"status": "ok" if os.environ.get("HASS_TOKEN") else "missing"},
        "gcp_key": {"status": "ok" if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else "missing"},
        "weather_api": {"status": "ok" if os.environ.get("WEATHER_API_KEY") else "missing"},
        "mcp_bridge": {"status": "active"}
    }

# [HMI v2] IHM v2 par défaut sur la racine (/). L'ancienne IHM est servie sous /v1
ihm_v2_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ihm-v2", "dist")
if os.path.isdir(ihm_v2_dist):
    app.mount("/v1", StaticFiles(directory=static_dir, html=True), name="static_v1")
    app.mount("/", StaticFiles(directory=ihm_v2_dist, html=True), name="ihm_v2")
    logging.getLogger("gui_server").info(f"[HMI v2] Nouvelle IHM v2 servie sous / (build : {ihm_v2_dist}). Ancienne v1 sous /v1")
else:
    # Fallback si l'IHM v2 n'a pas été buildée
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    logging.getLogger("gui_server").warning("[HMI v2] Build ihm-v2/dist introuvable. Fallback sur l'IHM v1 (static/)")


if __name__ == "__main__":
    import argparse

    import uvicorn

    # --host/--port étaient silencieusement IGNORÉS (port 8000 codé en dur) alors
    # que la commande de prod documentée les passe : parsés réellement désormais.
    # Priorité : argument CLI > variable d'env PORT > 8000.
    # Écoute sur 0.0.0.0 pour permettre l'accès depuis le réseau local (Steam Deck, Freebox, etc.)
    parser = argparse.ArgumentParser(description="Serveur HMI tab5-engine")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--reload", action="store_true",
                        help="Hot-reload sélectif (filtres anti-boucle sur cache/DB)")
    args = parser.parse_args()

    if args.reload:
        logger.info("[STARTUP] Lancement d'Uvicorn en mode Hot-Reload sélectif.")
        uvicorn.run(
            "gui_server:app",
            host=args.host,
            port=args.port,
            reload=True,
            reload_dirs=["core", "agents", "tools", "api", "memory"],
            reload_excludes=["*.json", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "static/*", "moteur.log"]
        )
    else:
        logger.info(f"[STARTUP] Lancement d'Uvicorn en mode standard sur {args.host}:{args.port}.")
        uvicorn.run("gui_server:app", host=args.host, port=args.port, reload=False)
