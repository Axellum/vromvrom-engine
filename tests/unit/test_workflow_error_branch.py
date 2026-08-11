"""
tests/unit/test_workflow_error_branch.py — Branche d'échec du Workflow-as-Code (#T219).

Second volet de #T219. Le premier volet (traversée des nœuds `condition` dans
`WorkflowBridge.get_next_agents`) savait déjà router un `status="error"` vers la
sortie `out-false`, mais AUCUN appelant ne l'atteignait avec ce statut :
`Engine._run_agent_loop` sortait de la boucle (`break`) dès `update.status ==
"error"`, avant le bloc qui consulte le graphe. Toute branche d'échec dessinée
dans l'éditeur HMI était donc du code mort en production.

Ces tests verrouillent les deux moitiés du comportement attendu :
- une branche d'échec **explicitement dessinée** (`out-false`) est empruntée ;
- une simple arête « toujours active » ne suffit PAS à dévier du fail-fast
  historique (sinon le moteur enchaînerait sur l'agent suivant à chaque erreur).
"""

import asyncio
import os

import pytest

from core.state import GlobalState, StateUpdate, TaskPayload
from core.workflow_bridge import WorkflowBridge, _normalize_workflow
from core.workflow_executor import WorkflowExecutor

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_JSON = os.path.join(_REPO, "workflows", "Default.json")


def _bridge_from(data: dict) -> WorkflowBridge:
    """Construit un WorkflowBridge sur un workflow en mémoire (sans fichier)."""
    bridge = WorkflowBridge(workflow_path="/nonexistent/for-tests.json")
    bridge._cache = _normalize_workflow(data)
    return bridge


def _workflow_avec_branche_echec() -> dict:
    """reviewer → Succès? → [Fin (out-true) | planner (out-false)]."""
    return {
        "nodes": [
            {"id": "n-rev", "type": "agent", "agentName": "reviewer"},
            {"id": "n-cond", "type": "condition", "agentName": None, "label": "Succès ?"},
            {"id": "n-end", "type": "end", "agentName": None},
            {"id": "n-heal", "type": "agent", "agentName": "planner"},
        ],
        "connections": [
            {"from": "n-rev", "fromPort": "out", "to": "n-cond"},
            {"from": "n-cond", "fromPort": "out-true", "to": "n-end"},
            {"from": "n-cond", "fromPort": "out-false", "to": "n-heal"},
        ],
    }


def _workflow_sans_branche_echec() -> dict:
    """executor → reviewer par une seule arête « toujours active » (out)."""
    return {
        "nodes": [
            {"id": "n-exec", "type": "agent", "agentName": "executor"},
            {"id": "n-rev", "type": "agent", "agentName": "reviewer"},
        ],
        "connections": [{"from": "n-exec", "fromPort": "out", "to": "n-rev"}],
    }


# ──────────────────────────────────────────────────────────────────
# Volet 1 — résolution (WorkflowBridge)
# ──────────────────────────────────────────────────────────────────

def test_branche_echec_explicite_retenue():
    bridge = _bridge_from(_workflow_avec_branche_echec())
    assert bridge.get_next_agents("reviewer", "error", require_explicit_condition=True) == ["planner"]


def test_arete_toujours_active_rejetee_en_mode_explicite():
    """Une arête sans condition ne doit pas suffire à dévier du fail-fast."""
    bridge = _bridge_from(_workflow_sans_branche_echec())
    assert bridge.get_next_agents("executor", "error") == ["reviewer"]
    assert bridge.get_next_agents("executor", "error", require_explicit_condition=True) == []


def test_mode_explicite_naffecte_pas_le_chemin_succes():
    """Non-régression : le comportement par défaut reste inchangé."""
    bridge = _bridge_from(_workflow_avec_branche_echec())
    assert bridge.get_next_agents("reviewer", "success") == []
    assert bridge.get_next_agents("reviewer", "error") == ["planner"]


def test_cible_atteignable_par_deux_chemins_dont_un_explicite():
    """La cible est rejetée via l'arête « toujours active » mais retenue via
    l'arête out-false : le marquage anti-cycle ne doit pas la condamner."""
    data = {
        "nodes": [
            {"id": "n-a", "type": "agent", "agentName": "executor"},
            {"id": "c-1", "type": "condition", "agentName": None},
            {"id": "n-b", "type": "agent", "agentName": "planner"},
        ],
        "connections": [
            # Chemin direct, non conditionné → rejeté en mode explicite
            {"from": "n-a", "fromPort": "out", "to": "n-b"},
            # Chemin conditionné passant par le nœud condition → accepté
            {"from": "n-a", "fromPort": "out", "to": "c-1"},
            {"from": "c-1", "fromPort": "out-false", "to": "n-b"},
        ],
    }
    bridge = _bridge_from(data)
    assert bridge.get_next_agents("executor", "error", require_explicit_condition=True) == ["planner"]


