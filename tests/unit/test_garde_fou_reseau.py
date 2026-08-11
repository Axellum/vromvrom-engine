"""Preuve que le garde-fou réseau (tests/unit/conftest.py) mord réellement.

Ce test n'a pas besoin d'exemption : sa tentative de connexion EST le scénario
que le garde-fou doit bloquer (hôte externe, hors loopback). Si le garde-fou
saute (ou est supprimé), le connect vers 192.0.2.1:443 échoue avec une OSError
(adresse TEST-NET non routable, RFC 5737) au lieu du Failed attendu et ce test
devient rouge — la suite ne peut plus redevenir « verte » en silence.
"""

import socket

import pytest

from tests.unit.conftest import _ADRESSE_PREUVE


def test_une_connexion_reseau_echoue_avec_message_explicite():
    """Une tentative de connexion vers un hôte externe doit échouer."""
    with pytest.raises(pytest.fail.Exception) as excinfo:
        socket.socket().connect(_ADRESSE_PREUVE)

    msg = str(excinfo.value)
    # Le message nomme le test fautif et la destination contactée…
    assert "test_une_connexion_reseau_echoue_avec_message_explicite" in msg
    assert "192.0.2.1:443" in msg
    # …et indique la marche à suivre (mock ou marqueur `live`).
    assert "unittest.mock.patch" in msg
    assert "@pytest.mark.live" in msg
