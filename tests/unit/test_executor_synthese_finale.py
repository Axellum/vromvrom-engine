"""
tests/unit/test_executor_synthese_finale.py — la synthèse du modèle prime (#T311).

Mesuré le 11/08 sur une campagne de 8 demandes réelles adressées au moteur
(`POST /api/execute`, serveur lancé depuis un worktree seedé avec `.env`,
`models_registry.db` et `google_token.json`).

Scénario « Qu'est-ce que j'ai de prévu demain ? », session `chat_4d9bbfac5a` :
  tour 1 → `get_calendar_events`  (15 événements réels remontés)
  tour 2 → `list_calendars`
  tour 3 → `get_calendar_events`
  tour 4 → `get_tasks`
  tour 5 → aucun appel d'outil, 131 tokens de complétion → sortie de boucle

Le modèle avait donc rédigé sa réponse. L'utilisateur, lui, a reçu :

    Résultat 'get_calendar_events' : 📅 15 événement(s) à venir :
      1. Repos | 2026-08-10 → 2026-08-12
      2. Travail Optique | 2026-08-12T10:15:00+02:00 → ...

c'est-à-dire le calendrier brut, non filtré sur « demain ». La donnée arrivait
jusqu'au moteur et était jetée sur la dernière ligne d'`execute()` :
`result_data` préférait inconditionnellement l'écho des outils à la réponse.

Même symptôme mesuré le même jour sur les scénarios mail, système (`Get-Process`
répété) et comparaison de fichiers — soit 4 des 8 demandes de la campagne.

Les tests pilotent le vrai `ExecutorAgent.execute()` : seule la boucle ReAct est
remplacée (elle seule fait du réseau), pour que la ligne d'assemblage corrigée
soit réellement exercée.
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.executor import ExecutorAgent
from core.state import TaskPayload

# Le calendrier réel remonté le 11/08, tronqué aux deux premières entrées.
ECHO_CALENDRIER = (
    "Résultat 'get_calendar_events' : 📅 15 événement(s) à venir :\n"
    "  1. Repos | 2026-08-10 → 2026-08-12\n"
    "  2. Travail Optique | 2026-08-12T10:15:00+02:00 → 2026-08-12T20:15:00+02:00"
)
REPONSE_REDIGEE = "Demain (12/08) tu travailles à l'optique de 10h15 à 20h15."


def _agent() -> ExecutorAgent:
    """ExecutorAgent minimal : ni passerelle réelle, ni outils, ni réseau."""
    gateway = MagicMock()
    gateway.get_provider_for_tier.return_value = ("modele-de-test", MagicMock())
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    agent = ExecutorAgent(llm_gateway=gateway, tool_registry=registry)
    agent._sandbox = None
    return agent


async def _execute(last_results: list[str], final_text_response: str):
    """Joue `execute()` en neutralisant la seule partie qui parle au réseau."""
    agent = _agent()
    payload = TaskPayload(
        task_objective="Qu'est-ce que j'ai de prévu demain ?",
        relevant_context="Nouvelle requête utilisateur (Initiale).",
        # Neutralise le micro-cycle de revue locale (Phase 4) : il instancie un
        # ReviewerAgent qui appellerait le réseau. Ce n'est pas l'objet du test,
        # et il n'altère pas la ligne d'assemblage que l'on vérifie ici.
        metadata={"is_local_review_correction": True},
    )

    async def _faux_react(*args, **kwargs):
        # (résultats d'outils, réponse rédigée, un outil a tourné, pas d'erreur)
        return last_results, final_text_response, bool(last_results), None

    with patch.object(ExecutorAgent, "_execute_react_loop", side_effect=_faux_react), \
         patch("core.llm_gateway.load_config", return_value={}):
        return await agent.invoke(payload)


# ── Le cas de production (échoue sur master) ─────────────────────────────────

@pytest.mark.asyncio
async def test_la_reponse_redigee_prime_sur_l_echo_des_outils():
    """Le cas exact de `chat_4d9bbfac5a` : la synthèse ne doit plus être jetée."""
    maj = await _execute([ECHO_CALENDRIER, "Résultat 'get_tasks' : Aucune tâche."],
                         REPONSE_REDIGEE)

    assert maj.result_data == REPONSE_REDIGEE
    assert not maj.result_data.startswith("Résultat '"), (
        "L'utilisateur reçoit encore l'écho brut des outils au lieu de sa réponse."
    )


@pytest.mark.asyncio
async def test_la_trace_des_outils_n_est_pas_perdue():
    """La synthèse remplace l'écho dans la réponse, pas dans les métadonnées."""
    maj = await _execute([ECHO_CALENDRIER], REPONSE_REDIGEE)
    assert maj.metadata.get("tool_trace") == [ECHO_CALENDRIER]


# ── Ce qui ne doit pas régresser ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sans_reponse_redigee_l_echo_reste_le_seul_contenu():
    """Tours épuisés : le modèle n'a rien rédigé, l'écho est tout ce qu'on a."""
    echo = "Résultat 'run_terminal_command' : Get-Date -Format 'yyyy-MM-dd'"
    maj = await _execute([echo], "")
    assert maj.result_data == echo


@pytest.mark.asyncio
async def test_une_reponse_vide_de_blancs_ne_masque_pas_l_echo():
    """Une complétion réduite à des espaces n'est pas une réponse."""
    echo = "Résultat 'read_file' : contenu"
    maj = await _execute([echo], "   \n  ")
    assert maj.result_data == echo


@pytest.mark.asyncio
async def test_reponse_redigee_seule_sans_outil():
    """Réponse conversationnelle pure : elle passe telle quelle, sans trace."""
    maj = await _execute([], "Bonjour, tout va bien.")
    assert maj.result_data == "Bonjour, tout va bien."
    assert "tool_trace" not in maj.metadata
