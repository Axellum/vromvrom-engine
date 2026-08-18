"""
tests/unit/test_executor_json_illisible_t343.py — un JSON d'arguments illisible
n'exécute plus l'outil à vide (#T343-A).

Défauts corrigés dans `agents/executor.py` (bloc #T341) :
  (a) quand `json.loads(kwargs_str)` échouait, `kwargs = {}` et l'appel partait
      à l'exécution SANS ses arguments, en silence — `set_temperature()` sans
      température, `write_file()` sans chemin. Le message d'erreur construit
      était écrasé sans condition par `_execute_tool_with_retry`.
  (b) la clé de déduplication devenait `(nom, "{}")` pour TOUS les appels au
      JSON invalide : `json.dumps({})` ne lève jamais, donc l'`except` censé
      basculer sur la chaîne brute était inatteignable. Deux malformés
      DIFFÉRENTS étaient vus comme un doublon exact.

Correctif : un appel non décodable n'est JAMAIS exécuté, reçoit un message
`role: "tool"` qui nomme l'erreur de décodage (l'API exige une réponse pour
chaque tool_call du message assistant), et sa clé de déduplication repose sur
la chaîne BRUTE pour que deux malformés différents restent deux appels
différents. La borne MAX_TOOL_CALLS_PER_TURN et la déduplication nominale ne
sont pas touchées (`tests/unit/test_executor_borne_appels_outil_t341.py`
passe inchangé).

Les tests pilotent la vraie boucle `_execute_react_loop` : seul
`provider.generate_async` est mocké, et `registry.execute` compte les
exécutions réelles. Aucun réseau, aucun LLM réel.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.executor import ExecutorAgent


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


def _tool_call_brut(func_name: str, arguments_bruts: str, idx: int) -> dict:
    """Un tool_call dont `arguments` est une chaîne brute (pas forcément du JSON)."""
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": func_name, "arguments": arguments_bruts},
    }


def _reponse_avec_tool_calls(tool_calls: list[dict]) -> dict:
    return {"content": "", "tool_calls": tool_calls}


def _messages_tool(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["role"] == "tool"]


# ── Test central : un JSON invalide n'exécute pas l'outil ────────────────────

@pytest.mark.asyncio
async def test_json_invalide_outil_non_execute_et_message_nomme_le_decodage():
    """Un tool_call aux arguments illisibles ne part pas à l'exécution à vide."""
    compteur = [0]
    agent, registry = _agent(compteur)

    # JSON franchement invalide : pas d'accolade fermante, valeur non JSON.
    arguments_bruts = '{"entity_id": "temperature.salon"'
    tool_calls = [_tool_call_brut("get_state", arguments_bruts, 0)]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Terminé.",
    ])

    messages: list[dict] = []
    _, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="quelle est la température du salon ?",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    # L'outil n'est PAS exécuté : zéro appel réel, zéro kwargs vide.
    assert compteur[0] == 0, f"exécutions réelles : {compteur[0]}"
    assert registry.execute.await_count == 0

    # Le message `tool` renvoyé au modèle nomme l'erreur de décodage.
    outils = _messages_tool(messages)
    assert len(outils) == 1, f"{len(outils)} messages tool"
    contenu = outils[0]["content"]
    assert "arguments JSON invalides" in contenu
    assert "outil non exécuté" in contenu
    assert "Expecting" in contenu or "Unterminated" in contenu or "JSON" in contenu

    # Le tour reste valide : l'agent répond au tour suivant.
    assert tool_executed is False
    assert last_error is None
    assert final_text == "Terminé."


# ── Deux malformés différents ne sont pas des doublons l'un de l'autre ───────

