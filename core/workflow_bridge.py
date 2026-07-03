"""
core/workflow_bridge.py — Pont entre l'éditeur de workflow HMI et le moteur Python.

Lit le fichier agents_workflows.json sauvegardé par le HMI et expose
la configuration des agents au Planner, à la Factory et au gui_server.
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Chemin par défaut du fichier workflow (à côté de config.json, dans moteur_agents/)
_DEFAULT_WORKFLOW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agents_workflows.json"
)

# Agents built-in (toujours enregistrés, non suppressibles)
BUILTIN_AGENTS = {"planner", "executor", "antigravity_agent", "ha_agent"}

# Agents non-ciblables par le Planner (utilisés en interne uniquement)
NON_TARGETABLE_AGENTS = {"planner", "router"}


class WorkflowBridge:
    """
    Pont entre le fichier agents_workflows.json (HMI) et le moteur Python.
    
    Responsabilités :
    - Lire le workflow sérialisé par le frontend
    - Extraire la liste des agents enregistrés et leurs tiers
    - Générer l'enum dynamique pour le schéma JSON du Planner
    - Injecter les descriptions d'agents dans le prompt du Planner
    """

    def __init__(self, workflow_path: str = _DEFAULT_WORKFLOW_PATH):
        self.workflow_path = workflow_path
        self._cache: Optional[dict] = None

    def load_workflow(self) -> dict:
        """Charge et met en cache le fichier de workflow."""
        try:
            if os.path.exists(self.workflow_path):
                with open(self.workflow_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cache = data
                    logger.info(f"[WORKFLOW_BRIDGE] Workflow chargé : {len(data.get('nodes', []))} nœuds, {len(data.get('connections', []))} connexions.")
                    return data
        except Exception as e:
            logger.warning(f"[WORKFLOW_BRIDGE] Impossible de charger le workflow : {e}")
        
        self._cache = {"nodes": [], "connections": []}
        return self._cache

    def reload(self) -> dict:
        """Force le rechargement du workflow (invalidation du cache)."""
        self._cache = None
        return self.load_workflow()

    def _ensure_loaded(self) -> dict:
        """S'assure que le workflow est chargé."""
        if self._cache is None:
            self.load_workflow()
        return self._cache

    def get_agent_nodes(self) -> list[dict]:
        """Retourne tous les nœuds de type 'agent' du workflow."""
        data = self._ensure_loaded()
        return [n for n in data.get("nodes", []) if n.get("type") == "agent"]

    def get_registered_agent_names(self) -> list[str]:
        """
        Retourne la liste unique des noms d'agents présents dans le workflow.
        Inclut les built-in + les custom.
        """
        names = set()
        for node in self.get_agent_nodes():
            name = (node.get("agentName") or "").strip()
            if name and name not in NON_TARGETABLE_AGENTS:
                names.add(name)
        # S'assurer que les built-in ciblables sont toujours présents
        for builtin in BUILTIN_AGENTS:
            if builtin not in NON_TARGETABLE_AGENTS:
                names.add(builtin)
        return sorted(names)

    def get_agent_tiers(self) -> dict:
        """
        Retourne un dictionnaire {agent_name: tier} depuis le workflow.
        Si un agent apparaît plusieurs fois, le dernier tier lu est utilisé.
        """
        tiers = {}
        for node in self.get_agent_nodes():
            name = (node.get("agentName") or "").strip()
            tier = node.get("tier", "automatique")
            if name:
                tiers[name] = tier
        return tiers

    def get_custom_agent_names(self) -> list[str]:
        """Retourne uniquement les agents custom (pas dans BUILTIN_AGENTS)."""
        all_agents = self.get_registered_agent_names()
        return [a for a in all_agents if a not in BUILTIN_AGENTS]

    def get_custom_agents_config(self) -> list[dict]:
        """
        Retourne la configuration des agents custom avec leurs métadonnées
        (nom, tier, label, etc.) pour instanciation dynamique.
        """
        configs = []
        seen = set()
        for node in self.get_agent_nodes():
            name = (node.get("agentName") or "").strip()
            if name and name not in BUILTIN_AGENTS and name not in NON_TARGETABLE_AGENTS and name not in seen:
                seen.add(name)
                configs.append({
                    "name": name,
                    "label": node.get("label", name),
                    "tier": node.get("tier", "automatique"),
                    "icon": node.get("icon", "🤖"),
                })
        return configs

    def get_planner_enum(self) -> list[str]:
        """
        Retourne la liste des target_agents utilisables dans le schéma JSON du Planner.
        Exclut les agents non-ciblables (planner, router).
        """
        return self.get_registered_agent_names()

    def build_agents_description(self) -> str:
        """
        Génère une description textuelle de chaque agent disponible
        pour enrichir le system prompt du Planner.
        """
        descriptions = {
            "executor": "Agent exécuteur généraliste. Boucle ReAct avec outils (read_file, write_file, run_terminal_command, call_api, outils MCP). Pour les actions techniques simples.",
            "antigravity_agent": "Agent expert pour les tâches de raisonnement avancé, design LVGL, architecture complexe. Tier fort obligatoire.",
            "ha_agent": "Agent spécialisé Home Assistant & SQLite Recorder. Pour les opérations domotiques (services HA, requêtes SQL, pilotage appareils).",
        }

        lines = ["## Agents disponibles pour le plan :"]
        for name in self.get_registered_agent_names():
            desc = descriptions.get(name, f"Agent custom '{name}' (hérite des outils Executor).")
            lines.append(f"- **{name}** : {desc}")

        return "\n".join(lines)

    def inject_into_planner_prompt(self, base_prompt: str) -> str:
        """
        Enrichit le system prompt du Planner avec la liste dynamique des agents.
        Insérée avant la section 'FORMAT DE SORTIE'.
        """
        agents_section = f"\n\n{self.build_agents_description()}\n"
        
        # Insertion avant 'FORMAT DE SORTIE' si présent, sinon à la fin
        marker = "FORMAT DE SORTIE"
        if marker in base_prompt:
            idx = base_prompt.index(marker)
            return base_prompt[:idx] + agents_section + "\n" + base_prompt[idx:]
        else:
            return base_prompt + agents_section

    def get_connections(self) -> list[dict]:
        """Retourne toutes les connexions du workflow avec leurs conditions."""
        data = self._ensure_loaded()
        return data.get("connections", [])

    def get_next_agents(self, current_node_name: str, status: str) -> list[str]:
        """
        Résout les transitions conditionnelles du graphe de workflow.
        
        Logique de résolution :
        1. Cherche les connexions sortantes depuis le nœud courant
        2. Filtre par condition : 'success', 'error', ou null (toujours)
        3. Retourne les noms d'agents cibles correspondants
        
        Args:
            current_node_name: Nom de l'agent courant (ex: "executor")
            status: Résultat de l'agent ("success" ou "error")
        
        Returns:
            Liste des agents cibles. Vide si aucune connexion trouvée.
        """
        data = self._ensure_loaded()
        connections = data.get("connections", [])
        nodes = {n.get("id"): n for n in data.get("nodes", [])}
        
        # Trouver les nœuds correspondant à l'agent courant
        current_node_ids = [
            n_id for n_id, n in nodes.items()
            if (n.get("agentName") or "").strip() == current_node_name
        ]
        
        if not current_node_ids:
            return []
        
        # Résoudre les transitions conditionnelles
        next_agents = []
        for conn in connections:
            from_id = conn.get("from")
            if from_id not in current_node_ids:
                continue
            
            condition = conn.get("condition")  # None, "success", "error"
            to_id = conn.get("to")
            
            # La connexion est active si :
            # - Pas de condition (toujours active)
            # - La condition match le statut courant
            if condition is None or condition == status:
                target_node = nodes.get(to_id, {})
                target_name = (target_node.get("agentName") or "").strip()
                if target_name and target_name not in next_agents:
                    next_agents.append(target_name)
        
        if next_agents:
            logger.info(
                f"[WORKFLOW_BRIDGE] Transition conditionnelle : "
                f"{current_node_name} ({status}) → {next_agents}"
            )
        
        return next_agents

