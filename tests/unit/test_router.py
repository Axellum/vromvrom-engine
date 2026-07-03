"""
test_router.py — Tests unitaires du Router de classification des requêtes.

Vérifie :
- La classification par mots-clés (catégories dominantes)
- Les courts-circuits déterministes HA (zero-LLM)
- Le routage des requêtes complexes vers le Planner
- L'injection de contexte 3-Layers
- La détection de la routine FIN DE SESSION
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.router import Router


class TestRouterClassification:
    """Tests de classification par mots-clés du Router."""

    def setup_method(self):
        """Initialise un Router avec les dépendances mockées."""
        self.router = Router.__new__(Router)
        self.router.default_agent = "planner"
        self.router.rag_engine = None
        self.router.llm_gateway = None
        self.router.config = {}
        
        # Mock du ContextLoader pour éviter de charger les fichiers réels
        from unittest.mock import MagicMock
        self.router.context_loader = MagicMock()
        self.router.context_loader.load_all.return_value = None
        self.router.context_loader.reload_if_stale.return_value = None
        self.router.context_loader.get_context_for_categories.return_value = ""
        
        # Mock des mémoires épisodique et sémantique
        self.router.episode_store = MagicMock()
        del self.router.episode_store.query_relevant_episodes_async
        self.router.episode_store.query_relevant_episodes.return_value = ""
        self.router.fact_store = MagicMock()
        del self.router.fact_store.get_facts_for_context_async
        self.router.fact_store.get_facts_for_context.return_value = ""
        
        # Initialiser les catégories (copie de router.py)
        self.router.categories = {
            "casual_chat": {
                "keywords": ["bonjour", "salut", "hello", "hi", "merci", "thanks"],
                "weight": 1.0
            },
            "home_assistant": {
                "keywords": ["lumière", "clim", "volet", "switch", "sensor", "esphome",
                             "tab5", "automation", "température", "humidité", "yeelight"],
                "weight": 1.5
            },
            "code_generation": {
                "keywords": ["code", "python", "javascript", "c++", "fonction", "classe",
                             "algorithme", "bug", "asyncio", "script", "refactoring"],
                "weight": 1.2
            },
            "database": {
                "keywords": ["sqlite", "bdd", "base de données", "table", "sql", "requête",
                             "select", "recorder"],
                "weight": 1.4
            },
            "files": {
                "keywords": ["fichier", "dossier", "directory", "lire", "écrire", "créer"],
                "weight": 1.0
            },
            "analysis": {
                "keywords": ["analyse", "audit", "rapport", "benchmark", "performance",
                             "optimisation", "token", "coût", "pricing"],
                "weight": 1.2
            },
        }

    # --- Tests de classification par catégorie ---

    async def test_classification_casual_chat(self):
        """Une salutation simple doit être classée en casual_chat."""
        payload, agent = await self.router.analyze_request("Bonjour, comment vas-tu ?")
        assert payload.metadata["dominant_category"] == "casual_chat"
        assert agent == "executor"  # Court-circuit direct

    async def test_classification_home_assistant(self):
        """Une requête domotique simple doit cibler le ha_agent."""
        payload, agent = await self.router.analyze_request("Quelle est la température du salon ?")
        assert payload.metadata["dominant_category"] == "home_assistant"

    async def test_classification_code_generation(self):
        """Une requête de code doit être classée en code_generation."""
        payload, agent = await self.router.analyze_request("Écris un script Python avec asyncio")
        assert payload.metadata["dominant_category"] == "code_generation"

    async def test_classification_database(self):
        """Une requête SQL doit être classée en database."""
        payload, agent = await self.router.analyze_request("Exécute une requête SELECT sur le recorder SQLite")
        assert payload.metadata["dominant_category"] == "database"

    async def test_classification_analysis(self):
        """Une requête d'audit doit être classée en analysis."""
        payload, agent = await self.router.analyze_request("Fais un audit de performance et optimisation des coûts")
        assert payload.metadata["dominant_category"] == "analysis"


class TestRouterDeterministicShortcuts:
    """Tests des courts-circuits déterministes HA (zero-LLM)."""

    def setup_method(self):
        """Initialise un Router avec les dépendances mockées."""
        self.router = Router.__new__(Router)
        self.router.default_agent = "planner"
        self.router.rag_engine = None
        self.router.llm_gateway = None
        self.router.config = {}
        
        from unittest.mock import MagicMock
        self.router.context_loader = MagicMock()
        self.router.context_loader.load_all.return_value = None
        self.router.context_loader.reload_if_stale.return_value = None
        self.router.context_loader.get_context_for_categories.return_value = ""
        self.router.episode_store = MagicMock()
        self.router.episode_store.query_relevant_episodes.return_value = ""
        self.router.fact_store = MagicMock()
        self.router.fact_store.get_facts_for_context.return_value = ""
        self.router.categories = {
            "casual_chat": {"keywords": ["bonjour", "salut"], "weight": 1.0},
            "home_assistant": {"keywords": ["lumière", "volet", "yeelight"], "weight": 1.5},
            "code_generation": {"keywords": ["code", "python"], "weight": 1.2},
            "database": {"keywords": ["sqlite", "sql", "recorder"], "weight": 1.4},
            "files": {"keywords": ["fichier", "dossier"], "weight": 1.0},
            "analysis": {"keywords": ["analyse", "audit"], "weight": 1.2},
        }
        self.router._ha_commands = [
            {"service": "light.turn_on", "entity_id": "light.lumiere", "phrases": ["allume la lumiere"]},
            {"service": "light.turn_off", "entity_id": "light.lumiere", "phrases": ["eteins la lumiere"]},
            {"service": "cover.open_cover", "entity_id": "cover.volet", "phrases": ["ouvre le volet"]},
            {"service": "cover.close_cover", "entity_id": "cover.volet", "phrases": ["ferme le volet"]},
        ]

    async def test_shortcut_allume_lumiere(self):
        """'Allume la lumière' doit déclencher un court-circuit déterministe."""
        payload, agent = await self.router.analyze_request("Allume la lumière")
        assert agent == "ha_agent"
        assert payload.metadata.get("routing_type") == "ha_deterministic"
        assert payload.metadata.get("direct_tool_call") is not None
        assert payload.metadata["direct_tool_call"]["name"] == "mcp_ha_custom_call_service"
        assert payload.metadata["direct_tool_call"]["arguments"]["service"] == "light.turn_on"

    async def test_shortcut_eteins_lumiere(self):
        """'Éteins la lumière' doit déclencher un court-circuit déterministe."""
        payload, agent = await self.router.analyze_request("Éteins la lumière")
        assert agent == "ha_agent"
        assert payload.metadata["direct_tool_call"]["arguments"]["service"] == "light.turn_off"

    async def test_shortcut_ouvre_volet(self):
        """'Ouvre le volet' doit déclencher un court-circuit déterministe."""
        payload, agent = await self.router.analyze_request("Ouvre le volet")
        assert agent == "ha_agent"
        assert payload.metadata["direct_tool_call"]["arguments"]["service"] == "cover.open_cover"

    async def test_shortcut_ferme_volet(self):
        """'Ferme le volet' doit déclencher un court-circuit déterministe."""
        payload, agent = await self.router.analyze_request("Ferme le volet")
        assert agent == "ha_agent"
        assert payload.metadata["direct_tool_call"]["arguments"]["service"] == "cover.close_cover"


