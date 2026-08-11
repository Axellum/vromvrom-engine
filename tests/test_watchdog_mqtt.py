"""
tests/test_watchdog_mqtt.py — Watchdog MQTT : compatibilité paho 2.x
══════════════════════════════════════════════════════════════════════════════

Comble l'angle mort relevé à la PR #169 (bump paho-mqtt 1.6 → 2.1) : la CI
n'exerçait aucun chemin MQTT du watchdog, donc ni le passage à l'API de
callbacks v2, ni la supervision de reconnexion n'étaient couverts.

Trois défauts sont verrouillés ici :
  1. client paho construit en API v1 (déprécié, supprimé en paho 3.0)
  2. callbacks aux signatures v1 (l'API v2 passe 5 arguments → TypeError)
  3. `_connected` jamais positionné → superviseur bloqué, reconnexion morte
"""

import asyncio
import warnings

import pytest

from core.watchdog import WatchdogConfig, WatchdogDaemon

paho_client = pytest.importorskip("paho.mqtt.client")


TOPICS = ("homeassistant/status", "zigbee2mqtt/bridge/state")


def _daemon() -> WatchdogDaemon:
    daemon = WatchdogDaemon(WatchdogConfig(topics=TOPICS, mqtt_client_id="test-watchdog"))
    daemon._loop = asyncio.get_running_loop()
    return daemon


def _reason_code(identifier: int):
    from paho.mqtt.reasoncodes import ReasonCode
    return ReasonCode(paho_client.CONNACK >> 4, identifier=identifier)


class _ClientEspion:
    """Capture les souscriptions demandées par le callback de connexion."""

    def __init__(self):
        self.souscriptions = []

    def subscribe(self, topic, qos=0):
        self.souscriptions.append((topic, qos))
        return (0, len(self.souscriptions))


def test_paho_2x_installe():
    """requirements.txt impose paho-mqtt>=2.1 : l'API v2 doit être disponible."""
    assert hasattr(paho_client, "CallbackAPIVersion"), (
        "paho-mqtt < 2.0 détecté — le watchdog utilise l'API de callbacks v2. "
        "Mettre l'environnement à jour : pip install -r requirements.txt"
    )


async def test_on_connect_souscrit_puis_debloque_le_superviseur():
    """Régression : les souscriptions ne doivent pas court-circuiter `_connected.set()`.

    L'ancienne écriture `[subscribe(...) for t in topics] or set()` ne posait
    jamais l'événement (une liste non vide est vraie), donc `_supervisor`
    restait bloqué sur `await self._connected.wait()` et ne détectait aucune
    déconnexion.
    """
    daemon = _daemon()
    espion = _ClientEspion()
    daemon._mqtt_client = espion  # client courant : ses callbacks font autorité

    daemon._on_connect(
        espion,
        None,
        paho_client.ConnectFlags(session_present=False),
        _reason_code(0),
        None,
    )
    await asyncio.sleep(0)

    assert espion.souscriptions == [(t, 1) for t in TOPICS]
    assert daemon._connected.is_set(), "_connected doit être posé après un CONNACK accepté"


async def test_on_connect_refuse_ne_debloque_pas():
    """Un CONNACK en échec (identifiants invalides) ne doit pas simuler une connexion."""
    daemon = _daemon()
    espion = _ClientEspion()
    daemon._mqtt_client = espion

    daemon._on_connect(
        espion,
        None,
        paho_client.ConnectFlags(session_present=False),
        _reason_code(135),  # Not authorized
        None,
    )
    await asyncio.sleep(0)

    assert espion.souscriptions == []
    assert not daemon._connected.is_set()


async def test_on_disconnect_reveille_le_superviseur():
    """La déconnexion doit retomber l'événement pour relancer le cycle de backoff."""
    daemon = _daemon()
    espion = _ClientEspion()
    daemon._mqtt_client = espion
    daemon._connected.set()

    daemon._on_disconnect(
        espion,
        None,
        paho_client.DisconnectFlags(is_disconnect_packet_from_server=True),
        _reason_code(0),
        None,
    )
    await asyncio.sleep(0)

    assert not daemon._connected.is_set()


async def test_connack_tardif_d_une_tentative_abandonnee_est_ignore():
    """Régression : un CONNACK en retard ne doit pas faire croire à une connexion.

    Après expiration du délai de connexion, le superviseur abandonne son client
    et repart sur un nouveau. Si le client abandonné recevait quand même son
    CONNACK et posait `_connected`, l'attente de la tentative suivante rendrait
    la main immédiatement : le superviseur entrerait dans sa boucle interne en
    se croyant connecté, sans MQTT et sans jamais déclencher de backoff — le
    blocage même que ce module corrige, réintroduit par une autre porte.
    """
    daemon = _daemon()
    abandonne = _ClientEspion()
    courant = _ClientEspion()

    # Le superviseur a abandonné `abandonne` et suit désormais `courant`.
    daemon._mqtt_client = courant

    daemon._on_connect(
        abandonne,
        None,
        paho_client.ConnectFlags(session_present=False),
        _reason_code(0),
        None,
    )
    await asyncio.sleep(0)

    assert abandonne.souscriptions == [], "un client abandonné ne doit plus souscrire"
    assert not daemon._connected.is_set(), (
        "_connected ne doit pas être posé par une tentative abandonnée"
    )

    # Le client courant, lui, débloque bien le superviseur.
    daemon._on_connect(
        courant,
        None,
        paho_client.ConnectFlags(session_present=False),
        _reason_code(0),
        None,
    )
    await asyncio.sleep(0)

    assert daemon._connected.is_set()


async def test_deconnexion_d_un_client_abandonne_n_affecte_pas_le_courant():
    """Le `_on_disconnect` d'un client abandonné ne doit pas couper le courant."""
    daemon = _daemon()
    abandonne = _ClientEspion()
    courant = _ClientEspion()
    daemon._mqtt_client = courant
    daemon._connected.set()

    daemon._on_disconnect(
        abandonne,
        None,
        paho_client.DisconnectFlags(is_disconnect_packet_from_server=True),
        _reason_code(0),
        None,
    )
    await asyncio.sleep(0)

    assert daemon._connected.is_set(), (
        "la déconnexion d'un client abandonné ne doit pas faire retomber l'état du courant"
    )


async def test_client_construit_sans_api_depreciee():
    """Le superviseur doit instancier paho en API v2.

    Le broker visé est volontairement injoignable : seule la construction du
    client nous intéresse, la connexion échoue ensuite et part en backoff.
    """
    daemon = WatchdogDaemon(WatchdogConfig(
        mqtt_host="127.0.0.1",
        mqtt_port=1,               # port fermé → ConnectionRefused immédiat
        topics=TOPICS,
        backoff_initial=30.0,      # une seule tentative pendant le test
        ha_log_path="",
    ))
    daemon._loop = asyncio.get_running_loop()

    with warnings.catch_warnings(record=True) as captures:
        warnings.simplefilter("always")
        tache = asyncio.create_task(daemon._supervisor())
        await asyncio.sleep(1.0)
        tache.cancel()
        try:
            await tache
        except asyncio.CancelledError:
            pass

    # paho émet l'avertissement avec stacklevel=2 : le fichier rapporté est
    # celui de l'appelant, pas paho. On filtre donc sur le message.
    depreciations = [
        str(c.message)
        for c in captures
        if issubclass(c.category, DeprecationWarning)
        and "callback api version" in str(c.message).lower()
    ]
    assert not depreciations, f"API paho dépréciée utilisée : {depreciations}"