@pytest.mark.asyncio
async def test_deux_json_invalides_differents_ne_sont_pas_des_doublons():
    """Deux malformés DIFFÉRENTS restent deux appels, aucun n'est un doublon."""
    compteur = [0]
    agent, registry = _agent(compteur)

    # Deux chaînes brutes différentes, toutes deux invalides.
    tool_calls = [
        _tool_call_brut("get_state", '{"entity_id": "temperature.salon"', 0),
        _tool_call_brut("get_state", '{"entity_id": "temperature.chambre"', 1),
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Terminé.",
    ])

    messages: list[dict] = []
    _, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="quelles températures ?",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    # Aucun des deux n'est exécuté, et aucun n'est traité comme doublon de
    # l'autre : chacun reçoit son propre message d'erreur de décodage.
    assert compteur[0] == 0
    outils = _messages_tool(messages)
    assert len(outils) == 2, f"{len(outils)} messages tool"
    assert all("non exécuté" in m["content"] for m in outils)
    assert all("doublon" not in m["content"] for m in outils), [
        m["content"] for m in outils
    ]
    assert all("arguments JSON invalides" in m["content"] for m in outils)

    # Les deux tool_call_id distincts ont chacun leur réponse (règle API).
    assert {m["tool_call_id"] for m in outils} == {"call_0", "call_1"}
    assert tool_executed is False
    assert last_error is None
    assert final_text == "Terminé."


# ── Le résumé du tour ne confond pas JSON illisible et borne ────────────────

@pytest.mark.asyncio
async def test_log_distingue_json_illisible_de_la_borne(caplog):
    """Un tour avec UNIQUEMENT des JSON invalides ne s'annonce pas comme un
    dépassement de MAX_TOOL_CALLS_PER_TURN : le compteur « au-delà de la
    borne » ne compte que les rejets de borne (revue Bugbot PR #301)."""
    compteur = [0]
    agent, registry = _agent(compteur)

    tool_calls = [
        _tool_call_brut("get_state", '{"entity_id": "temperature.salon"', 0),
        _tool_call_brut("get_state", '{pas du json', 1),
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Terminé.",
    ])

    messages: list[dict] = []
    with caplog.at_level(logging.WARNING, logger="agents.executor"):
        await agent._execute_react_loop(
            messages=messages,
            user_prompt="températures",
            provider=provider,
            tools_schemas=[],
            max_turns=2,
            session_id=None,
            use_search_grounding=False,
        )

    assert compteur[0] == 0
    resume = [r.getMessage() for r in caplog.records
              if "appel(s) d'outil écarté(s) ce tour" in r.getMessage()]
    assert len(resume) == 1, resume
    assert "2 JSON illisible(s)" in resume[0], resume[0]
    assert "0 au-delà de la borne" in resume[0], resume[0]


# ── Non-régression : le nominal et les garde-fous ne bougent pas ─────────────

@pytest.mark.asyncio
async def test_appel_valide_et_malforme_coexistent():
    """Un appel valide s'exécute, un appel malformé du même tour ne l'est pas."""
    compteur = [0]
    agent, registry = _agent(compteur)

    tool_calls = [
        _tool_call_brut("get_state", '{"entity_id": "temperature.salon"}', 0),
        _tool_call_brut("get_state", '{pas du json', 1),
    ]
    provider = MagicMock()
    provider.generate_async = AsyncMock(side_effect=[
        _reponse_avec_tool_calls(tool_calls),
        "Terminé.",
    ])

    messages: list[dict] = []
    last_results, final_text, tool_executed, last_error = await agent._execute_react_loop(
        messages=messages,
        user_prompt="température du salon",
        provider=provider,
        tools_schemas=[],
        max_turns=2,
        session_id=None,
        use_search_grounding=False,
    )

    # Le valide est exécuté (1), le malformé ne l'est pas.
    assert compteur[0] == 1, f"exécutions réelles : {compteur[0]}"
    outils = _messages_tool(messages)
    assert len(outils) == 2
    non_executes = [m for m in outils if "non exécuté" in m["content"]]
    assert len(non_executes) == 1
    assert "arguments JSON invalides" in non_executes[0]["content"]
    assert last_results == ["Résultat 'get_state' : ok"]
    assert tool_executed is True
    assert last_error is None
    assert final_text == "Terminé."
