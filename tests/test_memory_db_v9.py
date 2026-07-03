"""
tests/test_memory_db_v9.py — Tests unitaires pour la refonte SQLite V9 (FTS5 + Graph-RAG).

Vérifie :
  - La présence des colonnes commit_hash et severity.
  - La recherche plein texte FTS5 (BM25) avec fallback.
  - La liaison bidirectionnelle fait-entité (Graph-RAG).
  - La persistance et la cohérence relationnelle.

Usage :
    python -X utf8 -m pytest tests/test_memory_db_v9.py -v
"""

import os
import sys
import pytest

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory.memory_db import MemoryDB


@pytest.fixture(scope="function")
def temp_memory_db(tmp_path):
    """Fixture qui crée une base de données temporaire pour chaque test."""
    db_file = tmp_path / "test_memory.db"
    db = MemoryDB(str(db_file))
    yield db
    # S'assurer de fermer toutes les connexions s'il en reste (SQLite verrouille sous Windows)
    # Le destructeur se charge de la fermeture de base


class TestMemoryDBV9:
    """Tests du module memory/memory_db.py version V9 (SQLite FTS5 & Graph-RAG)."""

    def test_schema_v9_columns(self, temp_memory_db):
        """Vérifie que les nouvelles colonnes commit_hash et severity sont bien créées."""
        conn = temp_memory_db._get_conn()
        try:
            cursor = conn.execute("PRAGMA table_info(facts)")
            columns = {row["name"]: row["type"] for row in cursor.fetchall()}
            assert "commit_hash" in columns
            assert "severity" in columns
            assert columns["severity"] == "TEXT"
        finally:
            conn.close()

    def test_fact_entity_links_schema(self, temp_memory_db):
        """Vérifie que la table de liaison fact_entity_links est présente."""
        conn = temp_memory_db._get_conn()
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cursor.fetchall()]
            assert "fact_entity_links" in tables
        finally:
            conn.close()

    def test_fts5_virtual_table_and_triggers(self, temp_memory_db):
        """Vérifie que la table FTS5 fts_facts est présente et que les triggers fonctionnent."""
        # 1. Vérifier la présence de la table virtuelle
        conn = temp_memory_db._get_conn()
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cursor.fetchall()]
            assert "fts_facts" in tables
        finally:
            conn.close()

        # 2. Insérer un fait et vérifier qu'il est synchronisé dans FTS5
        fact_id = temp_memory_db.upsert_fact(
            category="esphome",
            title="Bug Rétroéclairage",
            content="Le rétroéclairage de la tablette Tab5 V2 utilise le GPIO 15.",
            tags="hardware,gpio,tab5",
            commit_hash="a1b2c3d4",
            severity="major"
        )
        
        # Vérifier directement dans la table virtuelle
        conn = temp_memory_db._get_conn()
        try:
            row = conn.execute("SELECT * FROM fts_facts WHERE fact_id = ?", (fact_id,)).fetchone()
            assert row is not None
            assert row["title"] == "Bug Rétroéclairage"
            assert row["content"] == "Le rétroéclairage de la tablette Tab5 V2 utilise le GPIO 15."
        finally:
            conn.close()

    def test_search_facts_fts5(self, temp_memory_db):
        """Vérifie que la méthode search_facts retourne les bons faits via FTS5."""
        temp_memory_db.upsert_fact(
            category="esphome",
            title="Audio DAC ES8388",
            content="Correction de l'init audio du DAC en forçant le registre 0x04 à 0x00.",
            tags="audio,i2c"
        )
        temp_memory_db.upsert_fact(
            category="moteur",
            title="Deadlock SQLite WAL",
            content="Utiliser RLock au lieu de Lock simple pour éviter les réentrances sous Windows.",
            tags="asyncio,concurrency"
        )

        # Recherche de mot-clé spécifique
        results = temp_memory_db.search_facts("Deadlock")
        assert len(results) == 1
        assert results[0]["title"] == "Deadlock SQLite WAL"
        assert "RLock" in results[0]["content"]

        # Recherche sur une autre catégorie
        results_audio = temp_memory_db.search_facts("audio")
        assert len(results_audio) == 1
        assert results_audio[0]["title"] == "Audio DAC ES8388"

    def test_graph_rag_linkage(self, temp_memory_db):
        """Vérifie le fonctionnement de la liaison Graph-RAG fait-entité."""
        # 1. Créer un fait
        fact_id = temp_memory_db.upsert_fact(
            category="esphome",
            title="Wake word active switch",
            content="Le commutateur de wake word s'appuie sur boot_complete pour éviter les bootloops.",
            tags="esphome,wakeword"
        )

        # 2. Créer une entité dans le graphe
        temp_memory_db.upsert_graph_entity(
            name="switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active",
            entity_type="HA_Entity",
            observations=["Vrai commutateur du wake word Tab5 V2"]
        )

        # 3. Créer la liaison
        ok = temp_memory_db.link_fact_to_entity(
            fact_id=fact_id,
            entity_name="switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active"
        )
        assert ok is True

        # 4. Rechercher les faits connectés à l'entité
        facts = temp_memory_db.get_connected_facts_for_entity(
            entity_name="switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active"
        )
        assert len(facts) == 1
        assert facts[0]["id"] == fact_id
        assert facts[0]["title"] == "Wake word active switch"

        # 5. Rechercher les entités connectées au fait
        entities = temp_memory_db.get_connected_entities_for_fact(fact_id=fact_id)
        assert len(entities) == 1
        assert entities[0]["name"] == "switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active"

    @pytest.mark.asyncio
    async def test_async_graph_rag_linkage(self, temp_memory_db):
        """Vérifie le fonctionnement asynchrone de la liaison fait-entité."""
        # 1. Créer un fait
        fact_id = await temp_memory_db.upsert_fact_async(
            category="esphome",
            title="Audio DAC ES8388 Async",
            content="Le registre I2C 0x04 doit être configuré à 0x00 sur le Tab5 V2.",
            tags="audio,i2c"
        )

        # 2. Créer l'entité graphe
        await temp_memory_db.upsert_graph_entity_async(
            name="media_player.m5stack_tab5_home_assistant_hmi_tab5_media_player",
            entity_type="HA_Entity",
            observations=["Vrai lecteur audio du Tab5 V2"]
        )

        # 3. Créer la liaison asynchrone
        ok = await temp_memory_db.link_fact_to_entity_async(
            fact_id=fact_id,
            entity_name="media_player.m5stack_tab5_home_assistant_hmi_tab5_media_player"
        )
        assert ok is True

        # 4. Récupérer les faits asynchronement
        facts = await temp_memory_db.get_connected_facts_for_entity_async(
            entity_name="media_player.m5stack_tab5_home_assistant_hmi_tab5_media_player"
        )
        assert len(facts) == 1
        assert facts[0]["id"] == fact_id
        assert facts[0]["title"] == "Audio DAC ES8388 Async"


