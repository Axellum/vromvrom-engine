"""
core/workflow_executor.py — Workflow-as-Code : exécution dynamique du DAG.

Le moteur lit le fichier Default.json (ou tout workflow actif) et exécute
les transitions conditionnelles définies graphiquement dans le HMI.

Principe :
    Au lieu que le Planner décide seul du routage, le WorkflowExecutor
    utilise les connexions du graphe (WorkflowBridge.get_next_agents)
    pour résoudre les transitions après chaque agent :
    
    1. L'agent X termine avec status="success" ou "error"
    2. WorkflowExecutor consulte les connexions : X → Y (si success), X → Z (si error)
    3. Les agents cibles sont retournés pour exécution par le DAGRunner
    
Cela permet de définir des workflows complexes (branches, conditions, boucles)
directement depuis l'éditeur visuel du HMI, sans modifier le code Python.

Intégration dans engine.py :
    Le WorkflowExecutor est utilisé comme résolveur de transitions APRÈS
    la boucle séquentielle d'agents, en complément du DAGRunner existant.
"""

import logging

from core.workflow_bridge import WorkflowBridge
from core.state import TaskPayload

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """
    Exécute dynamiquement les transitions définies dans le workflow JSON.
    
    Responsabilités :
    - Résoudre la prochaine étape après chaque agent (via WorkflowBridge)
    - Construire les TaskPayload pour les agents cibles
    - Gérer les branches conditionnelles (success/error → agents différents)
    """

    def __init__(self, bridge: WorkflowBridge = None):
        self._bridge = bridge or WorkflowBridge()
        self._bridge._ensure_loaded()

    def resolve_next_tasks(
        self,
        current_agent: str,
        status: str,
        result_data: str = "",
        session_id: str = "",
    ) -> list[TaskPayload]:
        """
        Résout les transitions conditionnelles du workflow et retourne
        les TaskPayload correspondants.
        
        Args:
            current_agent: Nom de l'agent qui vient de terminer
            status: "success" ou "error"
            result_data: Résultat de l'agent (transmis comme contexte)
            session_id: ID de la session en cours
            
        Returns:
            Liste de TaskPayload à exécuter (peut être vide si pas de transition)
        """
        next_agents = self._bridge.get_next_agents(current_agent, status)

        if not next_agents:
            return []

        tasks = []
        for i, agent_name in enumerate(next_agents):
            task = TaskPayload(
                task_objective=f"Transition workflow : {current_agent} ({status}) → {agent_name}",
                relevant_context=str(result_data)[:500] if result_data else "",
                metadata={
                    "target_agent": agent_name,
                    "session_id": session_id,
                    "workflow_transition": True,
                    "source_agent": current_agent,
                    "source_status": status,
                    "stage_id": 1,
                    "task_id": f"wf_{current_agent}_{agent_name}_{i}",
                },
                task_id=f"wf_{current_agent}_{agent_name}_{i}",
            )
            tasks.append(task)
            logger.info(
                f"[WORKFLOW_EXEC] Transition : {current_agent} ({status}) → {agent_name}"
            )

        return tasks

    def has_transitions(self, agent_name: str) -> bool:
        """Vérifie si un agent a des transitions sortantes dans le workflow."""
        connections = self._bridge.get_connections()
        data = self._bridge._ensure_loaded()
        nodes = {n.get("id"): n for n in data.get("nodes", [])}

        node_ids = [
            n_id for n_id, n in nodes.items()
            if (n.get("agentName") or "").strip() == agent_name
        ]

        return any(
            conn.get("from") in node_ids
            for conn in connections
        )

    def get_workflow_summary(self) -> dict:
        """Retourne un résumé du workflow actif pour le debugging."""
        data = self._bridge._ensure_loaded()
        nodes = data.get("nodes", [])
        connections = data.get("connections", [])
        
        agents = [
            n.get("agentName", "?") 
            for n in nodes 
            if n.get("type") == "agent"
        ]
        
        transitions = []
        nodes_map = {n.get("id"): n for n in nodes}
        for conn in connections:
            from_node = nodes_map.get(conn.get("from"), {})
            to_node = nodes_map.get(conn.get("to"), {})
            transitions.append({
                "from": from_node.get("agentName", "?"),
                "to": to_node.get("agentName", "?"),
                "condition": conn.get("condition")
            })
        
        return {
            "agents": agents,
            "transitions": transitions,
            "total_nodes": len(nodes),
            "total_connections": len(connections)
        }
