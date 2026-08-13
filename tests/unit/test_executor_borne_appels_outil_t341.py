"""
tests/unit/test_executor_borne_appels_outil_t341.py — un tour emballé ne fait
plus 2 181 appels d'outil (#T341).

Mesuré le 12/08 en production, session `chat_aa88ff7bed` : la question
« quelle température fait-il dans le salon ? » a produit 2 181 détections
d'appel pour 2 tours (1 500 en une seconde), 150 exécutions réelles et 144 Ko
de réponse HTTP finale. Le seul garde-fou existant était le rate limit PAR
SESSION du ToolRegistry (`_TOOL_RATE_LIMITS["default"] = 150`) : les 2 031
appels au-delà recevaient « Erreur rate_limit », renvoyée au modèle, et la
boucle continuait.

Ce correctif ajoute, dans `agents/executor.py`, deux garde-fous PAR TOUR :
  • une déduplication sur (nom d'outil + arguments exacts) — jamais le nom
    seul : `get_state('salon')` puis `get_state('chambre')` sont deux appels
    légitimes ;
  • une borne haute `MAX_TOOL_CALLS_PER_TURN = 20` pour les appels distincts
    en rafale.
Chaque appel écarté reçoit un message `role: "tool"` qui informe le modèle
(l'API exige de toute façon une réponse pour chaque tool_call du message
assistant), le log nomme le nombre écarté, et la boucle se poursuit : le
résultat du premier appel reste valide, la demande ne part pas en erreur.

Les tests pilotent la vraie boucle `_execute_react_loop` : seul
`provider.generate_async` est mocké, et `registry.execute` compte les
exécutions réelles. Aucun réseau, aucun LLM réel.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.executor import MAX_TOOL_CALLS_PER_TURN, ExecutorAgent


def _agent(compteur_executions: list[int]) -> tuple[ExecutorAgent, MagicMock]:
    """ExecutorAgent minimal : passerelle et registre mockés, zéro réseau."""
    gateway = MagicMock()
    gateway.get_provider_for_tier.return_value = ("modele-de-test", MagicMock())
    registry = MagicMock()
    registry.get_all_schemas.return_value = []

    async def _exec(name, kwargs, agent_name=None):
        compteur_executions[0] += 1
        return "ok"

    registry.execute = AsyncMock(side_effect=_exec)
    agent = ExecutorAgent(llm_gateway=gateway, tool_registry=registry)
    agent._sandbox = None
    return agent, registry


def _tool_call(func_name: str, arguments: dict, idx: int) -> dict:
    """Un tool_call au format renvoyé par les providers (API OpenAI)."""
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {
            "name": func_name,
            "arguments": str(arguments).replace("'", '"'),
        },
    }


def _reponse_avec_tool_calls(tool_calls: list[dict]) -> dict:
    return {"content": "", "tool_calls": tool_calls}


def _messages_tool(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["role"] == "tool"]


# ── Le cas de production (échoue sur master) ─────────────────────────────────

@pytest.mark.asyncio
async def test_500_appels_identiques_une_seule_execution(caplog):
    """Le cas exact de `chat_aa88ff7bed` : 500 doublons → 1 exécution."""
    caplog.set_level(logging.WARNING, logger="agents.executor")
    compteur = [0]
    agent, registry = _agent(compteur)

    # 500 tool_calls IDENTIQUES (nom + arguments), comme le modèle emballé.
    tool_calls = [
        _tool_call("get_account_status", {"provider": "ha-custom"}, i)
        for i in range(500)
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "La température du salon est de 21 °C.",
    ])

    messages: list[dict] = []
    last_results, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="quelle température fait-il dans le salon ?",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    # 1 seule exécution réelle (et non 500, ni 150 plafonnés par la session).
    assert compteur[0] == 1, f"exécutions réelles : {compteur[0]}"
    assert registry.execute.await_count == 1

    # Un message `tool` par tool_call (l'API l'exige), 499 qui informent.
    outils = _messages_tool(messages)
    assert len(outils) == 500, f"{len(outils)} messages tool"
    non_executes = [m for m in outils if "non exécuté" in m["content"]]
    assert len(non_executes) == 499, len(non_executes)

    # Le modèle est informé : raison (doublon) ET nombre écarté.
    assert "doublon exact" in non_executes[0]["content"]
    assert "499 appel(s) écarté(s)" in non_executes[-1]["content"]
    assert all(m["tool_call_id"].startswith("call_") for m in non_executes)

    # Le journal nomme le nombre écarté et la ventilation.
    recap = [r for r in caplog.records if "écarté(s) ce tour" in r.getMessage()]
    assert recap, "aucun log récapitulatif du nombre écarté"
    assert "499 appel(s) d'outil écarté(s) ce tour" in recap[-1].getMessage()
    assert "499 doublon(s)" in recap[-1].getMessage()

    # Le tour emballé ne fait pas échouer la demande : l'agent continue et
    # rédige sa réponse au tour suivant.
    assert tool_executed is True
    assert last_error is None
    assert final_text == "La température du salon est de 21 °C."
    assert last_results == ["Résultat 'get_account_status' : ok"]


# ── La non-régression : les appels multiples légitimes passent ───────────────

@pytest.mark.asyncio
async def test_5_appels_distincts_5_executions():
    """get_state('salon') puis get_state('chambre') sont deux appels légitimes."""
    compteur = [0]
    agent, registry = _agent(compteur)

    pieces = ["salon", "chambre", "cuisine", "bureau", "sdb"]
    tool_calls = [
        _tool_call("get_state", {"entity_id": f"temperature.{piece}"}, i)
        for i, piece in enumerate(pieces)
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Voici les températures.",
    ])

    messages: list[dict] = []
    last_results, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="quelles sont les températures de chaque pièce ?",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    assert compteur[0] == 5, f"exécutions réelles : {compteur[0]}"
    assert len(_messages_tool(messages)) == 5
    assert all("non exécuté" not in m["content"] for m in _messages_tool(messages))
    assert last_error is None
    assert final_text == "Voici les températures."
    assert len(last_results) == 5


# ── La borne haute : distincts en rafale, au-delà de 20 ──────────────────────

@pytest.mark.asyncio
async def test_25_appels_distincts_la_borne_garde_20(caplog):
    """Des appels distincts en rafale sont bornés, jamais tronqués en silence."""
    caplog.set_level(logging.WARNING, logger="agents.executor")
    compteur = [0]
    agent, _ = _agent(compteur)

    tool_calls = [
        _tool_call("lire_entite", {"entity_id": f"entity.{i}"}, i)
        for i in range(25)
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Terminé.",
    ])

    messages: list[dict] = []
    _, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="lis 25 entités",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    assert compteur[0] == MAX_TOOL_CALLS_PER_TURN, compteur[0]
    non_executes = [m for m in _messages_tool(messages) if "non exécuté" in m["content"]]
    assert len(non_executes) == 5, len(non_executes)
    assert "borne de 20 appels d'outil par tour atteinte" in non_executes[0]["content"]
    recap = [r for r in caplog.records if "écarté(s) ce tour" in r.getMessage()]
    assert recap and "5 appel(s) d'outil écarté(s) ce tour" in recap[-1].getMessage()
    assert tool_executed is True
    assert last_error is None
    assert final_text == "Terminé."
