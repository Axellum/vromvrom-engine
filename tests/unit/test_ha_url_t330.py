"""
tests/unit/test_ha_url_t330.py — plus aucune URL HA en clair en dur (#T330).

Mesuré le 12/08 : `http://192.168.1.x:8123` était écrit en dur comme valeur
de repli à 9 endroits (5 fichiers) — une URL en clair vers un serveur qui
n'écoute qu'en HTTPS : un défaut FAUX. Home Assistant refoule le clair sans
répondre (`RemoteDisconnected`, cf. #T329), et ces valeurs finissaient
recopiées dans un prompt, un contexte RAG, puis proposées à un agent.

La mesure réelle (12 sites, 8 fichiers) dépasse l'énoncé du board : en plus
des 9 sites connus, `tools/ha_entity_ingest.py` portait un défaut http en
clair, et `gui_server.py` + `services/execute_service.py` des défauts https
en dur (même famille : IP + port supposés).

Le correctif : `core/ha_url.py::get_ha_url()` (même famille que
`core/ha_token.py::get_ha_token()`, T239) — HASS_URL prioritaire, repli
HA_URL, AUCUN défaut : une URL absente retourne "" et chaque appelant nomme
la variable manquante dans son message d'échec.

Les tests vérifient, variables d'environnement retirées, qu'aucun des sites
ne peut plus fabriquer d'URL vers le port 8123 ; et qu'avec HASS_URL définie,
le comportement est exactement celui d'avant.
"""
import pathlib

import pytest

from core.ha_url import get_ha_url


@pytest.fixture
def sans_url_ha(monkeypatch):
    """Variables d'URL HA retirées ; token défini pour atteindre les vérifs URL."""
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")


# ── Le helper lui-même ───────────────────────────────────────────────────────

def test_sans_variables_le_helper_ne_fabrique_aucune_url(monkeypatch):
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)
    assert get_ha_url() == ""


def test_hass_url_prioritaire_sur_ha_url(monkeypatch):
    monkeypatch.setenv("HASS_URL", "https://hass.exemple:8123")
    monkeypatch.setenv("HA_URL", "https://repli.exemple:8123")
    assert get_ha_url() == "https://hass.exemple:8123"


def test_ha_url_en_repli(monkeypatch):
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.setenv("HA_URL", "https://repli.exemple:8123")
    assert get_ha_url() == "https://repli.exemple:8123"


# ── Comportement inchangé quand HASS_URL est définie ─────────────────────────

def test_hass_url_definie_comportement_inchange(monkeypatch):
    """Avec HASS_URL définie, les sites se comportent exactement comme avant."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    assert get_ha_url() == "https://192.168.1.x:8123"

    from core.tab5_pusher import Tab5Pusher
    pusher = Tab5Pusher()
    assert pusher.ha_url == "https://192.168.1.x:8123"

    from core.vocal_tools import _ha_credentials
    assert _ha_credentials() == ("https://192.168.1.x:8123", "jeton-de-test")

    from api.routes.ha import _get_ha_credentials
    assert _get_ha_credentials() == ("https://192.168.1.x:8123", "jeton-de-test")


# ── Sans variables : chaque site échoue en nommant la variable ───────────────

def test_tab5_pusher_sans_url(monkeypatch):
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)
    from core.tab5_pusher import Tab5Pusher
    assert Tab5Pusher().ha_url == ""


def test_vocal_credentials_sans_url(monkeypatch):
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)
    from core.vocal_tools import _ha_credentials
    ha_url, _ = _ha_credentials()
    assert ha_url == ""


def test_route_ha_credentials_sans_url(sans_url_ha):
    from fastapi import HTTPException

    from api.routes.ha import _get_ha_credentials
    with pytest.raises(HTTPException) as exc:
        _get_ha_credentials()
    assert "URL Home Assistant non configurée" in exc.value.detail
    assert "HASS_URL/HA_URL" in exc.value.detail


@pytest.mark.asyncio
async def test_ha_health_sans_url(sans_url_ha):
    from api.routes.ha import ha_health
    resultat = await ha_health()
    assert "URL Home Assistant absente" in resultat["error"]
    assert "HASS_URL/HA_URL" in resultat["error"]


@pytest.mark.asyncio
async def test_check_ha_health_sans_url(sans_url_ha):
    from core.daemon_loop import _check_ha_health
    resultat = await _check_ha_health()
    assert resultat["status"] == "skipped"
    assert "URL Home Assistant non configurée" in resultat["details"]["reason"]


@pytest.mark.asyncio
async def test_get_ha_state_sans_url(sans_url_ha):
    from core.daemon_loop import _get_ha_state
    with pytest.raises(ValueError) as exc:
        await _get_ha_state("sensor.test")
    assert "URL Home Assistant non configurée" in str(exc.value)


@pytest.mark.asyncio
async def test_send_ha_notification_sans_url(sans_url_ha):
    from core.daemon_loop import _send_ha_notification
    assert await _send_ha_notification("titre", "message") is False


@pytest.mark.asyncio
async def test_search_ha_entities_sans_url(sans_url_ha):
    from core.mcp_tools.homeassistant import search_ha_entities
    message = await search_ha_entities("température", "")
    assert "URL Home Assistant non configurée" in message
    assert "HASS_URL/HA_URL" in message


@pytest.mark.asyncio
async def test_execute_ha_action_sans_url(sans_url_ha):
    from core.mcp_tools.homeassistant import execute_ha_action
    message = await execute_ha_action("light.salon", "light.turn_on")
    assert "URL Home Assistant non configurée" in message
    assert "HASS_URL/HA_URL" in message


@pytest.mark.asyncio
async def test_ingest_ha_entities_sans_url(sans_url_ha):
    from tools.ha_entity_ingest import ingest_ha_entities
    assert await ingest_ha_entities() is False


def test_read_ha_credentials_sans_url(sans_url_ha):
    from services.execute_service import _read_ha_credentials
    assert _read_ha_credentials() == ("jeton-de-test", "")


# ── Anti-régression structurelle ─────────────────────────────────────────────

def test_aucune_url_ha_en_dur_dans_le_code_runtime():
    """Aucune valeur par défaut http OU https vers le port 8123 dans le code.

    Le vrai garde-fou du lot : une URL en clair réintroduite (ou un défaut
    https recopié ailleurs) ferait échouer ce test. Les commentaires
    historiques (backticks, pas de guillemets) ne sont pas visés.
    """
    racine = pathlib.Path(__file__).resolve().parents[2]
    exclus = {".claude", ".venv", ".pytest_cache", "tests", "scratch",
              "backups_prod"}
    coupables = []
    for p in racine.rglob("*.py"):
        if any(part in exclus for part in p.parts):
            continue
        for i, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if '"http://192.168.1.x:8123"' in ligne \
                    or '"https://192.168.1.x:8123"' in ligne:
                coupables.append(f"{p.relative_to(racine)}:{i}")
    assert not coupables, (
        "URL HA en dur réintroduite dans le code runtime (utiliser "
        f"core.ha_url.get_ha_url) : {coupables}"
    )
