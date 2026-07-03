# -*- coding: utf-8 -*-
"""
core/elo_core.py — Cœur Elo partagé (fonctions pures, sans I/O).

Abstraction commune aux deux systèmes Elo du moteur, qui notent des entités
DIFFÉRENTES mais avec la MÊME mécanique mathématique :

  - [[elo_scorer]]  : note des couples (modèle, domaine)   → table model_elo_scores
  - [[elo_router]]  : note des routing_types               → table elo_routing

Avant cette unification, la formule Elo, le K-factor et la conversion
score→outcome étaient dupliqués dans les deux modules (avec des constantes
légèrement divergentes). Ce module centralise la mécanique ; chaque système
conserve ses propres constantes (Elo de référence, K) et sa propre persistance.

Toutes les fonctions sont pures et déterministes → testables sans BDD.
"""

from typing import Tuple

# Constantes par défaut (chaque appelant peut surcharger).
DEFAULT_K = 32.0
DEFAULT_DIVISOR = 400.0  # Échelle Elo standard (échecs)


def expected_score(elo: float, opponent_elo: float, divisor: float = DEFAULT_DIVISOR) -> float:
    """
    Probabilité de victoire attendue d'un joueur (formule Elo classique).

        expected = 1 / (1 + 10^((opponent - elo) / divisor))

    Retourne une valeur dans [0, 1].
    """
    return 1.0 / (1.0 + 10.0 ** ((opponent_elo - elo) / divisor))


def updated_elo(old_elo: float, actual: float, expected: float, k: float = DEFAULT_K) -> float:
    """
    Nouveau score Elo après un match.

        new_elo = old_elo + k * (actual - expected)

    Args:
        old_elo:  score avant le match
        actual:   résultat réel (1.0 victoire, 0.5 nul, 0.0 défaite)
        expected: probabilité attendue (cf. expected_score)
        k:        facteur K (amplitude de l'ajustement)
    """
    return old_elo + k * (actual - expected)


def adaptive_k(total_matches: int, k_initial: float, k_stable: float, threshold: int) -> float:
    """
    K-factor adaptatif : élevé au début (convergence rapide), réduit ensuite
    (stabilisation). En dessous de `threshold` matchs → k_initial, sinon k_stable.
    """
    return k_initial if total_matches < threshold else k_stable


def reviewer_score_to_outcome(reviewer_score: float) -> Tuple[float, str]:
    """
    Convertit un score ReviewerAgent (1-10) en outcome Elo + libellé.

        >= 7.0  → victoire (1.0, "win")
        4.0-6.9 → nul      (0.5, "draw")
        < 4.0   → défaite  (0.0, "loss")
    """
    if reviewer_score >= 7.0:
        return 1.0, "win"
    if reviewer_score < 4.0:
        return 0.0, "loss"
    return 0.5, "draw"
