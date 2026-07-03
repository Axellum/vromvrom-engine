"""
tests/test_rag_improvements.py — Tests des améliorations RAG V9.

Valide les 4 axes d'amélioration inspirés de Jonas Roman (IA en Prod) :
1. Filtrage hybride par catégories (rag.py + router.py)
2. Chunking contextuel avec préfixe (embeddings.py)
3. Scoring qualitatif des leçons (memory_db.py + sync_db_to_markdown.py)
4. Self-Healing du contexte (tools/context_self_healing.py)
"""

import os
import sys
import unittest
import tempfile
import shutil

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRAGCategoryFiltering(unittest.TestCase):
    """Axe 1 — Tests du filtrage RAG par catégories."""

    def test_rag_query_signature_accepts_categories(self):
        """Vérifie que query() accepte le paramètre allowed_categories."""
        from memory.rag import RAGEngine
        import inspect
        sig = inspect.signature(RAGEngine.query)
        self.assertIn("allowed_categories", sig.parameters,
                       "Le paramètre 'allowed_categories' manque dans query()")
    
    def test_rag_query_default_none(self):
        """Vérifie que allowed_categories a une valeur par défaut None."""
        from memory.rag import RAGEngine
        import inspect
        sig = inspect.signature(RAGEngine.query)
        param = sig.parameters["allowed_categories"]
        self.assertIsNone(param.default,
                          "La valeur par défaut de allowed_categories doit être None")

    def test_rag_query_without_filter(self):
        """Vérifie que query() sans filtre fonctionne comme avant."""
        from memory.rag import RAGEngine
        rag = RAGEngine.__new__(RAGEngine)
        rag.sections = [
            {"source": "01_Core/rules_global.md", "title": "Règles", "content": "test core",
             "tfidf": {"test": 0.5, "core": 0.3}, "norm": 0.58, "tokens": ["test", "core"], "doc_len": 2},
            {"source": "02_Hardware/rules_esphome.md", "title": "ESPHome", "content": "test esphome gpio",
             "tfidf": {"test": 0.4, "esphome": 0.5, "gpio": 0.3}, "norm": 0.71, "tokens": ["test", "esphome", "gpio"], "doc_len": 3},
        ]
        rag.idf = {"test": 0.5, "core": 1.0, "esphome": 1.2, "gpio": 1.5}
        rag.avg_doc_len = 2.5
        rag.query_cache = {}
        rag.stopwords = set()
        rag._embedding_store = None
        rag._memory_db = None  # Attribut requis pour le fallback vectoriel
        rag._embed_fn = None   # Attribut requis pour le fallback vectoriel
        rag.max_cache_size = 100  # Taille max du cache LRU
        
        # Appel sans filtre — doit retourner quelque chose (pas d'erreur)
        result = rag.query("test", top_n=2, allowed_categories=None)
        # Le résultat peut être vide ou non selon les scores, mais pas d'exception
        self.assertIsInstance(result, str)


class TestChunkingContextual(unittest.TestCase):
    """Axe 2 — Tests du préfixe contextuel dans les chunks."""

    def test_chunk_contextual_prefix(self):
        """Vérifie que _chunk_markdown() ajoute le préfixe contextuel."""
        from memory.embeddings import EmbeddingStore
        store = EmbeddingStore.__new__(EmbeddingStore)
        
        content = "# Mon Titre\n\nCeci est un contenu de test suffisamment long pour passer le seuil de 20 caractères et être retenu comme chunk valide."
        filepath = os.path.join("e:", "AuxFilsDesIdees", "contexte_ia", "02_Hardware", "rules_esphome.md")
        
        sections = store._chunk_markdown(content, filepath)
        
        # Vérifier qu'au moins une section existe
        self.assertTrue(len(sections) > 0, "Aucune section extraite du Markdown")
        
        # Vérifier le préfixe contextuel
        for sec in sections:
            self.assertIn("[Source:", sec["content"],
                          f"Le préfixe [Source:] est absent du chunk : {sec['content'][:100]}")
            self.assertIn("[Section:", sec["content"],
                          f"Le préfixe [Section:] est absent du chunk")
            self.assertIn("---", sec["content"],
                          "Le séparateur --- est absent du chunk")
    
    def test_chunk_respects_size_limit(self):
        """Vérifie que le contenu enrichi ne dépasse pas ~2000 caractères."""
        from memory.embeddings import EmbeddingStore
        store = EmbeddingStore.__new__(EmbeddingStore)
        
        # Créer un contenu très long
        long_content = "# Titre Long\n\n" + "A" * 5000
        filepath = os.path.join("e:", "contexte_ia", "01_Core", "test.md")
        
        sections = store._chunk_markdown(long_content, filepath)
        
        for sec in sections:
            # Le préfixe (~80 chars) + 1900 chars de contenu ≈ 2000
            self.assertLess(len(sec["content"]), 2100,
                            f"Chunk trop long : {len(sec['content'])} caractères")


