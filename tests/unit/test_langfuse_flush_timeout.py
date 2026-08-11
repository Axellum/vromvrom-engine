"""
tests/unit/test_langfuse_flush_timeout.py — Le flush ne bloque plus (#T272).

Diagnostic du 11/08, par capture de pile `faulthandler` : la suite unitaire est
passée de 16 secondes à **plus de 10 minutes** sur un seul test — celui qui
exécute une session complète — bloquée dans `core/langfuse_bridge.py::flush`.

Ce flush est dans le chemin de finalisation de CHAQUE session
(`Engine._finalize_session` appelle `end_trace()` puis `flush()`). En test,
c'est le garde-fou réseau qui coupe la connexion ; en production, ce sera une
coupure Internet ou une panne du service Langfuse. Le `try/except` qui
l'entourait ne protégeait de rien : une ATTENTE n'est pas une exception.

Le test qui prouve la tâche est `test_flush_lent_rend_la_main` : il MESURE le
temps écoulé. Sans borne, il ne se termine pas avant 10 secondes.
"""

import logging
import time

import pytest

from core.langfuse_bridge import LangfuseBridge


class _ClientLent:
    """Client dont le flush ne répond jamais assez vite (réseau coupé)."""

    def __init__(self, duree=10.0):
        self.duree = duree
        self.flush_appele = False

    def flush(self):
        self.flush_appele = True
        time.sleep(self.duree)

    def shutdown(self):
        time.sleep(self.duree)


class _ClientNormal:
    def __init__(self):
        self.flushs = 0
        self.shutdowns = 0

    def flush(self):
        self.flushs += 1

    def shutdown(self):
        self.shutdowns += 1


class _ClientQuiEchoue:
    def flush(self):
        raise ConnectionError("serveur Langfuse injoignable")

    def shutdown(self):
        raise ConnectionError("serveur Langfuse injoignable")


def _bridge(client, monkeypatch, timeout="1"):
    monkeypatch.setenv("MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S", timeout)
    bridge = LangfuseBridge.__new__(LangfuseBridge)
    bridge._enabled = True
    bridge._client = client
    return bridge


def test_flush_lent_rend_la_main(monkeypatch):
    """LE test de la tâche : un flush de 10 s ne retient pas l'appelant."""
    client = _ClientLent(duree=10.0)
    bridge = _bridge(client, monkeypatch, timeout="1")

    debut = time.monotonic()
    bridge.flush()
    ecoule = time.monotonic() - debut

    assert ecoule < 2.0, f"le flush a retenu l'appelant {ecoule:.1f}s"
    assert client.flush_appele, "l'envoi doit bien être tenté, seulement pas attendu"


def test_flush_lent_journalise_un_avertissement(monkeypatch, caplog):
    bridge = _bridge(_ClientLent(duree=10.0), monkeypatch, timeout="1")

    with caplog.at_level(logging.WARNING, logger="core.langfuse_bridge"):
        bridge.flush()

    assert any("abandonné" in m for m in caplog.messages), caplog.messages


def test_flush_lent_ne_leve_aucune_exception(monkeypatch):
    """La fin de session ne doit jamais échouer à cause de la télémétrie."""
    bridge = _bridge(_ClientLent(duree=10.0), monkeypatch, timeout="1")
    bridge.flush()  # ne lève pas


def test_client_normal_inchange(monkeypatch):
    client = _ClientNormal()
    bridge = _bridge(client, monkeypatch, timeout="5")

    debut = time.monotonic()
    bridge.flush()
    assert time.monotonic() - debut < 1.0
    assert client.flushs == 1


def test_shutdown_borne_aussi(monkeypatch):
    """`shutdown()` appelle aussi `flush()` : il doit être borné de la même façon."""
    bridge = _bridge(_ClientLent(duree=10.0), monkeypatch, timeout="1")

    debut = time.monotonic()
    bridge.shutdown()

    assert time.monotonic() - debut < 2.0


def test_shutdown_normal_ferme_le_client(monkeypatch):
    client = _ClientNormal()
    bridge = _bridge(client, monkeypatch, timeout="5")
    bridge.shutdown()
    assert (client.flushs, client.shutdowns) == (1, 1)


def test_erreur_reseau_reste_absorbee(monkeypatch, caplog):
    """Une exception du client ne doit pas remonter (comportement d'avant conservé)."""
    bridge = _bridge(_ClientQuiEchoue(), monkeypatch, timeout="1")

    with caplog.at_level(logging.WARNING, logger="core.langfuse_bridge"):
        bridge.flush()

    assert any("injoignable" in m for m in caplog.messages)


def test_bridge_desactive_ne_fait_rien(monkeypatch):
    bridge = LangfuseBridge.__new__(LangfuseBridge)
    bridge._enabled = False
    bridge._client = None
    bridge.flush()
    bridge.shutdown()  # aucun effet, aucune exception


@pytest.mark.parametrize("valeur,attendu", [("2.5", 2.5), ("0.05", 0.1), ("vite", 5.0), ("", 5.0)])
def test_lecture_du_delai(monkeypatch, valeur, attendu):
    """Délai surchargeable, plancher à 0,1 s, valeur illisible ignorée."""
    monkeypatch.setenv("MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S", valeur)
    assert LangfuseBridge._timeout_flush() == attendu


def test_delai_par_defaut(monkeypatch):
    monkeypatch.delenv("MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S", raising=False)
    assert LangfuseBridge._timeout_flush() == 5.0
