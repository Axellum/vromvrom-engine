"""
tests/unit/test_workflow_bridge_conditions.py — Traversée des nœuds condition (#T219).

Les nœuds de type 'condition' (ex. "Succès ?") n'ont jamais d'agentName, dans
aucun schéma : avant le fix, get_next_agents() ne pouvait résoudre AUCUNE
transition passant par un nœud condition (vérifié contre workflows/Default.json,
le workflow canonique). Le statut de l'agent d'origine est désormais propagé au
travers du nœud condition vers ses arêtes out-true/out-false.
"""

import os

from core.workflow_bridge import WorkflowBridge, _normalize_workflow

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_JSON = os.path.join(_REPO, "workflows", "Default.json")


def _bridge_from(data: dict) -> WorkflowBridge:
    """Construit un WorkflowBridge sur un workflow en mémoire (sans fichier)."""
    bridge = WorkflowBridge(workflow_path="/nonexistent/for-tests.json")
    bridge._cache = _normalize_workflow(data)
    return bridge


def _mini_workflow() -> dict:
    """Reviewer → Succès? → [Fin | planner], ancien schéma (fromPort)."""
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


def test_condition_traversee_vers_self_healing():
    bridge = _bridge_from(_mini_workflow())
    assert bridge.get_next_agents("reviewer", "error") == ["planner"]


def test_condition_traversee_vers_fin_sans_agent():
    """Branche succès → nœud end sans agent : aucune cible (fin propre)."""
    bridge = _bridge_from(_mini_workflow())
    assert bridge.get_next_agents("reviewer", "success") == []


def test_transition_directe_sans_condition_inchangee():
    data = {
        "nodes": [
            {"id": "n-exec", "type": "agent", "agentName": "executor"},
            {"id": "n-rev", "type": "agent", "agentName": "reviewer"},
        ],
        "connections": [{"from": "n-exec", "fromPort": "out", "to": "n-rev"}],
    }
    bridge = _bridge_from(data)
    assert bridge.get_next_agents("executor", "success") == ["reviewer"]


def test_cycle_de_conditions_ne_boucle_pas():
    """Deux nœuds condition qui se pointent mutuellement : garde anti-cycle."""
    data = {
        "nodes": [
            {"id": "n-a", "type": "agent", "agentName": "executor"},
            {"id": "c-1", "type": "condition", "agentName": None},
            {"id": "c-2", "type": "condition", "agentName": None},
            {"id": "n-b", "type": "agent", "agentName": "reviewer"},
        ],
        "connections": [
            {"from": "n-a", "fromPort": "out", "to": "c-1"},
            {"from": "c-1", "fromPort": "out", "to": "c-2"},
            {"from": "c-2", "fromPort": "out", "to": "c-1"},  # cycle
            {"from": "c-2", "fromPort": "out-true", "to": "n-b"},
        ],
    }
    bridge = _bridge_from(data)
    assert bridge.get_next_agents("executor", "success") == ["reviewer"]


def test_nouveau_schema_react_flow_traverse_aussi():
    """Même graphe au nouveau schéma edges/source/target/label + data.agent."""
    data = {
        "nodes": [
            {"id": "n-rev", "type": "agent", "data": {"agent": "reviewer"}},
            {"id": "n-cond", "type": "condition", "data": {"agent": "?"}},
            {"id": "n-heal", "type": "agent", "data": {"agent": "planner"}},
        ],
        "edges": [
            {"source": "n-rev", "target": "n-cond", "label": "out"},
            {"source": "n-cond", "target": "n-heal", "label": "out-false"},
        ],
    }
    bridge = _bridge_from(data)
    assert bridge.get_next_agents("reviewer", "error") == ["planner"]


def test_workflow_canonique_default_json():
    """Contre le vrai fichier workflows/Default.json : les deux branches du
    nœud "Succès ?" (node-7) se résolvent enfin (#T219, avant : [] et [])."""
    bridge = WorkflowBridge(workflow_path=_DEFAULT_JSON)
    # Reviewer (node-10) → Succès? (node-7) → Self-Healing (node-9, planner)
    assert bridge.get_next_agents("reviewer", "error") == ["planner"]
    # Branche succès → Résultat Final (node-8, end) : aucune cible
    assert bridge.get_next_agents("reviewer", "success") == []
    # Transition directe existante non affectée
    assert bridge.get_next_agents("executor", "success") == ["reviewer"]
