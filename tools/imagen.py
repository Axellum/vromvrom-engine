"""
tools/imagen.py — Provider Imagen 4 et outil de génération d'images Google AI.

[P6] Expose la génération d'images via l'API Imagen 4 (predict endpoint)
comme :
  1. Un outil enregistrable dans le ToolRegistry (pour les agents)
  2. Des fonctions appelables par la route API REST /api/generate-image

Modèles disponibles (testés le 26/05/2026) :
  - imagen-4.0-generate-001       : Standard (0.04$/image)
  - imagen-4.0-fast-generate-001  : Rapide (0.02$/image)
  - imagen-4.0-ultra-generate-001 : Ultra qualité (0.08$/image)

L'API utilise le endpoint `predict` de Vertex AI via clé API (pas OAuth).
"""

import os
import base64
import time
import logging
import requests

logger = logging.getLogger("tools.imagen")

# Modèles Imagen disponibles avec leurs tarifs
IMAGEN_MODELS = {
    "standard": {
        "model": "imagen-4.0-generate-001",
        "cost_usd": 0.04,
        "description": "Imagen 4 Standard — bon équilibre qualité/coût"
    },
    "fast": {
        "model": "imagen-4.0-fast-generate-001",
        "cost_usd": 0.02,
        "description": "Imagen 4 Fast — génération rapide, coût réduit"
    },
    "ultra": {
        "model": "imagen-4.0-ultra-generate-001",
        "cost_usd": 0.08,
        "description": "Imagen 4 Ultra — qualité maximale"
    },
}


def generate_image(
    prompt: str,
    output_path: str = "",
    model_variant: str = "fast",
    aspect_ratio: str = "1:1",
    number_of_images: str = "1",
) -> str:
    """Génère une image à partir d'un prompt textuel via l'API Google Imagen 4.
    
    Args:
        prompt: Description textuelle de l'image à générer (en français ou anglais)
        output_path: Chemin de sauvegarde de l'image (défaut: images/imagen_<timestamp>.png)
        model_variant: Variante du modèle ('fast', 'standard', 'ultra'). Défaut: 'fast'
        aspect_ratio: Ratio d'aspect ('1:1', '3:4', '4:3', '9:16', '16:9'). Défaut: '1:1'
        number_of_images: Nombre d'images à générer ('1' à '4'). Défaut: '1'
    
    Returns:
        Message de succès avec le chemin du fichier, ou message d'erreur.
    """
    # Clé API — utiliser la payante si disponible, sinon la gratuite
    api_key = os.environ.get("GEMINI_PAYANT_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "Erreur: Aucune clé API Gemini configurée (GEMINI_API_KEY ou GEMINI_PAYANT_API_KEY)"
    
    # Résolution du modèle
    variant = model_variant.lower().strip()
    model_info = IMAGEN_MODELS.get(variant)
    if not model_info:
        return f"Erreur: Variante '{variant}' inconnue. Choisir parmi : {', '.join(IMAGEN_MODELS.keys())}"
    
    model_name = model_info["model"]
    
    # Validation du nombre d'images
    try:
        num_images = min(4, max(1, int(number_of_images)))
    except (ValueError, TypeError):
        num_images = 1
    
    # Chemin de sortie par défaut
    if not output_path:
        images_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "images"
        )
        os.makedirs(images_dir, exist_ok=True)
        timestamp = int(time.time())
        output_path = os.path.join(images_dir, f"imagen_{timestamp}.png")
    else:
        # Créer le dossier parent si nécessaire
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    # Construction du payload API
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:predict?key={api_key}"
    
    payload = {
        "instances": [
            {"prompt": prompt}
        ],
        "parameters": {
            "sampleCount": num_images,
            "aspectRatio": aspect_ratio,
        }
    }
    
    logger.info(
        f"[Imagen] Génération d'image : modèle={model_name}, "
        f"ratio={aspect_ratio}, n={num_images}"
    )
    
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=(5.0, 120.0)  # 5s connect, 120s read (génération longue)
        )
        
        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error(f"[Imagen] Erreur HTTP {resp.status_code} : {error_text}")
            return f"Erreur API Imagen (HTTP {resp.status_code}) : {error_text}"
        
        data = resp.json()
        predictions = data.get("predictions", [])
        
        if not predictions:
            return "Erreur: L'API n'a retourné aucune image. Le prompt est peut-être bloqué par les filtres de sécurité."
        
        # Sauvegarder les images générées
        saved_paths = []
        for i, pred in enumerate(predictions):
            image_b64 = pred.get("bytesBase64Encoded")
            if not image_b64:
                continue
            
            # Déterminer le chemin pour cette image
            if i == 0:
                save_path = output_path
            else:
                base, ext = os.path.splitext(output_path)
                save_path = f"{base}_{i+1}{ext}"
            
            # Décoder et sauvegarder
            image_bytes = base64.b64decode(image_b64)
            with open(save_path, "wb") as f:
                f.write(image_bytes)
            
            saved_paths.append(save_path)
            logger.info(f"[Imagen] Image sauvegardée : {save_path} ({len(image_bytes)} bytes)")
        
        if not saved_paths:
            return "Erreur: Aucune image n'a pu être décodée depuis la réponse API."
        
        # Enregistrer le coût dans le token tracker
        try:
            from core.token_tracker import record_usage
            cost = model_info["cost_usd"] * len(saved_paths)
            record_usage(model_name, 0, 0, cost_usd=cost)
        except Exception:
            pass
        
        cost_total = model_info["cost_usd"] * len(saved_paths)
        paths_str = ", ".join(saved_paths)
        return (
            f"Succès: {len(saved_paths)} image(s) générée(s) avec {model_name}.\n"
            f"Fichier(s) : {paths_str}\n"
            f"Coût estimé : ${cost_total:.3f}"
        )
        
    except requests.exceptions.Timeout:
        return "Erreur: Timeout lors de la génération d'image (>120s). Réessayez avec le modèle 'fast'."
    except Exception as e:
        logger.error(f"[Imagen] Erreur inattendue : {e}")
        return f"Erreur lors de la génération d'image : {e}"


