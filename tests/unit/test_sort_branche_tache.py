"""
tests/unit/test_sort_branche_tache.py — la branche d'une tâche morte disparaît.

Depuis #484, une porte qualité ne détruit plus le travail : la branche est
conservée et poussée « pour qu'un autre clone reprenne la correction ». Juste,
mais la règle ne regardait que le message d'erreur, jamais le verdict.

Une tâche `abandoned` a brûlé ses trois tentatives : aucun rejeu ne viendra,
et « un autre clone reprendra » décrit quelque chose qui n'arrive jamais. Sa
branche était quand même poussée. Mesuré le 24/08 : **88 branches orphelines**
(74 sur moteur_agents, 14 sur le dépôt Tab5), toutes nées là.

Ces tests figent le tri à trois voies, et surtout la propriété de sûreté :
une tâche abandonnée nettoie SANS `git_rollback_checkpoint`, dont le
`clean -fd` détruirait les fichiers non suivis d'un dépôt à politique humaine
— le Tab5 travaille directement dans `00ProjetTab`, le dépôt d'Axel.
"""

import pytest

from agents.dreamer_agent import (
    SORT_CONSERVER,
    SORT_ROLLBACK,
    SORT_SUPPRIMER,
    _sort_de_la_branche,
)

# Messages réels relevés en base le 24/08.
PORTE_ARBORESCENCE = (
    "Le pipeline s'est déclaré terminé mais les fichiers livrés ne respectent "
    "pas l'arborescence du dépôt : '.qoder/wiki_publish' est posé à la racine"
)
PORTE_LIVRABLE = (
    "Le pipeline s'est déclaré terminé mais le livrable est insuffisant : "
    "tâche d'analyse sans rapport dans docs/analyses/*.md"
)
BRANCHE_IDENTIQUE = (
    "Le pipeline s'est déclaré terminé mais la branche task/52_1787294067 "
    "est identique à master : aucun fichier n'a changé"
)
PANNE_RESEAU = "HTTPSConnectionPool(host='open.bigmodel.cn', port=443): Read timed out."


@pytest.mark.parametrize("erreur", [PORTE_ARBORESCENCE, PORTE_LIVRABLE])
def test_un_failed_conserve_sa_branche(erreur):
    """Un rejeu est prévu : le travail reste disponible pour la correction."""
    assert _sort_de_la_branche(erreur, "failed") == SORT_CONSERVER


@pytest.mark.parametrize("erreur", [PORTE_ARBORESCENCE, PORTE_LIVRABLE])
def test_un_abandoned_supprime_sa_branche(erreur):
    """
    Le cas des 88 orphelines : plus aucune tentative, donc plus de branche.

    C'est LE test de non-régression — si `_sort_de_la_branche` cesse de
    regarder le statut, il tombe.
    """
    assert _sort_de_la_branche(erreur, "abandoned") == SORT_SUPPRIMER


def test_une_branche_vide_part_au_rollback():
    """Rien à garder : comportement historique inchangé."""
    assert _sort_de_la_branche(BRANCHE_IDENTIQUE, "failed") == SORT_ROLLBACK
    assert _sort_de_la_branche(BRANCHE_IDENTIQUE, "abandoned") == SORT_ROLLBACK


def test_une_panne_reseau_n_est_pas_une_porte_qualite():
    """
    Une panne d'infra ne produit pas de livrable à corriger.

    Symétrique de #T398 : l'infra ne doit pas être traitée comme un échec
    de la tâche, dans un sens comme dans l'autre.
    """
    assert _sort_de_la_branche(PANNE_RESEAU, "failed") == SORT_ROLLBACK


def test_le_rollback_reste_reserve_aux_cas_sans_travail():
    """
    Propriété de sûreté : `abandoned` ne doit JAMAIS router vers le rollback
    quand il y a du travail sur la branche.

    `git_rollback_checkpoint` fait `reset --hard` + `clean -fd`. Sur le Tab5
    (politique humaine, dépôt `00ProjetTab`), cela effacerait les fichiers non
    suivis d'Axel. La suppression de branche doit donc rester un chemin
    distinct — jamais un alias du rollback.
    """
    for erreur in (PORTE_ARBORESCENCE, PORTE_LIVRABLE):
        assert _sort_de_la_branche(erreur, "abandoned") != SORT_ROLLBACK
