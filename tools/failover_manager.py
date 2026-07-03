"""
tools/failover_manager.py — Antigravity Phase 4 : Gestionnaire de failover Deck (côté PC)
══════════════════════════════════════════════════════════════════════════════════════════

Classe asyncio singleton qui subscribe au topic MQTT 'antigravity/deck/failover'
et maintient l'état de disponibilité du Steam Deck.

Intégration dans le Moteur :
    from tools.failover_manager import FailoverManager
    failover_mgr = FailoverManager()
    await failover_mgr.start()
    # Dans app_state ou le runtime :
    status = await failover_mgr.get_deck_status()
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise ImportError("paho-mqtt requis : pip install paho-mqtt")

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
MQTT_TOPIC_FAILOVER = "antigravity/deck/failover"
MQTT_TOPIC_HEARTBEAT = "antigravity/deck/heartbeat"


class FailoverManager:
    """
    Gestionnaire de failover asyncio pour le Steam Deck.

    Écoute le topic MQTT 'antigravity/deck/failover' et maintient un état
    cohérent sur la disponibilité du Deck dans le Moteur.

    Usage :
        mgr = FailoverManager(mqtt_host="${OLLAMA_HOST:-localhost}")
        await mgr.start()
        status = await mgr.get_deck_status()
        await mgr.stop()
    """

    _instance: Optional["FailoverManager"] = None

    def __new__(cls, *args, **kwargs):
        """Pattern singleton — une seule instance par processus."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        mqtt_host: str = "192.168.1.100",  # PC écoute en tant que broker
        mqtt_port: int = 1883,
        client_id: str = "moteur_failover_manager",
    ):
        # Éviter la double initialisation (singleton)
        if hasattr(self, "_initialized") and self._initialized:
            return

        self.mqtt_host    = mqtt_host
        self.mqtt_port    = mqtt_port
        self.client_id    = client_id

        # ── État du Deck ──
        self.deck_failover_active: bool          = False
        self.failover_count:       int           = 0
        self.last_seen:            Optional[str] = None  # ISO timestamp
        self.last_status:          str           = "unknown"

        # ── Internals ──
        self._mqtt_client: Optional[mqtt.Client] = None
        self._connected   = False
        self._started     = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = True

        log.info("[FailoverManager] Initialisé. Broker MQTT : %s:%d", mqtt_host, mqtt_port)

    # ─────────────────────────────────────────────────────────────────────────
    # MQTT callbacks
    # ─────────────────────────────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Callback de connexion MQTT (API v2)."""
        if hasattr(reason_code, "value"):
            rc = reason_code.value
        else:
            rc = reason_code

        if rc == 0:
            self._connected = True
            client.subscribe(MQTT_TOPIC_FAILOVER, qos=1)
            client.subscribe(MQTT_TOPIC_HEARTBEAT, qos=0)
            log.info("[FailoverManager] Connecté au broker MQTT, subscriptions actives.")
        else:
            log.error("[FailoverManager] Échec connexion MQTT (rc=%s)", rc)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code=None, properties=None):
        self._connected = False
        log.warning("[FailoverManager] Déconnecté du broker MQTT.")

    def _on_message(self, client, userdata, message):
        """Traite les messages MQTT reçus du Deck."""
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            status  = payload.get("status", "unknown")
            ts      = payload.get("timestamp")

            log.info("[FailoverManager] Message reçu : topic=%s status=%s",
                     message.topic, status)

            if message.topic == MQTT_TOPIC_FAILOVER:
                self._process_failover_event(status, ts)
            elif message.topic == MQTT_TOPIC_HEARTBEAT:
                self.last_seen = ts or datetime.now(timezone.utc).isoformat()

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.warning("[FailoverManager] Message MQTT invalide : %s", e)

    def _process_failover_event(self, status: str, timestamp: Optional[str]):
        """Met à jour l'état interne selon l'événement reçu."""
        prev_active = self.deck_failover_active

        if status == "ACTIVATED":
            self.deck_failover_active = True
            self.failover_count      += 1
            self.last_seen            = timestamp
            log.warning("[FailoverManager] ⚠️  FAILOVER ACTIVÉ (total: %d)", self.failover_count)

        elif status == "RECOVERED":
            self.deck_failover_active = False
            self.last_seen            = timestamp
            log.info("[FailoverManager] ✅ Deck rétabli — failover désactivé.")

        self.last_status = status

        # Callback optionnel dans la boucle asyncio
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._on_failover_changed(prev_active, self.deck_failover_active),
                self._loop
            )

    async def _on_failover_changed(self, was_active: bool, is_active: bool):
        """Hook asyncio appelé lors d'un changement d'état. Override possible."""
        if was_active != is_active:
            log.info("[FailoverManager] Changement d'état failover : %s → %s",
                     was_active, is_active)

    # ─────────────────────────────────────────────────────────────────────────
    # Cycle de vie
    # ─────────────────────────────────────────────────────────────────────────
    async def start(self):
        """Démarre la connexion MQTT en arrière-plan (thread paho)."""
        if self._started:
            log.debug("[FailoverManager] Déjà démarré.")
            return

        self._loop = asyncio.get_running_loop()

        self._mqtt_client = mqtt.Client(
            client_id=self.client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        self._mqtt_client.on_connect    = self._on_connect
        self._mqtt_client.on_disconnect = self._on_disconnect
        self._mqtt_client.on_message    = self._on_message

        try:
            self._mqtt_client.connect_async(self.mqtt_host, self.mqtt_port, keepalive=60)
            self._mqtt_client.loop_start()
            self._started = True
            log.info("[FailoverManager] Démarré. En attente de messages MQTT...")
        except Exception as e:
            log.error("[FailoverManager] Impossible de démarrer : %s", e)
            raise

    async def stop(self):
        """Arrête proprement la connexion MQTT."""
        if not self._started:
            return
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        self._started = False
        log.info("[FailoverManager] Arrêté.")

    # ─────────────────────────────────────────────────────────────────────────
    # API publique
    # ─────────────────────────────────────────────────────────────────────────
    async def get_deck_status(self) -> dict:
        """
        Retourne un dictionnaire décrivant l'état courant du Deck.

        Returns:
            {
                "failover_active": bool,
                "failover_count": int,
                "last_seen": str | None,     # ISO timestamp
                "last_status": str,
                "mqtt_connected": bool,
            }
        """
        return {
            "failover_active": self.deck_failover_active,
            "failover_count":  self.failover_count,
            "last_seen":       self.last_seen,
            "last_status":     self.last_status,
            "mqtt_connected":  self._connected,
        }

    @classmethod
    def reset_singleton(cls):
        """Utilitaire de test : réinitialise le singleton."""
        cls._instance = None
