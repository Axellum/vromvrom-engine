"""Tests unitaires de core.ha_token (accesseur unique du token HA, T239)."""

from unittest.mock import patch

from core.ha_token import get_ha_token


def test_hass_token_seul():
    """HASS_TOKEN seul → retourné tel quel."""
    with patch.dict("os.environ", {"HASS_TOKEN": "tok-hass", "HA_TOKEN": ""}, clear=False):
        assert get_ha_token() == "tok-hass"


def test_ha_token_seul():
    """HA_TOKEN seul → repli correct sur l'ancienne convention."""
    with patch.dict("os.environ", {"HASS_TOKEN": "", "HA_TOKEN": "tok-ha"}, clear=False):
        assert get_ha_token() == "tok-ha"


def test_hass_token_prioritaire_sur_ha_token():
    """Les deux définis → HASS_TOKEN gagne (priorité documentée)."""
    with patch.dict("os.environ", {"HASS_TOKEN": "tok-hass", "HA_TOKEN": "tok-ha"}, clear=False):
        assert get_ha_token() == "tok-hass"


def test_aucun_token_retourne_chaine_vide():
    """Aucune des deux variables → chaîne vide, jamais d'exception."""
    with patch.dict("os.environ", {"HASS_TOKEN": "", "HA_TOKEN": ""}, clear=False):
        assert get_ha_token() == ""
