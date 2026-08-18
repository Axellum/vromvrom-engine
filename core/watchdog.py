"""
core/watchdog.py — Watchdog MQTT 24/7 pour la domotique.

Architecture 3 niveaux :
  - Niveau 1 : classification regex locale (coût zéro)
  - Niveau 2 : escalade LLM via /v1/chat/completions
  - Niveau 3 : notification Tab5 via tab5_pusher

Démarrage dans lifespan() de gui_server.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("watchdog")


def _client_id_par_defaut() -> str:
    """
    [#T322] Identifiant MQTT unique par hôte : « watchdog-domus-<machine> ».

    `MQTT_CLIENT_ID` reste prioritaire pour forcer une valeur précise. Le nom de
    machine est nettoyé (caractères sûrs seulement) et l'ensemble est tronqué à
    23 caractères — la limite du client_id en MQTT 3.1, que certains brokers
    appliquent encore.
    """
    force = os.getenv("MQTT_CLIENT_ID")
    if force:
        return force
    try:
        machine = socket.gethostname() or "inconnu"
    except Exception:
        machine = "inconnu"
    machine = re.sub(r"[^A-Za-z0-9-]", "-", machine).strip("-").lower() or "inconnu"
    return f"wd-domus-{machine}"[:23]

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
SEVERITY_COLORS = {"info": "blue", "warning": "yellow", "error": "orange", "critical": "red"}


def _build_rules() -> list[tuple[re.Pattern, re.Pattern, str]]:
    return [
        (re.compile(r"^homeassistant/status$"),             re.compile(r"offline", re.I), "critical"),
        (re.compile(r"^esphome/[^/]+/status$"),             re.compile(r"offline", re.I), "warning"),
        (re.compile(r"^homeassistant/[^/]+/availability$"), re.compile(r"offline", re.I), "warning"),
        (re.compile(r"^zigbee2mqtt/bridge/state$"),         re.compile(r"offline", re.I), "error"),
    ]


@dataclass
class WatchdogConfig:
    mqtt_host: str = "192.168.1.10"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    # [#T322] Identifiant MQTT UNIQUE PAR HÔTE. MQTT impose l'unicité du
    # client_id : quand deux clients partagent le même, le broker éjecte
    # l'ancien à chaque connexion du nouveau — qui se reconnecte aussitôt, et
    # ainsi de suite. Avec la valeur figée « watchdog-domus », le moteur de dev
    # (poste Windows) et la prod (Deck) se sont battus **15 209 fois en une
    # journée** (11/08), soit ~869 déconnexions par heure : le watchdog
    # domotique 24/7 était de fait inopérant dès qu'une seconde instance
    # tournait. Le conflit n'est apparu que le 11/08 parce qu'il a fallu
    # attendre la création du login `moteur_watchdog` (#T236, le 10/08) pour
    # que les DEUX instances puissent enfin s'authentifier et donc se disputer
    # l'identifiant.
    mqtt_client_id: str = field(default_factory=lambda: _client_id_par_defaut())
    moteur_url: str = "http://localhost:8000"
    moteur_model: str = "auto"
    anti_spam_seconds: float = 300.0
    backoff_initial: float = 1.0
    backoff_max: float = 60.0
    backoff_factor: float = 2.0
    connect_timeout: float = 15.0  # délai max d'attente du CONNACK avant retry
    topics: tuple[str, ...] = (
        "homeassistant/status",
        "esphome/+/status",
        "homeassistant/+/availability",
        "zigbee2mqtt/bridge/state",
    )
    escalate_min_severity: str = "warning"
    http_timeout: float = 10.0
    extra_sinks: list[Callable[[str, str, str], Any]] = field(default_factory=list)
    ha_log_path: str = r"\\192.168.1.10\config\home-assistant.log"
    log_poll_interval: float = 14400.0  # 4 heures par défaut


class WatchdogDaemon:
    """
    Vigile MQTT 24/7.
    Boucle : MQTT → classification regex → anti-spam → escalade LLM → push Tab5.
    """

    def __init__(self, config: WatchdogConfig | None = None):
        self.config = config or WatchdogConfig()
        self._rules = _build_rules()
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mqtt_client = None
        self._connected = asyncio.Event()
        self._stopping = asyncio.Event()
        self._last_alert: dict[str, float] = {}
        self._http = None
        self._consumer_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._log_poller_task: asyncio.Task | None = None
        self._stats: dict[str, int] = {
            "messages_received": 0,
            "alerts_emitted": 0,
            "alerts_escalated": 0,
            "alerts_spam_filtered": 0,
            "reconnects": 0,
        }

    async def start(self) -> None:
        if self._consumer_task is not None:
            return
        self._stopping.clear()
        self._loop = asyncio.get_running_loop()
        try:
            import aiohttp
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.http_timeout)
            )
        except ImportError:
            logger.warning("[WATCHDOG] aiohttp absent — escalade LLM désactivée.")

        self._supervisor_task = asyncio.create_task(self._supervisor(), name="watchdog-supervisor")
        self._consumer_task = asyncio.create_task(self._consumer(), name="watchdog-consumer")
        self._log_poller_task = asyncio.create_task(self._log_poller(), name="watchdog-log-poller")
        logger.info("[WATCHDOG] Démarré → MQTT %s:%d", self.config.mqtt_host, self.config.mqtt_port)

    async def stop(self) -> None:
        self._stopping.set()
        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
                self._mqtt_client.loop_stop()
            except Exception:
                pass
            self._mqtt_client = None

        for task in (self._supervisor_task, self._consumer_task, self._log_poller_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if self._http:
            await self._http.close()
            self._http = None

        logger.info("[WATCHDOG] Arrêté. Stats: %s", self._stats)

    def get_stats(self) -> dict:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Logique de classification et filtrage
    # ------------------------------------------------------------------

    def _classify_severity(self, topic: str, payload: str) -> str:
        for topic_re, payload_re, severity in self._rules:
            if topic_re.match(topic) and payload_re.search(payload):
                return severity
        return "info"

    def _is_spam(self, topic: str, severity: str) -> bool:
        key = f"{topic}|{severity}"
        now = time.time()
        if now - self._last_alert.get(key, 0.0) < self.config.anti_spam_seconds:
            return True
        self._last_alert[key] = now
        return False

    # ------------------------------------------------------------------
    # Escalade et notification
    # ------------------------------------------------------------------

    async def _escalate(self, topic: str, payload: str, severity: str) -> None:
        if not self._http:
            return
        body = {
            "model": self.config.moteur_model,
            "messages": [{
                "role": "user",
                "content": (
                    f"Alerte domotique détectée.\n"
                    f"Topic MQTT: {topic}\nPayload: {payload}\nSévérité: {severity}\n"
                    f"Analyse et propose une action corrective en 2-3 phrases."
                ),
            }],
            "max_tokens": 256,
        }
        try:
            url = f"{self.config.moteur_url}/v1/chat/completions"
            async with self._http.post(url, json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    analysis = data["choices"][0]["message"]["content"]
                    logger.info("[WATCHDOG] Analyse LLM: %s", analysis)
                    await self._notify_tab5(
                        title=f"ALERTE {severity.upper()}: {topic.split('/')[-1]}",
                        body=analysis[:120],
                        color=SEVERITY_COLORS.get(severity, "orange"),
                    )
                    self._stats["alerts_escalated"] += 1
        except Exception as exc:
            logger.warning("[WATCHDOG] Escalade échouée: %s", exc)

    async def _notify_tab5(self, title: str, body: str, color: str) -> None:
        try:
            from core.tab5_pusher import push_notification
            await push_notification(title=title, body=body, color=color)
        except Exception as exc:
            logger.debug("[WATCHDOG] Tab5 push ignoré: %s", exc)

    async def _handle(self, topic: str, payload: str) -> None:
        severity = self._classify_severity(topic, payload)
        if severity == "info":
            return
        self._stats["alerts_emitted"] += 1
        if self._is_spam(topic, severity):
            self._stats["alerts_spam_filtered"] += 1
            return
        if SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(self.config.escalate_min_severity, 1):
            await self._escalate(topic, payload, severity)

    # ------------------------------------------------------------------
    # Tâches asyncio internes
    # ------------------------------------------------------------------

    async def _consumer(self) -> None:
        while not self._stopping.is_set():
            try:
                topic, payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            self._stats["messages_received"] += 1
            try:
                await self._handle(topic, payload)
            except Exception:
                logger.exception("[WATCHDOG] Erreur traitement topic=%s", topic)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = "<binaire>"
        userdata["loop"].call_soon_threadsafe(
            userdata["queue"].put_nowait, (msg.topic, payload)
        )

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        """Callback de connexion (paho API v2, appelé depuis le thread réseau).

        Souscrit aux topics PUIS débloque le superviseur. Les deux actions doivent
        être séquentielles : les combiner en une expression `or` faisait sauter le
        `set()` (une liste de souscriptions non vide est toujours vraie).
        """
        if client is not self._mqtt_client:
            # CONNACK tardif d'une tentative déjà abandonnée (délai de connexion
            # expiré) : l'honorer poserait `_connected` alors que le client courant,
            # lui, n'est pas connecté — le superviseur repartirait dans sa boucle
            # interne sans MQTT, exactement le blocage que ce module corrige.
            logger.debug("[WATCHDOG] CONNACK ignoré : provient d'une tentative abandonnée.")
            return
        if reason_code.is_failure:
            logger.error("[WATCHDOG] Connexion MQTT refusée par le broker : %s", reason_code)
            return
        for topic in self.config.topics:
            client.subscribe(topic, qos=1)
        self._loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None) -> None:
        """Callback de déconnexion (paho API v2) : réveille le superviseur pour le backoff."""
        if client is not self._mqtt_client:
            return  # déconnexion d'un client abandonné : sans effet sur le courant
        self._loop.call_soon_threadsafe(self._connected.clear)

    async def _supervisor(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("[WATCHDOG] paho-mqtt absent — watchdog MQTT désactivé.")
            return

        backoff = self.config.backoff_initial
        while not self._stopping.is_set():
            try:
                # Repartir d'un état « déconnecté » à chaque tentative. Sans ce
                # clear, un CONNACK arrivé juste après l'expiration du délai
                # laissait `_connected` posé : le `wait_for` de la tentative
                # suivante rendait la main immédiatement et le superviseur se
                # croyait connecté alors que son client ne l'était pas.
                self._connected.clear()

                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.config.mqtt_client_id,
                    clean_session=True,
                )
                if self.config.mqtt_username:
                    client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)

                client.user_data_set({"loop": self._loop, "queue": self._queue})
                client.on_message = self._on_message
                client.on_connect = self._on_connect
                client.on_disconnect = self._on_disconnect

                # Publier le client AVANT de lancer la boucle réseau : les callbacks
                # comparent leur `client` à celui-ci pour écarter les tentatives
                # abandonnées, et un CONNACK très rapide ne doit pas être pris pour
                # un retardataire.
                self._mqtt_client = client
                client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
                client.loop_start()

                # Un CONNACK refusé (identifiants invalides) ne passe jamais par
                # _connected : borner l'attente évite un blocage définitif ici.
                try:
                    await asyncio.wait_for(
                        self._connected.wait(), timeout=self.config.connect_timeout
                    )
                except TimeoutError:
                    # Abandonner ce client : le retirer d'abord de `_mqtt_client`,
                    # pour qu'un CONNACK arrivant pendant l'arrêt soit écarté par
                    # les callbacks au lieu de poser `_connected` pour rien.
                    self._mqtt_client = None
                    try:
                        client.disconnect()
                    except Exception as exc:  # noqa: BLE001 — client jamais connecté
                        logger.debug("[WATCHDOG] disconnect() du client abandonné : %s", exc)
                    client.loop_stop()
                    logger.warning(
                        "[WATCHDOG] Aucun CONNACK en %.0fs, retry dans %.1fs",
                        self.config.connect_timeout, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * self.config.backoff_factor, self.config.backoff_max)
                    continue

                backoff = self.config.backoff_initial

                while not self._stopping.is_set() and self._connected.is_set():
                    await asyncio.sleep(0.5)

                if self._stopping.is_set():
                    break

                client.loop_stop()
                self._stats["reconnects"] += 1
                logger.warning("[WATCHDOG] MQTT déconnecté, retry dans %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * self.config.backoff_factor, self.config.backoff_max)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[WATCHDOG] Échec MQTT: %s, retry dans %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * self.config.backoff_factor, self.config.backoff_max)

    async def _log_poller(self) -> None:
        import os
        while not self._stopping.is_set():
            try:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.config.log_poll_interval)
                    break
                except TimeoutError:
                    pass

                if os.path.exists(self.config.ha_log_path):
                    def read_tail():
                        try:
                            with open(self.config.ha_log_path, encoding='utf-8', errors='ignore') as f:
                                f.seek(0, 2)
                                size = f.tell()
                                f.seek(max(0, size - 500000))
                                return f.readlines()
                        except Exception as e:
                            return [f"Erreur lecture: {e}"]

                    lines = await asyncio.to_thread(read_tail)
                    error_lines = [l for l in lines if "ERROR" in l or "Traceback" in l or "Bootloop" in l]

                    if error_lines:
                        payload = "".join(error_lines[-5:])[:500]
                        self._loop.call_soon_threadsafe(
                            self._queue.put_nowait, ("homeassistant/status", payload)
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[WATCHDOG] Erreur log_poller: %s", exc)
                await asyncio.sleep(60)


def create_watchdog_daemon(config_dict: dict) -> WatchdogDaemon:
    """Factory : crée un WatchdogDaemon depuis un dict de configuration."""
    cfg = WatchdogConfig(
        mqtt_host=config_dict.get("mqtt_host", "192.168.1.10"),
        mqtt_port=int(config_dict.get("mqtt_port", 1883)),
        mqtt_username=config_dict.get("mqtt_username"),
        mqtt_password=config_dict.get("mqtt_password"),
        moteur_url=config_dict.get("moteur_url", "http://localhost:8000"),
        moteur_model=config_dict.get("moteur_model", "auto"),
    )
    return WatchdogDaemon(cfg)
