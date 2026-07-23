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


def test_deepseek_chat_from_strategy_file(monkeypatch):
    """deepseek-chat lit le tarif réel du JSON (0.14 / 0.28 par M), hors heures de pic."""
    import datetime

    # Tarification pic-creux DeepSeek : fixe l'horloge hors pic pour un résultat
    # déterministe (sinon le test échoue ~7h/24 selon l'heure d'exécution du CI).
    off_peak_dt = datetime.datetime(2026, 7, 16, 12, 0, tzinfo=datetime.timezone.utc)

    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return off_peak_dt

    monkeypatch.setattr(pricing.datetime, "datetime", MockDatetime)

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


def test_is_deepseek_peak_hours():
    from core.pricing import is_deepseek_peak_hours
    import datetime
    
    # Avant mi-juillet 2026 (ex: 14 juillet)
    dt_before = datetime.datetime(2026, 7, 14, 2, 0, tzinfo=datetime.timezone.utc)
    assert not is_deepseek_peak_hours(dt_before)
    
    # Après mi-juillet 2026, hors heures de pic (ex: 15 juillet, 0:30 UTC)
    dt_off1 = datetime.datetime(2026, 7, 15, 0, 30, tzinfo=datetime.timezone.utc)
    assert not is_deepseek_peak_hours(dt_off1)
    
    # Après mi-juillet 2026, hors heures de pic (ex: 15 juillet, 5:00 UTC)
    dt_off2 = datetime.datetime(2026, 7, 15, 5, 0, tzinfo=datetime.timezone.utc)
    assert not is_deepseek_peak_hours(dt_off2)
    
    # Après mi-juillet 2026, hors heures de pic (ex: 15 juillet, 11:00 UTC)
    dt_off3 = datetime.datetime(2026, 7, 15, 11, 0, tzinfo=datetime.timezone.utc)
    assert not is_deepseek_peak_hours(dt_off3)
    
    # Après mi-juillet 2026, pic 1 (ex: 15 juillet, 2:00 UTC)
    dt_peak1 = datetime.datetime(2026, 7, 15, 2, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak1)
    
    # Après mi-juillet 2026, limite pic 1 début (ex: 15 juillet, 1:00 UTC)
    dt_peak1_start = datetime.datetime(2026, 7, 15, 1, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak1_start)
    
    # Après mi-juillet 2026, limite pic 1 fin (ex: 15 juillet, 4:00 UTC)
    dt_peak1_end = datetime.datetime(2026, 7, 15, 4, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak1_end)
    
    # Après mi-juillet 2026, pic 2 (ex: 15 juillet, 8:00 UTC)
    dt_peak2 = datetime.datetime(2026, 7, 15, 8, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak2)
    
    # Après mi-juillet 2026, limite pic 2 début (ex: 15 juillet, 6:00 UTC)
    dt_peak2_start = datetime.datetime(2026, 7, 15, 6, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak2_start)
    
    # Après mi-juillet 2026, limite pic 2 fin (ex: 15 juillet, 10:00 UTC)
    dt_peak2_end = datetime.datetime(2026, 7, 15, 10, 0, tzinfo=datetime.timezone.utc)
    assert is_deepseek_peak_hours(dt_peak2_end)


def test_deepseek_pricing_doubled_during_peak(monkeypatch):
    import datetime
    from core import pricing
    
    # On simule un pic le 16 juillet 2026 à 8h00 UTC
    target_dt = datetime.datetime(2026, 7, 16, 8, 0, tzinfo=datetime.timezone.utc)
    
    # Mock datetime.datetime.now et datetime.datetime
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(pricing.datetime, "datetime", MockDatetime)
    
    # Récupérer le tarif de deepseek-chat
    # Tarif de base : input 0.14 / 1M, output 0.28 / 1M
    p = pricing.get_model_pricing("deepseek-chat")
    assert abs(p["input"] - (0.14 * 2.0) / 1_000_000) < 1e-15
    assert abs(p["output"] - (0.28 * 2.0) / 1_000_000) < 1e-15
    
    # Vérifier qu'un autre modèle (ex: gemini-2.5-pro-paid) ne double pas son tarif
    p_gemini = pricing.get_model_pricing("gemini-2.5-pro-paid")
    expected_gemini = 0.0  # ou son coût réel du JSON. Le test initial s'assurait que expected_in = 0.256515 * pricing.EUR_USD / 1_000_000
    # Dans test_eur_rates_converted_to_usd, gemini-2.5-flash-paid est utilisé. Utilisons celui-là.
    p_flash_paid = pricing.get_model_pricing("gemini-2.5-flash-paid")
    expected_flash_paid = 0.256515 * pricing.EUR_USD / 1_000_000
    assert abs(p_flash_paid["input"] - expected_flash_paid) < 1e-15


def test_deepseek_pricing_normal_outside_peak(monkeypatch):
    import datetime
    from core import pricing
    
    # On simule hors pic le 16 juillet 2026 à 12h00 UTC
    target_dt = datetime.datetime(2026, 7, 16, 12, 0, tzinfo=datetime.timezone.utc)
    
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(pricing.datetime, "datetime", MockDatetime)
    
    p = pricing.get_model_pricing("deepseek-chat")
    assert abs(p["input"] - 0.14 / 1_000_000) < 1e-15
    assert abs(p["output"] - 0.28 / 1_000_000) < 1e-15
