"""
tools/cloud_tts.py — Synthèse vocale via Google Cloud Text-to-Speech v1.

[Phase 3 - P3.1] Utilise l'API Cloud TTS v1 (REST directe avec clé API).
Offre des voix WaveNet et Neural2 de haute qualité.

Free Tier : 4M caractères/mois (Standard), 1M caractères/mois (WaveNet/Neural2)

Avantages vs Gemini TTS :
  - Voix plus naturelles (WaveNet, Neural2)
  - SSML support (pauses, emphase, prosodie, prononciation)
  - Choix de 400+ voix dans 50+ langues
  - Contrôle fin du pitch et de la vitesse
  - Formats audio : MP3, WAV, OGG_OPUS, LINEAR16

Architecture :
  - Utilise la clé API payante (GEMINI_PAYANT_API_KEY = projet avec billing)
  - Les APIs Cloud requièrent le billing activé, même pour le Free Tier
  - Fallback automatique vers Gemini TTS si Cloud TTS échoue
"""

import os
import base64
import logging
import requests
from typing import Optional, Literal

logger = logging.getLogger("tools.cloud_tts")

# Voix françaises recommandées (qualité décroissante)
FRENCH_VOICES = {
    "neural2_female": {"name": "fr-FR-Neural2-A", "type": "Neural2"},
    "neural2_male": {"name": "fr-FR-Neural2-D", "type": "Neural2"},
    "wavenet_female": {"name": "fr-FR-Wavenet-A", "type": "WaveNet"},
    "wavenet_male": {"name": "fr-FR-Wavenet-B", "type": "WaveNet"},
    "standard_female": {"name": "fr-FR-Standard-A", "type": "Standard"},
    "standard_male": {"name": "fr-FR-Standard-B", "type": "Standard"},
}

# Voix anglaises
ENGLISH_VOICES = {
    "neural2_female": {"name": "en-US-Neural2-F", "type": "Neural2"},
    "neural2_male": {"name": "en-US-Neural2-D", "type": "Neural2"},
    "wavenet_female": {"name": "en-US-Wavenet-F", "type": "WaveNet"},
    "wavenet_male": {"name": "en-US-Wavenet-D", "type": "WaveNet"},
}


