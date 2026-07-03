#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory/chroma_memory.py — Mémoire vectorielle ChromaDB persistante.

Stocke et recherche :
- Les épisodes compressés (DreamerAgent V9.2) → collection 'episodes'
- Les faits de memory.db → collection 'facts'

Intégré dans le pipeline RAG hybride (triple : TF-IDF + BM25 + ChromaDB).
Le DreamerAgent appelle migrate_*() chaque nuit (étape 2.8).

ChromaDB est optionnel : si absent, ChromaMemoryStub intercepte
silencieusement toutes les méthodes (no-op).

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Import ChromaDB (optionnel)
# ──────────────────────────────────────────────────────────────────

try:
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    CHROMA_AVAILABLE = True
    logger.debug("[CHROMA] chromadb disponible")
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("[CHROMA] chromadb non installé — utilisation du stub (pip install chromadb)")


# ──────────────────────────────────────────────────────────────────
# Stub : même interface, no-op (chromadb absent)
# ──────────────────────────────────────────────────────────────────

class ChromaMemoryStub:
    """
    Implémentation stub quand chromadb n'est pas installé.
    Même API que ChromaMemory — ne fait rien mais ne plante pas.
    """

    def __init__(self, persist_dir: str = "./chroma_db"):
        self.persist_dir = persist_dir
        logger.warning("[CHROMA] ChromaMemoryStub actif (chromadb absent)")

    async def migrate_compressed_episodes(self, episodes_dir: str) -> dict:
        return {"migrated": 0, "skipped": 0, "errors": 0, "stub": True}

    async def migrate_facts_from_sqlite(self, db_path: str) -> dict:
        return {"migrated": 0, "skipped": 0, "stub": True}

    async def search_episodes(
        self, query: str, n_results: int = 5, min_date: str = None
    ) -> List[dict]:
        return []

    async def search_facts(
        self, query: str, n_results: int = 5, category: str = None
    ) -> List[dict]:
        return []

    async def hybrid_search(self, query: str, n_results: int = 5) -> dict:
        return {"episodes": [], "facts": [], "merged": []}

    def get_stats(self) -> dict:
        return {"episodes": 0, "facts": 0, "available": False}


# ──────────────────────────────────────────────────────────────────
# ChromaMemory : implémentation complète
# ──────────────────────────────────────────────────────────────────