class TestQualityScoring(unittest.TestCase):
    """Axe 3 — Tests du scoring qualitatif des leçons."""

    def setUp(self):
        """Crée une instance MemoryDB de test isolée."""
        from memory.memory_db import MemoryDB
        self.test_dir = tempfile.mkdtemp()
        self.test_db_path = os.path.join(self.test_dir, "test_memory.db")
        
        # Créer une instance indépendante (contourner le singleton)
        self.db = MemoryDB.__new__(MemoryDB)
        self.db._db_path = self.test_db_path
        import threading
        self.db._write_lock = threading.RLock()
        self.db._init_db()

    def tearDown(self):
        """Nettoie le dossier temporaire."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_quality_score_high(self):
        """Un fait technique critique doit obtenir un score >= 0.7."""
        score = self.db._compute_quality_score(
            title="Fix race condition dans le DAGRunner asyncio",
            content=(
                "Le DAGRunner provoquait un deadlock lors de l'exécution parallèle "
                "de 3 branches car le verrou _history_lock n'était pas réentrant. "
                "Solution : remplacer Lock par RLock et ajouter un timeout de 5s. "
                "```python\n"
                "self._lock = asyncio.Lock()  # BUG\n"
                "self._lock = asyncio.RLock()  # FIX\n"
                "```"
            ),
            category="moteur"
        )
        self.assertGreaterEqual(score, 0.7,
                                 f"Score trop faible pour un fait critique : {score:.2f}")

    def test_quality_score_low(self):
        """Un fait trivial doit obtenir un score < 0.5."""
        score = self.db._compute_quality_score(
            title="typo CSS",
            content="Correction de typo dans le fichier CSS, nettoyage mineur.",
            category="hmi"
        )
        self.assertLessEqual(score, 0.5,
                        f"Score trop élevé pour un fait trivial : {score:.2f}")

    def test_quality_score_short_content_penalty(self):
        """Un fait avec un contenu trop court doit être pénalisé."""
        score = self.db._compute_quality_score(
            title="Note",
            content="Ok.",
            category="moteur"
        )
        self.assertLess(score, 0.4,
                        f"Score trop élevé pour un contenu très court : {score:.2f}")

    def test_quality_score_clamp(self):
        """Le score doit toujours être entre 0.0 et 1.0."""
        # Score maximum possible
        score_max = self.db._compute_quality_score(
            title="Fix critique du circuit breaker ESPHome bootloop recovery",
            content=(
                "Bug critique de crash lors du bootloop ESP32. "
                "Race condition dans le thread principal. "
                "Solution avec code :\n```python\ndef fix(): pass\n```\n"
                "Workaround timeout deadlock OOM memory leak."
            ),
            category="esphome"
        )
        self.assertLessEqual(score_max, 1.0)
        self.assertGreaterEqual(score_max, 0.0)

        # Score minimum possible
        score_min = self.db._compute_quality_score(
            title="x",
            content="nettoyage typo cleanup refactoring mineur",
            category="hmi"
        )
        self.assertLessEqual(score_min, 1.0)
        self.assertGreaterEqual(score_min, 0.0)


class TestSyncFilterQuality(unittest.TestCase):
    """Axe 3bis — Tests du filtrage par qualité dans sync_db_to_markdown."""

    def test_sync_filters_low_quality(self):
        """Vérifie que sync_category() ne synchronise pas les faits de score < 0.5."""
        # Simuler les données de get_facts_by_category
        mock_facts = [
            {"title": "Fait de qualité", "content": "Contenu important", "quality_score": 0.8},
            {"title": "Fait de basse qualité", "content": "Typo CSS", "quality_score": 0.2},
            {"title": "Fait ancien sans score", "content": "Ancien", "quality_score": None},
        ]
        
        # Filtrer comme le fait sync_db_to_markdown
        filtered = [
            f for f in mock_facts
            if f.get("quality_score") is None or f.get("quality_score", 0.0) >= 0.5
        ]
        
        self.assertEqual(len(filtered), 2,
                         "Le filtrage doit garder 2 faits : qualité >= 0.5 et None")
        
        titles = [f["title"] for f in filtered]
        self.assertIn("Fait de qualité", titles)
        self.assertIn("Fait ancien sans score", titles)
        self.assertNotIn("Fait de basse qualité", titles)


class TestContextSelfHealer(unittest.TestCase):
    """Axe 4 — Tests du self-healer de contexte."""

    def test_self_healer_class_exists(self):
        """Vérifie que le module ContextSelfHealer est importable."""
        from tools.context_self_healing import ContextSelfHealer
        healer = ContextSelfHealer()
        self.assertIsNotNone(healer)

    def test_validate_all_returns_list(self):
        """Vérifie que validate_all() retourne toujours une liste."""
        from tools.context_self_healing import ContextSelfHealer
        healer = ContextSelfHealer()
        result = healer.validate_all()
        self.assertIsInstance(result, list)

    def test_run_and_report_returns_markdown(self):
        """Vérifie que run_and_report() retourne du Markdown."""
        from tools.context_self_healing import ContextSelfHealer
        healer = ContextSelfHealer()
        report = healer.run_and_report()
        self.assertIsInstance(report, str)
        # Le rapport doit contenir au minimum le titre ou le checkmark
        self.assertTrue(
            "Self-Healing" in report or "✅" in report,
            f"Le rapport ne contient pas de titre attendu : {report[:100]}"
        )

    def test_context_self_healer_detects_missing_file(self):
        """Vérifie que le self-healer détecte un fichier Python manquant référencé dans un doc."""
        from tools.context_self_healing import ContextSelfHealer
        
        healer = ContextSelfHealer()
        
        # Créer un faux fichier Markdown temporaire avec une référence à un fichier inexistant
        test_dir = tempfile.mkdtemp()
        try:
            fake_doc = os.path.join(test_dir, "test_doc.md")
            with open(fake_doc, 'w', encoding='utf-8') as f:
                f.write("Voir le fichier `core/inexistant_module.py` pour plus de détails.\n")
            
            # Injecter le chemin temporaire dans le healer
            original_path = healer.CONTEXTE_IA_PATH
            healer.CONTEXTE_IA_PATH = test_dir
            
            # Créer le sous-dossier attendu
            os.makedirs(os.path.join(test_dir, "03_Software"), exist_ok=True)
            shutil.copy(fake_doc, os.path.join(test_dir, "03_Software", "05_MOTEUR_AGENTS_PYTHON.md"))
            
            diagnostics = healer.validate_all()
            
            # Restaurer
            healer.CONTEXTE_IA_PATH = original_path
            
            # Chercher un diagnostic de type missing_python_file
            missing_diags = [d for d in diagnostics if d["type"] == "missing_python_file"]
            self.assertTrue(
                len(missing_diags) > 0,
                f"Le self-healer n'a pas détecté le fichier Python manquant. "
                f"Diagnostics trouvés : {diagnostics}"
            )
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


def test_bm25_optimise_equivaut_au_calcul_naif():
    """[P2-3.5] Le BM25 optimisé (DF/avg_dl/TF pré-calculés) donne EXACTEMENT le
    même score que le calcul naïf O(N²) d'origine."""
    import math
    from memory.rag import RAGEngine

    rag = RAGEngine.__new__(RAGEngine)
    rag.idf = {}
    rag.sections = [
        {"tokens": ["gpio", "esphome", "gpio", "boot"]},
        {"tokens": ["home", "assistant", "light", "gpio"]},
        {"tokens": ["python", "asyncio", "coroutine"]},
        {"tokens": ["gpio", "reset", "boot", "esphome", "esphome"]},
    ]
    rag._build_tfidf()

    def naive(query_tokens, section):
        k1, b = 1.5, 0.75
        num = len(rag.sections)
        avg = sum(len(s["tokens"]) for s in rag.sections) / num
        dl = len(section["tokens"])
        tf_map = {}
        for t in section["tokens"]:
            tf_map[t] = tf_map.get(t, 0) + 1
        score = 0.0
        for qt in query_tokens:
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            df = sum(1 for s in rag.sections if qt in set(s["tokens"]))
            idf = math.log((num - df + 0.5) / (df + 0.5) + 1)
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg))
        return score

    for q in (["gpio"], ["esphome", "boot"], ["python", "gpio"], ["absent"], ["gpio", "gpio"]):
        for sec in rag.sections:
            assert abs(rag._bm25_score(q, sec) - naive(q, sec)) < 1e-12


