"""
tools/minimax_image.py — Outil de génération d'images via l'API MiniMax (modèle image-01).

Permet de générer des images de haute qualité et de les sauvegarder localement à partir de descriptions textuelles.
"""

import os
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger("tools.minimax_image")

class MiniMaxImageProvider:
    """Provider MiniMax pour la génération d'images (modèle image-01).
    
    Usage:
        img_provider = MiniMaxImageProvider()
        path = img_provider.generate_image("A futuristic smart home dashboard...")
    """
    
    BASE_URL = "https://api.minimax.io/v1/image_generation"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Clé API MiniMax (défaut: MINIMAX_API_KEY du .env)
        """
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.available = bool(self.api_key)
        
        if not self.available:
            logger.warning("[MiniMax Image] Aucune clé API configurée dans l'environnement")
            
    def generate_image(
        self,
        prompt: str,
        output_path: Optional[str] = None,
        aspect_ratio: str = "1:1",
        model: str = "image-01"
    ) -> Optional[str]:
        """Génère une image et la sauvegarde en local.
        
        Args:
            prompt: Description textuelle de l'image.
            output_path: Chemin local où enregistrer l'image générée (PNG/JPEG).
            aspect_ratio: Ratio d'aspect de l'image ("1:1", "16:9", "9:16", "4:3", etc.).
            model: Identifiant du modèle (défaut: image-01).
            
        Returns:
            Chemin absolu du fichier image sauvegardé, ou None en cas d'erreur.
        """
        if not self.available:
            logger.error("[MiniMax Image] Clé API absente. Génération impossible.")
            return None
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio
        }
        
        try:
            logger.info(f"[MiniMax Image] Envoi de la requête de génération d'image à {self.BASE_URL}")
            response = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=40.0)
            
            if response.status_code != 200:
                logger.error(f"[MiniMax Image] Erreur HTTP {response.status_code}: {response.text}")
                return None
                
            res_json = response.json()
            base_resp = res_json.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                logger.error(f"[MiniMax Image] Erreur API interne: {base_resp.get('status_msg')} (code: {base_resp.get('status_code')})")
                return None
                
            image_urls = res_json.get("data", {}).get("image_urls", [])
            if not image_urls:
                logger.error("[MiniMax Image] Aucune URL d'image retournée par l'API")
                return None
                
            image_url = image_urls[0]
            logger.info(f"[MiniMax Image] Image générée avec succès. Téléchargement depuis : {image_url}")
            
            # Téléchargement de l'image binaire
            image_response = requests.get(image_url, timeout=20.0)
            if image_response.status_code != 200:
                logger.error(f"[MiniMax Image] Échec du téléchargement de l'image (HTTP {image_response.status_code})")
                return None
                
            # Déterminer le chemin de sortie par défaut
            if not output_path:
                images_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "images"
                )
                os.makedirs(images_dir, exist_ok=True)
                timestamp = int(time.time())
                output_path = os.path.join(images_dir, f"minimax_{timestamp}.jpg")
            else:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                
            with open(output_path, "wb") as f:
                f.write(image_response.content)
                
            logger.info(f"[MiniMax Image] ✅ Image sauvegardée avec succès : {output_path} ({len(image_response.content)} octets)")
            return output_path
            
        except Exception as e:
            logger.error(f"[MiniMax Image] Erreur inattendue lors de la génération : {e}")
            return None

# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def minimax_image_generate(
    prompt: str,
    output_path: str = "",
    aspect_ratio: str = "1:1",
) -> str:
    """Génère une image à partir d'un prompt textuel via le modèle image-01 de MiniMax.
    
    Ratios disponibles : '1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3'.
    
    Args:
        prompt: Description de l'image à générer
        output_path: Chemin local optionnel pour enregistrer l'image (défaut: dossier images/ auto)
        aspect_ratio: Format de l'image (défaut: '1:1')
        
    Returns:
        Chemin absolu du fichier généré ou un message d'erreur.
    """
    provider = MiniMaxImageProvider()
    if not provider.available:
        return "Erreur : La clé MINIMAX_API_KEY est manquante."
        
    res = provider.generate_image(prompt=prompt, output_path=output_path or None, aspect_ratio=aspect_ratio)
    if res:
        return f"✅ Image MiniMax générée avec succès dans : {res}"
    else:
        return "❌ Échec de la génération de l'image via MiniMax."
