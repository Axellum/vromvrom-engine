"""Tests du cache sémantique LLM (Phase 3, item 17)."""

import pytest

from core.semantic_cache import CHROMA_AVAILABLE, SemanticCache, _prompt_id

pytestmark = pytest.mark.skipif(not CHROMA_AVAILABLE, reason="chromadb non installé")


class _HashEmbedding:
    """Embedding 100% local et déterministe : remplace l'ONNX par défaut de chromadb,
    qui télécharge son modèle depuis S3 au premier usage (réseau externe interdit par
    le garde-fou de tests/unit/conftest.py — voir #T261).

    Vecteur one-hot indexé par le hash du texte (cosine) : deux textes identiques →
    similarité 1, deux textes distincts → 0. Suffisant pour tester la logique de
    similarité du cache sans dépendre d'un cache modèle local ni du réseau.
    """

    # Contrat du protocole chromadb EmbeddingFunction (1.5.x) : `name` est une
    # méthode, appelée lors de la (dé)sérialisation de la config de collection.
    @staticmethod
    def name() -> str:
        return "hash-embedding-local"

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]

    def __call__(self, input):
        import hashlib

        texts = [input] if isinstance(input, str) else list(input)
        vecs = []
        for text in texts:
            idx = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:2], "big") % 65536
            vec = [0.0] * 65536
            vec[idx] = 1.0
            vecs.append(vec)
        return vecs

    def embed_query(self, input):
        # chromadb 1.5.x appelle embed_query() pour les query_texts.
        return self.__call__(input)

    def is_legacy(self) -> bool:
        return False

    def get_config(self):
        return {}

    @classmethod
    def build_from_config(cls, config):
        return cls()

    def validate_config_update(self, old_config, new_config):
        pass


def _cache(threshold=0.95):
    import uuid

    import chromadb
    from chromadb.config import Settings

    # Client éphémère + collection UNIQUE par test (EphemeralClient est partagé
    # dans le process → sans nom unique les tests se contamineraient).
    # Télémetrie coupée + embedding local : sans cela, le DefaultEmbeddingFunction
    # ONNX de chromadb télécharge son modèle depuis S3 au premier usage.
    return SemanticCache(
        client=chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False)),
        collection_name=f"test_cache_{uuid.uuid4().hex}",
        similarity_threshold=threshold,
        embedding_function=_HashEmbedding(),
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
    import core.semantic_cache as sc_mod
    from core.llm.providers.deepseek import FallbackProvider

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
    import core.semantic_cache as sc_mod
    from core.llm.providers.deepseek import FallbackProvider

    c = _cache()
    monkeypatch.setattr(sc_mod, "get_semantic_cache", lambda: c)

    fake = _CountingProvider()
    fp = FallbackProvider([("fake-model-optout", fake)])

    fp.generate("SYS", "question", use_semantic_cache=False)
    fp.generate("SYS", "question", use_semantic_cache=False)

    assert fake.calls == 2  # aucun hit : le cache est ignoré