class CloudTTSProvider:
    """Provider Cloud TTS v1 avec support SSML et voix premium.
    
    Usage:
        tts = CloudTTSProvider()
        audio_path = tts.synthesize("Bonjour, je suis votre assistant.")
        audio_path = tts.synthesize("<speak>Bonjour <break time='500ms'/> monde</speak>", ssml=True)
    """
    
    BASE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Clé API Google (défaut: GEMINI_API_KEY du .env)
        """
        self.api_key = api_key or os.environ.get("CLOUD_API_KEY", "")
        self.available = bool(self.api_key)
        
        if not self.available:
            logger.warning("[Cloud TTS] Aucune clé API configurée")
    
    def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        language: str = "fr-FR",
        voice_name: Optional[str] = None,
        voice_gender: str = "FEMALE",
        ssml: bool = False,
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
        audio_encoding: Literal["MP3", "LINEAR16", "OGG_OPUS"] = "MP3",
    ) -> Optional[str]:
        """Synthétise du texte ou SSML en audio.
        
        Args:
            text: Texte brut ou SSML à synthétiser
            output_path: Chemin de sortie (défaut: auto-généré dans /tmp/)
            language: Code langue (ex: "fr-FR", "en-US")
            voice_name: Nom exact de la voix (ex: "fr-FR-Neural2-A")
            voice_gender: Genre si voice_name non spécifié ("FEMALE", "MALE")
            ssml: Si True, traiter le texte comme du SSML
            speaking_rate: Vitesse de parole (0.25 à 4.0, défaut 1.0)
            pitch: Hauteur de voix en demi-tons (-20.0 à 20.0, défaut 0.0)
            audio_encoding: Format audio de sortie
        
        Returns:
            Chemin du fichier audio généré, ou None en cas d'erreur
        """
        if not self.available:
            logger.error("[Cloud TTS] Provider non disponible")
            return None
        
        # Sélection automatique de la voix
        if not voice_name:
            if language.startswith("fr"):
                voices = FRENCH_VOICES
            else:
                voices = ENGLISH_VOICES
            
            gender_key = "female" if voice_gender.upper() == "FEMALE" else "male"
            # Préférer Neural2 > WaveNet > Standard
            for quality in ["neural2", "wavenet", "standard"]:
                key = f"{quality}_{gender_key}"
                if key in voices:
                    voice_name = voices[key]["name"]
                    break
        
        # Construction du payload
        input_field = "ssml" if ssml else "text"
        payload = {
            "input": {input_field: text},
            "voice": {
                "languageCode": language,
                "name": voice_name,
            },
            "audioConfig": {
                "audioEncoding": audio_encoding,
                "speakingRate": speaking_rate,
                "pitch": pitch,
            },
        }
        
        # Appel API
        url = f"{self.BASE_URL}?key={self.api_key}"
        
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            
            if resp.status_code != 200:
                error_msg = resp.text[:300]
                logger.error(f"[Cloud TTS] Erreur HTTP {resp.status_code}: {error_msg}")
                return None
            
            data = resp.json()
            audio_content = data.get("audioContent", "")
            
            if not audio_content:
                logger.error("[Cloud TTS] Pas de contenu audio dans la réponse")
                return None
            
            # Décodage base64 → fichier audio
            audio_bytes = base64.b64decode(audio_content)
            
            # Chemin de sortie
            ext = {"MP3": ".mp3", "LINEAR16": ".wav", "OGG_OPUS": ".ogg"}.get(audio_encoding, ".mp3")
            if not output_path:
                import tempfile
                output_path = os.path.join(tempfile.gettempdir(), f"cloud_tts_output{ext}")
            
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            
            logger.info(
                f"[Cloud TTS] ✅ Audio généré : {output_path} "
                f"({len(audio_bytes)} bytes, voix={voice_name}, "
                f"chars={len(text)})"
            )
            return output_path
            
        except Exception as e:
            logger.error(f"[Cloud TTS] Erreur : {e}")
            return None
    
    def list_voices(self, language: str = "fr-FR") -> list:
        """Liste les voix disponibles pour une langue.
        
        Args:
            language: Code langue (ex: "fr-FR")
        
        Returns:
            Liste des voix avec nom, genre et type
        """
        url = f"https://texttospeech.googleapis.com/v1/voices?languageCode={language}&key={self.api_key}"
        
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return []
            
            voices = resp.json().get("voices", [])
            return [
                {
                    "name": v.get("name", ""),
                    "gender": v.get("ssmlGender", ""),
                    "natural_sample_rate": v.get("naturalSampleRateHertz", 0),
                    "language_codes": v.get("languageCodes", []),
                }
                for v in voices
            ]
        except Exception as e:
            logger.error(f"[Cloud TTS] Erreur list_voices : {e}")
            return []


# ── Fonction outil pour le ToolRegistry ──────────────────────────────

def cloud_tts_synthesize(
    text: str,
    voice: str = "neural2_female",
    language: str = "fr-FR",
    output_path: str = "",
) -> str:
    """Synthétise du texte en audio via Google Cloud TTS v1 (voix premium).
    
    Voix disponibles : 'neural2_female', 'neural2_male', 'wavenet_female',
    'wavenet_male', 'standard_female', 'standard_male'
    
    Args:
        text: Le texte à synthétiser en audio
        voice: Nom de la voix (défaut: 'neural2_female')
        language: Code langue (défaut: 'fr-FR')
        output_path: Chemin de sortie (défaut: auto)
    
    Returns:
        Chemin du fichier audio généré ou message d'erreur.
    """
    tts = CloudTTSProvider()
    
    if not tts.available:
        return "Erreur: Cloud TTS non disponible (clé API manquante)"
    
    # Résolution du nom de voix
    voices = FRENCH_VOICES if language.startswith("fr") else ENGLISH_VOICES
    voice_info = voices.get(voice)
    voice_name = voice_info["name"] if voice_info else None
    
    result = tts.synthesize(
        text=text,
        output_path=output_path or None,
        language=language,
        voice_name=voice_name,
    )
    
    if result:
        return f"✅ Audio généré : {result} (voix: {voice_name or voice})"
    else:
        return "❌ Échec de la synthèse vocale Cloud TTS"
