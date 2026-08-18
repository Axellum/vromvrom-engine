"""Tests provider Alibaba DashScope Coding Plan (Lite)."""
import os
import threading

import pytest

import core.models_db as models_db
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


@pytest.fixture
def _catalogue_dashscope(tmp_path, monkeypatch):
    """Isole models_registry.db dans tmp_path et y enregistre le provider DashScope.

    [#T297] Avant, ce test vérifiait l'état d'une base de production
    (models_registry.db peuplée par seed_models_db.py) : il échouait dans tout
    worktree neuf dépourvu de cette base. Désormais le test construit lui-même
    l'état qu'il vérifie, en appelant le code de production d'enregistrement
    (core.models_db.upsert_provider / upsert_model / upsert_subscription — chemin
    canonique aussi utilisé par seed_models_db.py) sur une base temporaire. Motif
    identique à tests/unit/test_quota_collector.py (monkeypatch de _DB_PATH +
    _thread_local).

    Les données ci-dessous sont un miroir fidèle de seed_models_db.py
    (provider dashscope + 10 modèles + abonnement dashscope_coding_lite) : si le
    seed évolue, ce miroir doit suivre.
    """
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())

    models_db.upsert_provider(
        "dashscope",
        name="Alibaba DashScope Coding Plan",
        type="subscription",
        api_endpoint="https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions",
        auth_method="api_key",
        confidentiality="training",
        cascade_priority=2.5,
        notes=(
            "Coding Plan Lite (¥40/mois, stock) — clé sk-sp-*, endpoint coding-intl. "
            "Quota requêtes (pas tokens) : 1200/5h, 9000/sem, 18000/mois. "
            "CGU : usage prévu pour outils coding interactifs (pas backend automatisé)."
        ),
    )
    for modele in (
        {"id": "dashscope/qwen3.7-plus", "provider_id": "dashscope", "display_name": "Qwen3.7 Plus (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Flagship Qwen Coding Plan — vision + thinking, contexte 1M", "notes": "Lite amorti (~¥40/mois). ID API exact: qwen3.7-plus", "last_tested": "2026-07-26"},
        {"id": "dashscope/qwen3.6-plus", "provider_id": "dashscope", "display_name": "Qwen3.6 Plus (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Qwen Coding Plan — vision + thinking", "notes": "ID API exact: qwen3.6-plus", "last_tested": "2026-07-26"},
        {"id": "dashscope/qwen3.5-plus", "provider_id": "dashscope", "display_name": "Qwen3.5 Plus (Coding Plan)", "tier": "subscription", "routing_tier": "moyen", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Qwen Coding Plan polyvalent — vision", "notes": "ID API exact: qwen3.5-plus", "last_tested": "2026-07-26"},
        {"id": "dashscope/qwen3-max-2026-01-23", "provider_id": "dashscope", "display_name": "Qwen3 Max 2026-01-23 (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 262144, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Qwen Max snapshot Coding Plan", "notes": "ID API exact: qwen3-max-2026-01-23", "last_tested": "2026-07-26"},
        {"id": "dashscope/qwen3-coder-next", "provider_id": "dashscope", "display_name": "Qwen3 Coder Next (Coding Plan)", "tier": "subscription", "routing_tier": "moyen", "context_input": 262144, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Défaut moteur Coding Plan — génération de code (pas de thinking)", "notes": "Thinking non supporté. ID API exact: qwen3-coder-next", "last_tested": "2026-07-26"},
        {"id": "dashscope/qwen3-coder-plus", "provider_id": "dashscope", "display_name": "Qwen3 Coder Plus (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Code + contexte 1M (pas de thinking)", "notes": "Thinking non supporté. ID API exact: qwen3-coder-plus", "last_tested": "2026-07-26"},
        {"id": "dashscope/kimi-k2.5", "provider_id": "dashscope", "display_name": "Kimi K2.5 (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 262144, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Moonshot Kimi via Coding Plan — vision + agentique", "notes": "ID API exact: kimi-k2.5", "last_tested": "2026-07-26"},
        {"id": "dashscope/glm-5", "provider_id": "dashscope", "display_name": "GLM-5 (Coding Plan)", "tier": "subscription", "routing_tier": "fort", "context_input": 202752, "context_output": 32768, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "GLM-5 via forfait Alibaba (distinct de Zhipu payant)", "notes": "ID API exact: glm-5 — ne pas confondre avec provider zhipu", "last_tested": "2026-07-26"},
        {"id": "dashscope/glm-4.7", "provider_id": "dashscope", "display_name": "GLM-4.7 (Coding Plan)", "tier": "subscription", "routing_tier": "moyen", "context_input": 202752, "context_output": 32768, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "GLM-4.7 via forfait Alibaba", "notes": "ID API exact: glm-4.7", "last_tested": "2026-07-26"},
        {"id": "dashscope/MiniMax-M2.5", "provider_id": "dashscope", "display_name": "MiniMax M2.5 (Coding Plan)", "tier": "subscription", "routing_tier": "moyen", "context_input": 196608, "context_output": 32768, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "polyvalent", "recommended_use": "MiniMax via Coding Plan (distinct de MINIMAX_API_KEY)", "notes": "ID API exact: MiniMax-M2.5 (casse significative)", "last_tested": "2026-07-26"},
    ):
        mid = modele.pop("id")
        models_db.upsert_model(mid, **modele)

    models_db.upsert_subscription(
        "dashscope_coding_lite",
        name="Alibaba Coding Plan Lite",
        cost_monthly_usd=5.5,
        rolling_window_hours=5,
        # Quota = requêtes (pas tokens). hourly_token_limit stocke le plafond
        # 5h en « unités requête » pour suivi approximatif.
        hourly_token_limit=1200,
        monthly_token_limit=18000,
        estimated_messages_limit=18000,
        models=DASHSCOPE_MODEL_IDS,
        advantages=(
            "¥40/mois (~$5.5) — 10 modèles (Qwen/GLM/Kimi/MiniMax). "
            "Quotas : 1200 req/5h (fenêtre glissante), 9000/sem, 18000/mois. "
            "Clé sk-sp-* + endpoint coding-intl. Lite : plus de renouvellement après 2026-04-13."
        ),
        recommended_use=(
            "Code / agentique amorti. Défaut moteur : dashscope/qwen3-coder-next. "
            "⚠️ CGU : prévu pour outils coding interactifs, pas backends automatisés."
        ),
    )


def test_dashscope_models_db_registration(_catalogue_dashscope):
    """Provider + 10 modèles + abonnement Lite enregistrés dans models_registry.db.

    [#T297] Autopharmaceutique : le test enregistre le provider, ses 10 modèles
    et l'abonnement via core.models_db.upsert_provider / upsert_model /
    upsert_subscription (chemin canonique d'enregistrement, aussi utilisé par
    seed_models_db.py), puis vérifie la relecture via get_provider / get_model /
    get_subscriptions sur la même base temporaire. Plus de dépendance à une base
    de production préexistante.
    """
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


def test_dashscope_gateway_ne_instancie_plus(monkeypatch):
    """D-8 : clé présente ≠ providers construits (401 + CGU backend)."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-sp-test-key-for-unit")
    gateway = LLMGateway()
    assert gateway.providers.get("dashscope") is None
    assert gateway.providers.get("dashscope/qwen3-coder-next") is None
    assert not any(
        nom == "dashscope" or nom.startswith("dashscope/")
        for nom in gateway.providers
    )


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

    from core.llm_gateway import DASHSCOPE_ACTIF

    if not DASHSCOPE_ACTIF:
        pytest.skip("Dashscope désactivé (D-8) — pas d'inférence live")

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
