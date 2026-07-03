"""
plugins/example_weather/agent.py — Agent météo exemple pour le système de plugins.

Démontre comment créer un agent custom chargé automatiquement par le PluginRegistry.
Hérite de BaseAgent et implémente invoke() pour récupérer la météo.
"""

from agents.base_agent import BaseAgent
from core.state import TaskPayload, StateUpdate


class WeatherAgent(BaseAgent):
    """Agent plugin exemple qui fournit des informations météo."""

    NAME = "weather_agent"
    SYSTEM_PROMPT = (
        "Tu es un agent spécialisé dans les informations météorologiques. "
        "Tu utilises l'outil call_api pour interroger les APIs de météo "
        "et tu retournes un résumé clair des prévisions."
    )

    def __init__(self, **kwargs):
        # BaseAgent attend (name, system_prompt) ; on ignore les kwargs
        # additionnels (llm_gateway, tool_registry...) fournis par le registre.
        super().__init__(name=self.NAME, system_prompt=self.SYSTEM_PROMPT)
        self.default_city = "Paris"

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        """
        Traite une requête météo.
        
        Ce plugin exemple retourne un message statique.
        En production, il utiliserait call_api pour interroger
        une vraie API météo (OpenWeatherMap, etc.)
        """
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=(
                f"🌤️ Plugin météo activé. "
                f"Requête : '{payload.task_objective}'. "
                f"(Ce plugin est un exemple — connectez-le à une vraie API météo)."
            ),
            metadata={"plugin": "example_weather"},
        )
