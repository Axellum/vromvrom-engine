"""
memory/embeddings.py — Module d'embeddings vectoriels pour la recherche sémantique.

Connecte le tab5-engine à ChromaDB (déjà présent dans chromadb_data/)
pour une recherche RAG vectorielle qui complète le TF-IDF/BM25 existant.

Architecture :
- [P2-3.3] Espace d'embedding UNIQUE : Gemini (gemini-embedding-2, 3072 dims).
  Collection 'moteur_context_gemini'. Activé uniquement si la clé API Gemini est
  présente (sinon recherche vectorielle ChromaDB désactivée → fallback memory.db).
- Interface query_similar() compatible avec RAGEngine pour la fusion RRF
- Re-indexation incrémentale (hash des documents pour éviter les doublons)

Créé dans le cadre de l'audit V5.5 (Axe M1 — score Mémoire 72% → cible 88%).
"""

import os
import hashlib
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Nom de la collection ChromaDB principale
COLLECTION_NAME = "moteur_context"


class EmbeddingStore:
    """
    Interface de recherche vectorielle via ChromaDB.

    Gère l'indexation des documents Markdown du dossier contexte_ia/
    et la recherche par similarité sémantique (embeddings).
    
    [P2-3.3] Espace d'embedding UNIQUE : collection 'moteur_context_gemini'
    (gemini-embedding-2, 3072 dims). Activée seulement si la clé API Gemini est
    disponible. Plus de collection MiniLM ni de fusion multi-espaces.

    Usage:
        store = EmbeddingStore()
        store.index_documents()  # Indexation initiale
        results = store.query_similar("optimiser la consommation électrique", top_n=3)
    """

    def __init__(self, persist_dir: str = None):
        """
        Args:
            persist_dir: Chemin du dossier de persistance ChromaDB.
                         Par défaut : moteur_agents/chromadb_data/
        """
        if persist_dir is None:
            persist_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "chroma_db",
            )
        self._persist_dir = persist_dir
        self._client = None
        self._collection = None
        self._gemini_fn = None          # Fonction d'embedding Gemini (espace unique)
        self._available = False

        # [P2-3.3] Espace d'embedding UNIQUE = Gemini. ChromaDB n'est activé que si
        # la fonction d'embedding Gemini est disponible (clé API). Plus de collection
        # MiniLM locale ni de fusion multi-espaces (le +10% sur cosinus mixtes était
        # incorrect : on comparait des scores de modèles différents).
        try:
            import chromadb
            from chromadb.config import Settings

            gemini_fn = None
            try:
                if os.environ.get("GEMINI_API_KEY"):
                    from memory.gemini_embedding_fn import GeminiEmbeddingFunction
                    _fn = GeminiEmbeddingFunction(
                        model="gemini-embedding-2",
                        task_type="RETRIEVAL_DOCUMENT",
                    )
                    if _fn.available:
                        gemini_fn = _fn
            except Exception as gem_err:
                logger.info(f"[EMBEDDINGS] Fonction d'embedding Gemini indisponible : {gem_err}")

            if gemini_fn is None:
                logger.info(
                    "[EMBEDDINGS] Embeddings Gemini indisponibles (clé API absente) — "
                    "recherche vectorielle ChromaDB désactivée (espace unique Gemini)."
                )
                return

            self._gemini_fn = gemini_fn
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=f"{COLLECTION_NAME}_gemini",
                embedding_function=self._gemini_fn,
                metadata={"hnsw:space": "cosine"},  # Similarité cosinus
            )
            self._available = True
            logger.info(
                f"[EMBEDDINGS] ChromaDB (espace Gemini) initialisé. Collection "
                f"'{COLLECTION_NAME}_gemini' : {self._collection.count()} documents "
                f"(dim={self._gemini_fn.dimension})."
            )
        except ImportError:
            logger.warning(
                "[EMBEDDINGS] ChromaDB non installé. "
                "Installez-le avec : pip install chromadb"
            )
        except Exception as e:
            logger.error(f"[EMBEDDINGS] Erreur d'initialisation ChromaDB : {e}")

    @property
    def is_available(self) -> bool:
        """Indique si ChromaDB est disponible et initialisé."""
        return self._available

    def index_documents(self, doc_dir: str = None, force_reindex: bool = False) -> int:
        """
        Indexe les documents Markdown du dossier contexte_ia/ dans ChromaDB.

        Utilise le hash MD5 du contenu pour éviter les doublons.
        Seuls les documents modifiés sont réindexés (incrémental).

        Args:
            doc_dir: Chemin du dossier de documents (défaut: contexte_ia/)
            force_reindex: Si True, réindexe tous les documents

        Returns:
            Nombre de documents indexés/mis à jour.
        """
        if not self._available:
            return 0

        if doc_dir is None:
            # Remonter au dossier e:\AuxFilsDesIdees\contexte_ia
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            doc_dir = os.path.join(base_dir, "contexte_ia")

        if not os.path.exists(doc_dir):
            logger.warning(f"[EMBEDDINGS] Dossier introuvable : {doc_dir}")
            return 0


        indexed_count = 0
        subdirs = ["01_Core", "02_Hardware", "03_Software"]

        for subdir in subdirs:
            subdir_path = os.path.join(doc_dir, subdir)
            if not os.path.exists(subdir_path):
                continue

            for root, _, files in os.walk(subdir_path):
                for fname in files:
                    if not fname.endswith((".md", ".markdown")):
                        continue

                    filepath = os.path.join(root, fname)
                    try:
                        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                        # Chunking par sections Markdown
                        sections = self._chunk_markdown(content, filepath)

                        for section in sections:
                            doc_id = self._compute_doc_id(section["source"], section["title"])
                            content_hash = hashlib.md5(
                                section["content"].encode("utf-8")
                            ).hexdigest()

                            # Vérifier si le document existe déjà (et n'a pas changé)
                            if not force_reindex:
                                try:
                                    existing = self._collection.get(ids=[doc_id])
                                    if (
                                        existing
                                        and existing["metadatas"]
                                        and existing["metadatas"][0].get("content_hash") == content_hash
                                    ):
                                        continue  # Document inchangé, skip
                                except Exception:
                                    pass

                            metadata_dict = {
                                    "source": section["source"],
                                    "title": section["title"],
                                    "content_hash": content_hash,
                                    "category": os.path.basename(os.path.dirname(filepath)),
                            }
                            
                            # Indexer dans la collection Gemini (espace unique)
                            self._collection.upsert(
                                ids=[doc_id],
                                documents=[section["content"]],
                                metadatas=[metadata_dict],
                            )
                            indexed_count += 1

                    except Exception as e:
                        logger.error(f"[EMBEDDINGS] Erreur d'indexation de {filepath} : {e}")

        logger.info(
            f"[EMBEDDINGS] Indexation terminée : {indexed_count} section(s) (Gemini) mises à jour."
        )
        return indexed_count

    def query_similar(
        self, query: str, top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Recherche les documents les plus similaires à la requête par embeddings.

        [P2-3.3] Espace UNIQUE Gemini : une seule collection, scores = cosinus
        natif (plus de fusion multi-espaces ni de bonus +10% artificiel).

        Args:
            query: La requête utilisateur en langage naturel
            top_n: Nombre de résultats à retourner

        Returns:
            Liste de dictionnaires avec les champs :
            - source: le fichier source
            - title: le titre de la section
            - content: le contenu de la section
            - score: le score de similarité cosinus (0-1, 1 = identique)
            - engine: 'gemini'
        """
        if not self._available or not query:
            return []

        all_results = {}  # clé = doc_id (source::title), valeur = résultat
        try:
            count = self._collection.count()
            if count > 0:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=min(top_n * 2, count),
                    include=["documents", "metadatas", "distances"],
                )
                if results and results["documents"] and results["documents"][0]:
                    for i, doc in enumerate(results["documents"][0]):
                        metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                        distance = results["distances"][0][i] if results["distances"] else 1.0
                        similarity = max(0.0, 1.0 - distance)
                        key = f"{metadata.get('source', '')}::{metadata.get('title', '')}"
                        all_results[key] = {
                            "source": metadata.get("source", "inconnu"),
                            "title": metadata.get("title", ""),
                            "content": doc,
                            "score": round(similarity, 4),
                            "category": metadata.get("category", ""),
                            "engine": "gemini",
                        }
        except Exception as e:
            logger.warning(f"[EMBEDDINGS] Erreur recherche vectorielle : {e}")

        output = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:top_n]
        if output:
            logger.info(
                f"[EMBEDDINGS] Recherche vectorielle (Gemini) : {len(output)} résultat(s) "
                f"(meilleur score : {output[0]['score']})"
            )
        return output

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques de la collection ChromaDB (espace Gemini)."""
        if not self._available:
            return {"available": False}

        return {
            "available": True,
            "collection_name": f"{COLLECTION_NAME}_gemini",
            "document_count": self._collection.count(),
            "persist_dir": self._persist_dir,
            "model": self._gemini_fn._model if self._gemini_fn else "N/A",
            "dimension": self._gemini_fn.dimension if self._gemini_fn else 0,
        }

    # ─────────────────────────────────────────────────────────
    # Méthodes privées
    # ─────────────────────────────────────────────────────────

    def _chunk_markdown(self, content: str, filepath: str) -> List[Dict[str, str]]:
        """
        Découpe un document Markdown en sections par headers.
        
        Chaque chunk est préfixé avec des métadonnées
        de contexte inline (source, catégorie, titre de section) pour améliorer
        la qualité sémantique des embeddings vectoriels.
        """
        import re

        filename = os.path.basename(filepath)
        category = os.path.basename(os.path.dirname(filepath))
        source = f"{category}/{filename}"

        sections = []
        pattern = r"(^|\n)(#{1,4}\s+[^\n]+)"
        parts = re.split(pattern, content)

        # Introduction (avant le premier header)
        intro = parts[0].strip()
        if intro and len(intro) > 50:
            # Préfixe contextuel pour l'introduction
            contextual_prefix = (
                f"[Source: {source} | Catégorie: {category}]\n"
                f"[Section: Introduction]\n"
                f"---\n"
            )
            enriched_content = contextual_prefix + intro[:1900]
            sections.append({
                "source": source,
                "title": "Introduction",
                "content": enriched_content,
            })

        # Sections par header
        i = 1
        while i < len(parts):
            if i + 1 < len(parts):
                header = parts[i + 1].strip() if i + 1 < len(parts) else ""
                section_content = parts[i + 2].strip() if i + 2 < len(parts) else ""
                title = re.sub(r"^#+\s+", "", header)

                if section_content and len(section_content) > 20:
                    # Préfixe contextuel pour chaque section
                    contextual_prefix = (
                        f"[Source: {source} | Catégorie: {category}]\n"
                        f"[Section: {title}]\n"
                        f"---\n"
                    )
                    enriched_content = contextual_prefix + section_content[:1900]
                    sections.append({
                        "source": source,
                        "title": title,
                        "content": enriched_content,
                    })
            i += 3

        return sections

    @staticmethod
    def _compute_doc_id(source: str, title: str) -> str:
        """Génère un identifiant unique et déterministe pour un document."""
        raw = f"{source}::{title}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()
