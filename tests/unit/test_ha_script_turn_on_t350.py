"""
#T350 — Les commandes de volet n'attendent plus la fin du script.

Le script HA `script.blind_action` contient un `delay` de 26 s dans ses
branches open/close. Or `POST /api/services/script/<id>` ne répond qu'une fois
le script terminé, alors qu'`execute_ha_service` impose un timeout de 10 s :
une commande « ouvre/ferme le volet » ne pouvait jamais recevoir sa réponse.

Correctif : tout appel `script.<id>` passe désormais par `script.turn_on`, qui
rend la main immédiatement. PIÈGE : `script.turn_on` n'accepte PAS les
paramètres du script à plat — il faut `{"entity_id": "script.<id>",
"variables": {…}}`. Sans `variables`, le script démarre mais aucune branche
`{{ action == '…' }}` ne correspond (pas de `default`), et HA répond quand même
200 : volet immobile, moteur qui annonce le succès. On asserte donc le CORPS du
POST, pas seulement l'URL.
"""

import aiohttp
import pytest

import services.execute_service as es
from services.execute_service import match_ha_command


# [T356] Depuis que execute_ha_service vérifie l'état de l'entité avant le POST,
# les tests du POST simulent une entité VIVANTE (sinon la lecture d'état ferait
# un vrai GET réseau, bloqué par le garde-fou). read_ha_state est async.
async def _etat_vivant(entity_id: str) -> dict:
    return {"entity_id": entity_id, "state": "off", "attributes": {}}


class _FakeResp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""


class _FakeSession:
    """Enregistre chaque POST HA : (url, payload, timeout)."""

    def __init__(self):
        self.calls: list[tuple[str, dict, aiohttp.ClientTimeout]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, timeout))
        return _FakeResp(200)


@pytest.mark.asyncio
async def test_volet_passe_par_script_turn_on_avec_variables(monkeypatch):
    """
    LE TEST CENTRAL : une commande volet produit un POST vers
    /api/services/script/turn_on dont le corps contient `variables` avec la
    bonne action ET l'entity_id du script. On asserte le corps, pas l'URL.
    """
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    ok, texte = await es.execute_ha_service(
        "script.blind_action", "cover.volet_salon",
        service_data={"action": "close"},
    )

    assert ok
    assert len(fake.calls) == 1
    url, payload, timeout = fake.calls[0]
    assert url.endswith("/api/services/script/turn_on"), url
    assert payload["entity_id"] == "script.blind_action"
    assert payload["variables"] == {"action": "close"}
    assert timeout.total == 10


@pytest.mark.asyncio
async def test_volet_open_passe_aussi_par_script_turn_on(monkeypatch):
    """La branche open est couverte par le même mécanisme, avec action 'open'."""
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    ok, _ = await es.execute_ha_service(
        "script.blind_action", "cover.volet_salon",
        service_data={"action": "open"},
    )

    assert ok
    url, payload, _ = fake.calls[0]
    assert url.endswith("/api/services/script/turn_on")
    assert payload["entity_id"] == "script.blind_action"
    assert payload["variables"] == {"action": "open"}


@pytest.mark.asyncio
async def test_service_ordinaire_inchange_light_turn_off(monkeypatch):
    """
    LE TEST JUMEAU (non-régression) : un service ordinaire part exactement
    comme avant — même URL, même corps, même timeout. light.turn_off a répondu
    en 271 ms le 17/08 sur ce chemin : il ne doit rien changer.
    """
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    ok, texte = await es.execute_ha_service("light.turn_off", "light.living_room")

    assert ok
    assert len(fake.calls) == 1
    url, payload, timeout = fake.calls[0]
    assert url.endswith("/api/services/light/turn_off"), url
    assert payload == {"entity_id": "light.living_room"}
    assert timeout.total == 10
    assert "éteinte" in texte


@pytest.mark.asyncio
async def test_autre_script_generique_utilise_aussi_turn_on(monkeypatch):
    """Tout appel `script.<id>` (pas seulement le volet) passe par turn_on."""
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    ok, _ = await es.execute_ha_service(
        "script.garden_watering", "script.garden_watering",
        service_data={"duree": 10},
    )

    assert ok
    url, payload, _ = fake.calls[0]
    assert url.endswith("/api/services/script/turn_on")
    assert payload["entity_id"] == "script.garden_watering"
    assert payload["variables"] == {"duree": 10}


# ── Reconnaissance des formes STT réellement dites ───────────────────────────

def test_remonte_et_remontre_ouvrent_le_volet():
    """« remonte » et sa transcription « remontre » sont des ordres d'ouverture."""
    for phrase in ("remonte le volet", "remontre le volet", "relève le volet"):
        m = match_ha_command(phrase)
        assert m is not None, phrase
        assert m.service == "script.blind_action", phrase
        assert m.service_data == {"action": "open"}, phrase


def test_descend_continue_de_fermer():
    """« descend le volet » continue de fermer (non-régression)."""
    m = match_ha_command("descend le volet")
    assert m is not None
    assert m.service == "script.blind_action"
    assert m.service_data == {"action": "close"}


def test_monte_existant_toujours_reconnu():
    """La reconnaissance existante (« monte », « ouvre ») n'est pas cassée."""
    for phrase in ("monte le volet", "ouvre le volet"):
        m = match_ha_command(phrase)
        assert m is not None
        assert m.service_data == {"action": "open"}


# ── Garde-fou : volet sans action refusé, même sur le nouveau chemin ─────────

@pytest.mark.asyncio
async def test_volet_sans_action_refuse(monkeypatch):
    """Le refus documenté (volet appelé sans `action`) tient toujours."""
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))

    ok, texte = await es.execute_ha_service(
        "script.blind_action", "cover.volet_salon",
        service_data=None,
    )

    assert not ok
    assert fake.calls == [], "un volet sans action ne doit produire AUCUN POST"
    assert "volet" in texte.lower()
