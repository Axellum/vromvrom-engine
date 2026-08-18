"""
Sur l'hôte Home Assistant, le token configuré prime sur l'en-tête du modèle (#T340).

Mesuré en production le 12/08 sur `c6f4cd2`, juste après le déploiement de #T332,
sur la demande qui sert de fil rouge depuis le matin — « quelle température dans
le salon ? » (session `chat_aa88ff7bed`) :

    22:55:04,786  [#T329/#T332] Hôte HA en clair → bascule vers https://…/api/states
    (aucune ligne d'injection)
    22:55:04,903  [ha_agent] Erreur d'outil (auth) : Erreur (HTTP 401): Unauthorized

Trois mesures pour départager, toutes faites dans le conteneur de production :

  1. le token de la configuration est **valide** — `HTTP 200` sur `/api/` ;
  2. `call_api(url)` **sans en-tête** → bascule, injection, **HTTP 200**, entités réelles ;
  3. `call_api(url, headers_json='{"Authorization": "Bearer YOUR_LONG_LIVED_ACCESS_TOKEN"}')`
     → bascule, **aucune injection, aucun log**, **401**.

Le cas 3 reproduit la signature exacte de la production. L'agent fournit donc
lui-même une autorisation — un placeholder de documentation, la seule chose qu'il
puisse produire, puisque le vrai token n'est pas dans son prompt et ne doit pas y
être. Le garde-fou de #T332 protégeait une valeur qui ne peut pas être bonne.

La correction est volontairement étroite : **rien ne change hors de l'hôte HA
configuré**, où `call_api` continue de transmettre les en-têtes tels quels.
"""

import json

import pytest
import requests

import tools.api as api


@pytest.fixture(autouse=True)
def _env_ha_neutre(monkeypatch):
    """Point de départ déterministe : chaque test pose lui-même ce dont il a besoin."""
    for cle in ("HASS_URL", "HA_URL", "HASS_TOKEN", "HA_TOKEN"):
        monkeypatch.delenv(cle, raising=False)


class _Reponse:
    status_code = 200
    text = '{"state": "22.4"}'

    def json(self):
        return {"state": "22.4"}


def _mock_session_ha(monkeypatch, vus: dict):
    """L'hôte HA passe par `ha_requests_session`, jamais par `requests.request`."""

    class _Session:
        def request(self, method, url, headers=None, **kwargs):
            vus["url"] = url
            vus["headers"] = headers or {}
            return _Reponse()

    monkeypatch.setattr("core.ha_tls.ha_requests_session", lambda: _Session())
    monkeypatch.setattr(
        requests, "request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("hôte HA → session, pas requests.request")),
    )


PLACEHOLDER = "Bearer YOUR_LONG_LIVED_ACCESS_TOKEN"


def test_autorisation_inventee_par_le_modele_est_remplacee(monkeypatch):
    """Le cas de production : le placeholder du modèle cède la place au vrai token."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    sortie = api.call_api(
        "https://192.168.1.10:8123/api/states",
        headers_json=json.dumps({"Authorization": PLACEHOLDER}),
    )

    assert vus["headers"]["Authorization"] == "Bearer jeton-de-test"
    assert "22.4" in sortie


def test_remplacement_survit_a_la_bascule_https(monkeypatch):
    """
    Chaîne complète telle qu'elle est jouée en production : le modèle écrit une
    URL en clair ET un en-tête inventé. Les deux doivent être corrigés, et rien
    ne doit partir en clair sur le LAN.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    appels = []

    class _Session:
        def request(self, method, url, headers=None, **kwargs):
            appels.append((url, (headers or {}).get("Authorization")))
            return _Reponse()

    def _refus_clair(method, url, headers=None, **kwargs):
        raise AssertionError(f"aucune sonde HTTP en clair vers HA (reçu {url!r})")

    monkeypatch.setattr(requests, "request", _refus_clair)
    monkeypatch.setattr("core.ha_tls.ha_requests_session", lambda: _Session())

    api.call_api(
        "http://192.168.1.10:8123/api/states",
        headers_json=json.dumps({"Authorization": PLACEHOLDER}),
    )

    assert appels == [("https://192.168.1.10:8123/api/states", "Bearer jeton-de-test")]


