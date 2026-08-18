"""
`call_api` : une URL en clair vers un port TLS ne doit plus tuer la tâche (#T329).

Mesuré en PRODUCTION le 12/08 (session `chat_683ea8b2cd`) — « Quelle est la
température actuelle dans le salon ? » :

    Appel API distant: GET http://192.168.1.10:8123/api/states
    [ha_agent] Erreur d'outil de call_api : Erreur de réseau HTTP:
      ('Connection aborted.', RemoteDisconnected('Remote end closed connection
      without response'))
    [ha_agent] Échec final après auto-correction locale infructueuse

Home Assistant n'écoute qu'en **HTTPS** sur ce port (`HASS_URL=https://…:8123`,
certificat auto-signé). Un serveur TLS qui reçoit du HTTP en clair ferme la
connexion sans jamais répondre — d'où `RemoteDisconnected`, un message qui ne
nomme pas sa cause.

Vérifié en prod, même hôte, même port :
    http://192.168.1.10:8123/api/   → ConnectionError / RemoteDisconnected
    https://192.168.1.10:8123/api/  → HTTP 401 (le serveur répond)

⚠️ Ce n'est donc PAS la cause de #T323 (connexion keep-alive réutilisée) : ici
`requests.request()` ouvre une session neuve à chaque appel. Deux symptômes
proches, deux causes distinctes — l'hypothèse initiale d'une cause commune a été
falsifiée par la mesure.

Le rejeu en HTTPS est sûr même en POST : le serveur TLS n'a rien pu traiter,
puisqu'il n'a jamais lu de requête valide.
"""

import pytest
import requests

import tools.api as api


@pytest.fixture(autouse=True)
def _env_ha_neutre(monkeypatch):
    """
    Isole HASS_URL / HA_URL — sans quoi ces tests dépendent de l'ordre de la suite.

    Régression réelle : ces deux tests passaient isolément et ÉCHOUAIENT en suite
    complète. Dès qu'un test antérieur laisse `HASS_URL` pointer 192.168.1.10,
    `_est_hote_ha()` devient vrai, le rejeu part dans `ha_requests_session()` —
    que ces tests ne mockent pas — et tente une VRAIE connexion, bloquée par le
    garde-fou réseau du conftest. Les tests qui veulent l'hôte HA le posent eux-
    mêmes explicitement, après cette fixture.
    """
    monkeypatch.delenv("HASS_URL", raising=False)
    monkeypatch.delenv("HA_URL", raising=False)


class _Reponse:
    status_code = 200
    text = '{"state": "22.4"}'

    def json(self):
        return {"state": "22.4"}


def _erreur_port_tls():
    return requests.exceptions.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed "
        "connection without response'))"
    )


def test_bascule_en_https_et_reussit(monkeypatch):
    """Le cas mesuré : l'appel en clair échoue, le rejeu en HTTPS aboutit."""
    appels = []

    def _fake_request(method, url, **kwargs):
        appels.append(url)
        if url.startswith("http://"):
            raise _erreur_port_tls()
        return _Reponse()

    monkeypatch.setattr(requests, "request", _fake_request)

    sortie = api.call_api("http://192.168.1.10:8123/api/states")

    assert appels == [
        "http://192.168.1.10:8123/api/states",
        "https://192.168.1.10:8123/api/states",
    ]
    assert "22.4" in sortie
    assert "Erreur" not in sortie


def test_url_et_query_preservees(monkeypatch):
    """La bascule ne doit changer QUE le schéma."""
    vues = []

    def _fake_request(method, url, **kwargs):
        vues.append(url)
        if url.startswith("http://"):
            raise _erreur_port_tls()
        return _Reponse()

    monkeypatch.setattr(requests, "request", _fake_request)
    api.call_api("http://ha.local:8123/api/history?filter=abc#frag")

    assert vues[1] == "https://ha.local:8123/api/history?filter=abc#frag"


def test_pas_de_bascule_sur_une_autre_erreur_reseau(monkeypatch):
    """Un hôte injoignable ne doit pas déclencher un second appel inutile."""
    appels = []

    def _fake_request(method, url, **kwargs):
        appels.append(url)
        raise requests.exceptions.ConnectionError("Name or service not known")

    monkeypatch.setattr(requests, "request", _fake_request)

    sortie = api.call_api("http://hote-inexistant.invalid/api")

    assert len(appels) == 1
    assert "Erreur de réseau HTTP" in sortie


def test_url_deja_en_https_ne_rejoue_pas(monkeypatch):
    """Une URL déjà chiffrée qui échoue ne doit pas boucler."""
    appels = []

    def _fake_request(method, url, **kwargs):
        appels.append(url)
        raise _erreur_port_tls()

    monkeypatch.setattr(requests, "request", _fake_request)

    api.call_api("https://192.168.1.10:8123/api/states")

    assert len(appels) == 1


def test_message_d_erreur_nomme_la_cause(monkeypatch):
    """
    Si le rejeu échoue aussi, le message doit être ACTIONNABLE : c'est ce qui
    manquait à la boucle d'auto-correction de l'Executor, qui abandonnait sur
    une trace urllib3 ne nommant pas la cause.
    """
    def _fake_request(method, url, **kwargs):
        raise _erreur_port_tls()

    monkeypatch.setattr(requests, "request", _fake_request)

    sortie = api.call_api("http://192.168.1.10:8123/api/states?filter=temp#frag")

    assert "https://192.168.1.10:8123/api/states?filter=temp#frag" in sortie
    assert "TLS" in sortie


def test_politique_tls_du_projet_pour_l_hote_ha(monkeypatch):
    """L'hôte HA (certificat auto-signé) suit HA_VERIFY_TLS ; les autres non."""
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    monkeypatch.setenv("HA_VERIFY_TLS", "false")

    assert api._verify_pour("https://192.168.1.10:8123/api/states") is False
    # Un hôte quelconque garde la vérification standard : un outil générique ne
    # doit pas devenir un trou de sécurité pour tout Internet.
    assert api._verify_pour("https://exemple.invalid/api") is True


def test_rejeu_hote_ha_passe_par_ha_requests_session(monkeypatch):
    """
    Bugbot : `verify=` seul ignore HA_TLS_SERVER_HOSTNAME. L'hôte HA doit
    rejouer via `ha_requests_session` (même chemin que MCP / vocal).
    """
    monkeypatch.setenv("HASS_URL", "https://192.168.1.10:8123")
    appels_session = []

    class _Session:
        def request(self, method, url, **kwargs):
            appels_session.append(url)
            return _Reponse()

    def _fake_request(method, url, **kwargs):
        if url.startswith("http://"):
            raise _erreur_port_tls()
        raise AssertionError("le rejeu HA ne doit pas passer par requests.request")

    monkeypatch.setattr(requests, "request", _fake_request)
    monkeypatch.setattr("core.ha_tls.ha_requests_session", lambda: _Session())

    sortie = api.call_api("http://192.168.1.10:8123/api/states")

    assert appels_session == ["https://192.168.1.10:8123/api/states"]
    assert "22.4" in sortie


def test_detection_ne_se_declenche_que_sur_la_bonne_signature():
    """Garde-fou de la détection elle-même."""
    assert api._ressemble_a_un_port_tls("http://x:8123/a", _erreur_port_tls())
    assert not api._ressemble_a_un_port_tls("https://x:8123/a", _erreur_port_tls())
    assert not api._ressemble_a_un_port_tls(
        "http://x:8123/a", requests.exceptions.ConnectionError("timed out"),
    )
