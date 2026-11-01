"""
core/vigie/notification.py — Envoi vers le téléphone (`notify.<service>`) et vers HA.

Téléphone : service `notify.<MOTEUR_VIGIE_NOTIFY_SERVICE>` de Home Assistant
(application compagnon Android). Clés de `data` vérifiées le 07/10/2026 dans
la documentation compagnon :
  - `tag` : une notification du même tag REMPLACE la précédente
    https://companion.home-assistant.io/docs/notifications/notifications-basic/#replacing
  - `group` : regroupe visuellement les notifications
    https://companion.home-assistant.io/docs/notifications/notifications-basic/#grouping
  - `clickAction` (Android ; `url` sur iOS) : URL ouverte au toucher, URL
    externe complète admise
    https://companion.home-assistant.io/docs/notifications/notifications-basic/#opening-a-url
  - `priority: high` + `ttl: 0` : livraison immédiate même téléphone en veille
    (sinon une notification peut attendre que l'écran se rallume)
    https://companion.home-assistant.io/docs/notifications/critical-notifications/
  - limite documentée : 500 notifications poussées par jour et par appareil
    https://companion.home-assistant.io/docs/notifications/notification-details/
    (d'où le regroupement au-delà de 3 alertes et le silence de nuit).

Notification persistante HA : canal existant `core.daemon_loop._send_ha_notification`.

En passage à blanc, rien ne part : chaque envoi est consigné dans `journal`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from core.vigie.modeles import nettoyer_texte

logger = logging.getLogger(__name__)

GROUPE = "vigie"


def _base_ha(url: str) -> str:
    url = (url or "").rstrip("/")
    return url[:-4] if url.endswith("/api") else url


async def _post_ha_defaut(chemin: str, payload: dict) -> int:
    """POST vers l'API REST de HA ; rend le code HTTP (0 si non configuré ou injoignable)."""
    import aiohttp

    from core.ha_tls import ha_ssl_context
    from core.ha_token import get_ha_token
    from core.ha_url import get_ha_url

    jeton, url = get_ha_token(), _base_ha(get_ha_url())
    if not jeton or not url:
        logger.warning("[VIGIE] HASS_URL / HASS_TOKEN absents : notification impossible.")
        return 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{url}{chemin}",
                headers={"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=ha_ssl_context(),
            ) as reponse:
                return reponse.status
    except Exception as exc:
        # Le type seulement : le message pourrait citer l'adresse.
        logger.warning("[VIGIE] Home Assistant injoignable (%s).", type(exc).__name__)
        return 0


async def _persistante_defaut(titre: str, message: str, notification_id: str) -> bool:
    from core.daemon_loop import _send_ha_notification

    return bool(await _send_ha_notification(titre, message, notification_id=notification_id))


class Notifieur:
    def __init__(
        self,
        service: str,
        *,
        a_blanc: bool = False,
        post: Callable[[str, dict], Awaitable[int]] | None = None,
        persistante: Callable[[str, str, str], Awaitable[bool]] | None = None,
    ):
        self.service = service
        self.a_blanc = a_blanc
        self._post = post or _post_ha_defaut
        self._persistante = persistante or _persistante_defaut
        self.journal: list[dict] = []   # ce qui est (ou serait) parti

    async def telephone(self, titre: str, message: str, *, tag: str,
                        lien: str | None = None, urgent: bool = False) -> bool:
        data: dict = {"tag": tag, "group": GROUPE}
        if lien:
            data["clickAction"] = lien
        if urgent:
            data["priority"] = "high"
            data["ttl"] = 0
        payload = {
            "title": nettoyer_texte(titre, 120),
            # Le message garde ses retours à la ligne (liste du résumé) : seul le titre est mis à plat.
            "message": (message or "")[:1500],
            "data": data,
        }
        self.journal.append({"canal": "telephone", **payload})
        if self.a_blanc:
            return True
        if not self.service:
            logger.error("[VIGIE] MOTEUR_VIGIE_NOTIFY_SERVICE non réglé : alerte non envoyée.")
            return False
        statut = await self._post(f"/api/services/notify/{self.service}", payload)
        if statut not in (200, 201):
            logger.warning("[VIGIE] notify.%s a répondu HTTP %s.", self.service, statut)
            return False
        return True

    async def persistante(self, titre: str, message: str, notification_id: str) -> bool:
        self.journal.append({"canal": "persistante", "title": titre, "message": message,
                             "notification_id": notification_id})
        if self.a_blanc:
            return True
        return await self._persistante(titre, message, notification_id)
