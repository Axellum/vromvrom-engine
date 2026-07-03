# -*- coding: utf-8 -*-
"""Tests du cœur Elo partagé (core/elo_core.py) — fonctions pures."""

import pytest

from core.elo_core import (
    expected_score,
    updated_elo,
    adaptive_k,
    reviewer_score_to_outcome,
)


def test_expected_score_equal_elo_is_half():
    # Deux adversaires de même niveau → probabilité 0.5.
    assert expected_score(1500, 1500) == pytest.approx(0.5)
    assert expected_score(1200, 1200) == pytest.approx(0.5)


def test_expected_score_is_symmetric():
    # P(A bat B) + P(B bat A) == 1.
    a = expected_score(1600, 1400)
    b = expected_score(1400, 1600)
    assert a + b == pytest.approx(1.0)
    assert a > 0.5 > b  # le plus fort est favori


def test_updated_elo_gain_on_upset_win():
    # Victoire alors qu'on était défavori → gain positif net.
    exp = expected_score(1400, 1600)        # ~0.24
    new = updated_elo(1400, actual=1.0, expected=exp, k=32)
    assert new > 1400
    assert new - 1400 == pytest.approx(32 * (1.0 - exp))


def test_updated_elo_loss_decreases():
    exp = expected_score(1500, 1500)        # 0.5
    new = updated_elo(1500, actual=0.0, expected=exp, k=32)
    assert new == pytest.approx(1500 - 16)  # -K/2 sur un match nul perdu


def test_adaptive_k_switches_at_threshold():
    assert adaptive_k(0, 32, 16, 30) == 32
    assert adaptive_k(29, 32, 16, 30) == 32
    assert adaptive_k(30, 32, 16, 30) == 16   # bascule à >= seuil
    assert adaptive_k(100, 32, 16, 30) == 16


@pytest.mark.parametrize("score,outcome,label", [
    (10.0, 1.0, "win"),
    (7.0, 1.0, "win"),
    (6.9, 0.5, "draw"),
    (4.0, 0.5, "draw"),
    (3.9, 0.0, "loss"),
    (1.0, 0.0, "loss"),
])
def test_reviewer_score_to_outcome(score, outcome, label):
    assert reviewer_score_to_outcome(score) == (outcome, label)