def test_workflow_canonique_fail_fast_preserve():
    """workflows/Default.json : l'Executor n'a qu'une sortie « out » vers le
    Reviewer — une erreur d'Executor doit continuer à couper la session."""
    bridge = WorkflowBridge(workflow_path=_DEFAULT_JSON)
    assert bridge.get_next_agents("executor", "error", require_explicit_condition=True) == []
    # Le Reviewer, lui, a bien une branche d'échec dessinée (node-7 → node-9).
    assert bridge.get_next_agents("reviewer", "error", require_explicit_condition=True) == ["planner"]


# ──────────────────────────────────────────────────────────────────
# Volet 2 — consommation (Engine._run_agent_loop)
# ──────────────────────────────────────────────────────────────────

class _AgentEnErreur:
    """Agent qui échoue systématiquement."""

    def __init__(self, name: str):
        self.name = name

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        return StateUpdate(
            agent_name=self.name,
            status="error",
            result_data=None,
            error_message=f"Échec simulé de {self.name}",
            metadata={},
        )


class _BudgetPermissif:
    def check(self):
        return None


def _engine_de_test(workflow: dict, agent_name: str):
    """Engine réel, mais avec workflow en mémoire, DAG et checkpoint neutralisés."""
    from core.engine import Engine

    engine = Engine(session_id="t219_error_branch")
    engine.state = GlobalState(session_id="t219_error_branch")
    engine.state.current_payload = TaskPayload(task_objective="objectif de test")
    engine.agents = {agent_name: _AgentEnErreur(agent_name)}
    engine._workflow_executor = WorkflowExecutor(bridge=_bridge_from(workflow))

    appels: list = []

    async def _fake_dag(dag_tasks, initial_payload, max_session_tokens, budget):
        appels.append(list(dag_tasks))
        return {}, False

    engine._handle_dag_execution = _fake_dag
    engine._checkpoint_mgr.save = lambda state: None
    return engine, appels


def _lancer(engine):
    return asyncio.run(
        engine._run_sequential_agents(
            initial_payload=TaskPayload(task_objective="objectif de test"),
            starting_agent=next(iter(engine.agents)),
            budget=_BudgetPermissif(),
            max_session_tokens=100_000,
            _lf=None,
        )
    )


def test_engine_emprunte_la_branche_echec_du_graphe():
    """#T219 : une erreur d'agent doit désormais atteindre la branche out-false."""
    engine, appels = _engine_de_test(_workflow_avec_branche_echec(), "reviewer")
    has_error, _ = _lancer(engine)

    assert len(appels) == 1, "la branche d'échec du workflow n'a pas été exécutée"
    cibles = [t.metadata.get("target_agent") for t in appels[0]]
    assert cibles == ["planner"]
    assert has_error is False, "le DAG correctif a réussi : la session ne doit pas être en erreur"


def test_message_derreur_transmis_a_la_branche_de_recuperation():
    """Sans le motif de l'échec, l'agent de récupération corrigerait à l'aveugle
    (`result_data` vaut None sur un StateUpdate en erreur)."""
    engine, appels = _engine_de_test(_workflow_avec_branche_echec(), "reviewer")
    _lancer(engine)

    contexte = appels[0][0].relevant_context
    assert "Échec simulé de reviewer" in contexte


def test_engine_fail_fast_sans_branche_echec_dessinee():
    """Non-régression : sans sortie conditionnée par l'échec, on coupe comme avant."""
    engine, appels = _engine_de_test(_workflow_sans_branche_echec(), "executor")
    has_error, _ = _lancer(engine)

    assert appels == [], "aucune transition ne doit être injectée sans branche explicite"
    assert has_error is True


def test_engine_propage_lechec_du_dag_correctif():
    """Si la branche d'échec échoue à son tour, la session reste en erreur."""
    engine, appels = _engine_de_test(_workflow_avec_branche_echec(), "reviewer")

    async def _fake_dag_ko(dag_tasks, initial_payload, max_session_tokens, budget):
        appels.append(list(dag_tasks))
        return {}, True

    engine._handle_dag_execution = _fake_dag_ko
    has_error, _ = _lancer(engine)

    assert len(appels) == 1
    assert has_error is True


@pytest.mark.parametrize("statut_agent", ["error"])
def test_engine_emet_levenement_workflow_error_branch(statut_agent):
    """L'IHM doit pouvoir tracer la déviation (évènement SSE dédié)."""
    engine, _ = _engine_de_test(_workflow_avec_branche_echec(), "reviewer")
    evenements: list = []

    async def _on_event(nom, payload):
        evenements.append((nom, payload))

    engine.on_event = _on_event
    _lancer(engine)

    noms = [nom for nom, _ in evenements]
    assert "workflow_error_branch" in noms
    payload = dict(evenements[noms.index("workflow_error_branch")][1])
    assert payload["source_agent"] == "reviewer"
    assert payload["targets"] == ["planner"]
