"""
tests/unit/test_cooldown_imputable.py — Qui est fautif, le provider ou la tâche ? (#T395)

Tout échec de tâche plaçait son provider en cooldown de 30 minutes. Or les
garde-fous de qualité ajoutés ces derniers jours (#T388 branche vide, #T392
branche identique à sa base, #T394 code qui ne parse pas) échouent en une
poignée de secondes — et le provider, lui, a parfaitement répondu.

Mesuré le 18/08 : trois tâches échouent en 1,4 s sur des contrôles de qualité,
leurs trois providers partent en cooldown, et le cycle s'arrête aussitôt sur
« aucun provider libre » — quatre tâches jamais tentées.

Depuis la sonde de santé (#T387), un provider réellement mort est déjà écarté en
tête de cycle : le cooldown n'est plus la première ligne de défense, et un
cooldown injustifié coûte bien plus cher qu'un provider ré-essayé une fois.
"""

import pytest

from core.budget_guard import echec_imputable_au_provider


# ── Échecs de la TÂCHE : le provider n'y est pour rien ──────────────────────

@pytest.mark.parametrize("erreur", [
    "Le pipeline s'est déclaré terminé mais la branche task/15_178 est identique "
    "à master : aucun fichier n'a été écrit dans /home/deck/…",
    "Le pipeline s'est déclaré terminé mais le code produit ne parse pas : "
    "services/ha_state_query.py: ligne 279 — unterminated string literal",
    "Le pipeline s'est terminé sur un échec.",
    "❌ 'analyze'",
    "",
])
def test_un_echec_de_qualite_ne_penalise_pas_le_provider(erreur):
    assert echec_imputable_au_provider(erreur) is False


# ── Échecs du PROVIDER : cooldown justifié ──────────────────────────────────

@pytest.mark.parametrize("erreur", [
    "ReadTimeout: la requête a dépassé 90s",
    "HTTPStatusError: Client error '429 Too Many Requests'",
    "HTTPStatusError: Client error '402 Payment Required' for url 'https://api.cerebras.ai/…'",
    "HTTPStatusError: Client error '404 Not Found' — model_archived",
    "ConnectTimeout: HTTPConnectionPool(host='192.168.1.10', port=1234)",
    "Server error '503 Service Unavailable'",
    "Authentication Fails, Your api key is invalid",
    "quota exceeded for this project",
])
def test_une_vraie_panne_declenche_le_cooldown(erreur):
    assert echec_imputable_au_provider(erreur) is True


def test_la_detection_ignore_la_casse():
    assert echec_imputable_au_provider("TIMEOUT après 90 secondes") is True
    assert echec_imputable_au_provider("Rate Limit atteint") is True


def test_le_defaut_est_de_ne_pas_penaliser():
    """
    Face à un message inconnu, on préfère ré-essayer un provider sain que se
    priver d'un fournisseur pendant 30 min. La sonde de santé rattrape les
    providers réellement morts au cycle suivant.
    """
    assert echec_imputable_au_provider("erreur inattendue quelque part") is False
    assert echec_imputable_au_provider(None) is False
