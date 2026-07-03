"""
conftest.py — Fixtures partagées pour les tests pytest du tab5-engine.

Fournit des mocks réutilisables pour :
- LLMGateway (pas d'appels API réels)
- ToolRegistry (outils simulés)
- GlobalState / TaskPayload (données de test)
- Router (avec RAG et ContextLoader mockés)
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, AsyncMock

# Ajout du répertoire parent au PYTHONPATH pour les imports relatifs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# [P0-1.1] Clé d'API de test : les routes sensibles exigent désormais require_auth
# (fail-closed). On fixe une clé pour toute la suite afin de pouvoir exercer les
# endpoints protégés en envoyant le Bearer correspondant (cf. fixture auth_headers).
TEST_API_KEY = "test-moteur-key"
os.environ.setdefault("MOTEUR_API_KEY", TEST_API_KEY)

from core.state import GlobalState, TaskPayload


@pytest.fixture
def auth_headers():
    """Header Authorization Bearer valide pour les routes protégées par require_auth."""
    return {"Authorization": f"Bearer {os.environ['MOTEUR_API_KEY']}"}


# ──────────────────────────────────────────────────────────────────
# Fixtures : Modèles de données Pydantic
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_payload():
    """Payload de test standard pour les agents."""
    return TaskPayload(
        task_objective="Créer un fichier de test hello.py",
        relevant_context="Contexte de test unitaire.",
        metadata={
            "session_id": "test_session_001",
            "model_tier": "leger",
            "routing_type": "default",
        }
    )


@pytest.fixture
def sample_payload_ha():
    """Payload orienté domotique (pour le Router et HAAgent)."""
    return TaskPayload(
        task_objective="Allume la lumière du salon",
        relevant_context="",
        metadata={
            "session_id": "test_session_ha",
            "model_tier": "leger",
        }
    )


@pytest.fixture
def sample_payload_complex():
    """Payload complexe nécessitant un plan DAG multi-stages."""
    return TaskPayload(
        task_objective=(
            "Refactoring complet du module engine.py : "
            "découper la méthode run() en 3 sous-modules (dag_runner, healing, review_loop), "
            "migrer les tests existants et vérifier la non-régression avec pytest."
        ),
        relevant_context="Architecture tab5-engine.",
        metadata={
            "session_id": "test_session_complex",
            "model_tier": "fort",
        }
    )


@pytest.fixture
def global_state():
    """État global initialisé pour les tests."""
    return GlobalState(session_id="test_session_001")


# ──────────────────────────────────────────────────────────────────
# Fixtures : LLM Gateway mockée (aucun appel API réel)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_provider():
    """Provider LLM mocké qui retourne des réponses préfabriquées."""
    provider = MagicMock()
    
    # Réponse texte par défaut
    provider.generate.return_value = "Réponse mockée du provider LLM."
    
    # Réponse JSON structurée par défaut (plan du Planner)
    provider.generate_structured.return_value = {
        "plan": [
            {
                "task_id": "task_1",
                "objective": "Lire le fichier source",
                "target_agent": "executor",
                "model_tier": "leger",
                "depends_on": []
            },
            {
                "task_id": "task_2",
                "objective": "Modifier le fichier",
                "target_agent": "executor",
                "model_tier": "moyen",
                "depends_on": ["task_1"]
            }
        ]
    }
    return provider


@pytest.fixture
def mock_gateway(mock_provider):
    """LLMGateway mockée : get_provider et get_provider_for_tier retournent le mock."""
    gateway = MagicMock()
    gateway.get_provider.return_value = mock_provider
    gateway.get_provider_for_tier.return_value = ("mock-model", mock_provider)
    return gateway


# ──────────────────────────────────────────────────────────────────
# Fixtures : ToolRegistry mocké
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_tool_registry():
    """ToolRegistry mocké avec des outils simulés."""
    registry = MagicMock()
    
    # execute() retourne un résultat simulé (async)
    registry.execute = AsyncMock(return_value="Fichier créé avec succès : hello.py")
    
    # Schémas d'outils simulés pour l'ExecutorAgent
    registry.get_all_schemas.return_value = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Écrire du contenu dans un fichier",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["filepath", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Lire le contenu d'un fichier",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"}
                    },
                    "required": ["filepath"]
                }
            }
        }
    ]
    return registry


# ──────────────────────────────────────────────────────────────────
# Fixtures : Router avec dépendances mockées
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_rag_engine():
    """RAG engine mocké (retourne un contexte simulé)."""
    rag = MagicMock()
    rag.query.return_value = "Contexte RAG simulé : documentation ESPHome Tab5."
    return rag


@pytest.fixture
def mock_context_loader():
    """ContextLoader mocké (charge un contexte 3-Layers simulé)."""
    loader = MagicMock()
    loader.load_all.return_value = None
    loader.reload_if_stale.return_value = None
    loader.get_context_for_categories.return_value = "Contexte 3-Layers simulé."
    return loader