def test_autorisation_fournie_intacte_hors_ha(monkeypatch):
    """
    Non-régression de sécurité — la contrepartie du remplacement.

    Hors de l'hôte HA configuré, `call_api` reste un client HTTP générique : il
    transmet ce qu'on lui donne et n'injecte rien. Sans cette limite, le token HA
    partirait vers n'importe quelle URL écrite par le modèle.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}

    def _fake_request(method, url, headers=None, **kwargs):
        vus["headers"] = headers or {}
        return _Reponse()

    monkeypatch.setattr(requests, "request", _fake_request)
    api.call_api(
        "https://api.exemple.invalid/v1/etat",
        headers_json='{"Authorization": "Bearer cle-de-l-api-tierce"}',
    )

    assert vus["headers"]["Authorization"] == "Bearer cle-de-l-api-tierce"
    assert "jeton-de-test" not in str(vus["headers"])


def test_casse_de_l_en_tete_indifferente(monkeypatch):
    """
    `authorization` en minuscules ne doit pas produire DEUX en-têtes concurrents :
    HTTP les fusionnerait en une valeur illisible, et le 401 reviendrait sans
    qu'aucun journal ne l'explique.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api(
        "https://192.168.1.10:8123/api/states",
        headers_json=json.dumps({"authorization": PLACEHOLDER}),
    )

    cles_auth = [cle for cle in vus["headers"] if cle.lower() == "authorization"]
    assert cles_auth == ["Authorization"]
    assert vus["headers"]["Authorization"] == "Bearer jeton-de-test"


def test_les_autres_en_tetes_sont_preserves(monkeypatch):
    """Seule l'autorisation est touchée : le reste (Content-Type…) passe intact."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api(
        "https://192.168.1.10:8123/api/services/cover/close_cover",
        method="POST",
        payload_json='{"entity_id": "cover.volet_salon"}',
        headers_json=json.dumps({"Authorization": PLACEHOLDER, "X-Trace": "abc"}),
    )

    assert vus["headers"]["Authorization"] == "Bearer jeton-de-test"
    assert vus["headers"]["X-Trace"] == "abc"
    assert vus["headers"]["Content-Type"] == "application/json"


def test_sans_token_configure_l_en_tete_fourni_est_conserve(monkeypatch):
    """
    Repli : si la configuration n'a aucun token, on ne dégrade pas l'appel en
    retirant ce que l'appelant avait mis — on n'a rien de mieux à proposer.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api(
        "https://192.168.1.10:8123/api/states",
        headers_json='{"Authorization": "Bearer jeton-de-l-appelant"}',
    )

    assert vus["headers"]["Authorization"] == "Bearer jeton-de-l-appelant"


def test_aucun_secret_dans_le_journal(monkeypatch, caplog):
    """
    Le journal explique le remplacement sans publier de secret : ni le token
    configuré, ni la valeur reçue (qui pourrait en être un dans un autre montage).
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-tres-secret")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    with caplog.at_level("INFO", logger="tools.api"):
        api.call_api(
            "https://192.168.1.10:8123/api/states",
            headers_json='{"Authorization": "Bearer secret-de-l-appelant"}',
        )

    journal = caplog.text
    assert "#T340" in journal, "le remplacement doit être tracé, sinon le 401 reste inexplicable"
    assert "jeton-tres-secret" not in journal
    assert "secret-de-l-appelant" not in journal


def test_injection_normale_inchangee(monkeypatch):
    """Non-régression #T332 : sans en-tête fourni, le comportement d'origine tient."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api("https://192.168.1.10:8123/api/states")

    assert vus["headers"]["Authorization"] == "Bearer jeton-de-test"
