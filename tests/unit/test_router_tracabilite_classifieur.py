"""
tests/unit/test_router_tracabilite_classifieur.py — le classifieur s'étiquette (#T312).

Mesuré le 11/08 sur une campagne de 8 demandes réelles (`POST /api/execute`) :
**5 lignes de `token_usage` sur 39 (13 %) n'avaient NI `agent_name` NI
`session_id`** — exactement une par demande passée par le slow-path LLM du
routeur (`llm_classifier_used=1` dans `routing_decisions`).

C'était le dernier consommateur anonyme du chemin interactif. `#T296` avait
classé cette absence comme légitime (« routeur, classificateur, script direct »)
mais `#T308` a depuis étiqueté tous les autres consommateurs hors agent pour la
raison inverse : ils consomment réellement, et anonymement. Le classifieur tourne
sur toute requête dont les mots-clés ne tranchent pas — soit 5 des 8 demandes
mesurées.

Vérifié en réel après correction, session `TEST_T312` :
    ('gemma-4-31b', 'router', 'TEST_T312', 348, 50)
là où la même ligne portait auparavant (modèle, None, None).
"""

from unittest.mock import MagicMock

import pytest

from core.agent_trace import lire_agent_courant, poser_agent_courant, restaurer_agent_courant
from core.router import Router


class _ProviderMouchard:
    """Provider factice : capture l'étiquette d'agent vue au moment de l'appel."""

    def __init__(self):
        self.agent_vu = "<jamais appelé>"
        self.session_vue = "<jamais appelé>"

    async def generate_structured_async(self, system_prompt, user_prompt, schema, **kwargs):
        # C'est ICI que la ligne de token_usage sera écrite en production :
        # l'étiquette doit être posée à cet instant précis.
        self.agent_vu = lire_agent_courant()
        self.session_vue = kwargs.get("session_id", "<absent>")
        return {"category": "analysis", "complexity": "simple",
                "target_agent": "executor", "confidence": 0.9}


def _routeur(provider):
    gateway = MagicMock()
    gateway.get_provider_for_tier.return_value = ("modele-leger-de-test", provider)
    return Router(llm_gateway=gateway)


# ── Le cas de production (échoue sur master) ─────────────────────────────────

@pytest.mark.asyncio
async def test_le_classifieur_s_etiquette_router():
    """La ligne de consommation doit dire « router », pas NULL."""
    provider = _ProviderMouchard()
    await _routeur(provider)._llm_classify("une requête sans mot-clé tranchant",
                                           session_id="sess_42")
    assert provider.agent_vu == "router"


@pytest.mark.asyncio
async def test_le_classifieur_transmet_sa_session():
    """Sans `session_id`, la dépense n'est rattachable à aucune demande (#T299)."""
    provider = _ProviderMouchard()
    await _routeur(provider)._llm_classify("une requête sans mot-clé tranchant",
                                           session_id="sess_42")
    assert provider.session_vue == "sess_42"


# ── Ce qui ne doit pas régresser ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l_etiquette_de_l_appelant_est_restauree():
    """
    Le routeur est invoqué depuis des chemins déjà étiquetés (fast-path vocal,
    outils). Si « router » débordait après coup, la suite serait facturée au
    mauvais consommateur — une étiquette fausse coûte plus cher qu'une absente
    (#T294).
    """
    jeton = poser_agent_courant("vocal_tools")
    try:
        provider = _ProviderMouchard()
        await _routeur(provider)._llm_classify("une requête", session_id="sess_42")
        assert provider.agent_vu == "router"
        assert lire_agent_courant() == "vocal_tools"
    finally:
        restaurer_agent_courant(jeton)


@pytest.mark.asyncio
async def test_sans_session_l_appel_reste_etiquete():
    """Un appel hors session garde son étiquette d'agent : les deux sont distincts."""
    provider = _ProviderMouchard()
    await _routeur(provider)._llm_classify("une requête")
    assert provider.agent_vu == "router"
    assert provider.session_vue == ""


@pytest.mark.asyncio
async def test_sans_gateway_pas_d_appel():
    """Garde-fou existant : aucun gateway, aucun appel, aucune exception."""
    routeur = Router(llm_gateway=None)
    assert await routeur._llm_classify("une requête") is None
