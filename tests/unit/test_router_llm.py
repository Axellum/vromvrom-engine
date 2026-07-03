"""
test_router_llm.py — Tests du slow path LLM du Router hybride.

Vérifie :
- Le slow path est activé quand aucun mot-clé ne matche (max_score < 0.05)
- Le LLM-classifier retourne une catégorie valide avec confiance suffisante
- Le fallback vers le Planner si la confiance est trop basse
- Le routeur fonctionne sans gateway (mode dégradé)
"""

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.router import Router


class TestRouterLLMSlowPath:
    """Tests du slow path LLM-classifier."""

    def _make_router_with_llm(self, llm_response: dict):
        """Factory : crée un Router avec un LLM mocké qui retourne llm_response."""
        router = Router.__new__(Router)
        router.default_agent = "planner"
        router.rag_engine = None
        router.config = {}
        
        # Mock du gateway LLM
        mock_provider = MagicMock()
        del mock_provider.generate_structured_async
        mock_provider.generate_structured.return_value = llm_response
        
        mock_gateway = MagicMock()
        mock_gateway.get_provider_for_tier.return_value = ("mock-local", mock_provider)
        router.llm_gateway = mock_gateway
        
        # Mock du ContextLoader et mémoires
        router.context_loader = MagicMock()
        router.context_loader.load_all.return_value = None
        router.context_loader.reload_if_stale.return_value = None
        router.context_loader.get_context_for_categories.return_value = ""
        router.episode_store = MagicMock()
        del router.episode_store.query_relevant_episodes_async
        router.episode_store.query_relevant_episodes.return_value = ""
        router.fact_store = MagicMock()
        del router.fact_store.get_facts_for_context_async
        router.fact_store.get_facts_for_context.return_value = ""
        
        # Catégories standard
        router.categories = {
            "casual_chat": {"keywords": ["bonjour", "salut"], "weight": 1.0},
            "home_assistant": {"keywords": ["lumière", "volet"], "weight": 1.5},
            "code_generation": {"keywords": ["code", "python"], "weight": 1.2},
            "database": {"keywords": ["sqlite", "sql"], "weight": 1.4},
            "files": {"keywords": ["fichier"], "weight": 1.0},
            "analysis": {"keywords": ["analyse", "audit"], "weight": 1.2},
        }
        return router

    async def test_llm_classify_high_confidence(self):
        """Quand le LLM classifie avec haute confiance, la catégorie est adoptée."""
        router = self._make_router_with_llm({
            "category": "home_assistant",
            "complexity": "simple",
            "target_agent": "ha_agent",
            "confidence": 0.92,
        })
        
        # Prompt sans aucun mot-clé reconnu → déclenche le slow path
        payload, agent = await router.analyze_request("mets le chauffage à 22 degrés")
        # Le LLM devrait classifier en home_assistant
        assert payload.metadata["dominant_category"] == "home_assistant"

    async def test_llm_classify_low_confidence_fallback(self):
        """Quand la confiance LLM est trop basse, le routeur délègue au Planner."""
        router = self._make_router_with_llm({
            "category": "code_generation",
            "complexity": "complex",
            "target_agent": "executor",
            "confidence": 0.3,  # En dessous du seuil MIN_LLM_CONFIDENCE
        })
        
        payload, agent = await router.analyze_request("fais quelque chose avec le machin")
        # Confiance insuffisante → default agent (planner) 
        assert agent == "planner"

    async def test_llm_classify_invalid_category(self):
        """Si le LLM retourne une catégorie inconnue, le routeur ignore le résultat."""
        router = self._make_router_with_llm({
            "category": "categorie_inventee",
            "complexity": "simple",
            "target_agent": "executor",
            "confidence": 0.95,
        })
        
        payload, agent = await router.analyze_request("truc bizarre que personne ne dit")
        # Catégorie inconnue → défaut vers le planner
        assert agent == "planner"

    async def test_no_llm_gateway_degrades_gracefully(self):
        """Sans gateway LLM, le routeur fonctionne en mode mots-clés uniquement."""
        router = Router.__new__(Router)
        router.default_agent = "planner"
        router.rag_engine = None
        router.llm_gateway = None  # Pas de gateway
        router.config = {}
        
        router.context_loader = MagicMock()
        router.context_loader.load_all.return_value = None
        router.context_loader.reload_if_stale.return_value = None
        router.context_loader.get_context_for_categories.return_value = ""
        router.episode_store = MagicMock()
        router.episode_store.query_relevant_episodes.return_value = ""
        router.fact_store = MagicMock()
        router.fact_store.get_facts_for_context.return_value = ""
        router.categories = {
            "casual_chat": {"keywords": ["bonjour"], "weight": 1.0},
            "home_assistant": {"keywords": ["lumière"], "weight": 1.5},
            "code_generation": {"keywords": ["code"], "weight": 1.2},
            "database": {"keywords": ["sqlite"], "weight": 1.4},
            "files": {"keywords": ["fichier"], "weight": 1.0},
            "analysis": {"keywords": ["analyse"], "weight": 1.2},
        }
        
        # Requête sans match → pas de crash, défaut vers planner
        payload, agent = await router.analyze_request("xyz totalement inconnu")
        assert agent == "planner"

    async def test_llm_exception_does_not_crash(self):
        """Si l'appel LLM lève une exception, le routeur continue sans crash."""
        router = self._make_router_with_llm({})  # Réponse vide
        
        # Forcer une exception dans le gateway
        router.llm_gateway.get_provider_for_tier.side_effect = Exception("Timeout LLM")
        
        # Ne doit pas crash
        payload, agent = await router.analyze_request("quelque chose d'ambigu")
        assert agent == "planner"  # Fallback vers défaut

    async def test_fast_path_skips_llm(self):
        """Quand un mot-clé est détecté, le LLM n'est jamais appelé."""
        router = self._make_router_with_llm({
            "category": "analysis",
            "confidence": 0.99,
            "complexity": "complex",
            "target_agent": "planner",
        })
        
        # "Bonjour" matche casual_chat → fast path, pas de LLM
        payload, agent = await router.analyze_request("Bonjour")
        assert payload.metadata["dominant_category"] == "casual_chat"
        # Le LLM ne devrait pas avoir été appelé
        router.llm_gateway.get_provider_for_tier.assert_not_called()
