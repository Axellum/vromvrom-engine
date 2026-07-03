"""
tools/cloud_translate.py — Traduction via Google Cloud Translation API v2.

[Phase 3 - P3.2] Utilise l'API Cloud Translation v2 (REST directe avec clé API).

Free Tier : 500 000 caractères/mois (détection + traduction combinés)

Utilisation dans le moteur :
  - Traduction automatique des docs ESPHome/HA anglais → français
  - Traduction des réponses d'APIs externes
  - Détection de langue sur les inputs utilisateur

Note : Requiert l'activation de translate.googleapis.com sur le projet
moteur-ia-free via la console GCP.
"""

import os
import logging
import requests
from typing import Optional, List

logger = logging.getLogger("tools.cloud_translate")


class CloudTranslateProvider:
    """Provider Cloud Translation v2 avec détection de langue automatique.
    
    Usage:
        translator = CloudTranslateProvider()
        result = translator.translate("Hello world", target="fr")
        lang = translator.detect("Bonjour le monde")
    """
    
    BASE_URL = "https://translation.googleapis.com/language/translate/v2"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CLOUD_API_KEY", "")
        self.available = bool(self.api_key)
    
    def translate(
        self,
        text: str,
        target: str = "fr",
        source: Optional[str] = None,
        format_type: str = "text",
    ) -> dict:
        """Traduit du texte.
        
        Args:
            text: Texte à traduire
            target: Langue cible (ex: "fr", "en", "es", "de")
            source: Langue source (auto-détectée si None)
            format_type: "text" ou "html"
        
        Returns:
            Dict avec translated_text, detected_source, chars_count
        """
        if not self.available:
            return {"error": "Cloud Translate non disponible"}
        
        params = {
            "q": text,
            "target": target,
            "format": format_type,
            "key": self.api_key,
        }
        if source:
            params["source"] = source
        
        try:
            resp = requests.post(self.BASE_URL, data=params, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"[Cloud Translate] HTTP {resp.status_code}: {resp.text[:200]}")
                return {"error": f"HTTP {resp.status_code}"}
            
            data = resp.json()
            translations = data.get("data", {}).get("translations", [])
            
            if not translations:
                return {"error": "Aucune traduction retournée"}
            
            t = translations[0]
            result = {
                "translated_text": t.get("translatedText", ""),
                "detected_source": t.get("detectedSourceLanguage", source or "?"),
                "target": target,
                "chars_count": len(text),
            }
            
            logger.info(
                f"[Cloud Translate] ✅ {result['detected_source']}→{target} "
                f"({len(text)} chars)"
            )
            return result
            
        except Exception as e:
            logger.error(f"[Cloud Translate] Erreur : {e}")
            return {"error": str(e)}
    
    def detect(self, text: str) -> dict:
        """Détecte la langue d'un texte.
        
        Returns:
            Dict avec language, confidence, is_reliable
        """
        if not self.available:
            return {"error": "Cloud Translate non disponible"}
        
        url = f"{self.BASE_URL}/detect"
        
        try:
            resp = requests.post(url, data={"q": text, "key": self.api_key}, timeout=10)
            
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}"}
            
            detections = resp.json().get("data", {}).get("detections", [[]])
            if detections and detections[0]:
                d = detections[0][0]
                return {
                    "language": d.get("language", "?"),
                    "confidence": d.get("confidence", 0),
                    "is_reliable": d.get("isReliable", False),
                }
            return {"error": "Aucune détection"}
            
        except Exception as e:
            return {"error": str(e)}
    
    def translate_batch(self, texts: List[str], target: str = "fr") -> List[dict]:
        """Traduit plusieurs textes en un seul appel.
        
        Args:
            texts: Liste de textes à traduire
            target: Langue cible
        
        Returns:
            Liste de résultats de traduction
        """
        if not self.available:
            return [{"error": "Non disponible"}]
        
        params = {
            "target": target,
            "format": "text",
            "key": self.api_key,
        }
        # L'API accepte plusieurs q= dans la même requête
        data_pairs = [("q", t) for t in texts]
        data_pairs.extend(params.items())
        
        try:
            resp = requests.post(self.BASE_URL, data=data_pairs, timeout=30)
            
            if resp.status_code != 200:
                return [{"error": f"HTTP {resp.status_code}"}]
            
            translations = resp.json().get("data", {}).get("translations", [])
            results = []
            for i, t in enumerate(translations):
                results.append({
                    "original": texts[i] if i < len(texts) else "",
                    "translated_text": t.get("translatedText", ""),
                    "detected_source": t.get("detectedSourceLanguage", "?"),
                })
            
            total_chars = sum(len(t) for t in texts)
            logger.info(f"[Cloud Translate] ✅ Batch {len(texts)} textes ({total_chars} chars)")
            return results
            
        except Exception as e:
            return [{"error": str(e)}]


# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def translate_text(text: str, target_lang: str = "fr", source_lang: str = "") -> str:
    """Traduit du texte via Google Cloud Translation.
    
    Langues supportées : fr, en, es, de, it, pt, zh, ja, ko, ar, ru, etc.
    
    Args:
        text: Le texte à traduire
        target_lang: Langue cible (défaut: 'fr')
        source_lang: Langue source (défaut: auto-détection)
    
    Returns:
        Le texte traduit ou un message d'erreur.
    """
    translator = CloudTranslateProvider()
    
    if not translator.available:
        return "Erreur: Cloud Translate non disponible (clé API manquante)"
    
    result = translator.translate(text, target=target_lang, source=source_lang or None)
    
    if "error" in result:
        return f"❌ Erreur de traduction : {result['error']}"
    
    src = result.get("detected_source", "?")
    return (
        f"🌍 Traduction ({src} → {target_lang}) :\n"
        f"{result['translated_text']}"
    )
