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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("watchdog")

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
    mqtt_host: str = "${HA_HOST:-192.168.1.x}"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_client_id: str = "watchdog-domus"
    moteur_url: str = "http://localhost:8000"
    moteur_model: str = "auto"
    anti_spam_seconds: float = 300.0
    backoff_initial: float = 1.0
    backoff_max: float = 60.0
    backoff_factor: float = 2.0
    topics: tuple[str, ...] = (
        "homeassistant/status",
        "esphome/+/status",
        "homeassistant/+/availability",
        "zigbee2mqtt/bridge/state",
    )
    escalate_min_severity: str = "warning"
    http_timeout: float = 10.0
    extra_sinks: list[Callable[[str, str, str], Any]] = field(default_factory=list)
    ha_log_path: str = r"\\${HA_HOST:-192.168.1.x}\config\home-assistant.log"
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
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mqtt_client = None
        self._connected = asyncio.Event()
        self._stopping = asyncio.Event()
        self._last_alert: dict[str, float] = {}
        self._http = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._supervisor_task: Optional[asyncio.Task] = None
        self._log_poller_task: Optional[asyncio.Task] = None
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
            except asyncio.TimeoutError:
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

    async def _supervisor(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("[WATCHDOG] paho-mqtt absent — watchdog MQTT désactivé.")
            return

        backoff = self.config.backoff_initial
        while not self._stopping.is_set():
            try:
                client = mqtt.Client(
                    client_id=self.config.mqtt_client_id,
                    clean_session=True,
                )
                if self.config.mqtt_username:
                    client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)

                client.user_data_set({"loop": self._loop, "queue": self._queue})
                client.on_message = self._on_message
                client.on_connect = lambda c, u, f, rc, p=None: (
                    [c.subscribe(t, qos=1) for t in self.config.topics]
                    or self._loop.call_soon_threadsafe(self._connected.set)
                ) if rc == 0 else None
                client.on_disconnect = lambda c, u, rc, p=None: (
                    self._loop.call_soon_threadsafe(self._connected.clear)
                )

                client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
                client.loop_start()
                self._mqtt_client = client

                await self._connected.wait()
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
                except asyncio.TimeoutError:
                    pass

                if os.path.exists(self.config.ha_log_path):
                    def read_tail():
                        try:
                            with open(self.config.ha_log_path, 'r', encoding='utf-8', errors='ignore') as f:
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
        mqtt_host=config_dict.get("mqtt_host", "${HA_HOST:-192.168.1.x}"),
        mqtt_port=int(config_dict.get("mqtt_port", 1883)),
        mqtt_username=config_dict.get("mqtt_username"),
        mqtt_password=config_dict.get("mqtt_password"),
        moteur_url=config_dict.get("moteur_url", "http://localhost:8000"),
        moteur_model=config_dict.get("moteur_model", "auto"),
    )
    return WatchdogDaemon(cfg)
