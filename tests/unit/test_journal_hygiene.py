"""
test_journal_hygiene.py — Hygiène du journal du Deck.

Deux défauts de lisibilité du journal, mesurés en prod :
  - DÉFAUT 1 : le warning TLS d'opt-out (HA_VERIFY_TLS=false) était répété à
    chaque construction de contexte SSL (~644 occurrences en 36 h). Il doit
    être émis une seule fois par démarrage, sans changer le comportement TLS.
  - DÉFAUT 2 : une HTTPError sans `response` (transport, DNS, timeout…)
    journalisait « Erreur HTTP ? : » et perdait l'exception (~78 occurrences
    en 36 h). On doit journaliser le type et le message de l'exception.
"""

from __future__ import annotations

import logging
import ssl
from unittest.mock import Mock, patch

import requests

from core import ha_tls
from core.gemini_native import GeminiNativeProvider, _http_error_detail

# ──────────────────────────────────────────────────────────────────
# DÉFAUT 1 — warning TLS d'opt-out unique par démarrage
# ──────────────────────────────────────────────────────────────────

def _env_tls_propre(monkeypatch):
    """Isole les variables TLS pour ne pas dépendre de l'environnement réel."""
    for cle in ("HA_VERIFY_TLS", "HA_CA_BUNDLE", "HA_TLS_SERVER_HOSTNAME"):
        monkeypatch.delenv(cle, raising=False)
    monkeypatch.setenv("HA_VERIFY_TLS", "false")
    # Réinitialise le drapeau de module : chaque test part d'un "démarrage" neuf.
    ha_tls._warning_optout_affiche = False


def test_warning_tls_emise_une_seule_fois_par_demarrage(monkeypatch, caplog):
    """Deux contextes opt-out n'émettent pas deux WARNING identiques.

    La première construction le journalise bien ; la seconde, non.
    Le comportement TLS, lui, reste identique aux deux appels.
    """
    _env_tls_propre(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="core.ha_tls"):
        ctx1 = ha_tls.ha_ssl_context()
        ctx2 = ha_tls.ha_ssl_context()

    messages = [r.message for r in caplog.records
                if "Vérification TLS DÉSACTIVÉE" in r.message]
    assert len(messages) == 1, (
        "le warning d'opt-out doit être émis une seule fois par démarrage"
    )

    # Le comportement TLS est inchangé : désactivation effective aux deux appels.
    for ctx in (ctx1, ctx2):
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False


def test_warning_tls_reexiste_au_demarrage_suivant(monkeypatch, caplog):
    """Un nouveau module (nouveau démarrage) ré-émet le warning une fois."""
    _env_tls_propre(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="core.ha_tls"):
        ha_tls.ha_ssl_context()

    # Simule un redémarrage : le drapeau est remis à zéro.
    ha_tls._warning_optout_affiche = False
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="core.ha_tls"):
        ha_tls.ha_ssl_context()

    messages = [r.message for r in caplog.records
                if "Vérification TLS DÉSACTIVÉE" in r.message]
    assert len(messages) == 1


def test_warning_tls_absent_quand_verification_active(monkeypatch, caplog):
    """Avec vérification active, aucun warning d'opt-out n'est émis."""
    for cle in ("HA_VERIFY_TLS", "HA_CA_BUNDLE", "HA_TLS_SERVER_HOSTNAME"):
        monkeypatch.delenv(cle, raising=False)
    ha_tls._warning_optout_affiche = False

    with caplog.at_level(logging.WARNING, logger="core.ha_tls"):
        ha_tls.ha_ssl_context()

    messages = [r.message for r in caplog.records
                if "Vérification TLS DÉSACTIVÉE" in r.message]
    assert not messages


# ──────────────────────────────────────────────────────────────────
# DÉFAUT 2 — HTTPError sans réponse journalisée avec type + message
# ──────────────────────────────────────────────────────────────────

def _http_error_sans_reponse():
    """Construit une HTTPError sans `response` (transport/DNS/timeout)."""
    return requests.exceptions.HTTPError("Connection aborted")


def _http_error_avec_reponse():
    """Construit une HTTPError avec une réponse 500 et un corps."""
    resp = Mock()
    resp.status_code = 500
    resp.text = "Erreur interne du serveur"
    err = requests.exceptions.HTTPError("500 Server Error")
    err.response = resp
    return err


def test_http_error_sans_reponse_journalise_type_et_message(caplog):
    """Sans réponse, on journalise le type et le message de l'exception."""
    err = _http_error_sans_reponse()

    with caplog.at_level(logging.ERROR, logger="core.gemini_native"):
        status, body = _http_error_detail(err)
        logging.getLogger("core.gemini_native").error(
            f"[GEMINI NATIF] Erreur HTTP {status} : {body}"
        )

    assert status == "HTTPError"
    assert body == "Connection aborted"
    assert "? " not in status
    assert "? " not in body
    # Le message journalisé porte l'information de l'exception.
    assert "HTTPError" in caplog.text
    assert "Connection aborted" in caplog.text


def test_http_error_avec_reponse_journalise_code_et_corps(caplog):
    """Avec réponse, on journalise toujours le code et le corps (comportement inchangé)."""
    err = _http_error_avec_reponse()

    with caplog.at_level(logging.ERROR, logger="core.gemini_native"):
        status, body = _http_error_detail(err)
        logging.getLogger("core.gemini_native").error(
            f"[GEMINI NATIF] Erreur HTTP {status} : {body}"
        )

    assert status == "500"
    assert body == "Erreur interne du serveur"
    assert "Erreur HTTP 500" in caplog.text
    assert "Erreur interne du serveur" in caplog.text


@patch("core.gemini_native.requests.post")
def test_generate_erreur_sans_reponse_journalise_exception(mock_post, caplog):
    """generate() sur HTTPError sans réponse journalise type + message, jamais « ? »."""
    # Une HTTPError sans `response` (transport/DNS/timeout) doit être
    # journalisée avec le type et le message de l'exception.
    mock_post.side_effect = requests.exceptions.HTTPError("DNS lookup failed")
    provider = GeminiNativeProvider(
        api_key="test-key-fake", model="gemini-3.5-flash", enable_explicit_cache=False
    )

    with caplog.at_level(logging.ERROR, logger="core.gemini_native"):
        try:
            provider.generate("sys", "user")
        except requests.exceptions.HTTPError:
            pass  # l'erreur est re-levée, on vérifie le journal

    assert "HTTPError" in caplog.text
    assert "DNS lookup failed" in caplog.text
    assert "Erreur HTTP ?" not in caplog.text
    assert "Erreur HTTP HTTPError" in caplog.text


@patch("core.gemini_native.requests.post")
def test_generate_erreur_http_avec_reponse_inchangee(mock_post, caplog):
    """generate() sur HTTPError avec réponse journalise code + corps (inchangé)."""
    resp = Mock()
    resp.status_code = 429
    resp.text = "Quota dépassé"
    err = requests.exceptions.HTTPError("429 Too Many Requests")
    err.response = resp
    mock_post.side_effect = err
    provider = GeminiNativeProvider(
        api_key="test-key-fake", model="gemini-3.5-flash", enable_explicit_cache=False
    )

    with caplog.at_level(logging.ERROR, logger="core.gemini_native"):
        try:
            provider.generate("sys", "user")
        except requests.exceptions.HTTPError:
            pass

    assert "Erreur HTTP 429" in caplog.text
    assert "Quota dépassé" in caplog.text
    assert "Erreur HTTP ?" not in caplog.text
