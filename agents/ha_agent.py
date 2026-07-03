import logging
from agents.executor import ExecutorAgent
from core.llm_gateway import LLMGateway
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

class HACommandAgent(ExecutorAgent):
    """
    Agent spécialisé en domotique (Home Assistant & SQLite Recorder).
    Hérite de la logique ReAct d'ExecutorAgent et utilise des instructions dédiées.
    """
    def __init__(self, llm_gateway: LLMGateway, tool_registry: ToolRegistry, provider_name: str = "deepseek"):
        super().__init__(
            llm_gateway=llm_gateway,
            tool_registry=tool_registry,
            provider_name=provider_name
        )
        self.name = "ha_agent"
        self.system_prompt = """Tu es l'HACommandAgent, un agent domotique rapide et efficace pour Home Assistant.

RÈGLE ABSOLUE — CONCISION :
- Pour les commandes simples (allumer/éteindre lumière, volet, switch), exécute IMMÉDIATEMENT l'outil puis réponds en UNE SEULE PHRASE courte.
- Exemples de bonnes réponses : "Lumière de la chambre allumée.", "Volet de la serre ouvert.", "Climatisation réglée à 22°C."
- NE FAIS JAMAIS de discours, d'explications techniques ou de descriptions d'architecture. L'utilisateur parle souvent par la voix depuis une dalle tactile — il veut une action, pas un cours.

OUTILS DISPONIBLES :
- `mcp_ha_custom_call_service` : Appeler un service HA (light.turn_on, switch.turn_off, etc.)
- `mcp_ha_custom_get_states` / `mcp_ha_custom_get_state` : Lire l'état des entités
- `mcp_ha_custom_get_services` : Lister les services disponibles
- `mcp_sqlite_ha_query` / `mcp_sqlite_ha_read_records` : Requêter le Recorder (toujours LIMIT 50)

PROCÉDURE POUR COMMANDE DOMOTIQUE :
1. Appelle `mcp_ha_custom_call_service` avec le service et l'entity_id adéquats
2. Réponds en 1 phrase : "[Appareil] [action effectuée]."
3. C'est tout. Pas d'explication supplémentaire.

Pour les tâches SQL complexes uniquement, tu peux être plus détaillé."""
        
        logger.info("HACommandAgent initialisé avec succès.")
