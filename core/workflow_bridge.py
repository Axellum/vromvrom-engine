"""
core/workflow_bridge.py — Pont entre l'éditeur de workflow HMI et le moteur Python.

Lit le fichier agents_workflows.json sauvegardé par le HMI et expose
la configuration des agents au Planner, à la Factory et au gui_server.
"""

import json
import logging
import os

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


def _infer_condition(conn: dict) -> str | None:
    """[Audit 2026-07-09, #T211] Résout la condition d'une connexion, quel que soit
    le champ utilisé selon la version de l'éditeur HMI : `condition` (générique, si
    jamais présent), `fromPort` (ancien éditeur HMI v1 — ex. "out-true"/"out-false"),
    `label` (éditeur React Flow / ihm-v2 — même convention "out-true"/"out-false")."""
    condition = conn.get("condition")
    if condition is not None:
        return condition
    port = conn.get("fromPort") or conn.get("label") or ""
    if port == "out-true":
        return "success"
    if port == "out-false":
        return "error"
    return None  # "out" ou toute autre valeur = connexion toujours active


def _normalize_workflow(data: dict) -> dict:
    """[Audit 2026-07-09, #T211] Normalise un workflow — ancien schéma HMI v1
    (nodes[].agentName/tier en racine, connections[].from/to/fromPort) OU nouveau
    schéma React Flow de l'éditeur ihm-v2 (nodes[].data.agent/tier, edges[].source/
    target/label) — vers la forme interne attendue par le reste de ce module.
    Idempotent : un workflow déjà au format interne traverse sans changement.
    """
    normalized_nodes = []
    for node in data.get("nodes", []):
        node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
        agent_name = node.get("agentName") or node_data.get("agent") or ""
        if agent_name == "?":
            # Placeholder de l'éditeur React Flow pour "aucun agent assigné"
            # (nœuds départ/condition/fin) — équivalent du agentName: null legacy.
            agent_name = ""
        tier = node.get("tier") if node.get("tier") is not None else node_data.get("tier", "automatique")
        label = node.get("label") or node_data.get("label") or agent_name
        icon = node.get("icon") or node_data.get("icon") or "🤖"
        # [#T233] Permissions d'outils : lu dans data (schéma React Flow) ou en
        # racine du nœud (legacy), absent/null = tous les outils, [] = aucun.
        allowed_tools = (
            node.get("allowed_tools")
            if node.get("allowed_tools") is not None
            else node_data.get("allowed_tools")
        )
        normalized_nodes.append({
            **node, "agentName": agent_name, "tier": tier, "label": label,
            "icon": icon, "allowed_tools": allowed_tools,
        })

    raw_connections = data.get("connections")
    if raw_connections is None:
        raw_connections = data.get("edges", [])

    normalized_connections = [
        {
            **conn,
            "from": conn.get("from") or conn.get("source"),
            "to": conn.get("to") or conn.get("target"),
            "condition": _infer_condition(conn),
        }
        for conn in raw_connections
    ]

    return {**data, "nodes": normalized_nodes, "connections": normalized_connections}


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
        self._cache: dict | None = None

    def load_workflow(self) -> dict:
        """Charge et met en cache le fichier de workflow (normalisé, cf. _normalize_workflow)."""
        try:
            if os.path.exists(self.workflow_path):
                with open(self.workflow_path, encoding="utf-8") as f:
                    data = _normalize_workflow(json.load(f))
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
                    # [#T233] Permissions d'outils (absent/None = tous, [] = aucun)
                    "allowed_tools": node.get("allowed_tools"),
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

    def get_next_agents(
        self,
        current_node_name: str,
        status: str,
        require_explicit_condition: bool = False,
    ) -> list[str]:
        """
        Résout les transitions conditionnelles du graphe de workflow.

        Logique de résolution :
        1. Cherche les connexions sortantes depuis le nœud courant
        2. Filtre par condition : 'success', 'error', ou null (toujours)
        3. TRAVERSE les nœuds de type 'condition' (#T219) : ces nœuds n'ont
           jamais d'agentName (ex. node-7 "Succès ?" de Default.json) — avant ce
           fix, aucune transition passant PAR un nœud condition ne se résolvait,
           dans aucun schéma. Le statut de l'agent d'origine est propagé aux
           arêtes sortantes du nœud condition (out-true=success/out-false=error).
        4. Retourne les noms d'agents cibles correspondants (les nœuds end/start
           sans agent terminent la branche sans cible).

        [#T219] Constat d'architecture : le branchement
        Reviewer→Succès?→[Fin|Self-Healing] du workflow canonique double une
        logique déjà câblée en dur dans core/review_loop.py (post-DAG) et
        core/healing.py (par tâche). La résolution du graphe reste la seule
        voie pour les workflows custom dessinés dans l'éditeur HMI
        (Workflow-as-Code), où un nœud condition cassait silencieusement tout
        le routage aval.

        Args:
            current_node_name: Nom de l'agent courant (ex: "executor")
            status: Résultat de l'agent ("success" ou "error")
            require_explicit_condition: [#T219] si True, ne retient que les
                cibles atteintes en empruntant AU MOINS une arête dont la
                condition vaut explicitement `status` (ex. `out-false` pour une
                erreur). Les arêtes sans condition ("toujours active") ne
                suffisent alors plus à elles seules. Utilisé par le moteur sur
                le chemin d'erreur pour ne dévier du fail-fast que si l'auteur
                du graphe a réellement dessiné une branche d'échec.

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

        # Index des connexions sortantes par nœud source
        outgoing: dict = {}
        for conn in connections:
            outgoing.setdefault(conn.get("from"), []).append(conn)

        # Parcours en largeur depuis les nœuds courants, avec traversée des
        # nœuds condition et garde anti-cycle (un nœud n'est traversé qu'une
        # fois). Chaque élément de la frontière porte un drapeau « une arête
        # explicitement conditionnée a déjà été empruntée sur ce chemin ».
        next_agents: list[str] = []
        visited: set = set(current_node_ids)
        frontier: list = [(n_id, False) for n_id in current_node_ids]

        while frontier:
            node_id, seen_explicit = frontier.pop(0)
            for conn in outgoing.get(node_id, []):
                condition = conn.get("condition")  # None, "success", "error"
                # La connexion est active si :
                # - Pas de condition (toujours active)
                # - La condition match le statut courant
                if condition is not None and condition != status:
                    continue

                path_explicit = seen_explicit or condition == status
                to_id = conn.get("to")
                if to_id in visited:
                    continue

                target_node = nodes.get(to_id, {})
                target_name = (target_node.get("agentName") or "").strip()
                if target_name:
                    if require_explicit_condition and not path_explicit:
                        # Cible atteinte uniquement par des arêtes « toujours
                        # actives » : elle n'est pas retenue ici, mais reste
                        # atteignable par un autre chemin, réellement conditionné
                        # (d'où l'absence de marquage `visited`).
                        continue
                    visited.add(to_id)
                    if target_name not in next_agents:
                        next_agents.append(target_name)
                elif target_node.get("type") == "condition":
                    # [#T219] Nœud condition sans agent : continuer la résolution
                    # au travers, en propageant le statut de l'agent d'origine.
                    visited.add(to_id)
                    frontier.append((to_id, path_explicit))
                # Autres nœuds sans agent (end, start...) : fin de branche.

        if next_agents:
            logger.info(
                f"[WORKFLOW_BRIDGE] Transition conditionnelle : "
                f"{current_node_name} ({status}) → {next_agents}"
            )

        return next_agents