class TestRouterComplexity:
    """Tests de détection de complexité du Router."""

    def setup_method(self):
        """Initialise un Router avec les dépendances mockées."""
        self.router = Router.__new__(Router)
        self.router.default_agent = "planner"
        self.router.rag_engine = None
        self.router.llm_gateway = None
        self.router.config = {}
        
        from unittest.mock import MagicMock
        self.router.context_loader = MagicMock()
        self.router.context_loader.load_all.return_value = None
        self.router.context_loader.reload_if_stale.return_value = None
        self.router.context_loader.get_context_for_categories.return_value = ""
        self.router.episode_store = MagicMock()
        self.router.episode_store.query_relevant_episodes.return_value = ""
        self.router.fact_store = MagicMock()
        self.router.fact_store.get_facts_for_context.return_value = ""
        self.router.categories = {
            "casual_chat": {"keywords": ["bonjour"], "weight": 1.0},
            "home_assistant": {"keywords": ["lumière", "volet"], "weight": 1.5},
            "code_generation": {"keywords": ["code", "python", "refactoring", "architecture"], "weight": 1.2},
            "database": {"keywords": ["sqlite", "sql"], "weight": 1.4},
            "files": {"keywords": ["fichier"], "weight": 1.0},
            "analysis": {"keywords": ["analyse", "audit", "optimiser"], "weight": 1.2},
        }

    async def test_complex_by_length(self):
        """Un prompt long (>220 chars) doit être détecté comme complexe."""
        long_prompt = "Analyse le code " + "x" * 250
        payload, agent = await self.router.analyze_request(long_prompt)
        assert payload.metadata["is_complex"] is True
        assert agent == "planner"  # Les requêtes complexes vont toujours au planner

    async def test_complex_by_keyword(self):
        """Les mots-clés de complexité (refactoring, architecture) déclenchent le mode complexe."""
        payload, agent = await self.router.analyze_request("Refactoring complet du moteur")
        assert payload.metadata["is_complex"] is True
        assert agent == "planner"

    async def test_simple_is_not_complex(self):
        """Une requête courte et simple ne doit pas être marquée complexe."""
        payload, agent = await self.router.analyze_request("Bonjour")
        assert payload.metadata["is_complex"] is False


class TestRouterFinDeSession:
    """Tests de la détection de la routine FIN DE SESSION."""

    def setup_method(self):
        self.router = Router.__new__(Router)
        self.router.default_agent = "planner"
        self.router.rag_engine = None
        self.router.llm_gateway = None
        self.router.config = {}
        
        from unittest.mock import MagicMock
        self.router.context_loader = MagicMock()
        self.router.context_loader.load_all.return_value = None
        self.router.context_loader.reload_if_stale.return_value = None
        self.router.context_loader.get_context_for_categories.return_value = ""
        self.router.episode_store = MagicMock()
        self.router.episode_store.query_relevant_episodes.return_value = ""
        self.router.fact_store = MagicMock()
        self.router.fact_store.get_facts_for_context.return_value = ""
        self.router.categories = {
            "casual_chat": {"keywords": ["bonjour"], "weight": 1.0},
            "home_assistant": {"keywords": ["lumière"], "weight": 1.5},
            "code_generation": {"keywords": ["code"], "weight": 1.2},
            "database": {"keywords": ["sqlite"], "weight": 1.4},
            "files": {"keywords": ["fichier"], "weight": 1.0},
            "analysis": {"keywords": ["analyse"], "weight": 1.2},
        }

    async def test_fin_de_session_detected(self):
        """'FIN DE SESSION' doit injecter la directive critique dans le contexte."""
        payload, agent = await self.router.analyze_request("FIN DE SESSION")
        assert "DIRECTIVE CRITIQUE" in payload.relevant_context

    async def test_sauvegarde_detected(self):
        """'SAUVEGARDE' doit aussi déclencher la routine FIN DE SESSION."""
        payload, agent = await self.router.analyze_request("SAUVEGARDE")
        assert "DIRECTIVE CRITIQUE" in payload.relevant_context
