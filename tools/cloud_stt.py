"""
tools/cloud_stt.py — Reconnaissance vocale via Google Cloud Speech-to-Text v1.

[Phase 3 - P3.4] Utilise l'API Cloud STT v1 (REST directe avec clé API).

Free Tier : 60 minutes/mois de transcription audio

Utilisation dans le moteur :
  - Commandes vocales depuis le Tab5 M5Stack
  - Transcription d'enregistrements audio
  - Pipeline voix : Micro → STT → Agent → TTS → Haut-parleur

Formats audio supportés : WAV, FLAC, MP3, OGG_OPUS, WEBM_OPUS
Note : Requiert l'activation de speech.googleapis.com sur moteur-ia-free.
"""

import os
import base64
import logging
import requests
from typing import Optional

logger = logging.getLogger("tools.cloud_stt")


class CloudSTTProvider:
    """Provider Cloud Speech-to-Text v1 pour la transcription audio."""
    
    BASE_URL = "https://speech.googleapis.com/v1/speech:recognize"
    
    # Mapping extension → encoding
    ENCODING_MAP = {
        ".wav": "LINEAR16",
        ".flac": "FLAC",
        ".mp3": "MP3",
        ".ogg": "OGG_OPUS",
        ".webm": "WEBM_OPUS",
    }
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CLOUD_API_KEY", "")
        self.available = bool(self.api_key)
    
    def transcribe(
        self,
        audio_path: str,
        language: str = "fr-FR",
        encoding: Optional[str] = None,
        sample_rate: int = 16000,
        enable_punctuation: bool = True,
        model: str = "latest_long",
    ) -> dict:
        """Transcrit un fichier audio en texte.
        
        Args:
            audio_path: Chemin vers le fichier audio
            language: Code langue (ex: "fr-FR", "en-US")
            encoding: Format audio (auto-détecté si None)
            sample_rate: Fréquence d'échantillonnage en Hz
            enable_punctuation: Ajouter la ponctuation automatique
            model: Modèle STT ("latest_long", "latest_short", "phone_call")
        
        Returns:
            Dict avec transcript, confidence, words_count
        """
        if not self.available:
            return {"error": "Cloud STT non disponible"}
        
        if not os.path.exists(audio_path):
            return {"error": f"Fichier introuvable : {audio_path}"}
        
        # Auto-détection du format
        if not encoding:
            ext = os.path.splitext(audio_path)[1].lower()
            encoding = self.ENCODING_MAP.get(ext, "LINEAR16")
        
        # Encoder l'audio en base64
        with open(audio_path, "rb") as f:
            audio_content = base64.b64encode(f.read()).decode("utf-8")
        
        # Vérifier la taille (limite API synchrone : ~10 MB / ~1 min)
        audio_size_mb = len(audio_content) * 3 / 4 / (1024 * 1024)
        if audio_size_mb > 10:
            return {"error": f"Fichier trop volumineux ({audio_size_mb:.1f} MB > 10 MB). Utilisez l'API async."}
        
        payload = {
            "config": {
                "encoding": encoding,
                "sampleRateHertz": sample_rate,
                "languageCode": language,
                "enableAutomaticPunctuation": enable_punctuation,
                "model": model,
            },
            "audio": {
                "content": audio_content,
            },
        }
        
        url = f"{self.BASE_URL}?key={self.api_key}"
        
        try:
            resp = requests.post(url, json=payload, timeout=60)
            
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            
            data = resp.json()
            results = data.get("results", [])
            
            if not results:
                return {"transcript": "", "confidence": 0, "words_count": 0}
            
            # Concaténer tous les segments
            full_transcript = ""
            total_confidence = 0
            count = 0
            
            for result in results:
                alternatives = result.get("alternatives", [])
                if alternatives:
                    best = alternatives[0]
                    full_transcript += best.get("transcript", "")
                    total_confidence += best.get("confidence", 0)
                    count += 1
            
            avg_confidence = total_confidence / max(count, 1)
            words = len(full_transcript.split())
            
            logger.info(
                f"[Cloud STT] ✅ Transcription : {words} mots, "
                f"confiance={avg_confidence:.1%}, lang={language}"
            )
            
            return {
                "transcript": full_transcript.strip(),
                "confidence": round(avg_confidence, 3),
                "words_count": words,
                "language": language,
            }
            
        except Exception as e:
            logger.error(f"[Cloud STT] Erreur : {e}")
            return {"error": str(e)}


# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def transcribe_audio(audio_path: str, language: str = "fr-FR") -> str:
    """Transcrit un fichier audio en texte via Google Cloud Speech-to-Text.
    
    Formats supportés : WAV, FLAC, MP3, OGG
    
    Args:
        audio_path: Chemin vers le fichier audio à transcrire
        language: Code langue (défaut: 'fr-FR')
    
    Returns:
        Le texte transcrit ou un message d'erreur.
    """
    stt = CloudSTTProvider()
    
    if not stt.available:
        return "Erreur: Cloud STT non disponible"
    
    result = stt.transcribe(audio_path=audio_path, language=language)
    
    if "error" in result:
        return f"❌ Erreur STT : {result['error']}"
    
    transcript = result.get("transcript", "")
    confidence = result.get("confidence", 0)
    words = result.get("words_count", 0)
    
    if not transcript:
        return "🔇 Aucune parole détectée dans l'audio"
    
    return (
        f"🎤 Transcription ({words} mots, confiance {confidence:.0%}) :\n"
        f"{transcript}"
    )
