"""
core/llm/providers/base.py — Classe de base abstraite pour les LLM providers.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
import asyncio
import shutil
import subprocess
import sys
import time


def run_cli_command(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """[P0-1.3] Exécute une commande CLI SANS shell (anti-injection).

    Remplace les anciens appels subprocess avec shell activé : le prompt et les
    arguments sont passés comme éléments de liste (jamais interpolés dans une
    chaîne shell). Résout l'exécutable et gère le cas Windows où les `.cmd`/`.bat`
    ne sont pas lançables directement via CreateProcess (on passe alors par
    `cmd /c`, toujours sans shell, donc sans interprétation de chaîne).
    """
    argv = list(cmd)
    if argv:
        argv[0] = shutil.which(argv[0]) or argv[0]
        if sys.platform == "win32" and str(argv[0]).lower().endswith((".cmd", ".bat")):
            argv = ["cmd", "/c", *argv]
    kwargs.pop("shell", None)  # garde-fou : on force shell=False ci-dessous
    return subprocess.run(argv, shell=False, **kwargs)

class LLMProvider(ABC):
    """Interface standard pour tout fournisseur de modèle de langage (SRP)."""
    
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        pass
        
    @abstractmethod
    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        pass

    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """
        Génère une réponse en streaming token-par-token.
        
        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None}
        
        Les providers qui supportent nativement le streaming (DeepSeek, Gemini)
        le surchargent. Les autres utilisent ce fallback qui simule le streaming
        en découpant la réponse complète en chunks.
        """
        full_response = self.generate(system_prompt, user_prompt, **kwargs)
        if isinstance(full_response, dict):
            yield {"token": str(full_response), "done": True, "usage": None}
            return
        
        text = str(full_response)
        words = text.split(" ")
        for i, word in enumerate(words):
            is_last = (i == len(words) - 1)
            yield {
                "token": word + ("" if is_last else " "),
                "done": is_last,
                "usage": None,
            }
            time.sleep(0.02)

    async def generate_async(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """
        [D5] Variante asynchrone de generate().

        Fondation de la migration vers des providers async natifs : fournit une
        interface async UNIFORME à tous les providers. Par défaut, exécute la
        méthode synchrone generate() dans un thread (non bloquant pour l'event
        loop). Les providers à I/O réseau (OpenAICompatibleProvider) surchargent
        cette méthode avec un vrai client httpx.AsyncClient.
        """
        return await asyncio.to_thread(self.generate, system_prompt, user_prompt, **kwargs)

    async def generate_structured_async(
        self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """[D5] Variante asynchrone de generate_structured(). Même principe que generate_async."""
        return await asyncio.to_thread(self.generate_structured, system_prompt, user_prompt, schema, **kwargs)
