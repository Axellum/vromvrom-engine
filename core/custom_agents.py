"""
core/custom_agents.py — Agents custom déclarés dans config.json (#T159).

Source de vérité de la section `custom_agents` de config.json, partagée par :
- core/factory.py et services/pipeline_service.py (enregistrement réel dans
  l'Engine — un seul helper pour ne pas recréer une divergence de sites, #T213) ;
- api/routes/agents_crud.py (CRUD IHM).

Un agent custom = un clone d'ExecutorAgent (boucle ReAct + outils) avec son
nom, son tier et éventuellement son prompt système propre. Complémentaire des
agents custom du WorkflowBridge (dessinés dans l'éditeur de workflow), qui
gardent leur mécanisme historique.
"""

import logging

logger = logging.getLogger(__name__)


def load_custom_agent_entries(config: dict, only_enabled: bool = False) -> list[dict]:
    """Entrées valides de config['custom_agents'] (dicts nommés uniquement)."""
    entries = [
        e for e in config.get("custom_agents", [])
        if isinstance(e, dict) and e.get("name")
    ]
    if only_enabled:
        entries = [e for e in entries if e.get("enabled", True)]
    return entries


def register_config_custom_agents(engine, gateway, tool_registry, config) -> int:
    """
    Instancie et enregistre dans l'Engine les agents custom activés de config.json.

    Ignore silencieusement les noms déjà enregistrés (priorité aux agents cœur
    et à ceux du WorkflowBridge, enregistrés avant). Retourne le nombre ajouté.
    """
    from agents.executor import ExecutorAgent

    added = 0
    for entry in load_custom_agent_entries(config, only_enabled=True):
        name = entry["name"]
        if name in getattr(engine, "agents", {}):
            continue
        try:
            agent = ExecutorAgent(
                llm_gateway=gateway,
                tool_registry=tool_registry,
                provider_name=entry.get("tier", "automatique"),
            )
            agent.name = name
            agent.system_prompt = entry.get("system_prompt") or (
                f"Tu es l'agent custom '{name}' ({entry.get('label', name)}).\n"
                f"Tu hérites de la boucle ReAct d'ExecutorAgent avec accès à tous les outils.\n"
                f"Exécute la tâche qui t'est assignée de manière rigoureuse et pédagogue."
            )
            engine.register_agent(agent)
            added += 1
            logger.info(f"[CUSTOM_AGENTS] Agent '{name}' enregistré (tier={entry.get('tier', 'automatique')}).")
        except Exception as e:
            logger.error(f"[CUSTOM_AGENTS] Échec de l'enregistrement de '{name}' : {e}")
    return added
