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


@pytest.fixture(autouse=True)
def _ajean_http_coupe_hors_live(monkeypatch, request):
    """D-19 : le fallback LAN `ajean_http_ok` ne doit pas sonder 192.168.1.10 en unit.

    `_choisir_provider_tache` GET /v1/models si AJEAN n'est pas dans `sains`.
    Sans ce stub, la CI tape le PC local et le garde-fou réseau casse la suite.
    Un test qui veut le chemin LAN patche `ajean_http_ok` à True lui-même.
    """
    if request.node.get_closest_marker("live"):
        return

    async def _coupe(_pid, timeout_s=1.0):
        return False

    monkeypatch.setattr("core.llm.ajean_adapter.ajean_http_ok", _coupe, raising=False)


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


# ─────────────────────────────────────────────────────────────────
# Isolation des chemins de slot DreamCoder (#T420)
# ─────────────────────────────────────────────────────────────────
#
# Même mécanique que ci-dessus, et découverte de la même façon : le load_dotenv()
# de niveau module des tests `live` fait entrer TOUT le .env de la machine dans le
# process. Depuis #T420, une machine qui draine y déclare
# MOTEUR_DREAMCODER_REPO_PATHS — et les tests du garde-fou « pas de drainage sans
# clone » se mettent à voir un clone dédié qu'ils n'ont pas configuré.
#
# Symptôme trompeur : 8 tests verts un par un, rouges en suite complète, et verts
# au CI (qui n'a pas de .env). Le réglage d'une machine ne doit pas décider du
# verdict de sa suite de tests.


@pytest.fixture(autouse=True)
def _isoler_slots_dreamcoder(monkeypatch):
    """
    Purge les réglages DreamCoder hérités du .env de la machine.

    Troisième occurrence du même piège : une machine qui draine déclare son clone
    (`MOTEUR_DREAMCODER_REPO_PATHS`, #T420) et sa fenêtre de tâche élargie
    (`MOTEUR_DREAMCODER_TACHE_TIMEOUT_S`, portée à 900 s le 21/08 pour les
    analyses). Les tests des garde-fous et des fenêtres voient alors une valeur
    qu'ils n'ont pas configurée — verts un par un, rouges en suite complète, et
    verts au CI qui n'a pas de `.env`. Le réglage d'une machine ne doit jamais
    décider du verdict de sa suite de tests.
    """
    for cle in ("MOTEUR_DREAMCODER_REPO_PATHS", "MOTEUR_DREAMCODER_TACHE_TIMEOUT_S"):
        monkeypatch.delenv(cle, raising=False)


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
