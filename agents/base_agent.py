from abc import ABC, abstractmethod
from core.state import TaskPayload, StateUpdate

class BaseAgent(ABC):
    """
    Classe abstraite fondamentale (Single Responsibility Principle).
    Définit l'interface standard pour tous les agents du moteur.
    """
    
    def __init__(self, name: str, system_prompt: str):
        """
        Initialise l'agent avec son nom unique et son prompt système (règles métier).
        """
        self.name = name
        self.system_prompt = system_prompt
        
    @abstractmethod
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        """
        Méthode principale d'exécution.
        Prend un Payload (contexte isolé) et retourne un Update (delta d'état).
        Doit être implémentée par chaque agent spécialisé.
        """
        pass
