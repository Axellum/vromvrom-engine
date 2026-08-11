import os

import pytest

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


def test_cerebras_models_db_registration():
    """Vérifie que le provider et les modèles Cerebras sont enregistrés dans models_registry.db."""
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
