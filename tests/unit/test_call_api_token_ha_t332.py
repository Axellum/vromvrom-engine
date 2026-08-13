"""
`call_api` authentifie ses appels à Home Assistant (#T332).

Suite directe de #T329, mesurée en production le 12/08 sur `4d8cdde`. Une fois
la bascule HTTPS en place, l'appel atteint enfin HA — et se fait refouler :

    21:55:55,806  Appel API distant: GET http://192.168.1.x:8123/api/states
    21:55:55,813  [#T329] a fermé la connexion sans répondre → rejeu en https
    21:55:55,929  [ha_agent] Erreur d'outil (auth) : Erreur (HTTP 401): Unauthorized
    21:55:55,930  [ha_agent] Échec final après auto-correction infructueuse

Progrès réel — `RemoteDisconnected` après 9,1 s est devenu `HTTP 401` en 7 ms —
mais la demande « quelle température dans le salon ? » échoue toujours.

L'agent appelait `/api/states` sans le moindre en-tête. Il ne peut pas faire
autrement : le token n'est pas dans son prompt, et il ne DOIT pas y être. C'est
donc à l'outil de l'ajouter, comme le font déjà les chemins MCP et vocal.

Deux garde-fous, tous deux couverts ici :
  - injection réservée à l'hôte HA de la configuration (égalité stricte), jamais
    vers une URL quelconque proposée par le modèle — sinon `call_api` devient un
    moyen d'exfiltrer le token vers n'importe quel serveur ;
  - une autorisation explicitement fournie n'est jamais écrasée.
"""

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
    """L'hôte HA passe par `ha_requests_session`, plus par `requests.request`."""

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


def test_token_injecte_pour_l_hote_ha(monkeypatch):
    """Le cas mesuré : plus de 401, l'appel part authentifié."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api("https://192.168.1.x:8123/api/states")

    assert vus["headers"].get("Authorization") == "Bearer jeton-de-test"


def test_aucune_injection_vers_un_hote_tiers(monkeypatch):
    """
    Garde-fou de sécurité : `call_api` reçoit des URL écrites par le modèle.
    Le token ne doit jamais partir ailleurs que vers l'hôte HA configuré.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}

    def _fake_request(method, url, headers=None, **kwargs):
        vus["headers"] = headers or {}
        return _Reponse()

    monkeypatch.setattr(requests, "request", _fake_request)
    api.call_api("https://exfiltration.invalid/collecte")

    assert "Authorization" not in vus["headers"], "le token a fuité vers un hôte tiers"


def test_autorisation_fournie_remplacee_sur_l_hote_ha(monkeypatch):
    """
    ⚠️ Règle INVERSÉE par #T340, après mesure en production.

    #T332 posait « une autorisation explicitement fournie n'est jamais écrasée ».
    Le principe est sain quand l'appelant est du code ; il ne l'est pas ici :
    l'appelant est un modèle, et le token HA n'est pas dans son prompt. Ce qu'il
    fournit est donc toujours inventé — et faisait échouer l'appel en 401 alors
    que le même appel sans en-tête réussissait. Sur l'hôte HA configuré, le token
    de la configuration prime désormais. Le garde-fou « ne rien toucher » reste
    entier pour tout autre hôte : cf. `test_autorisation_fournie_intacte_hors_ha`
    dans `test_call_api_token_prioritaire_t340.py`.
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api(
        "https://192.168.1.x:8123/api/states",
        headers_json='{"Authorization": "Bearer jeton-choisi"}',
    )

    assert vus["headers"]["Authorization"] == "Bearer jeton-de-test"


def test_injection_survit_a_la_bascule_https(monkeypatch):
    """
    Cas prod : URL en clair + hôte HA. Plus de sonde HTTP : bascule immédiate
    vers HTTPS + injection Bearer (aucun token en clair sur le LAN).
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    appels = []

    class _Session:
        def request(self, method, url, headers=None, **kwargs):
            appels.append((url, (headers or {}).get("Authorization")))
            return _Reponse()

    def _fake_request(method, url, headers=None, **kwargs):
        raise AssertionError(
            f"aucune sonde HTTP en clair vers HA (reçu {url!r} auth="
            f"{(headers or {}).get('Authorization')!r})"
        )

    monkeypatch.setattr(requests, "request", _fake_request)
    monkeypatch.setattr("core.ha_tls.ha_requests_session", lambda: _Session())

    sortie = api.call_api("http://192.168.1.x:8123/api/states")

    assert appels == [("https://192.168.1.x:8123/api/states", "Bearer jeton-de-test")]
    assert "22.4" in sortie


def test_jamais_de_bearer_sur_http_clair(monkeypatch):
    """Garde-fou : `_injecter_token_ha` refuse le schéma http://."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    headers = api._injecter_token_ha("http://192.168.1.x:8123/api/states", {})
    assert "Authorization" not in headers


def test_token_absent_ne_leve_pas(monkeypatch):
    """Sans token configuré, l'appel part sans en-tête et l'API répondra 401."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.x:8123")
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    vus = {}
    _mock_session_ha(monkeypatch, vus)

    api.call_api("https://192.168.1.x:8123/api/states")

    assert "Authorization" not in vus["headers"]


def test_injection_sans_hass_url_configuree(monkeypatch):
    """Hôte HA inconnu (variables absentes) : aucune injection, aucune erreur."""
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.setenv("HASS_TOKEN", "jeton-de-test")
    vus = {}

    def _fake_request(method, url, headers=None, **kwargs):
        vus["headers"] = headers or {}
        return _Reponse()

    monkeypatch.setattr(requests, "request", _fake_request)
    api.call_api("https://192.168.1.x:8123/api/states")

    assert "Authorization" not in vus["headers"]
