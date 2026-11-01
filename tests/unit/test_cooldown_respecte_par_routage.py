"""
tests/unit/test_cooldown_respecte_par_routage.py — Le cooldown vaut aussi pour le routage par historique (#T403).

Deux garde-fous ecrits a des moments differents, qui ne se connaissaient pas :

- `mark_provider_failed` (cooldown 30 min) n'etait consulte que par
  `BudgetGuard.get_available_provider`, la cascade de cout ;
- depuis #T393, c'est `_choisir_provider_tache` qui elit le provider dans le cas
  nominal, par taux de reussite historique, et il ne tombe sur la cascade qu'en
  SECOURS.

Le cooldown etait donc contourne dans le chemin le plus frequent.

Mesure le 18/08 a 22h05 en production : la tache #43 prend un
« 429 Too Many Requests » sur `gemini-free`, le provider part en cooldown, et la
tache #44 recoit `gemini-free` a la seconde suivante. Le cycle meurt sur « tous
les providers ont echoue » -- avec quatre providers sains sous la main.
"""

import pytest

from agents.dreamer_agent import _choisir_provider_tache
from core.budget_guard import BudgetGuard, mark_provider_failed, provider_en_cooldown


@pytest.fixture(autouse=True)
def _cooldowns_propres():
    """Le registre de cooldown est global au module : on le vide autour du test."""
    from core import budget_guard
    avant = dict(budget_guard._provider_cooldown)
    budget_guard._provider_cooldown.clear()
    yield
    budget_guard._provider_cooldown.clear()
    budget_guard._provider_cooldown.update(avant)


@pytest.mark.asyncio
async def test_un_provider_en_cooldown_nest_pas_reelu_par_lhistorique():
    sains = {"gemini-free", "cohere-free", "mistral-free", "deepseek-free"}

    mark_provider_failed("gemini-free")
    assert provider_en_cooldown("gemini-free") is True

    for _ in range(6):
        provider, raison, _exclusions = await _choisir_provider_tache(
            "generique", BudgetGuard(), sains, set())
        assert provider != "gemini-free", (
            f"provider en cooldown reelu par « {raison} » — c'est le defaut du 18/08")


@pytest.mark.asyncio
async def test_les_providers_sains_restent_eligibles():
    sains = {"gemini-free", "cohere-free"}
    mark_provider_failed("gemini-free")
    provider, _raison, _exc = await _choisir_provider_tache(
        "generique", BudgetGuard(), sains, set())
    assert provider == "cohere-free"


@pytest.mark.asyncio
async def test_tous_en_cooldown_ne_rend_aucun_provider():
    """Mieux vaut ne rien tenter que rejouer le provider qui vient de tomber."""
    sains = {"gemini-free", "cohere-free"}
    for p in sains:
        mark_provider_failed(p)
    provider, _raison, _exc = await _choisir_provider_tache(
        "generique", BudgetGuard(), sains, set())
    assert provider is None
