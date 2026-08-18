"""
tests/unit/test_gateway_direct_cb.py — Circuit Breaker sur l'accès direct (#T212).

Avant #T212, gateway.get_provider("<id littéral>") retournait le provider brut
(ClaudeInstructionsWrapper seul) : le Planner configuré sur un ID littéral
(config.json planner_model="gemini-3.5-flash-free") et 3 outils MCP
contournaient entièrement le Circuit Breaker. Désormais l'accès direct passe
par une cascade d'un seul élément (FallbackProvider), le même wrapping que
get_provider_for_tier().
"""

import json

import pytest

from core.llm.circuit_breaker import CircuitBreaker
from core.llm.providers.base import LLMProvider
from core.llm.providers.deepseek import ClaudeInstructionsWrapper, FallbackProvider
from core.llm_gateway import LLMGateway


class _FakeProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        return "réponse factice suffisamment longue pour l'heuristique de qualité"

    def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
        self.calls += 1
        return {"ok": True}


@pytest.fixture
def gateway():
    return LLMGateway()


@pytest.fixture(autouse=True)
def _clean_cb_registry():
    """Isole le registre global des Circuit Breakers entre les tests."""
    yield
    with CircuitBreaker._registry_lock:
        for name in [k for k in CircuitBreaker._registry if k.startswith("fake-")]:
            del CircuitBreaker._registry[name]


def test_get_provider_retourne_cascade_dun_element(gateway):
    provider = gateway.get_provider("local")
    assert isinstance(provider, FallbackProvider)
    assert len(provider.providers) == 1
    model_name, inner = provider.providers[0]
    assert model_name == "local"
    # L'injection CLAUDE.md (comportement historique) est conservée à l'intérieur
    assert isinstance(inner, ClaudeInstructionsWrapper)


def test_get_provider_inconnu_leve_toujours_valueerror(gateway):
    """Contrat inchangé : les appelants utilisent ValueError comme test de dispo."""
    with pytest.raises(ValueError, match="inconnu"):
        gateway.get_provider("modele-qui-nexiste-pas")


def test_acces_direct_respecte_le_circuit_ouvert(gateway):
    """CB ouvert → l'appel direct est court-circuité SANS toucher le provider."""
    fake = _FakeProvider()
    gateway.providers["fake-direct-cb"] = fake

    cb = CircuitBreaker.get_or_create("fake-direct-cb")
    for _ in range(5):
        cb.record_failure(RuntimeError("panne simulée"))
    assert cb.is_open()

    provider = gateway.get_provider("fake-direct-cb")
    with pytest.raises(Exception):
        provider.generate("sys", "user", use_semantic_cache=False)
    assert fake.calls == 0  # jamais appelé : le CB a bien protégé l'accès direct


def test_acces_direct_enregistre_le_succes_dans_le_cb(gateway):
    fake = _FakeProvider()
    gateway.providers["fake-direct-ok"] = fake

    provider = gateway.get_provider("fake-direct-ok")
    result = provider.generate("sys", "user", use_semantic_cache=False)

    assert "factice" in result
    assert fake.calls == 1
    cb = CircuitBreaker.get_or_create("fake-direct-ok")
    assert not cb.is_open()
    assert cb.get_stats().get("total_successes", cb.to_dict().get("total_successes", 1)) >= 1


def test_routing_policy_exclut_fable5_du_routage_auto(gateway):
    """claude-fable-5 est volontairement hors routage automatique (anti
    auto-escalade, décision Axel 07/2026) : même injecté dans un tier par la
    config ou la DB, _resolve_tier_models doit le filtrer. L'accès explicite
    par get_provider() reste permis."""
    config = {
        "tiers": {"fort": ["claude-fable-5", "deepseek-reasoner"]},
        "routing_policy": {"excluded_models": ["claude-fable-5"]},
    }
    _tier, allowed = gateway._resolve_tier_models("fort", config)
    assert "claude-fable-5" not in allowed


def test_routing_policy_exemple_exclut_fable5():
    """Le config.example.json public documente l'exclusion (pas le config.json
    privé, absent du miroir OSS)."""
    from pathlib import Path
    example = Path(__file__).resolve().parents[2] / "config.example.json"
    if not example.is_file():
        pytest.skip("config.example.json absent")
    policy = json.loads(example.read_text(encoding="utf-8")).get("routing_policy", {})
    exclus = policy.get("excluded_models", [])
    assert "claude-fable-5" in exclus
    assert "claude-sonnet-5" in exclus
    assert "claude-opus-4-8-direct" in exclus


def test_exclusions_defaut_couvrent_api_anthropic_et_or_free():
    """13/08 : le défaut code doit suffire même si le config Deck est figé."""
    from core.llm_gateway import EXCLUSIONS_CASCADE_DEFAUT
    assert "claude-sonnet-5" in EXCLUSIONS_CASCADE_DEFAUT
    assert "claude-opus-4-8-direct" in EXCLUSIONS_CASCADE_DEFAUT
    assert "claude-haiku-4-5-direct" in EXCLUSIONS_CASCADE_DEFAUT
    assert "meta-llama/llama-3.2-3b-instruct:free" in EXCLUSIONS_CASCADE_DEFAUT
    assert "dashscope/qwen3-coder-next" in EXCLUSIONS_CASCADE_DEFAUT


def test_exclusions_defaut_filtrent_meme_sans_config(gateway, monkeypatch):
    """Le défaut code filtre même si routing_policy.excluded_models est vide
    (cas du config.json Deck figé au deploy)."""
    import core.models_db as models_db

    monkeypatch.setattr(models_db, "get_models_for_tier", lambda _tier: [])
    config = {
        "tiers": {
            "leger": [
                "meta-llama/llama-3.2-3b-instruct:free",
                "gemini-3.5-flash-free",
            ]
        },
        "routing_policy": {"excluded_models": []},
    }
    _tier, allowed = gateway._resolve_tier_models("leger", config)
    assert "meta-llama/llama-3.2-3b-instruct:free" not in allowed
    assert "gemini-3.5-flash-free" in allowed


def test_exclusions_defaut_filtrent_prefixe_dashscope(gateway, monkeypatch):
    """D-8 : un dashscope/* inconnu de la liste exacte est quand même filtré."""
    import core.models_db as models_db

    monkeypatch.setattr(models_db, "get_models_for_tier", lambda _tier: [])
    config = {
        "tiers": {
            "fort": [
                "dashscope/modele-invente",
                "deepseek-reasoner",
            ]
        },
        "routing_policy": {"excluded_models": []},
    }
    _tier, allowed = gateway._resolve_tier_models("fort", config)
    assert "dashscope/modele-invente" not in allowed
    assert "deepseek-reasoner" in allowed


def test_gateway_plus_de_slugs_or_free(gateway):
    """Les slugs :free ne sont plus des providers — plus de 404 en tête de cascade."""
    assert "meta-llama/llama-3.3-70b-instruct:free" not in gateway.providers
    assert "meta-llama/llama-3.2-3b-instruct:free" not in gateway.providers
    if "openrouter" in gateway.providers:
        assert "openrouter/auto" in gateway.providers


def test_tier_sans_double_wrapping(gateway):
    """get_provider_for_tier ne doit PAS imbriquer des FallbackProvider
    (double CB / double retry) suite au passage de get_provider() en cascade."""
    from core.llm_gateway import load_config
    _, tier_provider = gateway.get_provider_for_tier("leger", load_config())
    assert isinstance(tier_provider, FallbackProvider)
    for _model, inner in tier_provider.providers:
        assert not isinstance(inner, FallbackProvider)
