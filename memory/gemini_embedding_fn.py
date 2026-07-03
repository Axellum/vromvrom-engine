"""
memory/gemini_embedding_fn.py — Fonction d'embedding Gemini pour ChromaDB.

[P7] Permet d'utiliser les modèles Gemini Embedding (gemini-embedding-2,
text-embedding-004) comme alternative aux embeddings locaux (all-MiniLM-L6-v2).

Avantages :
  - Dimension supérieure : 3072 (vs 384 pour MiniLM-L6)
  - Meilleure qualité sémantique multilingue (FR/EN)
  - Gratuit sur le Free Tier (1500 RPM)

Inconvénients :
  - Nécessite un appel réseau (latence ~200ms vs ~20ms local)
  - Soumis aux quotas API

Utilisation dans EmbeddingStore :
    from memory.gemini_embedding_fn import GeminiEmbeddingFunction
    store = EmbeddingStore(embedding_fn=GeminiEmbeddingFunction())
"""

import os
import logging
import requests
import hashlib
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# [P2-3.3] Classe de base ChromaDB. À partir de ChromaDB 1.5, l'interface
# EmbeddingFunction exige des méthodes (name(), get_config(), build_from_config(),
# is_legacy(), validate_config()…) pour pouvoir persister la collection. En héritant
# de la classe de base, on récupère tous les défauts du protocole et on n'override
# que le strict nécessaire. Fallback sur `object` si ChromaDB est absent, pour que
# le module reste importable (dégradation gracieuse, cf. EmbeddingStore).
try:
    from chromadb.api.types import EmbeddingFunction as _ChromaEmbeddingFunction
    _EF_BASE = _ChromaEmbeddingFunction
except Exception:  # pragma: no cover - chromadb non installé
    _EF_BASE = object

# Modèles d'embedding disponibles
EMBEDDING_MODELS = {
    "gemini-embedding-2": {
        "dimension": 3072,
        "description": "Dernier modèle Gemini Embedding (2026)",
    },
    "gemini-embedding-001": {
        "dimension": 3072,
        "description": "Premier modèle Gemini Embedding stable",
    },
    "text-embedding-004": {
        "dimension": 768,
        "description": "Modèle d'embedding léger (768 dims, plus rapide)",
    },
}