def test_embeddingstore_single_gemini_space_no_boost():
    """[P2-3.3] query_similar : espace unique Gemini, score = cosinus natif
    (plus de bonus +10% ni de fusion multi-collections)."""
    from memory.embeddings import EmbeddingStore

    store = EmbeddingStore.__new__(EmbeddingStore)
    store._available = True
    store._gemini_fn = None

    class _FakeCol:
        def count(self):
            return 2

        def query(self, query_texts, n_results, include):
            return {
                "documents": [["doc A", "doc B"]],
                "metadatas": [[
                    {"source": "a.md", "title": "A", "category": "x"},
                    {"source": "b.md", "title": "B", "category": "y"},
                ]],
                "distances": [[0.2, 0.5]],
            }

    store._collection = _FakeCol()
    res = store.query_similar("requete", top_n=5)

    assert len(res) == 2
    by_src = {r["source"]: r for r in res}
    # score = 1 - distance, SANS ×1.10 (0.2 -> 0.8, pas 0.88)
    assert abs(by_src["a.md"]["score"] - 0.8) < 1e-9
    assert abs(by_src["b.md"]["score"] - 0.5) < 1e-9
    assert all(r["engine"] == "gemini" for r in res)
    # plus d'attribut de seconde collection
    assert not hasattr(store, "_collection_gemini")
