"""
conftest.py — Fixtures partagées pour les tests pytest du tab5-engine.

Fournit des mocks réutilisables pour :
- LLMGateway (pas d'appels API réels)
- ToolRegistry (outils simulés)
- GlobalState / TaskPayload (données de test)
- Router (avec RAG et ContextLoader mockés)
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ajout du répertoire parent au PYTHONPATH pour les imports relatifs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# [P0-1.1] Clé d'API de test : les routes sensibles exigent désormais require_auth
# (fail-closed). On fixe une clé pour toute la suite afin de pouvoir exercer les
# endpoints protégés en envoyant le Bearer correspondant (cf. fixture auth_headers).
TEST_API_KEY = "test-moteur-key"
os.environ.setdefault("MOTEUR_API_KEY", TEST_API_KEY)

from core.state import GlobalState, TaskPayload

# ──────────────────────────────────────────────────────────────────
# Isolation de la base runtime pour TOUS les tests (#T348)
# ──────────────────────────────────────────────────────────────────
#
# Avant #T348, seul `core/event_store.py` déviait : il calculait son chemin par
# défaut indépendamment de `core.runtime_db`, si bien qu'un test qui appelait
# `override_db_path()` croyait être isolé pendant que l'EventStore continuait
# d'écrire dans la vraie base du dépôt (moteur_runtime.db). Le singleton
# `get_event_store()` figeait en plus l'instance — et donc le chemin — au
# premier appel du process.
#
# Cette fixture autouse réoriente la base runtime vers un fichier temporaire
# POUR CHAQUE test, de sorte que rien (EventStore compris) n'écrive jamais dans
# la base du dépôt. Compatible avec la vingtaine de tests qui appellent déjà
# `override_db_path()` et restaurent « l'ancien chemin » en sortie (motif
# `ancien = get_db_path()` … `override_db_path(ancien)`) : comme cette fixture
# s'exécute AVANT les fixtures du test, l'`ancien` qu'ils capturent est le
# chemin temporaire ici posé — jamais celui du dépôt. À la sortie du test, la
# fixture restaure le vrai chemin du dépôt.


@pytest.fixture(autouse=True)
def _isoler_base_runtime(tmp_path):
    """Récrit `core.runtime_db` vers une base temporaire isolée par test."""
    from core import runtime_db

    ancien = runtime_db.get_db_path()
    # Garder le basename `moteur_runtime.db` : test_runtime_db.test_db_path
    # vérifie ce suffixe (le dossier tmp_path assure déjà l'isolation).
    chemin_iso = str(tmp_path / "moteur_runtime.db")
    runtime_db.override_db_path(chemin_iso)
    try:
        yield
    finally:
        # Fermer le singleton EventStore AVANT de rendre le chemin / de laisser
        # pytest supprimer tmp_path : sinon la connexion WAL reste ouverte sur
        # les fichiers -wal/-shm et le cleanup échoue sous Windows (Bugbot #T348).
        try:
            from core import event_store as _es
            if _es._event_store_instance is not None:
                _es._event_store_instance.close()
                _es._event_store_instance = None
        except Exception:
            pass
        runtime_db.override_db_path(ancien)


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
