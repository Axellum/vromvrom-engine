# -*- coding: utf-8 -*-
"""
core/semantic_cache.py — Cache sémantique de réponses LLM (Phase 3, item 17).

Met en cache les réponses LLM indexées par l'EMBEDDING du prompt (ChromaDB,
distance cosine). Un nouveau prompt sémantiquement proche d'un prompt déjà vu
(similarité >= seuil) réutilise la réponse mémorisée → économie de tokens/latence.

FONDATION (additive) : ce module est autonome et testable ; il n'est PAS encore
branché sur le hot-path du gateway. Le wiring (lookup avant appel LLM + insertion
après) + le réglage du seuil en conditions réelles restent une étape délibérée.

Dégradation gracieuse : si chromadb est absent, le cache devient un no-op
(get() renvoie toujours None, put() ne fait rien).
"""

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("[SEM-CACHE] chromadb absent — cache sémantique désactivé (no-op).")

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "chroma_db"
_DEFAULT_THRESHOLD = 0.95  # similarité cosine minimale pour un hit


def _prompt_id(prompt: str) -> str:
    """Identifiant déterministe d'un prompt (pour l'upsert ChromaDB)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class SemanticCache:
    """Cache sémantique LLM sur ChromaDB (collection dédiée, distance cosine)."""

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = "llm_semantic_cache",
        similarity_threshold: float = _DEFAULT_THRESHOLD,
        client: Any = None,
        embedding_function: Any = None,
        enabled: bool = True,
    ):
        self.similarity_threshold = similarity_threshold
        self.collection = None
        self._hits = 0
        self._misses = 0

        # Court-circuit : cache désactivé par config → aucune init ChromaDB (no-op).
        if not enabled:
            logger.info("[SEM-CACHE] Désactivé par configuration (no-op).")
            return

        if not CHROMA_AVAILABLE:
            return

        try:
            if client is None:
                persist_dir = persist_dir or str(_DEFAULT_DIR)
                import os
                os.makedirs(persist_dir, exist_ok=True)
                client = chromadb.PersistentClient(
                    path=persist_dir,
                    settings=Settings(anonymized_telemetry=False),
                )
            embed_fn = embedding_function or DefaultEmbeddingFunction()
            self.collection = client.get_or_create_collection(
                name=collection_name,
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine", "description": "cache sémantique réponses LLM"},
            )
            logger.info(f"[SEM-CACHE] Initialisé (collection={collection_name}, "
                        f"seuil={similarity_threshold}).")
        except Exception as e:
            logger.error(f"[SEM-CACHE] Init échouée — cache désactivé : {e}")
            self.collection = None

    @property
    def enabled(self) -> bool:
        return self.collection is not None

    def get(self, prompt: str) -> Optional[str]:
        """Retourne une réponse mise en cache si un prompt assez proche existe, sinon None."""
        if not self.enabled or not prompt:
            return None
        try:
            res = self.collection.query(query_texts=[prompt], n_results=1)
            docs = res.get("documents") or [[]]
            dists = res.get("distances") or [[]]
            metas = res.get("metadatas") or [[]]
            if not docs[0]:
                self._misses += 1
                return None
            # distance cosine -> similarité = 1 - distance
            similarity = 1.0 - float(dists[0][0])
            if similarity >= self.similarity_threshold:
                self._hits += 1
                meta = metas[0][0] or {}
                logger.debug(f"[SEM-CACHE] HIT (sim={similarity:.3f})")
                return meta.get("response")
            self._misses += 1
            return None
        except Exception as e:
            logger.warning(f"[SEM-CACHE] get() échec : {e}")
            return None

    def put(self, prompt: str, response: str, model: Optional[str] = None) -> None:
        """Mémorise (prompt -> response). Upsert idempotent par hash de prompt."""
        if not self.enabled or not prompt or response is None:
            return
        try:
            self.collection.upsert(
                ids=[_prompt_id(prompt)],
                documents=[prompt],
                metadatas=[{
                    "response": str(response),
                    "model": model or "",
                    "ts": time.time(),
                }],
            )
        except Exception as e:
            logger.warning(f"[SEM-CACHE] put() échec : {e}")

    def stats(self) -> Dict[str, Any]:
        """Statistiques d'utilisation du cache."""
        total = self._hits + self._misses
        return {
            "enabled": self.enabled,
            "entries": self.collection.count() if self.enabled else 0,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "threshold": self.similarity_threshold,
        }


# ──────────────────────────────────────────────────────────────────────────
# Singleton process-wide (le hot-path du gateway partage une seule instance).
# Piloté par config.json → section "semantic_cache" :
#   { "enabled": false, "similarity_threshold": 0.95 }
# Défaut : DÉSACTIVÉ (opt-in) — un cache sémantique trop agressif peut renvoyer
# des réponses périmées pour des prompts contenant des variables volatiles
# (timestamps, états HA). Voir leçons « Famille D — Caching ».
# ──────────────────────────────────────────────────────────────────────────
import json
import threading

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
_SINGLETON: Optional["SemanticCache"] = None
_SINGLETON_LOCK = threading.Lock()


def _read_config_flags() -> tuple[bool, float]:
    """Lit (enabled, threshold) depuis config.json. Défaut : désactivé."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            sc = (json.load(f).get("semantic_cache") or {})
        return bool(sc.get("enabled", False)), float(
            sc.get("similarity_threshold", _DEFAULT_THRESHOLD)
        )
    except Exception:
        return False, _DEFAULT_THRESHOLD


def get_semantic_cache() -> "SemanticCache":
    """Retourne le singleton du cache sémantique, configuré depuis config.json."""
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            enabled, threshold = _read_config_flags()
            _SINGLETON = SemanticCache(similarity_threshold=threshold, enabled=enabled)
    return _SINGLETON


def reset_semantic_cache() -> None:
    """Réinitialise le singleton (utile pour les tests et le rechargement de config)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None
