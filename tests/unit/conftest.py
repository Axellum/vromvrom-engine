"""
conftest.py — Garde-fou réseau pour les tests unitaires (tests/unit/).

Aucun test unitaire ne doit ouvrir de vraie connexion réseau vers l'extérieur
de la machine : la suite doit rester verte sans clé d'API, sans quota et sans
réseau (c'est le critère d'acceptation de toutes les tâches déléguées du
projet). Un appel réseau réel dans un test produit un faux négatif (flaky,
dépendant du quota/latence) qui apprend à ignorer le rouge — voir #T261 et
#T268.

Mécanisme : remplacement de `socket.socket.connect` (point de blocage commun à
tous les clients TCP/TLS : requests, httpx, http.client, urllib, asyncio,
paramiko, MQTT…). Toute tentative vers un hôte NON-loopback échoue avec un
message qui nomme le test, l'adresse contactée, et indique la marche à suivre.

Pourquoi le loopback reste autorisé (choix documenté #T261) :
- `asyncio.new_event_loop()` crée sa « self-pipe » via `socket.socketpair()`,
  qui passe par un connect() sur 127.0.0.1 sur Windows : bloquer le loopback
  casserait toute la mécanique asyncio sans rien tester ;
- les tests qui démarrent leur propre serveur HTTP mocké local (ports
  éphémères) sont déterministes : aucun quota, aucune clé, aucune latence
  externe — ils ne peuvent pas produire le faux négatif que ce garde-fou
  élimine ;
- le risque résiduel (un test qui viserait un vrai service local, ex. LM
  Studio) est couvert par la CI : elle tourne sans aucun service local, ces
  connexions y échouent et le test ne peut pas devenir « vert » en silence.

Les tests marqués `@pytest.mark.live` (vraies API réseau voulues, exclus de la
suite par défaut via `addopts -m "not live"`) sont exemptés du garde-fou.
"""

import ipaddress
import socket

import pytest

# [T261] Adresse TEST-NET-1 (RFC 5737), non routable, jamais loopback : si le
# garde-fou saute, un connect réel échoue sans timeout DNS long.
_ADRESSE_PREUVE = ("192.0.2.1", 443)


def _est_loopback(host):
    """Vrai si `host` est une adresse de loopback (127.0.0.0/8, ::1).

    Les noms DNS et les formes non-IP sont traités comme externes : un nom
    d'hôte ne peut jamais être local à la machine de test.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _interdire_reseau(monkeypatch, request):
    """Garde-fou : échoue tout test unitaire tentant une connexion externe.

    Exempte les tests marqués `@pytest.mark.live` (appels réels voulus,
    relançables avec `python -m pytest -m live`).
    """
    if request.node.get_closest_marker("live"):
        return
    nom_test = request.node.name
    connect_original = socket.socket.connect

    def _connect_interdit(self, address, *args, **kwargs):
        """Remplaçant de `socket.socket.connect` : échoue si la destination est externe.

        `self` est l'instance socket, `address` la destination (tuple (hôte, port)
        pour AF_INET, chaîne pour AF_UNIX). Aucun paquet n'est émis vers
        l'extérieur : le test échoue avant tout contact avec le réseau.
        """
        if isinstance(address, tuple) and _est_loopback(address[0]):
            # Loopback : infra asyncio (self-pipe) ou serveur HTTP mocké du test.
            return connect_original(self, address, *args, **kwargs)
        dest = f"{address[0]}:{address[1]}" if isinstance(address, tuple) else str(address)
        pytest.fail(
            f"Le test « {nom_test} » a tenté une vraie connexion réseau vers {dest} : "
            "les tests unitaires ne doivent pas toucher le réseau externe (faux "
            "négatifs, quota, latence). Soit mockez l'appel (unittest.mock.patch "
            "sur le client HTTP), soit, si l'appel réel est voulu, marquez le test "
            "@pytest.mark.live pour l'exclure de la suite par défaut."
        )

    monkeypatch.setattr(socket.socket, "connect", _connect_interdit)


# ─────────────────────────────────────────────────────────────────
# Isolation des services d'observabilité (Langfuse / OTel)
# ─────────────────────────────────────────────────────────────────
#
# Certains tests chargent le .env du moteur (load_dotenv() dans les tests
# `live`) : si les clés LANGFUSE_* / OTEL_* du .env entrent dans le process,
# LangfuseBridge s'active et son client réel démarre des threads + un handler
# atexit qui exportent vers cloud.langfuse.com — des connexions externes qui
# tombent PENDANT un autre test et font échouer le garde-fou réseau (flaky).
#
# Ce fixture autouse purge ces variables AVANT chaque test et réinitialise le
# singleton : un test unitaire ne doit jamais activer de client d'observabilité
# réel, quel que soit le contenu du .env de la machine.


@pytest.fixture(autouse=True)
def _isoler_observabilite_externe(monkeypatch):
    """Purge les secrets d'observabilité et force LangfuseBridge en no-op."""
    for cle in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_GCP",
    ):
        monkeypatch.delenv(cle, raising=False)
    # Réinitialiser le singleton : s'il avait été initialisé avant la purge
    # (client réel), le prochain get_instance() le recrée en no-op.
    import core.langfuse_bridge as _lf_bridge

    _lf_bridge.LangfuseBridge._instance = None
    # OTel : re-setup no-op (le cache _setup_done d'un éventuel setup réel
    # empêcherait la purge de prendre effet).
    import core.otel as _otel

    _otel._tracer = None
    _otel._setup_done = False
