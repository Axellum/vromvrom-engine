"""
tests/unit/test_panne_infra_taches.py — Une panne d'infrastructure ne punit pas la tache (#T398).

Mesure en production le 18/08 : le .env du Deck perd ses cles a 19h09. A 19h00
la sonde comptait encore 4 providers sains (cohere-free, mistral-free,
gemini-free, deepseek-free) ; a 19h15 il n'en reste aucun, et cinq taches du
backlog passent en `failed` avec 0 token consomme -- punies pour une panne qui
n'est pas la leur. Six taches au total portaient ce verdict, chacune avec
retries=1 : deux pannes de plus et elles passaient en `abandoned` sans qu'un
seul modele les ait jamais lues.

Symetrique de #T395 (`echec_imputable_au_provider`, tests/unit/test_cooldown_imputable.py) :
celui-la protege le PROVIDER d'un echec de la tache, celui-ci protege la TACHE
d'un echec de l'infrastructure.
"""

import pytest

from core.budget_guard import echec_imputable_au_provider, panne_infrastructure

# Le message exact releve en base de production (backlog_tasks.error_message
# des taches 38, 42, 43, 44, 45 et 46, le 18/08 a 19h15).
MESSAGE_PROD = "Tous les providers configures ont echoue ou ont leurs circuits ouverts."


# -- Pannes d'infrastructure : la tache n'a jamais atteint un modele ---------

@pytest.mark.parametrize("erreur", [
    MESSAGE_PROD,
    "Tous les providers configures ont echoue ou ont leurs circuits ouverts.",
    "Aucun provider ne repond -- cycle annule.",
    "no provider available",
    "Aucun modele disponible pour le tier leger",
    # Le message REEL, avec ses accents : le piege de #T383 (marqueurs ecrits
    # sans accent, formulations produites avec) ne doit pas se rejouer ici.
    "Tous les providers configurés ont échoué ou ont leurs circuits ouverts.",
    "Aucun modèle disponible pour le tier léger",
])
def test_une_panne_dinfra_est_reconnue(erreur):
    assert panne_infrastructure(erreur) is True


# -- Tout le reste : la tache (ou le provider) est bien en cause ------------

@pytest.mark.parametrize("erreur", [
    "Le pipeline s'est declare termine mais la branche task/15_178 est identique a master",
    "le code produit ne parse pas : ligne 279 -- unterminated string literal",
    "Le pipeline s'est termine sur un echec.",
    "HTTPStatusError: Client error '429 Too Many Requests'",
    "ReadTimeout: la requete a depasse 90s",
    "",
    None,
])
def test_un_echec_ordinaire_nest_pas_une_panne_dinfra(erreur):
    assert panne_infrastructure(erreur) is False


def test_le_message_de_prod_netait_impute_ni_au_provider_ni_a_personne():
    """
    Avant ce correctif, le message de prod ne declenchait AUCUNE des deux
    branches connues : `echec_imputable_au_provider` le rendait False, donc il
    tombait dans « echec de la tache » -- la pire des trois lectures, celle qui
    brule un `retries`.
    """
    assert echec_imputable_au_provider(MESSAGE_PROD) is False
    assert panne_infrastructure(MESSAGE_PROD) is True


def test_les_deux_verdicts_sexcluent_sur_les_cas_reels():
    """Aucune erreur reelle ne doit etre a la fois panne d'infra et faute du provider."""
    cas_provider = [
        "HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.cohere.com'",
        "ConnectTimeout: HTTPConnectionPool(host='192.168.1.10', port=1234)",
        "HTTPStatusError: Client error '402 Payment Required'",
    ]
    for erreur in cas_provider:
        assert echec_imputable_au_provider(erreur) is True
        assert panne_infrastructure(erreur) is False
