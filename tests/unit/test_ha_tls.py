"""Tests unitaires de la politique TLS Home Assistant (core.ha_tls)."""

from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

from core.ha_tls import (
    ha_requests_verify,
    ha_ssl_context,
    ha_tls_pinned_hostname,
    ha_tls_verification_enabled,
)


def _env_propre(monkeypatch, **valeurs):
    """Isole les variables TLS : l'environnement réel ne doit pas fausser le test."""
    for cle in ("HA_VERIFY_TLS", "HA_CA_BUNDLE", "HA_TLS_SERVER_HOSTNAME"):
        monkeypatch.delenv(cle, raising=False)
    for cle, valeur in valeurs.items():
        monkeypatch.setenv(cle, valeur)


def test_verification_active_par_defaut(monkeypatch):
    _env_propre(monkeypatch)
    assert ha_tls_verification_enabled() is True
    ctx = ha_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_opt_out_explicite_desactive_la_verification(monkeypatch):
    _env_propre(monkeypatch, HA_VERIFY_TLS="false")
    ctx = ha_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_pinning_conserve_une_verification_complete(monkeypatch):
    """Imposer le nom ne doit RIEN relâcher : chaîne et nom restent vérifiés."""
    _env_propre(monkeypatch, HA_TLS_SERVER_HOSTNAME="cert.example.fr")
    ctx = ha_ssl_context()
    assert ha_tls_pinned_hostname() == "cert.example.fr"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_pinning_substitue_le_nom_verifie(monkeypatch):
    """L'IP de HASS_URL est remplacée par le nom couvert par le certificat."""
    _env_propre(monkeypatch, HA_TLS_SERVER_HOSTNAME="cert.example.fr")
    ctx = ha_ssl_context()

    with patch.object(ssl.SSLContext, "wrap_bio") as parent:
        ctx.wrap_bio(MagicMock(), MagicMock(), server_hostname="192.168.1.10")

    assert parent.call_args.kwargs["server_hostname"] == "cert.example.fr"


def test_sans_pinning_le_nom_demande_est_respecte(monkeypatch):
    _env_propre(monkeypatch)
    ctx = ha_ssl_context()
    assert type(ctx) is ssl.SSLContext

    with patch.object(ssl.SSLContext, "wrap_bio") as parent:
        ctx.wrap_bio(MagicMock(), MagicMock(), server_hostname="192.168.1.10")

    assert parent.call_args.kwargs["server_hostname"] == "192.168.1.10"


def test_requests_verify_suit_la_politique(monkeypatch):
    _env_propre(monkeypatch)
    assert ha_requests_verify() is True

    _env_propre(monkeypatch, HA_VERIFY_TLS="false")
    assert ha_requests_verify() is False
