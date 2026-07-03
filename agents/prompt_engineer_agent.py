import logging
import json
import asyncio
from core.state import TaskPayload, StateUpdate
from agents.base_agent import BaseAgent
from core.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

class PromptEngineerAgent(BaseAgent):
    """
    Agent spécialisé dans l'ingénierie de prompt.
    Prend une requête métier brute et retourne un prompt expert (Few-Shot, CoT, etc.)
    enrichi du contexte du système ou formaté pour une IA cible.
    """
    def __init__(self, llm_gateway: LLMGateway, provider_name: str = "fort"):
        super().__init__(
            name="prompt_engineer",
            system_prompt="""Tu es le PromptEngineerAgent, un expert absolu en ingénierie de prompt.
Ton rôle est de prendre la demande brouillonne de l'utilisateur et de construire un prompt parfait, hyper-structuré, 
destiné à être lu par un autre LLM (comme DeepSeek, Claude Opus ou Gemini).

Règles pour un bon prompt :
1. Définir un rôle clair (System Persona).
2. Fournir le contexte nécessaire de manière concise.
3. Utiliser des balises (ex: <instructions>, <context>) pour séparer les sections.
4. Demander à l'IA cible de réfléchir étape par étape (Chain-of-Thought) si nécessaire.
5. Définir explicitement le format de sortie attendu.

Retourne UNIQUEMENT le texte du prompt final optimisé, sans bavardage avant ni après, prêt à être copié-collé."""
        )
        self.gateway = llm_gateway
        self.provider_name = provider_name
        
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        from core.llm_gateway import load_config
        config = load_config()
        session_id = payload.metadata.get("session_id")
        
        # Détermination du fournisseur
        if self.provider_name in ["leger", "moyen", "fort", "automatique"]:
            _, provider = self.gateway.get_provider_for_tier(self.provider_name, config)
        else:
            try:
                provider = self.gateway.get_provider(self.provider_name)
            except ValueError:
                _, provider = self.gateway.get_provider_for_tier("fort", config)
                
        user_prompt = f"Objectif initial de l'utilisateur : {payload.task_objective}\nContexte fourni : {payload.relevant_context}\n\nGénère le prompt optimisé."
        
        logger.info("[PROMPT_ENGINEER] Génération d'un prompt hyper-optimisé...")
        
        try:
            # On laisse 60 secondes au modèle pour générer le prompt (un peu plus long si appel API complexe)
            response = await asyncio.wait_for(
                provider.generate_async(
                    system_prompt=self.system_prompt,
                    user_prompt=user_prompt,
                    session_id=session_id,
                ),
                timeout=60.0
            )
            
            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data=response,
                next_agent="END"
            )
            
        except Exception as e:
            logger.error(f"[PROMPT_ENGINEER] Échec de la génération : {e}")
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data=None,
                next_agent="END",
                error_message=str(e)
            )
