"""
tests/unit/test_fenetres_execution.py — Emboîtement des fenêtres de temps (#T277).

La règle, une seule : **toute fenêtre englobante doit être strictement plus
longue que ce qu'elle englobe**. Le moteur l'a violée trois fois, toujours de la
même façon — un mécanisme interne tué de l'extérieur avant d'avoir pu s'exercer :

- #T267 : approbation humaine de 300 s dans une session de 120 s → 8 demandes en
  24 h, 0 résolution, jamais une seule approbation possible ;
- #T277 : read timeout provider de 120 s dans un Planner borné à 90 s → la
  cascade ne pouvait pas basculer sur le modèle suivant (mesuré deux fois le
  11/08 : une seule tentative, plan vide) ;
- le même Planner (90 s) dans une session de fond de 120 s → 30 s pour le DAG,
  la revue et la finalisation.

Ces tests existent pour qu'un futur ajustement d'une valeur ne puisse plus
casser les autres en silence.
"""

import pytest

from core.fenetres_execution import (
    duree_planner_s,
    duree_session_fond_s,
    duree_session_interactive_s,
    duree_tentative_llm_s,
    verifier_emboitement,
)


def test_hierarchie_respectee_par_defaut():
    """Aucune violation avec les valeurs livrées."""
    assert verifier_emboitement() == []


def test_planner_permet_au_moins_deux_tentatives():
    """
    C'est LE point du correctif : la cascade doit pouvoir basculer.

    Avec 90 s de fenêtre et 120 s par tentative, le second modèle n'était jamais
    essayé — la cascade réparée par #T265 restait décorative sur les timeouts.
    """
    assert duree_planner_s() >= duree_tentative_llm_s() * 2


def test_session_de_fond_contient_le_planner_avec_marge():
    """Il doit rester du temps pour le DAG une fois le plan produit."""
    assert duree_session_fond_s() > duree_planner_s()
    # Marge utile, pas symbolique : au moins autant de temps que la planification.
    assert duree_session_fond_s() - duree_planner_s() >= duree_planner_s()


def test_surcharge_env_respectee(monkeypatch):
    monkeypatch.setenv("MOTEUR_PLANNER_TIMEOUT_S", "400")
    assert duree_planner_s() == 400.0


def test_surcharge_trop_courte_refusee(monkeypatch):
    """
    Une valeur inférieure à UNE tentative reproduirait exactement le bug.

    On la refuse au lieu de l'appliquer : un réglage qui neutralise la cascade
    ne doit pas pouvoir être posé par inadvertance.
    """
    monkeypatch.setenv("MOTEUR_PLANNER_TIMEOUT_S", "30")
    assert duree_planner_s() >= duree_tentative_llm_s() * 2


def test_surcharge_illisible_ignoree(monkeypatch):
    monkeypatch.setenv("MOTEUR_PLANNER_TIMEOUT_S", "vite")
    assert duree_planner_s() >= duree_tentative_llm_s() * 2


def test_violation_detectee(monkeypatch):
    """Contre-épreuve : le vérificateur doit voir une hiérarchie cassée."""
    monkeypatch.setattr("core.fenetres_execution.duree_session_fond_s", lambda: 60.0)
    violations = verifier_emboitement()
    assert violations
    assert any("Session de fond" in v for v in violations)


def test_session_interactive_inchangee():
    """
    Arbitrage produit assumé : un humain attend, on ne l'allonge pas.

    Ce test documente la valeur autant qu'il la verrouille — la changer doit
    être un acte délibéré.
    """
    assert duree_session_interactive_s() == 120.0


@pytest.mark.parametrize("famille", ["gemini", "openai_compat", "anthropic"])
def test_duree_tentative_lue_depuis_les_timeouts_reels(famille):
    """La durée vient de `core/llm_timeouts`, pas d'une constante recopiée."""
    assert duree_tentative_llm_s(famille) > 0
