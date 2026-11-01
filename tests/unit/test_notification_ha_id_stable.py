"""
tests/unit/test_notification_ha_id_stable.py — un canal récurrent remplace sa
notification HA au lieu d'en empiler une par jour.

Constaté le 02/09/2026 : 7 « Veille catalogue & tarifs » stagnaient dans l'UI
HA, une par jour. `persistent_notification.create` accepte un
`notification_id` : même id = la nouvelle notification remplace l'ancienne.

Vérifié ici :
  - `_send_ha_notification` ne met `notification_id` dans le payload que s'il
    est fourni (les alertes d'incident gardent une entrée par occurrence) ;
  - la veille catalogue et l'alerte quotas passent chacune leur id stable.
Aucun réseau : `aiohttp.ClientSession` et `_send_ha_notification` sont simulés.
"""
from unittest.mock import AsyncMock

import pytest


class _FausseReponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FausseSession:
    """Capture le JSON posté vers HA à la place d'`aiohttp.ClientSession`."""

    def __init__(self, capture):
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self._capture.append({"url": url, "json": kwargs.get("json")})
        return _FausseReponse()


@pytest.fixture
def envois_ha(monkeypatch):
    """URL + jeton HA factices, session HTTP simulée ; renvoie les envois."""
    import aiohttp

    monkeypatch.setenv("HASS_URL", "https://ha.exemple:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    capture = []
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FausseSession(capture))
    return capture


@pytest.mark.asyncio
async def test_payload_porte_l_id_quand_il_est_fourni(envois_ha):
    from core.daemon_loop import _send_ha_notification

    assert await _send_ha_notification("titre", "corps", notification_id="canal_x") is True
    assert envois_ha[0]["url"].endswith("/api/services/persistent_notification/create")
    assert envois_ha[0]["json"] == {
        "title": "titre", "message": "corps", "notification_id": "canal_x",
    }


@pytest.mark.asyncio
async def test_payload_sans_id_par_defaut(envois_ha):
    """Alerte d'incident : pas d'id, chaque occurrence reste visible dans HA."""
    from core.daemon_loop import _send_ha_notification

    assert await _send_ha_notification("titre", "corps") is True
    assert "notification_id" not in envois_ha[0]["json"]


@pytest.mark.asyncio
async def test_veille_catalogue_passe_son_id_stable(monkeypatch):
    envoi = AsyncMock(return_value=True)
    monkeypatch.setattr("core.daemon_loop._send_ha_notification", envoi)
    from core.veille_scheduler import _notifier_ha

    assert await _notifier_ha("rapport du jour") is True
    assert envoi.await_args.kwargs["notification_id"] == "moteur_veille_catalogue"


@pytest.mark.asyncio
async def test_alerte_quotas_passe_son_id_stable(monkeypatch):
    envoi = AsyncMock(return_value=True)
    monkeypatch.setattr("core.daemon_loop._send_ha_notification", envoi)
    monkeypatch.delenv("QUOTA_ALERTE_OBSERVATION", raising=False)
    from core.quota_alert import notifier_alerte

    assert await notifier_alerte("solde bas") is True
    assert envoi.await_args.kwargs["notification_id"] == "moteur_quotas_llm"
