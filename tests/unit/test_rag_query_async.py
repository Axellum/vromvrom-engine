"""Test du wrapper async du RAG (#T61).

`RAGEngine.query_async` doit déléguer à `query` sans bloquer l'event loop
(exécution dans un thread via asyncio.to_thread) et préserver les arguments.
"""
import asyncio

from memory.rag import RAGEngine


def test_query_async_delegue_a_query_dans_un_thread():
    """query_async renvoie le résultat de query et s'exécute hors event loop."""
    engine = RAGEngine.__new__(RAGEngine)  # pas d'init lourde (ChromaDB, embeddings)

    captured = {}
    main_thread = __import__("threading").get_ident()

    def fake_query(user_query, top_n=3, allowed_categories=None):
        captured["args"] = (user_query, top_n, allowed_categories)
        captured["thread"] = __import__("threading").get_ident()
        return f"RESULT::{user_query}::{top_n}"

    engine.query = fake_query

    result = asyncio.run(
        engine.query_async("optimiser la conso", top_n=5, allowed_categories=["moteur"])
    )

    assert result == "RESULT::optimiser la conso::5"
    assert captured["args"] == ("optimiser la conso", 5, ["moteur"])
    # to_thread → exécuté dans un thread worker, pas le thread principal
    assert captured["thread"] != main_thread


def test_query_async_est_bien_une_coroutine():
    """hasattr(rag_engine, 'query_async') + coroutine → la branche router l'utilise."""
    engine = RAGEngine.__new__(RAGEngine)
    engine.query = lambda *a, **k: ""
    coro = engine.query_async("x")
    assert asyncio.iscoroutine(coro)
    asyncio.run(coro)  # consommer la coroutine pour éviter le warning
