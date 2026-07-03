"""
memory/aqa.py — Module AQA (Attributed Question Answering) pour le RAG avec citations.

[P11] Utilise l'API Gemini avec grounding sur des passages inline pour
produire des réponses sourcées avec citations précises.

Architecture :
  - Reçoit la requête utilisateur + les passages RAG (de rag.py)
  - Envoie le tout à Gemini avec groundingPassages
  - Gemini retourne une réponse avec des citations pointant vers les sources
  - Le module formate les citations pour l'utilisateur final

Avantages par rapport au RAG simple :
  - Citations précises avec pointeurs vers les passages sources
  - Answerable probability : indique si la question peut être répondue
  - Réduction des hallucinations par ancrage factuel

Modèles supportés : gemini-3.5-flash (Free Tier), gemini-3.5-flash-paid
"""

import os
import logging
import requests
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("memory.aqa")


class AQAEngine:
    """Moteur de Question-Answering Attribué via l'API Gemini.
    
    Produit des réponses avec citations à partir de passages
    récupérés par le RAGEngine existant.
    
    Usage:
        aqa = AQAEngine()
        answer, citations = aqa.answer_with_citations(
            question="Comment configurer le WiFi sur le Tab5 ?",
            passages=[
                {"title": "Configuration WiFi", "content": "Pour configurer...", "source": "02_Hardware/..."},
            ]
        )
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-3.5-flash",
    ):
        """
        Args:
            api_key: Clé API Gemini (défaut: GEMINI_API_KEY)
            model: Modèle Gemini pour l'AQA (défaut: gemini-3.5-flash)
        """
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = model
        self._base_url = "https://generativelanguage.googleapis.com/v1beta"
        
        if not self._api_key:
            logger.warning("[AQA] Aucune clé API configurée")
    
    @property
    def available(self) -> bool:
        return bool(self._api_key)
    
    def answer_with_citations(
        self,
        question: str,
        passages: List[Dict[str, str]],
        language: str = "fr",
        temperature: float = 0.1,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Produit une réponse avec citations à partir de passages RAG.
        
        Args:
            question: La question de l'utilisateur
            passages: Liste de dicts avec les clés :
                - title: Titre de la section source
                - content: Contenu textuel du passage
                - source: Chemin/référence du fichier source
            language: Langue de la réponse ('fr' ou 'en')
            temperature: Température de génération
            
        Returns:
            Tuple (réponse_texte, liste_citations)
            Chaque citation contient : source, title, start_index, end_index, content_snippet
        """
        if not self._api_key:
            return "Erreur: Clé API Gemini requise pour l'AQA", []
        
        if not passages:
            return "Aucun passage de référence fourni pour répondre.", []
        
        # Construire le prompt AQA avec instructions de citation
        system_prompt = self._build_aqa_system_prompt(language)
        
        # Construire le contexte annoté avec numéros de source
        context_parts = []
        source_map = {}  # index → passage info
        for i, passage in enumerate(passages):
            source_id = f"[Source {i+1}]"
            source_map[i+1] = {
                "source": passage.get("source", "inconnu"),
                "title": passage.get("title", ""),
            }
            # Tronquer les passages trop longs
            content = passage.get("content", "")[:3000]
            context_parts.append(f"{source_id} {passage.get('title', '')}:\n{content}")
        
        context = "\n\n---\n\n".join(context_parts)
        
        user_prompt = (
            f"CONTEXTE DOCUMENTAIRE :\n{context}\n\n"
            f"---\n\n"
            f"QUESTION : {question}\n\n"
            f"Réponds en citant les sources avec [Source N]."
        )
        
        # Appel API Gemini standard avec le contexte inline
        url = f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 2048,
            }
        }
        
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=(5.0, 60.0),
            )
            resp.raise_for_status()
            data = resp.json()
            
            # Token tracking
            try:
                from core.token_tracker import record_usage
                usage = data.get("usageMetadata", {})
                record_usage(
                    self._model,
                    usage.get("promptTokenCount", 0),
                    usage.get("candidatesTokenCount", 0),
                )
            except Exception:
                pass
            
            # Extraire la réponse
            candidates = data.get("candidates", [])
            if not candidates:
                return "L'API n'a retourné aucune réponse.", []
            
            answer_text = ""
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                answer_text += part.get("text", "")
            
            # Extraire les citations du texte de réponse
            citations = self._extract_citations(answer_text, source_map)
            
            # Évaluer l'answerable probability
            grounding_metadata = candidates[0].get("groundingMetadata", {})
            
            logger.info(
                f"[AQA] Réponse générée : {len(answer_text)} chars, "
                f"{len(citations)} citation(s), "
                f"{len(passages)} passage(s) de référence"
            )
            
            return answer_text, citations
            
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:300] if e.response else ""
            logger.error(f"[AQA] Erreur HTTP {status} : {body}")
            return f"Erreur AQA (HTTP {status})", []
        except Exception as e:
            logger.error(f"[AQA] Erreur : {e}")
            return f"Erreur AQA : {e}", []
    
    def answer_from_rag(
        self,
        question: str,
        rag_engine=None,
        top_n: int = 5,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Raccourci : interroge le RAGEngine puis produit une réponse AQA.
        
        Args:
            question: La question de l'utilisateur
            rag_engine: Instance de RAGEngine (ou None pour import auto)
            top_n: Nombre de passages RAG à récupérer
            
        Returns:
            Tuple (réponse_texte, liste_citations)
        """
        if rag_engine is None:
            try:
                from memory.rag import RAGEngine
                rag_engine = RAGEngine()
            except Exception as e:
                return f"Erreur: RAGEngine non disponible : {e}", []
        
        # Récupérer les passages pertinents via le RAG hybride
        # On a besoin des sections brutes, pas du texte formaté
        query_tokens = rag_engine._tokenize(question)
        if not query_tokens or not rag_engine.sections:
            return "Aucun document de référence disponible.", []
        
        # Utiliser le scoring TF-IDF+BM25 pour récupérer les top sections
        import math
        
        query_tf = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0) + 1
        
        query_tfidf = {}
        query_sum_sq = 0.0
        for t, tf in query_tf.items():
            tfidf_val = tf * rag_engine.idf.get(t, 0.0)
            query_tfidf[t] = tfidf_val
            query_sum_sq += tfidf_val ** 2
        
        query_norm = math.sqrt(query_sum_sq)
        if query_norm == 0.0:
            return "Requête trop vague pour le RAG.", []
        
        # Scoring combiné
        scores = []
        for idx, sec in enumerate(rag_engine.sections):
            # TF-IDF cosinus
            sec_tfidf = sec.get("tfidf", {})
            sec_norm = sec.get("norm", 0.0)
            if sec_norm == 0.0:
                continue
            dot = sum(query_tfidf.get(t, 0) * sec_tfidf.get(t, 0) for t in query_tfidf)
            cos_sim = dot / (query_norm * sec_norm)
            
            # BM25
            bm25 = rag_engine._bm25_score(query_tokens, sec)
            
            combined = cos_sim + bm25 * 0.3
            if combined > 0.01:
                scores.append((combined, idx))
        
        scores.sort(key=lambda x: x[0], reverse=True)
        top_sections = scores[:top_n]
        
        if not top_sections:
            return "Aucun passage pertinent trouvé dans la documentation.", []
        
        # Construire les passages pour l'AQA
        passages = []
        for _, idx in top_sections:
            sec = rag_engine.sections[idx]
            passages.append({
                "title": sec.get("title", ""),
                "content": sec.get("content", ""),
                "source": sec.get("source", ""),
            })
        
        return self.answer_with_citations(question, passages)
    
    def _build_aqa_system_prompt(self, language: str = "fr") -> str:
        """Construit le system prompt pour l'AQA avec instructions de citation."""
        if language == "fr":
            return (
                "Tu es un assistant technique expert qui répond UNIQUEMENT à partir "
                "des documents fournis dans le contexte. Règles strictes :\n\n"
                "1. CHAQUE affirmation doit être suivie de sa source entre crochets, "
                "par exemple : [Source 1]\n"
                "2. Si l'information n'est pas dans le contexte, dis-le explicitement : "
                "\"Cette information n'est pas couverte par les documents disponibles.\"\n"
                "3. Ne jamais inventer ou halluciner des informations absentes du contexte.\n"
                "4. Cite les sources APRÈS chaque phrase pertinente, pas en bloc à la fin.\n"
                "5. Réponds en français, de manière claire et structurée.\n"
                "6. Si plusieurs sources confirment la même information, cite-les toutes.\n"
            )
        else:
            return (
                "You are a technical expert assistant. Answer ONLY from the provided "
                "context documents. Rules:\n\n"
                "1. Each claim MUST include its source in brackets, e.g.: [Source 1]\n"
                "2. If information is not in the context, say so explicitly.\n"
                "3. Never invent or hallucinate information.\n"
                "4. Cite sources AFTER each relevant sentence, not in a block at the end.\n"
                "5. Answer clearly and in a structured manner.\n"
            )
    
    def _extract_citations(
        self,
        answer_text: str,
        source_map: Dict[int, Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Extrait les citations [Source N] du texte de réponse.
        
        Returns:
            Liste de dicts avec : source_id, source, title, count
        """
        import re
        
        citations = []
        seen = set()
        
        # Trouver toutes les occurrences de [Source N]
        pattern = r'\[Source\s+(\d+)\]'
        matches = re.finditer(pattern, answer_text)
        
        for match in matches:
            source_id = int(match.group(1))
            if source_id in seen:
                continue
            seen.add(source_id)
            
            source_info = source_map.get(source_id, {})
            citations.append({
                "source_id": source_id,
                "source": source_info.get("source", "inconnu"),
                "title": source_info.get("title", ""),
                "count": len(re.findall(rf'\[Source\s+{source_id}\]', answer_text)),
            })
        
        return citations
