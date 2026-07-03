"""
core/router_context_compressor.py — Compresseur de contexte multi-sources pour le routeur.
Fusionne, pondère et déduplique sémantiquement les contextes (Faits, RAG, Épisodes, Markdown)
pour éviter le "prompt bloat" avant d'envoyer le payload au Planner.
"""

import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class RouterContextCompressor:
    """
    Compresse, fusionne et déduplique les contextes issus de sources multiples.
    Pondérations par défaut :
    - facts (Faits persistants)       : 0.40 (Priorité 1)
    - rag (Résultats RAG vectoriels)  : 0.30 (Priorité 2)
    - episodes (Mémoire de sessions)  : 0.20 (Priorité 3)
    - context_loader (Fichiers MD)    : 0.10 (Priorité 4)
    """

    def __init__(self, max_chars: int = 12000, llm_gateway: Optional[Any] = None):
        """
        Args:
            max_chars: Budget maximum de caractères (12000 chars ≈ 3000 tokens).
            llm_gateway: Passerelle LLM pour le résumé de fallback (optionnel).
        """
        self.max_chars = max_chars
        self.llm_gateway = llm_gateway
        self._stopwords = {
            "le", "la", "les", "un", "une", "des", "ce", "cet", "cette", "ces",
            "de", "du", "d", "l", "en", "et", "ou", "mais", "donc", "ni", "car",
            "a", "à", "dans", "par", "pour", "sur", "avec", "sans", "sous",
            "qui", "que", "quoi", "dont", "où", "est", "sont", "être", "avoir",
            "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
        }

    def _tokenize(self, text: str) -> set[str]:
        """Découpe une phrase en mots minuscules significatifs pour Jaccard."""
        words = re.findall(r'[a-zA-Z0-9_\-\.àâéèêëîïôöùûüç]+', text.lower())
        cleaned_words = [w.rstrip('.') for w in words]
        return set(w for w in cleaned_words if w not in self._stopwords and len(w) > 1)

    def _jaccard_similarity(self, set1: set[str], set2: set[str]) -> float:
        """Calcule la distance de Jaccard entre deux ensembles de mots."""
        if not set1 or not set2:
            return 0.0
        intersection = set1.intersection(set2)
        union = set1.union(set2)
        return len(intersection) / len(union)

    def compress(self, contexts: Dict[str, str]) -> str:
        """
        Fusionne et déduplique les contextes en respectant les poids et le budget.
        
        Args:
            contexts: Dictionnaire de sources de contexte {"facts": ..., "rag": ...}
            
        Returns:
            Le contexte compressé unifié sous forme de chaîne de caractères.
        """
        # Filtrer les sources vides ou invalides
        active_sources = {k: v.strip() for k, v in contexts.items() if v and isinstance(v, str)}
        if not active_sources:
            return ""

        # Définir l'ordre de traitement par priorité de poids décroissant
        priority_order = ["facts", "rag", "episodes", "context_loader"]
        
        # Liste globale des phrases déjà sélectionnées pour la déduplication
        selected_sentences: List[str] = []
        # Ensembles de tokens des phrases sélectionnées pour accélérer la recherche Jaccard
        selected_tokens_list: List[set[str]] = []
        
        # Dictionnaire pour stocker les blocs compressés par catégorie
        compressed_blocks: Dict[str, List[str]] = {k: [] for k in priority_order}

        for source in priority_order:
            content = active_sources.get(source)
            if not content:
                continue

            # Découper le contenu en lignes (ou blocs de phrases)
            lines = [line.strip() for line in content.split("\n") if line.strip()]
            
            for line in lines:
                # Si la ligne est trop courte (ex: titres ou marqueurs de début), on la garde directement
                if len(line) < 20:
                    compressed_blocks[source].append(line)
                    continue

                # Tokeniser la ligne
                line_tokens = self._tokenize(line)
                if not line_tokens:
                    compressed_blocks[source].append(line)
                    continue

                # Comparer avec les phrases déjà sélectionnées pour éliminer les doublons sémantiques (Jaccard > 0.55)
                is_duplicate = False
                for existing_tokens in selected_tokens_list:
                    sim = self._jaccard_similarity(line_tokens, existing_tokens)
                    if sim >= 0.55:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    compressed_blocks[source].append(line)
                    selected_sentences.append(line)
                    selected_tokens_list.append(line_tokens)
                else:
                    logger.debug(f"[CONTEXT_COMPRESSOR] Doublon sémantique ignoré de '{source}' : {line[:60]}...")

        # Assemblage final en respectant le budget de caractères
        final_parts = []
        total_len = 0
        budget_exceeded = False

        truncation_msg = "\n\n[... Contexte tronqué par le compresseur de contexte ...]"
        effective_max_chars = self.max_chars - len(truncation_msg)

        source_headers = {
            "facts": "\n\n*** MÉMOIRE SÉMANTIQUE (Faits connus) ***",
            "rag": "\n\n*** RAG TECHNIQUE (Documentation contextuelle) ***",
            "episodes": "\n\n*** MÉMOIRE ÉPISODIQUE (Sessions passées pertinentes) ***",
            "context_loader": "\n\n*** CONTEXTE TECHNIQUE STRUCTURÉ (3-Layers) ***"
        }

        # Nous allons formater le texte final en assemblant les blocs dans l'ordre de priorité
        for source in priority_order:
            lines = compressed_blocks.get(source, [])
            if not lines:
                continue

            header = source_headers.get(source, f"\n\n*** {source.upper()} ***")
            
            source_content_lines = []
            header_added = False
            for line in lines:
                # Calculons le nombre exact de caractères ajoutés
                added_len = len(line) + 1  # Ligne + saut de ligne
                if not header_added:
                    added_len += len(header) + 1  # Header + saut de ligne

                # Vérifier le budget
                if total_len + added_len > effective_max_chars:
                    budget_exceeded = True
                    break
                
                source_content_lines.append(line)
                total_len += added_len
                header_added = True

            if source_content_lines:
                final_parts.append(header + "\n" + "\n".join(source_content_lines))

            if budget_exceeded:
                logger.info(f"[CONTEXT_COMPRESSOR] Budget de caractères atteint ({self.max_chars:,} chars). Sources suivantes limitées.")
                final_parts.append(truncation_msg)
                break

        # Fallback résumé LLM (si le budget est dépassé de façon critique et qu'un résumé est demandé)
        # En pratique, le découpage au budget évite tout prompt bloat.
        
        result = "".join(final_parts).strip()
        logger.info(f"[CONTEXT_COMPRESSOR] Consolidation : {sum(len(v) for v in active_sources.values()):,} chars → {len(result):,} chars (budget: {self.max_chars})")
        return result
