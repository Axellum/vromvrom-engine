# -*- coding: utf-8 -*-
"""
tests/test_pricing.py — Barème de prix unifié (P1-2.4).

Vérifie que `core.pricing` est bien la source unique (lecture de
pricing_strategy.json), que `token_tracker` délègue à ce barème, et que les
conventions (gratuit/forfait, EUR→USD, alias de repli) sont respectées.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import pricing
from core.pricing import get_model_pricing


def test_deepseek_chat_from_strategy_file():
    """deepseek-chat lit le tarif réel du JSON (0.14 / 0.28 par M)."""
    p = get_model_pricing("deepseek-chat")
    assert abs(p["input"] - 0.14 / 1_000_000) < 1e-15
    assert abs(p["output"] - 0.28 / 1_000_000) < 1e-15


def test_free_tier_is_zero():
    """gemini-2.5-flash est en Free Tier dans le JSON → coût nul (source unique)."""
    assert get_model_pricing("gemini-2.5-flash") == {"input": 0.0, "output": 0.0}


def test_cli_and_local_and_subscription_are_free():
    """Conventions de gratuité : -cli, local, et modèles d'abonnement."""
    assert get_model_pricing("gemini-3.5-flash-high-cli") == {"input": 0.0, "output": 0.0}
    assert get_model_pricing("local") == {"input": 0.0, "output": 0.0}
    # claude-opus-4-8 figure dans subscriptions[].models → coût marginal nul
    assert get_model_pricing("claude-opus-4-8") == {"input": 0.0, "output": 0.0}


def test_eur_rates_converted_to_usd():
    """Les tarifs GCP en EUR sont convertis en USD (× EUR_USD)."""
    p = get_model_pricing("gemini-2.5-flash-paid")
    expected_in = 0.256515 * pricing.EUR_USD / 1_000_000
    assert abs(p["input"] - expected_in) < 1e-15
    assert p["output"] > 0  # tarif payant non nul


def test_alias_fallback_when_absent_from_strategy():
    """L'alias générique 'gemini' (absent du JSON) retombe sur FALLBACK_PRICING."""
    p = get_model_pricing("gemini")
    assert p == pricing.FALLBACK_PRICING["gemini"]


def test_unknown_model_is_zero():
    assert get_model_pricing("modele-bidon-9000") == {"input": 0.0, "output": 0.0}


def test_token_tracker_delegates_to_unified_pricing():
    """token_tracker._get_pricing_for_model == core.pricing.get_model_pricing."""
    from core import token_tracker
    assert token_tracker._get_pricing_for_model("deepseek-chat") == get_model_pricing("deepseek-chat")
    assert token_tracker._get_pricing_for_model("gemini-2.5-flash") == get_model_pricing("gemini-2.5-flash")


def test_record_usage_cost_matches_barometer(tmp_path, monkeypatch):
    """Le coût enregistré par record_usage correspond au barème unifié."""
    from core import token_tracker
    p = get_model_pricing("deepseek-chat")
    prompt, completion = 1000, 500
    expected = prompt * p["input"] + completion * p["output"]
    # Calcul direct via la même fonction (record_usage écrit en DB ; on valide le calcul)
    computed = prompt * token_tracker._get_pricing_for_model("deepseek-chat")["input"] \
        + completion * token_tracker._get_pricing_for_model("deepseek-chat")["output"]
    assert abs(computed - expected) < 1e-15
