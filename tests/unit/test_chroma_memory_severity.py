"""
tests/unit/test_chroma_memory_severity.py — Régression #T252.

Symptôme production (Deck, 10/08) : à chaque cycle du Dreamer, l'étape 2.8
journalisait :
    [DREAMER] [Etape 2.8] ChromaDB non disponible (non bloquant) :
        invalid literal for int() with base 10: 'minor'

Cause racine : la colonne `facts.severity` est TEXT depuis la V9
('minor'/'major'/'critical', cf. schéma memory_db.py), mais
`migrate_facts_from_sqlite` la castait en `int()` → ValueError sur le premier
fait non déprécié, propagé jusqu'au try/except de l'étape 2.8 qui le masquait
sous un faux « ChromaDB non disponible ».

Ce test reproduit le scénario : une vraie base SQLite avec un fait
severity='minor' et une fausse collection ChromaDB (aucun backend réel).
"""

import asyncio
import sqlite3

import pytest

from memory.chroma_memory import ChromaMemory


class FakeCollection:
    """Fausse collection ChromaDB : mémorise les upserts, aucun backend réel."""

    def __init__(self):
        self.upserts = []

    def get(self, **kwargs):
        return {"ids": []}

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


@pytest.fixture
def chroma_memory():
    """ChromaMemory sans __init__ (évite PersistentClient et le modèle ONNX)."""
    mem = object.__new__(ChromaMemory)
    mem.facts_col = FakeCollection()
    return mem


def _seed_facts_db(tmp_path, severity: str = "minor"):
    """Crée une base memory.db minimale avec un fait V9 (severity TEXT)."""
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY,
            category TEXT,
            title TEXT,
            content TEXT,
            tags TEXT,
            severity TEXT DEFAULT 'minor',
            created_at REAL,
            is_deprecated INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO facts (category, title, content, severity) VALUES (?, ?, ?, ?)",
        ("lecon", "Titre de test", "Contenu de test", severity),
    )
    conn.commit()
    conn.close()
    return str(db)


def test_migrate_facts_avec_severity_texte(chroma_memory, tmp_path):
    """
    Un fait V9 avec severity='minor' (TEXT) doit migrer sans lever d'exception :
    la métadonnée ChromaDB stocke la chaîne telle quelle, pas un int().
    """
    db_path = _seed_facts_db(tmp_path)

    result = asyncio.run(chroma_memory.migrate_facts_from_sqlite(db_path))

    assert result["migrated"] == 1
    assert result["skipped"] == 0
    assert len(chroma_memory.facts_col.upserts) == 1
    metadata = chroma_memory.facts_col.upserts[0]["metadatas"][0]
    assert metadata["severity"] == "minor"


def test_migrate_facts_severity_numerique_legacy(chroma_memory, tmp_path):
    """
    Tolérance legacy : une ancienne base avec severity numérique (int SQLite)
    doit aussi migrer (str() gère les deux formes).
    """
    db = tmp_path / "memory_legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY,
            category TEXT,
            title TEXT,
            content TEXT,
            tags TEXT,
            severity TEXT,
            created_at REAL,
            is_deprecated INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO facts (category, title, content, severity) VALUES (?, ?, ?, ?)",
        ("lecon", "Titre legacy", "Contenu legacy", 2),
    )
    conn.commit()
    conn.close()

    result = asyncio.run(chroma_memory.migrate_facts_from_sqlite(str(db)))

    assert result["migrated"] == 1
    metadata = chroma_memory.facts_col.upserts[0]["metadatas"][0]
    assert metadata["severity"] == "2"
