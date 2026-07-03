"""
tests/test_auth_ticket.py — Tickets d'accès éphémères SSE/WS (core/auth.py).

Vérifie le comportement de sécurité : usage unique, expiration, rejet des
tickets inconnus.
"""

import time

from core.auth import issue_ticket, verify_and_consume_ticket


def test_ticket_valide_une_seule_fois():
    """Un ticket émis est valide exactement une fois (usage unique)."""
    ticket = issue_ticket()
    assert verify_and_consume_ticket(ticket) is True
    # Rejoué : déjà consommé → refusé.
    assert verify_and_consume_ticket(ticket) is False


def test_ticket_inconnu_refuse():
    """Un ticket jamais émis (ou vide) est refusé."""
    assert verify_and_consume_ticket("ticket-bidon") is False
    assert verify_and_consume_ticket("") is False


def test_ticket_expire():
    """Un ticket dépassé son TTL est refusé (et non consommable)."""
    ticket = issue_ticket(ttl=1)
    time.sleep(1.1)
    assert verify_and_consume_ticket(ticket) is False


def test_tickets_uniques():
    """Deux émissions produisent des tickets distincts."""
    assert issue_ticket() != issue_ticket()
