"""Cadence horaire du dreamer : fenêtre :MM, une fois par heure civile."""

from datetime import datetime

from agents.dreamer_agent import _declenchement_horaire


def test_inactif_si_minute_absente():
    now = datetime(2026, 8, 23, 12, 0)
    ok, _ = _declenchement_horaire(now, None, "")
    assert ok is False


def test_pc_declenche_a_heure_pile():
    now = datetime(2026, 8, 23, 12, 2)
    ok, raison = _declenchement_horaire(now, 0, "")
    assert ok is True
    assert ":00" in raison


def test_deck_declenche_a_h_plus_30():
    now = datetime(2026, 8, 23, 12, 32)
    ok, raison = _declenchement_horaire(now, 30, "")
    assert ok is True
    assert ":30" in raison


def test_pc_ne_declenche_pas_a_h_plus_30():
    now = datetime(2026, 8, 23, 12, 32)
    ok, _ = _declenchement_horaire(now, 0, "")
    assert ok is False


def test_pas_deux_fois_dans_la_meme_heure():
    now = datetime(2026, 8, 23, 12, 3)
    ok, _ = _declenchement_horaire(now, 0, "2026-08-23T12:00:11")
    assert ok is False


def test_declenche_l_heure_suivante():
    now = datetime(2026, 8, 23, 13, 1)
    ok, _ = _declenchement_horaire(now, 0, "2026-08-23T12:00:11")
    assert ok is True