class TestGraphGCOrdering:
    """[P2-3.2] Le GC du graphe doit préserver l'ordre chronologique des observations."""

    def test_upsert_merge_preserves_order(self, temp_memory_db):
        """Les fusions d'observations conservent l'ordre (dict.fromkeys, pas set())."""
        for i in range(20):
            temp_memory_db.upsert_graph_entity("E1", "concept", [f"obs_{i:02d}"])
        graph = temp_memory_db.get_full_graph()
        ent = next(e for e in graph["entities"] if e["name"] == "E1")
        assert ent["observations"] == [f"obs_{i:02d}" for i in range(20)]

    def test_gc_keeps_most_recent_observations(self, temp_memory_db):
        """Le GC archive les anciennes et garde les N observations LES PLUS RÉCENTES."""
        for i in range(20):
            temp_memory_db.upsert_graph_entity("E2", "concept", [f"obs_{i:02d}"])
        temp_memory_db.gc_graph_entities(max_observations=5, max_age_days=3650)
        graph = temp_memory_db.get_full_graph()
        ent = next(e for e in graph["entities"] if e["name"] == "E2")
        # 1ère ligne = résumé GC, puis exactement les 5 dernières observations
        assert ent["observations"][0].startswith("[GC]")
        assert ent["observations"][1:] == [f"obs_{i:02d}" for i in range(15, 20)]

    def test_duplicate_observations_are_deduped(self, temp_memory_db):
        """Les doublons sont éliminés tout en gardant l'ordre de première apparition."""
        temp_memory_db.upsert_graph_entity("E3", "concept", ["a", "b"])
        temp_memory_db.upsert_graph_entity("E3", "concept", ["b", "c", "a", "d"])
        graph = temp_memory_db.get_full_graph()
        ent = next(e for e in graph["entities"] if e["name"] == "E3")
        assert ent["observations"] == ["a", "b", "c", "d"]