def generate_image_base64(
    prompt: str,
    model_variant: str = "fast",
    aspect_ratio: str = "1:1",
) -> dict:
    """Génère une image et retourne le résultat en base64 (pour la route API REST).
    
    Args:
        prompt: Description textuelle de l'image
        model_variant: Variante du modèle ('fast', 'standard', 'ultra')
        aspect_ratio: Ratio d'aspect
    
    Returns:
        Dict avec les clés : success, images (list de base64), model, cost_usd, error
    """
    api_key = os.environ.get("GEMINI_PAYANT_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"success": False, "error": "Aucune clé API Gemini configurée"}
    
    variant = model_variant.lower().strip()
    model_info = IMAGEN_MODELS.get(variant)
    if not model_info:
        return {"success": False, "error": f"Variante '{variant}' inconnue"}
    
    model_name = model_info["model"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:predict?key={api_key}"
    
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": aspect_ratio,
        }
    }
    
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=(5.0, 120.0)
        )
        
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        
        data = resp.json()
        predictions = data.get("predictions", [])
        
        images_b64 = []
        for pred in predictions:
            b64 = pred.get("bytesBase64Encoded")
            if b64:
                images_b64.append(b64)
        
        if not images_b64:
            return {"success": False, "error": "Aucune image retournée (filtres de sécurité ?)"}
        
        # Enregistrer le coût
        try:
            from core.token_tracker import record_usage
            record_usage(model_name, 0, 0, cost_usd=model_info["cost_usd"])
        except Exception:
            pass
        
        return {
            "success": True,
            "images": images_b64,
            "model": model_name,
            "cost_usd": model_info["cost_usd"],
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}