class ChromaMemory:
    """
    Gestionnaire de mémoire vectorielle ChromaDB persistante.

    Deux collections :
    - 'episodes' : épisodes compressés (DreamerAgent V9.2, is_compressed=True)
    - 'facts'    : faits non dépréciés depuis memory.db (table facts)

    Toutes les opérations ChromaDB (bloquantes) passent par asyncio.to_thread.
    """

    def __init__(self, persist_dir: str = "./chroma_db"):
        """
        Args:
            persist_dir: Chemin du répertoire de persistance ChromaDB local.
        """
        if not CHROMA_AVAILABLE:
            raise RuntimeError("[CHROMA] chromadb non installé (pip install chromadb)")

        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)

        # Client ChromaDB persistant (local, sans cloud)
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        # Embedding function embarquée ChromaDB (all-MiniLM-L6-v2, ~80MB)
        self._embed_fn = DefaultEmbeddingFunction()

        # Collection : épisodes compressés
        self.episodes_col = self.client.get_or_create_collection(
            name="episodes",
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine", "description": "épisodes compressés moteur"},
        )

        # Collection : faits SQLite
        self.facts_col = self.client.get_or_create_collection(
            name="facts",
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine", "description": "faits memory.db"},
        )

        logger.info(
            f"[CHROMA] Initialisé → {persist_dir} "
            f"(épisodes={self.episodes_col.count()}, faits={self.facts_col.count()})"
        )

    # ──────────────────────────────────────────────────────────────
    # Migration : Épisodes compressés
    # ──────────────────────────────────────────────────────────────

    async def migrate_compressed_episodes(self, episodes_dir: str) -> dict:
        """
        Migre les épisodes compressés (is_compressed=True) vers ChromaDB.
        Ignore les épisodes déjà présents (upsert idempotent).

        Args:
            episodes_dir: Répertoire contenant les fichiers JSON d'épisodes.

        Returns:
            {migrated, skipped, errors}
        """
        result = {"migrated": 0, "skipped": 0, "errors": 0}

        if not os.path.isdir(episodes_dir):
            logger.warning(f"[CHROMA] Répertoire épisodes introuvable : {episodes_dir}")
            return result

        # Récupérer les IDs déjà présents (évite les doublons)
        try:
            existing = await asyncio.to_thread(
                self.episodes_col.get, **{"limit": 100_000, "include": []}
            )
            existing_ids = set(existing["ids"])
        except Exception:
            existing_ids = set()

        json_files = sorted(Path(episodes_dir).glob("*.json"))

        for filepath in json_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    episode = json.load(f)

                # Filtrer : uniquement les compressés
                if not episode.get("is_compressed", False):
                    result["skipped"] += 1
                    continue

                ep_id = str(episode.get("id", filepath.stem))

                # Déjà présent → skip (idempotence)
                if ep_id in existing_ids:
                    result["skipped"] += 1
                    continue

                objective = episode.get("objective", "")
                document  = objective or json.dumps(episode, ensure_ascii=False)[:2000]

                metadata = {
                    "date":         str(episode.get("date", "")),
                    "objective":    objective[:100],
                    "total_tokens": int(episode.get("total_tokens", 0)),
                    "errors_count": int(episode.get("errors_count", 0)),
                    "filename":     filepath.name,
                }

                await asyncio.to_thread(
                    self.episodes_col.upsert,
                    ids=[ep_id],
                    documents=[document],
                    metadatas=[metadata],
                )
                result["migrated"] += 1
                logger.debug(f"[CHROMA] Épisode migré : {ep_id}")

            except Exception as e:
                logger.error(f"[CHROMA] Erreur migration {filepath.name} : {e}")
                result["errors"] += 1

        logger.info(f"[CHROMA] Migration épisodes : {result}")
        return result

    # ──────────────────────────────────────────────────────────────
    # Migration : Faits SQLite
    # ──────────────────────────────────────────────────────────────

    async def migrate_facts_from_sqlite(self, db_path: str) -> dict:
        """
        Migre les faits non dépréciés depuis memory.db vers ChromaDB.

        Args:
            db_path: Chemin vers memory.db (table facts).

        Returns:
            {migrated, skipped}
        """
        result = {"migrated": 0, "skipped": 0}

        # IDs déjà présents
        try:
            existing = await asyncio.to_thread(
                self.facts_col.get, **{"limit": 100_000, "include": []}
            )
            existing_ids = set(existing["ids"])
        except Exception:
            existing_ids = set()

        def _read_facts():
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT id, category, title, content, tags, severity, created_at "
                "FROM facts WHERE is_deprecated = 0"
            )
            rows = cur.fetchall()
            conn.close()
            return rows

        try:
            rows = await asyncio.to_thread(_read_facts)
        except Exception as e:
            logger.warning(f"[CHROMA] Lecture SQLite échouée : {e}")
            return result

        for row in rows:
            fact_id = str(row["id"])
            if fact_id in existing_ids:
                result["skipped"] += 1
                continue

            title   = row["title"] or ""
            content = row["content"] or ""
            doc     = f"{title}. {content}".strip() if title else content

            metadata = {
                "category":   str(row["category"] or ""),
                "tags":       str(row["tags"] or ""),
                "severity":   int(row["severity"] or 0),
                "created_at": str(row["created_at"] or ""),
            }

            try:
                await asyncio.to_thread(
                    self.facts_col.upsert,
                    ids=[fact_id],
                    documents=[doc],
                    metadatas=[metadata],
                )
                result["migrated"] += 1
            except Exception as e:
                logger.error(f"[CHROMA] Erreur upsert fact {fact_id} : {e}")

        logger.info(f"[CHROMA] Migration faits : {result}")
        return result

    # ──────────────────────────────────────────────────────────────
    # Recherche
    # ──────────────────────────────────────────────────────────────

    async def search_episodes(
        self,
        query: str,
        n_results: int = 5,
        min_date: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche sémantique dans les épisodes compressés.

        Args:
            query:     Texte de la requête.
            n_results: Nombre de résultats max.
            min_date:  Filtre date minimale (format YYYY-MM-DD).

        Returns:
            Liste de {id, objective, date, distance, metadata}.
        """
        try:
            where = {"date": {"$gte": min_date}} if min_date else None

            def _query():
                return self.episodes_col.query(
                    query_texts=[query],
                    n_results=min(n_results, max(1, self.episodes_col.count())),
                    where=where,
                )

            raw = await asyncio.to_thread(_query)
            results = []
            if raw["ids"] and raw["ids"][0]:
                for i, doc_id in enumerate(raw["ids"][0]):
                    meta = (raw["metadatas"][0][i] if raw.get("metadatas") else {}) or {}
                    dist = raw["distances"][0][i] if raw.get("distances") else 0.0
                    results.append({
                        "id":        doc_id,
                        "objective": meta.get("objective", ""),
                        "date":      meta.get("date", ""),
                        "distance":  round(float(dist), 4),
                        "metadata":  meta,
                    })
            return results

        except Exception as e:
            logger.error(f"[CHROMA] search_episodes erreur : {e}")
            return []

    async def search_facts(
        self,
        query: str,
        n_results: int = 5,
        category: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche sémantique dans les faits.

        Args:
            query:     Texte de la requête.
            n_results: Nombre de résultats max.
            category:  Filtre catégorie exacte.

        Returns:
            Liste de {id, title, content, category, distance}.
        """
        try:
            where = {"category": {"$eq": category}} if category else None

            def _query():
                return self.facts_col.query(
                    query_texts=[query],
                    n_results=min(n_results, max(1, self.facts_col.count())),
                    where=where,
                )

            raw = await asyncio.to_thread(_query)
            results = []
            if raw["ids"] and raw["ids"][0]:
                for i, doc_id in enumerate(raw["ids"][0]):
                    meta = (raw["metadatas"][0][i] if raw.get("metadatas") else {}) or {}
                    dist = raw["distances"][0][i] if raw.get("distances") else 0.0
                    doc  = (raw["documents"][0][i] if raw.get("documents") else "") or ""
                    # Séparer titre / contenu depuis le document
                    if ". " in doc:
                        parts   = doc.split(". ", 1)
                        title   = parts[0]
                        content = parts[1]
                    else:
                        title, content = "", doc

                    results.append({
                        "id":       doc_id,
                        "title":    title,
                        "content":  content[:300],
                        "category": meta.get("category", ""),
                        "distance": round(float(dist), 4),
                    })
            return results

        except Exception as e:
            logger.error(f"[CHROMA] search_facts erreur : {e}")
            return []

    async def hybrid_search(
        self,
        query: str,
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """
        Recherche hybride parallèle dans les deux collections.

        Returns:
            {episodes: [...], facts: [...], merged: [...]}
        """
        episodes_res, facts_res = await asyncio.gather(
            self.search_episodes(query, n_results),
            self.search_facts(query, n_results),
        )

        # Interleaving par pertinence (distance croissante)
        merged = []
        seen   = set()
        max_l  = max(len(episodes_res), len(facts_res))
        for i in range(max_l):
            if i < len(episodes_res):
                item = {**episodes_res[i], "source": "episode"}
                key  = f"ep_{item['id']}"
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
            if i < len(facts_res):
                item = {**facts_res[i], "source": "fact"}
                key  = f"fact_{item['id']}"
                if key not in seen:
                    seen.add(key)
                    merged.append(item)

        return {
            "episodes": episodes_res,
            "facts":    facts_res,
            "merged":   merged[:n_results * 2],
        }

    def get_stats(self) -> dict:
        """Retourne le nombre de documents dans chaque collection."""
        try:
            return {
                "episodes":  self.episodes_col.count(),
                "facts":     self.facts_col.count(),
                "available": True,
                "persist_dir": self.persist_dir,
            }
        except Exception as e:
            logger.error(f"[CHROMA] get_stats erreur : {e}")
            return {"episodes": 0, "facts": 0, "available": False}


# ──────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────

_chroma_instance: Optional[ChromaMemory] = None


def get_chroma_memory(persist_dir: str = None) -> "ChromaMemory | ChromaMemoryStub":
    """
    Retourne le singleton ChromaMemory (ou Stub si chromadb absent).
    Le chemin par défaut est ./chroma_db relatif au répertoire moteur.
    """
    global _chroma_instance

    if not CHROMA_AVAILABLE:
        return ChromaMemoryStub(persist_dir or "./chroma_db")

    if _chroma_instance is None or (
        persist_dir and _chroma_instance.persist_dir != persist_dir
    ):
        _chroma_instance = ChromaMemory(persist_dir or "./chroma_db")

    return _chroma_instance
