"""test_planner_reponse_non_objet.py — #FIX 20/08.

Un provider peut renvoyer une LISTE au lieu de l'objet attendu par le
planner. Avant le correctif, `response_json.get("plan")` crashe sur
`'list' object has no attribute 'get'` — observé en prod sur la task_38 le
19/08/2026. Le planner doit lever une erreur CLAIRE, rejouable par le
pipeline (retry propre), jamais un AttributeError cryptique.
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

# Ajout du répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from agents.planner import PlannerAgent
from core.state import TaskPayload


def _lancer_planner(reponse):
    async def _generate_structured_async(system_prompt, user_prompt, schema, **kw):
        return reponse

    provider = MagicMock()
    provider.generate_structured_async = _generate_structured_async
    gateway = MagicMock()
    gateway.get_provider.return_value = provider
    gateway.get_provider_for_tier.return_value = ("tier-fort", provider)

    planner = PlannerAgent(llm_gateway=gateway, provider_name="fort")
    return asyncio.run(planner.invoke(TaskPayload(
        task_objective="objectif", metadata={"session_id": "s_liste"},
    )))


class TestPlannerReponseNonObjet:
    def test_liste_produit_erreur_claire(self):
        """Une liste à la place du dict → StateUpdate d'erreur explicite.

        Le planner convertit toute exception en StateUpdate(status="error") :
        c'est ce contrat que le pipeline consomme. L'important est que
        `error_message` porte la cause CLAIRE (réponse non-objet), et plus le
        crash cryptique `'list' object has no attribute 'get'`.
        """
        update = _lancer_planner([{"plan": [{"task_id": "t1"}]}])
        assert update.status == "error"
        assert "réponse non-objet" in update.error_message
        assert "list" in update.error_message

    def test_dict_normal_passe(self):
        """Le chemin nominal reste inchangé."""
        reponse = {
            "plan": [{"task_id": "t1", "objective": "Faire X",
                      "target_agent": "executor", "model_tier": "leger",
                      "depends_on": []}],
            "criteres_acceptation": [],
        }
        update = _lancer_planner(reponse)
        assert update.status == "success"
        assert "Plan DAG généré" in update.result_data
