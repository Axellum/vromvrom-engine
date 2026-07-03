import os
import re
import math
import asyncio
import logging
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger(__name__)

class RAGEngine:
    """
    Moteur de RAG (Retrieval-Augmented Generation) local, léger et rapide.
    Indexe les fichiers de règles du dossier contexte_ia/ par sections Markdown
    et calcule leur similarité avec la requête utilisateur en utilisant TF-IDF.
    Gère un cache des requêtes pour optimiser les performances.
    """
    def __init__(self, doc_dir: str = None):
        if doc_dir is None:
            # Recherche relative du dossier contexte_ia à partir de e:\AuxFilsDesIdees\moteur_agents\memory\rag.py
            # 3 niveaux au-dessus : e:\AuxFilsDesIdees
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.doc_dir = os.path.join(base_dir, "contexte_ia")
        else:
            self.doc_dir = doc_dir
            
        self.sections: List[Dict[str, Any]] = []
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.query_cache: Dict[str, str] = {}
        self.max_cache_size = 50
        
        # Module d'embeddings vectoriels (ChromaDB)
        self._embedding_store = None
        try:
            from memory.embeddings import EmbeddingStore
            self._embedding_store = EmbeddingStore()
            if self._embedding_store.is_available:
                logger.info("[RAG] Embeddings ChromaDB connectés avec succès.")
            else:
                self._embedding_store = None
        except Exception as emb_err:
            logger.warning(f"[RAG] Embeddings ChromaDB non disponibles : {emb_err}")
        
        # Fallback : Recherche vectorielle via memory.db + Gemini Embeddings.
        # [P2-3.3] Même espace que EmbeddingStore (Gemini gemini-embedding-2) → les
        # deux backends sont cohérents, aucun mélange d'espaces vectoriels.
        self._memory_db = None
        self._embed_fn = None
        if not self._embedding_store:
            try:
                from memory.memory_db import MemoryDB
                self._memory_db = MemoryDB.get_instance()
                stats = self._memory_db.get_stats()
                if stats.get("embeddings", 0) > 0:
                    # Charger la fonction d'embedding pour les requêtes
                    try:
                        from memory.gemini_embedding_fn import GeminiEmbeddingFunction
                        self._embed_fn = GeminiEmbeddingFunction(
                            model="gemini-embedding-2",
                            task_type="RETRIEVAL_QUERY"
                        )
                        if self._embed_fn.available:
                            logger.info(
                                f"[RAG] Fallback memory.db activé : "
                                f"{stats['embeddings']} embeddings vectoriels disponibles."
                            )
                        else:
                            self._embed_fn = None
                            logger.info("[RAG] Gemini Embedding non configuré (clé API absente).")
                    except Exception as fn_err:
                        logger.warning(f"[RAG] GeminiEmbeddingFunction non disponible : {fn_err}")
                else:
                    logger.info("[RAG] memory.db ne contient aucun embedding. Recherche vectorielle désactivée.")
                    self._memory_db = None
            except Exception as db_err:
                logger.warning(f"[RAG] memory.db non disponible : {db_err}")
        
        # Liste simplifiée de mots vides (stopwords) français
        self.stopwords = {
            "le", "la", "les", "un", "une", "des", "ce", "cet", "cette", "ces",
            "de", "du", "des", "d", "l", "s", "se", "en", "et", "ou", "mais", "donc", "or", "ni", "car",
            "a", "à", "aux", "dans", "par", "pour", "sur", "avec", "sans", "sous", "parmi",
            "qui", "que", "quoi", "dont", "où", "comment", "pourquoi", "quand",
            "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "mon", "ton", "son",
            "est", "es", "suis", "sont", "était", "étaient", "avoir", "être", "faire", "plus",
            "pourriez", "pouvez", "faire", "creer", "créer", "fichier", "code", "mettre"
        }
        
        # Indexation des documents au démarrage
        self.load_documents()
        self._build_tfidf()

    def _tokenize(self, text: str) -> List[str]:
        """Découpe un texte en mots, passe en minuscules et filtre les stopwords."""
        # Garder uniquement les caractères alphanumériques et les tirets/underscores
        words = re.findall(r'[a-zA-Z0-9_\-]+', text.lower())
        return [w for w in words if w not in self.stopwords and len(w) > 1]

    def load_documents(self):
        """Parcourt récursivement les dossiers de contexte_ia et indexe les fichiers MD."""
        logger.info(f"[RAG] Chargement des documents dans : {self.doc_dir}")
        if not os.path.exists(self.doc_dir):
            logger.warning(f"[RAG] Dossier de contexte introuvable : {self.doc_dir}")
            return
            
        md_files = []
        # Rechercher uniquement dans 01_Core, 02_Hardware, 03_Software
        subdirs = ["01_Core", "02_Hardware", "03_Software"]
        for subdir in subdirs:
            subdir_path = os.path.join(self.doc_dir, subdir)
            if not os.path.exists(subdir_path):
                continue
            for root, _, files in os.walk(subdir_path):
                for f in files:
                    if f.endswith(('.md', '.markdown')):
                        md_files.append(os.path.join(root, f))
                        
        # Ajouter également le résumé_session de l'historique le plus récent
        # pour bénéficier des leçons apprises à chaud
        hist_dir = os.path.join(self.doc_dir, "historique")
        if os.path.exists(hist_dir):
            try:
                # Trouver le dossier le plus récent par nom (les dossiers commencent par YYYY-MM-DD)
                dirs = [d for d in os.listdir(hist_dir) if os.path.isdir(os.path.join(hist_dir, d)) and re.match(r'^\d{4}-\d{2}-\d{2}', d)]
                if dirs:
                    latest_dir = max(dirs)
                    resume_path = os.path.join(hist_dir, latest_dir, "resume_session.md")
                    if os.path.exists(resume_path):
                        md_files.append(resume_path)
                        logger.info(f"[RAG] Inclusion du dernier résumé de session historique : {latest_dir}/resume_session.md")
            except Exception as e:
                logger.warning(f"[RAG] Impossible de charger le dernier résumé de l'historique : {e}")

        for filepath in md_files:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self._chunk_document(filepath, content)
            except Exception as e:
                logger.error(f"[RAG] Erreur lors de la lecture de {filepath} : {e}")
                
        logger.info(f"[RAG] Indexation terminée. {len(self.sections)} sections indexées.")

    def _chunk_document(self, filepath: str, content: str):
        """Découpe un document en sections basées sur les headers Markdown (## ou ###)."""
        filename = os.path.basename(filepath)
        category = os.path.basename(os.path.dirname(filepath))
        
        # Séparer les sections par en-têtes ## ou ### ou #
        # regex capturant l'en-tête et le contenu qui suit
        pattern = r'(^|\n)(#{1,4}\s+[^\n]+)'
        parts = re.split(pattern, content)
        
        # Le premier élément peut être l'introduction (avant tout header)
        intro = parts[0].strip()
        if intro and len(intro) > 50:
            tokens = self._tokenize(intro)
            if tokens:
                self.sections.append({
                    "source": f"{category}/{filename}",
                    "title": "Introduction",
                    "content": intro,
                    "tokens": tokens
                })
                
        # Les éléments suivants viennent par triplets : (delimiters, header, content)
        # re.split renvoie les groupes capturés comme éléments séparés de la liste
        i = 1
        while i < len(parts):
            # parts[i] est le séparateur (^|\n)
            # parts[i+1] est le header (ex: "## 1. DAC ES8388 Audio")
            # parts[i+2] est le contenu de la section jusqu'au prochain header
            header = parts[i+1].strip()
            section_content = parts[i+2].strip() if i+2 < len(parts) else ""
            
            # Nettoyer le header pour avoir le titre propre
            title = re.sub(r'^#+\s+', '', header)
            
            full_section_text = f"{title}\n{section_content}"
            tokens = self._tokenize(full_section_text)
            
            if tokens and len(section_content) > 20:
                self.sections.append({
                    "source": f"{category}/{filename}",
                    "title": title,
                    "content": section_content,
                    "tokens": tokens
                })
            i += 3

    def _build_tfidf(self):
        """Calcule la fréquence de document inversée (IDF) pour tout le vocabulaire."""
        num_docs = len(self.sections)
        if num_docs == 0:
            return
            
        # 1. Compter dans combien de documents chaque mot apparaît (DF)
        doc_counts: Dict[str, int] = {}
        for sec in self.sections:
            unique_tokens = set(sec["tokens"])
            for t in unique_tokens:
                doc_counts[t] = doc_counts.get(t, 0) + 1

        # [P2-3.5] Conserver les DF pour BM25 (évite un re-scan O(N) par terme/section).
        self._doc_freq = doc_counts

        # 2. Calculer l'IDF de chaque mot
        for word, count in doc_counts.items():
            self.idf[word] = math.log((1 + num_docs) / (1 + count)) + 1.0

        # [P2-3.5] Longueur moyenne des documents pré-calculée une seule fois.
        self._avg_dl = sum(len(s["tokens"]) for s in self.sections) / num_docs

        # 3. Pré-calculer la norme TF-IDF de chaque section (pour normalisation cosinus)
        for sec in self.sections:
            # Calcul des fréquences de termes (TF)
            tf_map: Dict[str, int] = {}
            for t in sec["tokens"]:
                tf_map[t] = tf_map.get(t, 0) + 1

            # [P2-3.5] Conserver la TF par section pour BM25 (réutilisée au scoring).
            sec["tf"] = tf_map

            # Calcul du vecteur TF-IDF et de sa norme
            vector_sum_sq = 0.0
            sec_tfidf: Dict[str, float] = {}
            for t, tf in tf_map.items():
                tfidf_val = tf * self.idf.get(t, 0.0)
                sec_tfidf[t] = tfidf_val
                vector_sum_sq += tfidf_val ** 2

            sec["tfidf"] = sec_tfidf
            sec["norm"] = math.sqrt(vector_sum_sq)

    def _bm25_score(self, query_tokens: List[str], section: Dict[str, Any]) -> float:
        """
        Scoring BM25 (k1=1.5, b=0.75) pour une section donnée.
        Complémentaire au TF-IDF : meilleur pour les correspondances exactes.
        """
        k1, b = 1.5, 0.75
        num_docs = len(self.sections)
        if num_docs == 0:
            return 0.0

        # [P2-3.5] Statistiques pré-calculées dans _build_tfidf (avg_dl, DF, TF par
        # section) — élimine le re-scan O(N) du corpus à chaque terme. Fallback
        # défensif si l'index n'a pas encore été construit.
        avg_dl = getattr(self, "_avg_dl", 0.0) or 1.0
        doc_freq = getattr(self, "_doc_freq", None)
        sec_tf = section.get("tf")
        if sec_tf is None:
            sec_tf = {}
            for t in section["tokens"]:
                sec_tf[t] = sec_tf.get(t, 0) + 1

        dl = len(section["tokens"])
        score = 0.0

        for qt in query_tokens:
            tf = sec_tf.get(qt, 0)
            if tf == 0:
                continue
            # Nombre de documents contenant le terme (DF pré-calculé)
            if doc_freq is not None:
                df = doc_freq.get(qt, 0)
            else:
                df = sum(1 for s in self.sections if qt in set(s["tokens"]))
            idf = math.log((num_docs - df + 0.5) / (df + 0.5) + 1)
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))

        return score

    async def query_async(self, user_query: str, top_n: int = 3,
                          allowed_categories: Optional[List[str]] = None) -> str:
        """[#T61] Variante asynchrone de `query`.

        `query` est synchrone et déclenche, via le scoring vectoriel, l'embedding
        Gemini bloquant (`requests.post` + `time.sleep` de backoff). Sur le hot-path
        async (router slow-path), on délègue à un thread pour ne pas geler l'event
        loop. Le router détecte cette méthode (`hasattr(..., "query_async")`) et la
        préfère à son fallback `to_thread`.
        """
        return await asyncio.to_thread(
            self.query, user_query, top_n, allowed_categories
        )

    def query(self, user_query: str, top_n: int = 3,
              allowed_categories: Optional[List[str]] = None) -> str:
        """
        Recherche hybride TF-IDF + BM25 avec fusion RRF (Reciprocal Rank Fusion).
        Gère un cache LRU simple pour accélérer les requêtes récurrentes.
        
        Si allowed_categories est fourni, seules les
        sections issues de fichiers correspondant à ces catégories sont considérées.
        Cela réduit le bruit vectoriel et améliore la précision de retrieval à l'échelle.
        """
        if not user_query:
            return ""
            
        # Normaliser la requête pour la clé de cache
        cache_key = user_query.strip().lower()
        if cache_key in self.query_cache:
            logger.debug(f"[RAG] Query cache hit pour : {user_query[:50]}...")
            return self.query_cache[cache_key]
            
        query_tokens = self._tokenize(user_query)
        if not query_tokens or not self.sections:
            return ""
        
        # Filtrage par catégories : restreindre les sections candidates
        # aux fichiers correspondant aux catégories détectées par le Router.
        active_sections = self.sections
        if allowed_categories:
            try:
                from memory.context_loader import CATEGORY_FILES_MAP
                # Toujours inclure "core" (rules_global.md) pour les règles utilisateur
                all_cats = set(allowed_categories) | {"core"}
                allowed_files = set()
                for cat in all_cats:
                    for rel_path in CATEGORY_FILES_MAP.get(cat, []):
                        # Les sources dans self.sections sont au format "catégorie/fichier.md"
                        # ex: "02_Hardware/rules_esphome.md"
                        allowed_files.add(rel_path)
                
                if allowed_files:
                    active_sections = [
                        sec for sec in self.sections
                        if any(af.endswith(sec["source"].split("/")[-1])
                               for af in allowed_files)
                    ]
                    logger.info(
                        f"[RAG] Filtrage par catégories {list(all_cats)} : "
                        f"{len(active_sections)}/{len(self.sections)} sections retenues"
                    )
            except ImportError:
                logger.warning("[RAG] Import CATEGORY_FILES_MAP échoué, filtrage désactivé")
            
        # 1. Calculer le vecteur TF-IDF de la requête
        query_tf: Dict[str, int] = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0) + 1
            
        query_tfidf: Dict[str, float] = {}
        query_sum_sq = 0.0
        for t, tf in query_tf.items():
            tfidf_val = tf * self.idf.get(t, 0.0)
            query_tfidf[t] = tfidf_val
            query_sum_sq += tfidf_val ** 2
            
        query_norm = math.sqrt(query_sum_sq)
        if query_norm == 0.0:
            return ""
            
        # 2. Scoring TF-IDF (cosinus) pour chaque section (filtrée si catégories spécifiées)
        # Construire un set d'indices valides pour le filtrage par catégories
        active_indices = set(self.sections.index(s) for s in active_sections) if allowed_categories else None
        
        tfidf_scores: List[Tuple[float, int]] = []  # (score, index)
        for idx, sec in enumerate(self.sections):
            if active_indices is not None and idx not in active_indices:
                continue
            sec_tfidf = sec.get("tfidf", {})
            sec_norm = sec.get("norm", 0.0)
            if sec_norm == 0.0:
                continue
            dot_product = 0.0
            for t in query_tfidf:
                if t in sec_tfidf:
                    dot_product += query_tfidf[t] * sec_tfidf[t]
            similarity = dot_product / (query_norm * sec_norm)
            if similarity > 0.01:
                tfidf_scores.append((similarity, idx))
        
        # 3. Scoring BM25 pour chaque section
        bm25_scores: List[Tuple[float, int]] = []  # (score, index)
        for idx, sec in enumerate(self.sections):
            if active_indices is not None and idx not in active_indices:
                continue
            bm25 = self._bm25_score(query_tokens, sec)
            if bm25 > 0.01:
                bm25_scores.append((bm25, idx))
        
        # 4. Fusion par Reciprocal Rank Fusion (RRF) — k=60 (standard)
        k_rrf = 60
        rrf_scores: Dict[int, float] = {}
        
        # Classement TF-IDF
        tfidf_scores.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, idx) in enumerate(tfidf_scores):
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (k_rrf + rank + 1)
        
        # Classement BM25
        bm25_scores.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, idx) in enumerate(bm25_scores):
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (k_rrf + rank + 1)
        
        # 5. Trier par score RRF fusionne et prendre le top-N
        sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Scoring vectoriel via ChromaDB (3ème source de fusion)
        embedding_matches = []
        if self._embedding_store:
            try:
                emb_results = self._embedding_store.query_similar(user_query, top_n=top_n * 2)
                for emb_rank, emb_res in enumerate(emb_results):
                    # Chercher si ce résultat correspond à une section déjà indexée
                    matched_idx = None
                    for idx, sec in enumerate(self.sections):
                        if sec["source"] == emb_res["source"] and sec["title"] == emb_res.get("title", ""):
                            matched_idx = idx
                            break
                    
                    if matched_idx is not None:
                        # Ajouter au score RRF existant
                        rrf_scores[matched_idx] = rrf_scores.get(matched_idx, 0.0) + 1.0 / (k_rrf + emb_rank + 1)
                    else:
                        # Résultat trouvé uniquement par embeddings (synonymes, paraphrases)
                        embedding_matches.append(emb_res)
                
                # Re-trier après fusion des 3 sources
                sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
                logger.info(f"[RAG] Fusion triple TF-IDF+BM25+Embeddings : {len(emb_results)} résultats vectoriels intégrés")
            except Exception as emb_err:
                logger.warning(f"[RAG] Erreur de recherche vectorielle ChromaDB : {emb_err}")
        
        # Fallback : Recherche vectorielle via memory.db + Gemini Embeddings
        elif self._memory_db and self._embed_fn:
            try:
                import struct
                
                # Calculer l'embedding de la requête
                query_embedding = self._embed_fn.embed_query(user_query)
                
                if query_embedding:
                    # Récupérer tous les embeddings stockés (fichiers de contexte) avec leur titre s'il s'agit d'un fait
                    conn = self._memory_db._get_conn()
                    rows = conn.execute("""
                        SELECT e.source_type, e.source_id, e.chunk_text, e.embedding, f.title as fact_title
                        FROM embeddings e
                        LEFT JOIN facts f ON e.source_type = 'fact' AND CAST(f.id AS TEXT) = e.source_id
                    """).fetchall()
                    conn.close()
                    
                    # Calculer la similarité cosinus avec chaque chunk (optimisé avec numpy)
                    vector_scores = []
                    try:
                        import numpy as np
                        embeddings_list = []
                        valid_rows = []
                        for row in rows:
                            emb_bytes = row["embedding"]
                            if not emb_bytes:
                                continue
                            n = len(emb_bytes) // 4
                            stored_emb = struct.unpack(f'{n}f', emb_bytes)
                            embeddings_list.append(stored_emb)
                            valid_rows.append(row)
                            
                        if embeddings_list:
                            q_arr = np.array(query_embedding)
                            matrix = np.array(embeddings_list)
                            
                            # Calcul vectorisé global
                            dot_products = np.dot(matrix, q_arr)
                            norm_q = np.linalg.norm(q_arr)
                            norm_matrices = np.linalg.norm(matrix, axis=1)
                            
                            norm_product = norm_q * norm_matrices
                            cosims = np.where(norm_product > 0, dot_products / norm_product, 0.0)
                            
                            for idx, row in enumerate(valid_rows):
                                title = row["fact_title"] if row["fact_title"] else ""
                                vector_scores.append({
                                    "source": f"Database/Fact #{row['source_id']}" if row["source_type"] == "fact" else row["source_id"],
                                    "title": title,
                                    "content": row["chunk_text"],
                                    "score": float(cosims[idx]),
                                    "source_type": row["source_type"],
                                })
                    except ImportError:
                        # Fallback pur Python si numpy n'est pas disponible
                        for row in rows:
                            emb_bytes = row["embedding"]
                            if not emb_bytes:
                                continue
                            n = len(emb_bytes) // 4
                            stored_emb = list(struct.unpack(f'{n}f', emb_bytes))
                            
                            dot = sum(a * b for a, b in zip(query_embedding, stored_emb))
                            norm_q = math.sqrt(sum(a * a for a in query_embedding))
                            norm_s = math.sqrt(sum(a * a for a in stored_emb))
                            
                            cosim = dot / (norm_q * norm_s) if norm_q > 0 and norm_s > 0 else 0.0
                            
                            title = row["fact_title"] if row["fact_title"] else ""
                            vector_scores.append({
                                "source": f"Database/Fact #{row['source_id']}" if row["source_type"] == "fact" else row["source_id"],
                                "title": title,
                                "content": row["chunk_text"],
                                "score": cosim,
                                "source_type": row["source_type"],
                            })
                    
                    # Trier par similarité et prendre les top résultats
                    vector_scores.sort(key=lambda x: x["score"], reverse=True)
                    top_vector = vector_scores[:top_n * 2]
                    
                    for emb_rank, vr in enumerate(top_vector):
                        # [P2-3.3] Seuil sur cosinus Gemini (espace unique) — cohérent.
                        if vr["score"] < 0.3:  # Seuil de pertinence minimal
                            continue
                        
                        # Chercher correspondance dans les sections TF-IDF
                        matched_idx = None
                        for idx, sec in enumerate(self.sections):
                            # Comparer par source (nom de fichier)
                            if vr["source"] in sec["source"]:
                                # Vérifier un chevauchement de contenu significatif
                                overlap = len(set(vr["content"].split()) & set(sec["content"][:200].split()))
                                if overlap > 5:
                                    matched_idx = idx
                                    break
                        
                        if matched_idx is not None:
                            rrf_scores[matched_idx] = rrf_scores.get(matched_idx, 0.0) + 1.0 / (k_rrf + emb_rank + 1)
                        else:
                            embedding_matches.append(vr)
                    
                    # Re-trier après fusion des 3 sources
                    sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
                    logger.info(
                        f"[RAG] Fusion triple TF-IDF+BM25+GeminiEmb : "
                        f"{len(top_vector)} résultats vectoriels (top cosim: {top_vector[0]['score']:.3f})"
                    )
            except Exception as emb_err:
                logger.warning(f"[RAG] Erreur de recherche vectorielle memory.db : {emb_err}")
        
        top_sections = sorted_rrf[:top_n]
        
        if not top_sections:
            return ""
            
        output_parts = ["\n=== CONTEXTE TECHNIQUE CIBLÉ (RAG HYBRIDE TF-IDF+BM25+EMBEDDINGS) ==="]
        for idx, rrf_score in top_sections:
            sec = self.sections[idx]
            logger.info(f"[RAG] Match : {sec['source']} -> {sec['title']} (RRF: {rrf_score:.4f})")
            output_parts.append(
                f"\nSource : {sec['source']} > {sec['title']} (Score RRF : {rrf_score:.4f})\n"
                f"{sec['content']}"
            )
        
        # Ajouter les résultats uniquement trouvés par embeddings
        for emb_match in embedding_matches[:2]:
            output_parts.append(
                f"\n[EMBEDDING] Source : {emb_match['source']} > {emb_match.get('title', '')} "
                f"(Similarité : {emb_match['score']:.4f})\n"
                f"{emb_match['content'][:500]}"
            )
            
        result_text = "\n".join(output_parts)
        
        # Enregistrement dans le cache avec éviction simple
        if len(self.query_cache) >= self.max_cache_size:
            first_key = next(iter(self.query_cache))
            del self.query_cache[first_key]
            
        self.query_cache[cache_key] = result_text
        return result_text
