# -*- coding: utf-8 -*-
"""Tests du cache sémantique LLM (Phase 3, item 17)."""

import pytest

from core.semantic_cache import SemanticCache, _prompt_id, CHROMA_AVAILABLE

pytestmark = pytest.mark.skipif(not CHROMA_AVAILABLE, reason="chromadb non installé")


def _cache(threshold=0.95):
    import chromadb, uuid
    # Client éphémère + collection UNIQUE par test (EphemeralClient est partagé
    # dans le process → sans nom unique les tests se contamineraient).
    return SemanticCache(
        client=chromadb.EphemeralClient(),
        collection_name=f"test_cache_{uuid.uuid4().hex}",
        similarity_threshold=threshold,
    )


def test_exact_prompt_hits():
    c = _cache()
    assert c.get("allume la lumière du salon") is None  # vide au départ
    c.put("allume la lumière du salon", "OK, lumière allumée", model="test")
    assert c.get("allume la lumière du salon") == "OK, lumière allumée"


def test_unrelated_prompt_misses():
    c = _cache()
    c.put("quelle est la météo à Paris", "Il fait beau", model="test")
    # Prompt sémantiquement très éloigné → pas de hit au seuil 0.95.
    assert c.get("refactorise cette fonction récursive en Python") is None


def test_upsert_is_idempotent():
    c = _cache()
    c.put("bonjour", "salut 1")
    c.put("bonjour", "salut 2")  # même id → écrase
    assert c.collection.count() == 1
    assert c.get("bonjour") == "salut 2"


def test_threshold_controls_hit():
    # Seuil 0.0 : tout est un hit (la requête renvoie toujours le plus proche).
    c = _cache(threshold=0.0)
    c.put("texte de référence", "réponse")
    assert c.get("quelque chose de complètement différent") == "réponse"


def test_stats_and_prompt_id():
    c = _cache()
    c.put("a", "ra")
    c.get("a")          # hit
    c.get("zzz autre")  # miss
    s = c.stats()
    assert s["enabled"] is True
    assert s["hits"] == 1 and s["misses"] == 1
    assert s["hit_rate"] == 0.5
    assert _prompt_id("a") == _prompt_id("a") and len(_prompt_id("a")) == 16


# ── Tests de câblage sur le chokepoint FallbackProvider ──────────────────────

class _CountingProvider:
    """Provider factice : compte ses appels et renvoie une réponse adéquate fixe."""
    def __init__(self):
        self.calls = 0
    def generate(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        return "Réponse suffisamment longue pour être jugée adéquate."


def test_fallback_provider_uses_semantic_cache(monkeypatch):
    """2ᵉ appel identique → servi par le cache, le provider n'est PAS rappelé."""
    from core.llm.providers.deepseek import FallbackProvider
    import core.semantic_cache as sc_mod

    c = _cache()
    monkeypatch.setattr(sc_mod, "get_semantic_cache", lambda: c)

    fake = _CountingProvider()
    fp = FallbackProvider([("fake-model-cache-test", fake)])

    r1 = fp.generate("SYS", "même question")
    r2 = fp.generate("SYS", "même question")

    assert r1 == r2
    assert fake.calls == 1  # le 2ᵉ appel a été servi par le cache


def test_fallback_provider_opt_out(monkeypatch):
    """use_semantic_cache=False → le cache est court-circuité, provider rappelé."""
    from core.llm.providers.deepseek import FallbackProvider
    import core.semantic_cache as sc_mod

    c = _cache()
    monkeypatch.setattr(sc_mod, "get_semantic_cache", lambda: c)

    fake = _CountingProvider()
    fp = FallbackProvider([("fake-model-optout", fake)])

    fp.generate("SYS", "question", use_semantic_cache=False)
    fp.generate("SYS", "question", use_semantic_cache=False)

    assert fake.calls == 2  # aucun hit : le cache est ignoré
