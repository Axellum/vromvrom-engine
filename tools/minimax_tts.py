"""
tools/minimax_tts.py — Synthèse vocale expressive via l'API MiniMax Text-to-Audio (T2A) v2.

Permet de générer des fichiers audio MP3 ultra-réalistes à partir de texte en utilisant les voix
et le moteur de synthèse vocale MiniMax.

Modèles disponibles :
  - speech-02-hd (Haute définition / expressivité supérieure)
  - speech-2.8-hd (Génération de voix avancée)

Voix par défaut recommandées :
  - female-shaonv (Jeune fille / Style Anime)
  - female-yujie (Femme mûre / Voix professionnelle)
  - male-chengshu (Homme mûr / Posé)
  - male-qingnian (Jeune homme / Dynamique)
"""

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger("tools.minimax_tts")

class MiniMaxTTSProvider:
    """Provider MiniMax Text-to-Audio V2.
    
    Usage:
        tts = MiniMaxTTSProvider()
        audio_path = tts.synthesize("Bonjour, j'adore ma maison connectée !", voice_id="female-yujie")
    """
    
    BASE_URL = "https://api.minimax.io/v1/t2a_v2"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Clé API MiniMax (défaut: MINIMAX_API_KEY du .env)
        """
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.available = bool(self.api_key)
        
        if not self.available:
            logger.warning("[MiniMax TTS] Aucune clé API configurée dans l'environnement")
            
    def synthesize(
        self,
        text: str,
        voice_id: str = "female-shaonv",
        output_path: Optional[str] = None,
        model: str = "speech-02-hd",
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: int = 0,
        sample_rate: int = 32000
    ) -> Optional[str]:
        """Synthétise du texte en un fichier audio MP3.
        
        Args:
            text: Le texte à lire.
            voice_id: Identifiant de la voix (système ou clonée).
            output_path: Chemin où enregistrer le fichier MP3 final.
            model: Modèle de voix à utiliser (défaut: speech-02-hd).
            speed: Vitesse d'élocution (0.5 à 2.0, défaut 1.0).
            volume: Volume sonore (0.5 à 2.0, défaut 1.0).
            pitch: Hauteur de voix (-12 à 12, défaut 0).
            sample_rate: Fréquence d'échantillonnage de l'audio (16000 ou 32000).
            
        Returns:
            Chemin du fichier audio MP3 généré, ou None en cas d'erreur.
        """
        if not self.available:
            logger.error("[MiniMax TTS] Clé API absente. Synthèse impossible.")
            return None
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "text": text,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": volume,
                "pitch": pitch
            },
            "audio_setting": {
                "format": "mp3",
                "sample_rate": sample_rate
            }
        }
        
        try:
            logger.info(f"[MiniMax TTS] Requête de synthèse ({model}) pour {len(text)} caractères (voix: {voice_id})")
            response = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=20.0)
            
            if response.status_code != 200:
                logger.error(f"[MiniMax TTS] Erreur API HTTP {response.status_code}: {response.text}")
                return None
                
            res_json = response.json()
            base_resp = res_json.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                logger.error(f"[MiniMax TTS] Erreur interne API: {base_resp.get('status_msg')} (code: {base_resp.get('status_code')})")
                return None
                
            hex_audio = res_json.get("data", {}).get("audio", "")
            if not hex_audio:
                logger.error("[MiniMax TTS] Aucun flux audio retourné par l'API")
                return None
                
            # Décodage des données hexadécimales en binaire
            audio_bytes = bytes.fromhex(hex_audio)
            
            # Définir le chemin de sortie par défaut dans le dossier temp si non spécifié
            if not output_path:
                import tempfile
                output_path = os.path.join(tempfile.gettempdir(), f"minimax_tts_{voice_id}.mp3")
                
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
                
            logger.info(f"[MiniMax TTS] ✅ Audio MP3 généré avec succès : {output_path} ({len(audio_bytes)} octets)")
            return output_path
            
        except Exception as e:
            logger.error(f"[MiniMax TTS] Erreur inattendue durant la synthèse : {e}")
            return None

# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def minimax_tts_synthesize(
    text: str,
    voice_id: str = "female-shaonv",
    output_path: str = "",
) -> str:
    """Génère un fichier audio MP3 de synthèse vocale premium et expressive via MiniMax.
    
    Voix par défaut : 'female-shaonv' (jeune fille), 'female-yujie' (femme mûre),
    'male-chengshu' (homme mûr), 'male-qingnian' (jeune homme).
    
    Args:
        text: Le texte à synthétiser en audio
        voice_id: L'ID de la voix système ou clonée (défaut: 'female-shaonv')
        output_path: Chemin local optionnel pour enregistrer l'audio (défaut: temporaire auto)
        
    Returns:
        Le chemin absolu du fichier MP3 généré ou un message d'erreur.
    """
    provider = MiniMaxTTSProvider()
    if not provider.available:
        return "Erreur : La clé MINIMAX_API_KEY est manquante dans les variables d'environnement."
        
    res = provider.synthesize(text=text, voice_id=voice_id, output_path=output_path or None)
    if res:
        return f"✅ Audio MiniMax généré avec succès dans : {res}"
    else:
        return "❌ Échec de la génération audio via MiniMax TTS."
