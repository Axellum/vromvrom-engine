"""
Tests de concurrence SQLite (T86) — écritures simultanées sur memory_db et runtime_db.

Couvre :
  1. 5 coroutines écrivant en parallèle dans models_registry.db (test historique)
  2. 5 coroutines écrivant des faits dans memory.db (MemoryDB.upsert_fact)
  3. touch_fact concurrent sur le même fact_id
  4. Décroissance de pertinence pendant des écritures concurrentes
"""
import asyncio
import gc
import os
import sqlite3
import tempfile
import threading
import time
import pytest

from core.models_db import upsert_model, upsert_provider
import core.models_db as models_db


# ─── 1. Test historique : models_registry.db ─────────────────────────────────

@pytest.fixture
def isolated_models_db(monkeypatch):
    """
    Isole core.models_db de la vraie models_registry.db partagée (base de dev).

    Sans ce fixture, upsert_model/upsert_provider écrivent directement dans
    moteur_agents/models_registry.db — polluant la base de développement partagée
    avec des lignes de test (bug découvert le 03/07/2026 : provider
    "anthropic_gcp_chaos" + ~100 modèles "chaos_model_N"/"test_model_N").

    Les connexions SQLite de core.models_db sont mises en cache par thread
    (_thread_local.conn) — remplacer _thread_local par une nouvelle instance force
    TOUS les threads (y compris ceux déjà utilisés par un test précédent dans le
    même process pytest, via le pool d'asyncio.to_thread) à rouvrir une connexion
    contre le nouveau chemin patché au prochain appel. monkeypatch restaure les
    deux attributs automatiquement à la fin du test.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    monkeypatch.setattr(models_db, "_DB_PATH", db_path)
    monkeypatch.setattr(models_db, "_thread_local", threading.local())

    yield db_path

    # monkeypatch vient de remettre models_db._thread_local à l'original, donc les
    # connexions sqlite3 ouvertes pendant le test (une par worker thread utilisé par
    # asyncio.to_thread) deviennent orphelines et ne sont fermées que par le garbage
    # collector — non-déterministe. Sur Windows, un fichier avec un handle encore
    # ouvert ne peut pas être unlink() (contrairement à POSIX) : on force gc.collect()
    # et on retente brièvement avant d'abandonner proprement (fichier temp orphelin
    # dans %TEMP%, sans effet sur models_registry.db — la vraie base n'a jamais été
    # touchée par ce test).
    gc.collect()
    for attempt in range(5):
        try:
            os.unlink(db_path)
            break
        except PermissionError:
            if attempt == 4:
                break
            time.sleep(0.05)
            gc.collect()


@pytest.mark.asyncio
async def test_sqlite_concurrency_models(isolated_models_db):
    """100 insertions concurrentes dans une DB isolée (pas la vraie base de dev)."""
    provider_id = "anthropic_gcp_chaos"

    upsert_provider(
        provider_id,
        name="Anthropic GCP Chaos Test",
        type="pay_as_you_go",
    )

    async def insert_task(idx):
        await asyncio.sleep(0.001 * (idx % 5))
        await asyncio.to_thread(
            upsert_model,
            model_id=f"chaos_model_{idx}",
            provider_id=provider_id,
        )

    await asyncio.gather(*(insert_task(i) for i in range(100)))

    conn = sqlite3.connect(isolated_models_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM models WHERE provider_id=? AND id LIKE 'chaos_model_%'",
        (provider_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()

    assert count >= 100, f"Attendu >= 100 modèles, obtenu {count}"


# ─── 2. Concurrence sur memory_db (MemoryDB.upsert_fact) ─────────────────────

@pytest.mark.asyncio
async def test_memory_db_concurrent_upsert():
    """5 agents écrivent des faits en parallèle dans memory.db — pas de corruption."""
    from memory.memory_db import MemoryDB

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = MemoryDB(db_path)

        async def agent_write(agent_id: int):
            for i in range(10):
                await asyncio.to_thread(
                    db.upsert_fact,
                    category=f"agent_{agent_id}",
                    title=f"Fait agent {agent_id} numéro {i}",
                    content=f"Contenu du fait {i} produit par l'agent {agent_id}",
                    source_file="test_chaos",
                )

        await asyncio.gather(*[agent_write(a) for a in range(5)])

        # Vérification : 50 faits uniques attendus (5 agents × 10 faits)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM facts")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 50, f"Attendu 50 faits, obtenu {count}"
    finally:
        os.unlink(db_path)


# ─── 3. touch_fact concurrent sur le même fait ───────────────────────────────

@pytest.mark.asyncio
async def test_touch_fact_concurrent():
    """20 coroutines appelant touch_fact sur le même ID en parallèle."""
    from memory.memory_db import MemoryDB

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = MemoryDB(db_path)
        db.ensure_facts_columns()
        fact_id = db.upsert_fact(
            category="test",
            title="Fait partagé concurrent",
            content="Contenu",
        )

        async def touch():
            await asyncio.to_thread(db.touch_fact, fact_id)

        await asyncio.gather(*[touch() for _ in range(20)])

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT access_count FROM facts WHERE id = ?", (fact_id,))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["access_count"] == 20, (
            f"Attendu access_count=20 après 20 touch_fact, obtenu {row['access_count']}"
        )
    finally:
        os.unlink(db_path)


# ─── 4. decay_relevance pendant des écritures ────────────────────────────────

@pytest.mark.asyncio
async def test_decay_and_write_concurrent():
    """decay_relevance() et upsert_fact() s'exécutent en parallèle sans deadlock."""
    from memory.memory_db import MemoryDB

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = MemoryDB(db_path)

        # Pré-peupler quelques faits
        for i in range(10):
            db.upsert_fact(
                category="decay_test",
                title=f"Fait decay {i}",
                content=f"Contenu {i}",
            )

        async def decay():
            for _ in range(5):
                await asyncio.to_thread(db.decay_relevance, 0.01, 0.1)
                await asyncio.sleep(0.005)

        async def write():
            for i in range(10, 20):
                await asyncio.to_thread(
                    db.upsert_fact,
                    category="decay_test",
                    title=f"Fait decay {i}",
                    content=f"Contenu {i}",
                )
                await asyncio.sleep(0.003)

        # Les deux doivent se terminer sans exception
        await asyncio.gather(decay(), write())

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM facts WHERE category='decay_test'")
        count = cursor.fetchone()[0]
        conn.close()

        assert count >= 10, f"Les faits doivent subsister après decay, obtenu {count}"
    finally:
        os.unlink(db_path)
