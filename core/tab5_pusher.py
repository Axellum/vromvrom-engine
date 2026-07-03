"""
core/tab5_pusher.py — Push d'alertes et suggestions vers le Tab5 M5Stack via HA.

Communication moteur → Tab5 par entités Home Assistant :
  - input_text.moteur_notification  : popup LVGL court (255 chars max)
  - input_text.moteur_status        : statut moteur (indicator LED LVGL)
  - input_boolean.moteur_alert_active : alerte critique → LED rouge clignotante
  - input_text.dreamer_suggestions  : rapport anomalies DreamerAgent (onglet Console)

Prérequis HA :
  - Créer les entités dans configuration.yaml :
      input_text:
        moteur_notification: {max: 255}
        moteur_status:       {max: 100}
        dreamer_suggestions: {max: 255}
      input_boolean:
        moteur_alert_active: {}
  - Variable d'environnement : HA_TOKEN=<token_longue_duree>

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Configuration ──
DEFAULT_HA_URL = "http://${HA_HOST:-192.168.1.x}:8123"
DEFAULT_TIMEOUT = 3  # secondes

# ── Entités HA cibles ──
ENTITY_NOTIFICATION = "input_text.moteur_notification"
ENTITY_STATUS       = "input_text.moteur_status"
ENTITY_ALERT        = "input_boolean.moteur_alert_active"
ENTITY_DREAMER      = "input_text.dreamer_suggestions"


class Tab5Pusher:
    """
    Envoie des données vers le Tab5 via l'API REST de Home Assistant.

    Chaque méthode est idempotente et fault-tolerant :
    si HA est injoignable → log warning + retour False (jamais d'exception).
    """

    def __init__(
        self,
        ha_url: Optional[str] = None,
        ha_token: Optional[str] = None,
    ):
        """
        Args:
            ha_url:   URL HA (défaut : http://${HA_HOST:-192.168.1.x}:8123)
            ha_token: Token Bearer HA (défaut : env HA_TOKEN)
        """
        self.ha_url   = ha_url   or os.environ.get("HA_URL", DEFAULT_HA_URL)
        self.ha_token = ha_token or os.environ.get("HA_TOKEN", "")
        self._session: Optional[aiohttp.ClientSession] = None

        if not self.ha_token:
            logger.warning("[TAB5 PUSHER] HA_TOKEN manquant — les push échoueront silencieusement")

    # ──────────────────────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        """Headers Bearer pour l'API HA."""
        return {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type":  "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Retourne (ou crée) la session aiohttp partagée."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _post_state(self, entity_id: str, state: str) -> bool:
        """
        POST /api/states/{entity_id} avec {state: state}.

        Returns:
            True si HTTP 200/201, False sinon
        """
        url = f"{self.ha_url}/api/states/{entity_id}"
        try:
            session = await self._get_session()
            async with session.post(
                url, json={"state": state}, headers=self._headers()
            ) as resp:
                if resp.status in (200, 201):
                    logger.debug(
                        f"[TAB5 PUSHER] ✅ {entity_id} = \"{state[:60]}\""
                    )
                    return True
                else:
                    body = await resp.text()
                    logger.warning(
                        f"[TAB5 PUSHER] ❌ {entity_id} HTTP {resp.status} : {body[:100]}"
                    )
                    return False
        except asyncio.TimeoutError:
            logger.warning(f"[TAB5 PUSHER] ⏱️ Timeout 3s pour {entity_id}")
            return False
        except aiohttp.ClientError as e:
            logger.warning(f"[TAB5 PUSHER] 🔌 HA injoignable ({entity_id}) : {e}")
            return False

    # ──────────────────────────────────────────────────────────────
    # API publique
    # ──────────────────────────────────────────────────────────────

    async def push_notification(self, message: str, severity: str = "info") -> bool:
        """
        Envoie une notification courte vers le popup LVGL du Tab5.

        Args:
            message:  Texte (tronqué à 255 chars)
            severity: 'info' | 'warning' | 'critical'
                      → 'critical' active l'alerte LED rouge en plus

        Returns:
            True si succès
        """
        truncated = message[:255]
        logger.info(f"[TAB5 PUSHER] Push [{severity}] : {truncated[:80]}")

        ok = await self._post_state(ENTITY_NOTIFICATION, truncated)

        # Alerte critique → activer input_boolean
        if severity == "critical":
            alert_ok = await self._post_state(ENTITY_ALERT, "on")
            ok = ok and alert_ok

        return ok

    async def push_status(self, status: str) -> bool:
        """
        Envoie le statut du moteur (ex: 'running', 'idle', 'error').

        Args:
            status: Tronqué à 100 chars

        Returns:
            True si succès
        """
        logger.info(f"[TAB5 PUSHER] Push status : {status}")
        return await self._post_state(ENTITY_STATUS, status[:100])

    async def push_dreamer_report(self, report: str) -> bool:
        """
        Envoie le rapport DreamerAgent vers l'onglet Console du Tab5.

        Args:
            report: Rapport texte (tronqué à 255 chars)

        Returns:
            True si succès
        """
        truncated = report[:255]
        logger.info(f"[TAB5 PUSHER] Push Dreamer report : {truncated[:60]}...")
        return await self._post_state(ENTITY_DREAMER, truncated)

    async def push_anomaly_report(self, anomalies: List[Dict]) -> bool:
        """
        Formate et pousse un rapport d'anomalies domotiques.

        Args:
            anomalies: Liste de {entity, severity, ...}

        Returns:
            True si notification + dreamer_report réussis
        """
        if not anomalies:
            report = "RAS — Aucune anomalie détectée"
        else:
            details = ", ".join(
                f"{a.get('entity_id', a.get('entity', '?'))} "
                f"({a.get('severity', 'info').upper()})"
                for a in anomalies[:10]  # Limiter pour tenir en 255 chars
            )
            report = f"{len(anomalies)} anomalie(s) : {details}"

        logger.info(f"[TAB5 PUSHER] Push anomalies ({len(anomalies)}) : {report[:80]}")

        sev      = "critical" if any(a.get("severity") == "high" for a in anomalies) else "warning"
        notif_ok = await self.push_notification(report, severity=sev)
        dream_ok = await self.push_dreamer_report(report)

        return notif_ok and dream_ok

    async def clear_alert(self) -> bool:
        """Désactive l'alerte visuelle (LED rouge) sur le Tab5."""
        logger.info("[TAB5 PUSHER] Effacement alerte critique")
        return await self._post_state(ENTITY_ALERT, "off")

    async def close(self) -> None:
        """Ferme la session aiohttp proprement."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("[TAB5 PUSHER] Session fermée")


# ──────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────

_tab5_instance: Optional[Tab5Pusher] = None


def get_tab5_pusher() -> Tab5Pusher:
    """Retourne le singleton Tab5Pusher (chargement lazy)."""
    global _tab5_instance
    if _tab5_instance is None:
        _tab5_instance = Tab5Pusher()
    return _tab5_instance
