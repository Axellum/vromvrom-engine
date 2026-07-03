"""
tools/cloud_vision.py — Analyse d'images via Google Cloud Vision API v1.

[Phase 3 - P3.3] Utilise l'API Cloud Vision v1 (REST directe avec clé API).

Free Tier : 1000 requêtes/mois (détection de labels, texte OCR, objets)

Utilisation dans le moteur :
  - Analyse de captures d'écran Tab5 (debug UI)
  - OCR sur des documents scannés
  - Détection d'objets dans des photos domotiques
  - Lecture de QR codes et codes-barres

Note : Requiert l'activation de vision.googleapis.com sur moteur-ia-free.
"""

import os
import base64
import logging
import requests
from typing import Optional, List

logger = logging.getLogger("tools.cloud_vision")


class CloudVisionProvider:
    """Provider Cloud Vision v1 pour l'analyse d'images."""
    
    BASE_URL = "https://vision.googleapis.com/v1/images:annotate"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CLOUD_API_KEY", "")
        self.available = bool(self.api_key)
    
    def _encode_image(self, image_path: str) -> str:
        """Encode une image locale en base64."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    
    def analyze(
        self,
        image_path: Optional[str] = None,
        image_url: Optional[str] = None,
        features: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> dict:
        """Analyse une image (labels, texte, objets, etc.).
        
        Args:
            image_path: Chemin vers un fichier image local
            image_url: URL d'une image en ligne
            features: Liste de features à détecter. Défaut: ["LABEL_DETECTION", "TEXT_DETECTION"]
                Options: LABEL_DETECTION, TEXT_DETECTION, OBJECT_LOCALIZATION,
                         FACE_DETECTION, SAFE_SEARCH_DETECTION, WEB_DETECTION
            max_results: Nombre max de résultats par feature
        
        Returns:
            Dict avec labels, text, objects, etc.
        """
        if not self.available:
            return {"error": "Cloud Vision non disponible"}
        
        if not features:
            features = ["LABEL_DETECTION", "TEXT_DETECTION", "OBJECT_LOCALIZATION"]
        
        # Construction de l'image source
        if image_path:
            image = {"content": self._encode_image(image_path)}
        elif image_url:
            image = {"source": {"imageUri": image_url}}
        else:
            return {"error": "Il faut image_path ou image_url"}
        
        # Construction du payload
        payload = {
            "requests": [{
                "image": image,
                "features": [
                    {"type": f, "maxResults": max_results}
                    for f in features
                ],
            }]
        }
        
        url = f"{self.BASE_URL}?key={self.api_key}"
        
        try:
            resp = requests.post(url, json=payload, timeout=30)
            
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            
            responses = resp.json().get("responses", [])
            if not responses:
                return {"error": "Pas de réponse"}
            
            result = responses[0]
            
            # Extraction structurée
            parsed = {}
            
            # Labels
            labels = result.get("labelAnnotations", [])
            if labels:
                parsed["labels"] = [
                    {"description": l["description"], "score": round(l.get("score", 0), 3)}
                    for l in labels
                ]
            
            # Texte OCR
            text_annotations = result.get("textAnnotations", [])
            if text_annotations:
                parsed["full_text"] = text_annotations[0].get("description", "")
                parsed["text_blocks"] = len(text_annotations) - 1
            
            # Objets localisés
            objects = result.get("localizedObjectAnnotations", [])
            if objects:
                parsed["objects"] = [
                    {"name": o["name"], "score": round(o.get("score", 0), 3)}
                    for o in objects
                ]
            
            # Erreur éventuelle
            if result.get("error"):
                parsed["error"] = result["error"].get("message", "?")
            
            logger.info(
                f"[Cloud Vision] ✅ Analyse : "
                f"{len(parsed.get('labels', []))} labels, "
                f"{parsed.get('text_blocks', 0)} blocs texte, "
                f"{len(parsed.get('objects', []))} objets"
            )
            return parsed
            
        except Exception as e:
            logger.error(f"[Cloud Vision] Erreur : {e}")
            return {"error": str(e)}
    
    def ocr(self, image_path: str) -> str:
        """Extrait le texte d'une image (OCR simplifié).
        
        Returns:
            Le texte extrait ou un message d'erreur
        """
        result = self.analyze(image_path=image_path, features=["TEXT_DETECTION"])
        return result.get("full_text", result.get("error", "Aucun texte détecté"))


# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def analyze_image(image_path: str) -> str:
    """Analyse une image locale (labels, texte OCR, objets détectés).
    
    Args:
        image_path: Chemin vers le fichier image à analyser
    
    Returns:
        Résultat d'analyse formaté en texte.
    """
    vision = CloudVisionProvider()
    
    if not vision.available:
        return "Erreur: Cloud Vision non disponible"
    
    if not os.path.exists(image_path):
        return f"Erreur: Fichier introuvable : {image_path}"
    
    result = vision.analyze(image_path=image_path)
    
    if "error" in result:
        return f"❌ Erreur Vision : {result['error']}"
    
    lines = [f"🔍 Analyse de {os.path.basename(image_path)} :"]
    
    if result.get("labels"):
        lines.append("\n📌 Labels :")
        for l in result["labels"][:8]:
            lines.append(f"  • {l['description']} ({l['score']:.0%})")
    
    if result.get("full_text"):
        text = result["full_text"][:500]
        lines.append(f"\n📝 Texte OCR :\n  {text}")
    
    if result.get("objects"):
        lines.append("\n🎯 Objets détectés :")
        for o in result["objects"][:5]:
            lines.append(f"  • {o['name']} ({o['score']:.0%})")
    
    return "\n".join(lines)
