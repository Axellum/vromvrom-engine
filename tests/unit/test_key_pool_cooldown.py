"""Tests du garde-fou anti-boucle 429 du KeyPool Gemini (#T62).

Vérifie que, lorsque toutes les clés Free Tier sont en cooldown :
- `get_free_key(allow_cooldown=False)` retourne None (pas de clé garantie-429) ;
- `get_free_key()` (défaut) reste rétro-compatible (rend la clé qui expire le plus tôt) ;
- `seconds_until_available()` reflète le délai d'attente.
"""
import time

import pytest

from core.key_pool import GeminiKeyPool


@pytest.fixture
def pool(monkeypatch):
    """Pool à 2 clés Free Tier, chargé depuis l'environnement."""
    monkeypatch.setenv("GEMINI_API_KEY", "key-A")
    monkeypatch.setenv("GEMINI_API_KEY_2", "key-B")
    # Neutraliser les autres clés éventuellement présentes dans l'env réel
    for var in ("GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY_5", "GEMINI_API_KEY_6", "GEMINI_PAYANT_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return GeminiKeyPool()


def test_cle_disponible_retournee(pool):
    """Cas nominal : une clé non en cooldown est rendue dans les deux modes."""
    assert pool.get_free_key() in ("key-A", "key-B")
    assert pool.get_free_key(allow_cooldown=False) in ("key-A", "key-B")
    assert pool.seconds_until_available() == 0.0


def test_toutes_en_cooldown_strict_renvoie_none(pool):
    """Toutes les clés en cooldown + allow_cooldown=False → None (escalade)."""
    pool.report_rate_limit("key-A")
    pool.report_rate_limit("key-B")

    assert pool.get_free_key(allow_cooldown=False) is None
    # Le délai d'attente est positif et borné par le cooldown
    wait = pool.seconds_until_available()
    assert 0 < wait <= GeminiKeyPool.COOLDOWN_SECONDS


def test_toutes_en_cooldown_defaut_reste_retrocompatible(pool):
    """Mode par défaut : rend quand même la clé qui expire le plus tôt."""
    pool.report_rate_limit("key-A")
    pool.report_rate_limit("key-B")

    key = pool.get_free_key()  # allow_cooldown=True par défaut
    assert key in ("key-A", "key-B")


def test_une_seule_en_cooldown_bascule_sur_lautre(pool):
    """Une clé bloquée → l'autre est servie même en mode strict."""
    pool.report_rate_limit("key-A")
    # key-B reste disponible
    assert pool.get_free_key(allow_cooldown=False) == "key-B"


def test_expiration_cooldown_rend_la_cle_disponible(pool, monkeypatch):
    """Après expiration du cooldown, la clé redevient servie en mode strict."""
    pool.report_rate_limit("key-A")
    pool.report_rate_limit("key-B")
    assert pool.get_free_key(allow_cooldown=False) is None

    # Simuler l'écoulement du temps au-delà du cooldown
    future = time.time() + GeminiKeyPool.COOLDOWN_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: future)

    assert pool.get_free_key(allow_cooldown=False) in ("key-A", "key-B")
    assert pool.seconds_until_available() == 0.0
