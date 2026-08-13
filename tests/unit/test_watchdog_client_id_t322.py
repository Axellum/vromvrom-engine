"""
Identifiant MQTT du watchdog domotique (#T322).

Défaut mesuré en PRODUCTION le 12/08 : **15 209 déconnexions MQTT le 11/08**
(~869 par heure au moment de la mesure, 0 les jours précédents). Le watchdog
domotique 24/7 passait son temps à se reconnecter, donc ne surveillait plus rien.

Cause : `WatchdogConfig.mqtt_client_id` valait « watchdog-domus » EN DUR. MQTT
impose l'unicité du client_id — quand deux clients le partagent, le broker
éjecte l'ancien à chaque connexion du nouveau, qui se reconnecte aussitôt.
Le moteur de dev (poste Windows, PID 17380 vérifié connecté au broker) et la
prod (Deck) se sont donc mutuellement éjectés en boucle.

Départagé en prod par deux mesures, pas par lecture de code :
  - avec le client_id partagé : CONNACK Success puis DISCONNECT, 4 fois en 6 s ;
  - avec un client_id unique   : 1 connexion, 0 déconnexion en 20 s.
Donc broker sain, identifiants sains — c'est bien le client_id.

Chronologie qui confirme : le conflit démarre le 11/08 à 11:25, soit le
lendemain de la création du login `moteur_watchdog` (#T236, 10/08). Avant, aucune
des deux instances ne pouvait s'authentifier, donc aucune ne pouvait se disputer
l'identifiant.
"""
import re

from core.watchdog import WatchdogConfig, _client_id_par_defaut


def test_client_id_par_defaut_contient_le_nom_de_machine(monkeypatch):
    """Sans surcharge, l'identifiant est dérivé du nom d'hôte — donc distinct par machine."""
    monkeypatch.delenv("MQTT_CLIENT_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "steamdeck")

    assert _client_id_par_defaut() == "wd-domus-steamdeck"


def test_deux_machines_obtiennent_des_identifiants_distincts(monkeypatch):
    """Le cœur de #T322 : deux hôtes ne doivent JAMAIS partager le même client_id."""
    monkeypatch.delenv("MQTT_CLIENT_ID", raising=False)

    monkeypatch.setattr("socket.gethostname", lambda: "steamdeck")
    prod = _client_id_par_defaut()
    monkeypatch.setattr("socket.gethostname", lambda: "PC-AXEL-WINDOWS")
    dev = _client_id_par_defaut()

    assert prod != dev, (
        "les deux instances partagent encore un identifiant : elles s'éjecteront "
        "mutuellement du broker en boucle"
    )


def test_variable_d_environnement_prioritaire(monkeypatch):
    """MQTT_CLIENT_ID reste le moyen de forcer une valeur précise."""
    monkeypatch.setenv("MQTT_CLIENT_ID", "watchdog-choisi-a-la-main")
    assert _client_id_par_defaut() == "watchdog-choisi-a-la-main"


def test_identifiant_sain_et_borne(monkeypatch):
    """Nom d'hôte exotique : caractères nettoyés, longueur bornée à 23 (limite MQTT 3.1)."""
    monkeypatch.delenv("MQTT_CLIENT_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "Poste.Très_Long.Avec/Des:Caractères")

    identifiant = _client_id_par_defaut()

    assert len(identifiant) <= 23
    assert re.fullmatch(r"[A-Za-z0-9-]+", identifiant), identifiant


def test_nom_de_machine_indisponible_ne_leve_pas(monkeypatch):
    """Un hôte sans nom résolvable ne doit pas empêcher le watchdog de démarrer."""
    monkeypatch.delenv("MQTT_CLIENT_ID", raising=False)

    def _explose():
        raise OSError("nom d'hôte indisponible")

    monkeypatch.setattr("socket.gethostname", _explose)

    assert _client_id_par_defaut() == "wd-domus-inconnu"


def test_config_utilise_le_defaut_dynamique(monkeypatch):
    """La dataclass ne doit plus figer l'identifiant à l'import du module."""
    monkeypatch.delenv("MQTT_CLIENT_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "machine-a")
    config_a = WatchdogConfig()
    monkeypatch.setattr("socket.gethostname", lambda: "machine-b")
    config_b = WatchdogConfig()

    assert config_a.mqtt_client_id != config_b.mqtt_client_id
    assert config_a.mqtt_client_id != "watchdog-domus"
