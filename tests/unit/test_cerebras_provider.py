import os
import threading

import pytest

import core.models_db as models_db
from core.llm_gateway import LLMGateway
from core.models_db import get_model, get_provider


def test_cerebras_api_key_env():
    """Vérifie le format des clés Cerebras si présentes (skip en CI sans secret)."""
    keys = [
        ("CEREBRAS_API_KEY", os.getenv("CEREBRAS_API_KEY")),
        ("CEREBRAS_PAYANT_API_KEY", os.getenv("CEREBRAS_PAYANT_API_KEY")),
    ]
    present = [(name, key) for name, key in keys if key]
    if not present:
        pytest.skip("Aucune clé Cerebras configurée (CI sans secret)")
    for name, key in present:
        assert key.startswith("csk-"), f"{name} doit commencer par 'csk-'"


@pytest.fixture
def _catalogue_cerebras(tmp_path, monkeypatch):
    """Isole models_registry.db dans tmp_path et y enregistre le provider Cerebras.

    [#T297] Avant, ce test vérifiait l'état d'une base de production
    (models_registry.db peuplée par seed_models_db.py) : il échouait dans tout
    worktree neuf dépourvu de cette base. Désormais le test construit lui-même
    l'état qu'il vérifie, en appelant le code de production d'enregistrement
    (core.models_db.upsert_provider / upsert_model — chemin canonique aussi
    utilisé par seed_models_db.py) sur une base temporaire. Motif identique à
    tests/unit/test_quota_collector.py (monkeypatch de _DB_PATH + _thread_local).

    Les données ci-dessous sont un miroir fidèle de seed_models_db.py
    (provider cerebras + 3 modèles) : si le seed évolue, ce miroir doit suivre.
    """
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())

    models_db.upsert_provider(
        "cerebras",
        name="Cerebras API",
        type="pay_as_you_go",
        api_endpoint="https://api.cerebras.ai/v1/chat/completions",
        auth_method="api_key",
        confidentiality="none",
        cascade_priority=2.3,
        notes="Clé payante. Inférence Wafer Scale ultra-rapide (Cerebras Paid).",
    )
    for modele in (
        {
            "id": "gpt-oss-120b",
            "provider_id": "cerebras",
            "display_name": "GPT OSS 120B (Cerebras)",
            "tier": "paid",
            "routing_tier": "fort",
            "context_input": 8192,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "speciality": "raisonnement",
            "recommended_use": "Modèle géant 120B ultra-rapide (Cerebras)",
            "notes": "Clé Payante (Cerebras)",
        },
        {
            "id": "gemma-4-31b-cerebras",
            "provider_id": "cerebras",
            "display_name": "Gemma 4 31B (Cerebras)",
            "tier": "paid",
            "routing_tier": "moyen",
            "context_input": 8192,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "speciality": "ultra_rapide",
            "recommended_use": "Google Gemma 4 31B ultra-rapide (Cerebras Wafer Scale)",
            "notes": "Clé Payante (Cerebras)",
        },
        {
            "id": "zai-glm-4.7",
            "provider_id": "cerebras",
            "display_name": "Zai GLM 4.7 (Cerebras)",
            "tier": "paid",
            "context_input": 8192,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "supports_thinking": 1,
            "speciality": "raisonnement",
            "recommended_use": "Raisonnement polyvalent ultra-rapide (Cerebras)",
            "notes": "Clé Payante (Cerebras)",
        },
    ):
        mid = modele.pop("id")
        models_db.upsert_model(mid, **modele)


def test_cerebras_models_db_registration(_catalogue_cerebras):
    """Vérifie que le provider et les modèles Cerebras sont enregistrés dans models_registry.db.

    [#T297] Autopharmaceutique : le test enregistre le provider et ses modèles via
    core.models_db.upsert_provider / upsert_model (chemin canonique
    d'enregistrement, aussi utilisé par seed_models_db.py), puis vérifie la
    relecture via get_provider / get_model sur la même base temporaire. Plus de
    dépendance à une base de production préexistante.
    """
    provider = get_provider("cerebras")
    assert provider is not None, "Le provider 'cerebras' doit exister dans la BDD"
    assert provider["type"] == "pay_as_you_go"

    for model_id in ["gpt-oss-120b", "gemma-4-31b-cerebras", "zai-glm-4.7"]:
        m = get_model(model_id)
        assert m is not None, f"Le modèle '{model_id}' doit exister dans la BDD"
        assert m["provider_id"] == "cerebras"
        assert m["tier"] == "paid"


def test_cerebras_free_path_prefers_free_key(monkeypatch):
    """cerebras-free → gpt-oss-120b doit binder la clé free, pas la payante."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-free-test-key")
    monkeypatch.setenv("CEREBRAS_PAYANT_API_KEY", "csk-paid-test-key")
    gateway = LLMGateway()
    free_bound = gateway.providers.get("gpt-oss-120b")
    paid_bound = gateway.providers.get("gemma-4-31b-cerebras")
    assert free_bound is not None
    assert paid_bound is not None
    assert free_bound.api_key == "csk-free-test-key"
    assert paid_bound.api_key == "csk-paid-test-key"


@pytest.mark.live
def test_cerebras_live_generation():
    """Test d'inférence en direct sur l'API Cerebras pour valider la clé et la latence.

    Marqué `live` : exclu de la suite par défaut, relançable avec `python -m pytest -m live`.
    Le .env n'est chargé qu'ICI (jamais au niveau module) : un load_dotenv() au
    collect injecterait les secrets du .env dans le process de TOUTE la suite
    unitaire (ex: clés LANGFUSE → client réseau réel → garde-fou réseau).
    """
    from dotenv import load_dotenv

    load_dotenv()
    free_key = os.getenv("CEREBRAS_API_KEY")
    paid_key = os.getenv("CEREBRAS_PAYANT_API_KEY")
    if not free_key and not paid_key:
        pytest.skip("Aucune clé Cerebras configurée")

    gateway = LLMGateway()
    provider_gemma = gateway.providers.get("gemma-4-31b-cerebras")
    assert provider_gemma is not None, "Le provider 'gemma-4-31b-cerebras' doit être instancié dans LLMGateway"

    res_gemma = provider_gemma.generate(
        system_prompt="Tu es un assistant concis.",
        user_prompt="Bonjour ! Réponds en un seul mot: OK",
        max_tokens=100,
    )
    assert len(res_gemma) > 0, "La réponse de gemma-4-31b-cerebras ne doit pas être vide"

    provider_oss = gateway.providers.get("gpt-oss-120b")
    assert provider_oss is not None, "Le provider 'gpt-oss-120b' doit être instancié dans LLMGateway"

    res_oss = provider_oss.generate(
        system_prompt="Tu es un assistant concis.",
        user_prompt="Bonjour ! Réponds en un seul mot: OK",
        max_tokens=300,
    )
    assert len(res_oss) > 0, "La réponse de gpt-oss-120b ne doit pas être vide"
