"""Tests provider Alibaba DashScope Coding Plan (Lite)."""
import os

import pytest

from core.llm_gateway import LLMGateway
from core.models_db import get_model, get_provider, get_subscriptions

DASHSCOPE_MODEL_IDS = [
    "dashscope/qwen3.7-plus",
    "dashscope/qwen3.6-plus",
    "dashscope/qwen3.5-plus",
    "dashscope/qwen3-max-2026-01-23",
    "dashscope/qwen3-coder-next",
    "dashscope/qwen3-coder-plus",
    "dashscope/kimi-k2.5",
    "dashscope/glm-5",
    "dashscope/glm-4.7",
    "dashscope/MiniMax-M2.5",
]


def test_dashscope_api_key_env():
    """Vérifie le format de la clé Coding Plan si présente (skip CI sans secret)."""
    key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_CODING_PLAN_API_KEY")
    if not key:
        pytest.skip("Aucune clé DashScope configurée (CI sans secret)")
    assert key.startswith("sk-sp-"), "DASHSCOPE_API_KEY doit commencer par 'sk-sp-'"


def test_dashscope_models_db_registration():
    """Provider + 10 modèles + abonnement Lite enregistrés dans models_registry.db."""
    provider = get_provider("dashscope")
    assert provider is not None, "Le provider 'dashscope' doit exister dans la BDD"
    assert provider["type"] == "subscription"
    assert "coding" in (provider.get("api_endpoint") or "")

    for model_id in DASHSCOPE_MODEL_IDS:
        m = get_model(model_id)
        assert m is not None, f"Le modèle '{model_id}' doit exister dans la BDD"
        assert m["provider_id"] == "dashscope"
        assert m["tier"] == "subscription"
        assert m["cost_input_per_m"] == 0.0
        assert m["cost_output_per_m"] == 0.0

    subs = {s["id"]: s for s in get_subscriptions()}
    sub = subs.get("dashscope_coding_lite")
    assert sub is not None, "L'abonnement dashscope_coding_lite doit exister"
    assert sub["estimated_messages_limit"] == 18000
    assert sub["rolling_window_hours"] == 5


def test_dashscope_gateway_binds_key(monkeypatch):
    """Les alias dashscope/* doivent binder DASHSCOPE_API_KEY."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-sp-test-key-for-unit")
    gateway = LLMGateway()
    default = gateway.providers.get("dashscope")
    coder = gateway.providers.get("dashscope/qwen3-coder-next")
    assert default is not None
    assert coder is not None
    assert default.api_key == "sk-sp-test-key-for-unit"
    assert coder.api_key == "sk-sp-test-key-for-unit"
    assert coder.model == "qwen3-coder-next"
    assert "coding" in coder.base_url


@pytest.mark.live
def test_dashscope_live_generation():
    """Inférence live — skip si clé absente ou rejetée (401).

    Marqué `live` : exclu de la suite par défaut, relançable avec `python -m pytest -m live`.
    Le .env n'est chargé qu'ICI (jamais au niveau module) : un load_dotenv() au
    collect injecterait les secrets du .env dans le process de TOUTE la suite
    unitaire (ex: clés LANGFUSE → client réseau réel → garde-fou réseau).
    """
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_CODING_PLAN_API_KEY")
    if not key:
        pytest.skip("Aucune clé DashScope configurée")

    gateway = LLMGateway()
    provider = gateway.providers.get("dashscope/qwen3-coder-next")
    assert provider is not None, "dashscope/qwen3-coder-next doit être instancié"

    try:
        res = provider.generate(
            system_prompt="Tu es un assistant concis.",
            user_prompt="Réponds en un seul mot: OK",
            max_tokens=32,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid_api_key" in msg or "401" in msg or "invalid access token" in msg:
            pytest.skip(
                "Clé DashScope rejetée par /chat/completions (401) — "
                "re-copier/réinitialiser la clé sur la page Coding Plan"
            )
        raise

    assert len(res) > 0, "La réponse DashScope ne doit pas être vide"