class GeminiEmbeddingFunction(_EF_BASE):
    """Fonction d'embedding compatible ChromaDB utilisant l'API Gemini.
    
    Implémente l'interface chromadb.EmbeddingFunction pour être utilisée
    comme paramètre de collection ChromaDB.
    
    Usage:
        import chromadb
        from memory.gemini_embedding_fn import GeminiEmbeddingFunction
        
        client = chromadb.PersistentClient(path="./chroma_db")
        collection = client.get_or_create_collection(
            name="moteur_context_gemini",
            embedding_function=GeminiEmbeddingFunction(),
        )
    """
    
    def __init__(
        self,
        model: str = "gemini-embedding-2",
        api_key: Optional[str] = None,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ):
        """
        Args:
            model: Modèle d'embedding Gemini à utiliser
            api_key: Clé API Google (défaut: GEMINI_API_KEY depuis le .env)
            task_type: Type de tâche d'embedding :
                - RETRIEVAL_DOCUMENT : pour indexer des documents
                - RETRIEVAL_QUERY : pour les requêtes de recherche
                - SEMANTIC_SIMILARITY : pour comparer des textes
                - CLASSIFICATION : pour la classification
        """
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = model
        self._task_type = task_type
        self._base_url = "https://generativelanguage.googleapis.com/v1beta"
        
        # Validation
        if not self._api_key:
            logger.warning("[Gemini Embedding] Aucune clé API configurée. Fallback sur embeddings locaux.")
        
        model_info = EMBEDDING_MODELS.get(model, {})
        self._dimension = model_info.get("dimension", 3072)
        
        logger.info(
            f"[Gemini Embedding] Initialisé : modèle={model}, "
            f"dimension={self._dimension}, task_type={task_type}"
        )
    
    @property
    def dimension(self) -> int:
        """Dimension des vecteurs d'embedding produits."""
        return self._dimension
    
    @property
    def available(self) -> bool:
        """Indique si la fonction d'embedding est configurée et prête."""
        try:
            from core.key_pool import get_key_pool
            pool = get_key_pool()
            if pool and pool.free_key_count > 0:
                return True
        except ImportError:
            pass
        return bool(self._api_key or os.environ.get("GEMINI_API_KEY"))

    # ── Interface ChromaDB 1.5+ (persistance de la collection) ──────────────
    # Sans ces méthodes, get_or_create_collection() échoue avec
    # « 'GeminiEmbeddingFunction' object has no attribute 'name' ».
    @staticmethod
    def name() -> str:
        """Identifiant stable de la fonction d'embedding (sérialisation ChromaDB)."""
        return "gemini_embedding_fn"

    def get_config(self) -> Dict[str, Any]:
        """Config sérialisable de la fonction. N'inclut JAMAIS la clé API (secret)."""
        return {"model": self._model, "task_type": self._task_type}

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "GeminiEmbeddingFunction":
        """Reconstruit la fonction depuis une config sérialisée (clé API relue de l'env)."""
        return GeminiEmbeddingFunction(
            model=config.get("model", "gemini-embedding-2"),
            task_type=config.get("task_type", "RETRIEVAL_DOCUMENT"),
        )

    def default_space(self) -> str:
        """Espace de similarité par défaut : cosinus (collection créée en hnsw:space=cosine)."""
        return "cosine"

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Interface ChromaDB : génère les embeddings pour une liste de textes.
        
        Args:
            input: Liste de textes à encoder
            
        Returns:
            Liste de vecteurs d'embedding (dimension = self._dimension)
        """
        if not input:
            return []
        
        # L'API Gemini Embedding accepte du batch nativement
        # On traite par lots de 100 (limite API)
        all_embeddings = []
        batch_size = 100
        
        for i in range(0, len(input), batch_size):
            batch = input[i:i + batch_size]
            embeddings = self._embed_batch(batch)
            all_embeddings.extend(embeddings)
        
        return all_embeddings
    
    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Appelle l'API Gemini Embedding pour un lot de textes.
        
        Utilise l'endpoint batchEmbedContents pour optimiser les appels réseau.
        """
        # Résolution de la clé API via le KeyPool si non définie explicitement
        try:
            from core.key_pool import get_key_pool
            pool = get_key_pool()
        except ImportError:
            pool = None

        current_key = self._api_key
        if not current_key and pool:
            current_key = pool.get_free_key()

        if not current_key:
            current_key = os.environ.get("GEMINI_API_KEY")
            
        if not current_key:
            raise ValueError("Aucune clé API Gemini disponible pour générer des embeddings")

        # Construction du payload batch
        requests_payload = []
        for text in texts:
            truncated = text[:40000] if len(text) > 40000 else text
            requests_payload.append({
                "model": f"models/{self._model}",
                "content": {"parts": [{"text": truncated}]},
                "taskType": self._task_type,
            })
        
        payload = {"requests": requests_payload}
        
        max_attempts = 4
        backoff = 10.0
        
        for attempt in range(1, max_attempts + 1):
            url = f"{self._base_url}/models/{self._model}:batchEmbedContents?key={current_key}"
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=(5.0, 60.0),
                )
                
                # Gestion automatique de la rotation de clé sur 429
                if resp.status_code == 429:
                    if pool and not self._api_key:
                        pool.report_rate_limit(current_key)
                        next_key = pool.get_free_key()
                        if next_key and next_key != current_key:
                            logger.warning(f"[Gemini Embedding] 429 détecté (tentative {attempt}/{max_attempts}). Rotation vers la clé {next_key[:6]}...")
                            current_key = next_key
                            # Passer directement à l'essai suivant avec la nouvelle clé sans attendre
                            continue
                    
                    # Pas d'autre clé ou clé fixe, on attend
                    logger.warning(f"[Gemini Embedding] 429 détecté (tentative {attempt}/{max_attempts}). Pause de {backoff}s...")
                    import time
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                
                resp.raise_for_status()
                if pool and not self._api_key:
                    pool.report_success(current_key)
                    
                data = resp.json()
                
                embeddings = []
                for emb in data.get("embeddings", []):
                    values = emb.get("values", [])
                    embeddings.append(values)
                
                # Tracking des tokens (estimation : 1 token ≈ 4 chars)
                try:
                    from core.token_tracker import record_usage
                    total_chars = sum(len(t) for t in texts)
                    estimated_tokens = max(1, total_chars // 4)
                    record_usage(self._model, estimated_tokens, 0)
                except Exception:
                    pass
                
                logger.debug(
                    f"[Gemini Embedding] Batch de {len(texts)} textes encodé "
                    f"({len(embeddings)} vecteurs × {self._dimension} dims)"
                )
                
                return embeddings
                
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else "?"
                if status == 429:
                    logger.warning(f"[Gemini Embedding] HTTP 429 détecté via HTTPError (tentative {attempt}/{max_attempts}). Pause de {backoff}s...")
                    import time
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                
                body = e.response.text[:300] if e.response else ""
                logger.error(f"[Gemini Embedding] Erreur HTTP {status} : {body}")
                if attempt == max_attempts:
                    raise
                import time
                time.sleep(backoff)
                backoff *= 1.5
            except Exception as e:
                logger.error(f"[Gemini Embedding] Erreur batch embedding (tentative {attempt}/{max_attempts}) : {e}")
                if attempt == max_attempts:
                    raise
                import time
                time.sleep(backoff)
                backoff *= 1.5
        
        raise RuntimeError(f"Échec de génération d'embeddings après {max_attempts} tentatives.")
    
    def embed_query(self, input):
        """Interface ChromaDB 1.5+ : embed une ou plusieurs requêtes (RETRIEVAL_QUERY).

        ChromaDB 1.5 appelle `embed_query(input=[textes])` (mot-clé `input`, type liste)
        et attend une liste de vecteurs — un par texte (cf. CollectionCommon : il vérifie
        `len(embeddings) == 1` puis utilise `embeddings[0]`). L'ancienne signature
        `embed_query(text: str)` levait donc `unexpected keyword argument 'input'`, d'où
        les « 0 résultat » de `rag_search` malgré une collection peuplée.

        On reste rétro-compatible avec les appels internes legacy (memory/rag.py:417) qui
        passent une seule chaîne : dans ce cas on renvoie un unique vecteur. Le travail réel
        (cache SQLite + appel API Gemini en task_type RETRIEVAL_QUERY) est délégué à
        `_embed_query_single`.
        """
        if isinstance(input, str):
            return self._embed_query_single(input)
        return [self._embed_query_single(text) for text in input]

    def _embed_query_single(self, text: str) -> List[float]:
        """Embed un seul texte de requête (task_type=RETRIEVAL_QUERY).

        Utilise un cache SQLite local (memory.db) pour éviter les appels réseau redondants.
        Intègre une recherche sémantique approchée par similarité cosinus (seuil >= 0.97).
        """
        if not text:
            return []
            
        normalized_text = text.strip().lower()
        query_hash = hashlib.md5(normalized_text.encode('utf-8')).hexdigest()
        
        # 1. Tenter d'abord une correspondance par hash exact (ultra-rapide)
        try:
            from memory.memory_db import MemoryDB
            db = MemoryDB.get_instance()
            cached_emb = db.get_cached_query_embedding(query_hash)
            if cached_emb:
                logger.info(f"[Gemini Embedding] Query cache hit (exact hash) pour : '{normalized_text[:50]}...' (0ms)")
                return cached_emb
        except Exception as cache_err:
            logger.warning(f"[Gemini Embedding] Échec lecture cache SQLite : {cache_err}")
 
        # 2. Tenter une recherche de similarité sémantique approchée locale (seuil >= 0.97)
        try:
            import numpy as np
            import chromadb.utils.embedding_functions as ef
            from memory.memory_db import MemoryDB
            
            db = MemoryDB.get_instance()
            cached_entries = db.get_all_cached_query_embeddings()
            if cached_entries:
                if not hasattr(self, "_local_embed_fn") or self._local_embed_fn is None:
                    self._local_embed_fn = ef.DefaultEmbeddingFunction()
                
                local_emb_query = np.array(self._local_embed_fn([normalized_text])[0], dtype=np.float32)
                norm_query = np.linalg.norm(local_emb_query)
                
                if norm_query > 0:
                    best_score = -1.0
                    best_entry = None
                    
                    if not hasattr(self, "_local_emb_cache"):
                        self._local_emb_cache = {}
                        
                    for entry in cached_entries:
                        text_cached = entry["query_text"]
                        
                        if text_cached not in self._local_emb_cache:
                            try:
                                emb = self._local_embed_fn([text_cached])[0]
                                self._local_emb_cache[text_cached] = np.array(emb, dtype=np.float32)
                            except Exception:
                                continue
                        
                        local_emb_cached = self._local_emb_cache[text_cached]
                        norm_cached = np.linalg.norm(local_emb_cached)
                        if norm_cached > 0:
                            score = np.dot(local_emb_query, local_emb_cached) / (norm_query * norm_cached)
                            if score > best_score:
                                best_score = score
                                best_entry = entry
                                
                    if best_score >= 0.96:
                        logger.info(
                            f"[Gemini Embedding] Query cache hit sémantique (score={best_score:.4f} >= 0.96) pour : "
                            f"'{normalized_text[:50]}...' -> correspondant à '{best_entry['query_text'][:50]}...' (0ms)"
                        )
                        return best_entry["embedding"]
        except Exception as sem_err:
            logger.debug(f"[Gemini Embedding] Échec recherche sémantique locale : {sem_err}")
            
        # 3. Cache miss : Appel réseau API Gemini
        try:
            from core.key_pool import get_key_pool
            pool = get_key_pool()
        except ImportError:
            pool = None

        current_key = self._api_key
        if not current_key and pool:
            current_key = pool.get_free_key()
            
        if not current_key:
            current_key = os.environ.get("GEMINI_API_KEY")
            
        if not current_key:
            raise ValueError("Aucune clé API Gemini disponible pour générer des embeddings")

        payload = {
            "model": f"models/{self._model}",
            "content": {"parts": [{"text": text[:40000]}]},
            "taskType": "RETRIEVAL_QUERY",
        }
        
        max_attempts = 4
        backoff = 10.0
        
        for attempt in range(1, max_attempts + 1):
            url = (
                f"{self._base_url}/models/{self._model}:embedContent"
                f"?key={current_key}"
            )
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=(5.0, 30.0),
                )
                
                # Rotation automatique de clé sur 429
                if resp.status_code == 429:
                    if pool and not self._api_key:
                        pool.report_rate_limit(current_key)
                        next_key = pool.get_free_key()
                        if next_key and next_key != current_key:
                            logger.warning(f"[Gemini Embedding] 429 détecté pour query. Rotation vers la clé {next_key[:6]}...")
                            current_key = next_key
                            continue
                    
                    logger.warning(f"[Gemini Embedding] 429 détecté pour query (tentative {attempt}/{max_attempts}). Pause de {backoff}s...")
                    import time
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                
                resp.raise_for_status()
                if pool and not self._api_key:
                    pool.report_success(current_key)
                    
                data = resp.json()
                embedding = data.get("embedding", {}).get("values", [])
                
                # Enregistrer l'embedding dans le cache SQLite
                if embedding:
                    try:
                        from memory.memory_db import MemoryDB
                        db = MemoryDB.get_instance()
                        db.store_query_embedding_cache(query_hash, normalized_text, embedding)
                        logger.debug(f"[Gemini Embedding] Enregistré dans le cache SQLite : '{normalized_text[:50]}...'")
                        
                        if hasattr(self, "_local_embed_fn") and self._local_embed_fn is not None:
                            if not hasattr(self, "_local_emb_cache"):
                                self._local_emb_cache = {}
                            local_emb = self._local_embed_fn([normalized_text])[0]
                            self._local_emb_cache[normalized_text] = np.array(local_emb, dtype=np.float32)
                    except Exception as save_err:
                        logger.warning(f"[Gemini Embedding] Impossible de sauvegarder l'embedding dans le cache : {save_err}")
                        
                return embedding
                
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else "?"
                if status == 429:
                    logger.warning(f"[Gemini Embedding] HTTP 429 détecté pour query via HTTPError. Pause de {backoff}s...")
                    import time
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                if attempt == max_attempts:
                    raise
                import time
                time.sleep(backoff)
                backoff *= 1.5
            except Exception as e:
                logger.error(f"[Gemini Embedding] Erreur embed_query (tentative {attempt}/{max_attempts}) : {e}")
                if attempt == max_attempts:
                    raise
                import time
                time.sleep(backoff)
                backoff *= 1.5
                
        raise RuntimeError(f"Échec de génération de l'embedding de query après {max_attempts} tentatives.")

