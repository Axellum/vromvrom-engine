"""
core/vocal_tts_cache.py — Cache TTS vocal (texte canonique ↔ phrase_id ↔ MP3).

Charge scripts/domotic_phrases.yaml + phrases dynamiques moteur.
Utilisé par execute_service pour normaliser les réponses courtes Tab5/Assist.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PHRASES_YAML = _REPO_ROOT / "scripts" / "domotic_phrases.example.yaml"
_CACHE_DIRS = (
    _REPO_ROOT / "audio_lib",
)

# Phrases fixes du moteur (fallback si domotic_phrases.yaml absent sur le Deck)
_EXTRA_PHRASES: dict[str, str] = {
    "vocal_bonjour": "Bonjour, que veux-tu contrôler ?",
    "vocal_merci": "De rien. Que veux-tu contrôler ?",
    "vocal_ha_echec": "Je n'ai pas compris la commande domotique.",
    "lum_salon_on": "Lumière du salon allumée.",
    "lum_salon_off": "Lumière du salon éteinte.",
    "lum_chambre_on": "Lumière de la chambre allumée.",
    "lum_chambre_off": "Lumière de la chambre éteinte.",
    "lum_cuisine_on": "Lumière de la cuisine allumée.",
    "lum_cuisine_off": "Lumière de la cuisine éteinte.",
    "clim_salon_on": "Climatisation du salon allumée.",
    "clim_salon_off": "Climatisation du salon éteinte.",
    "vol_salon_ouvert": "Les volets du salon sont ouverts.",
    "vol_salon_ferme": "Les volets du salon sont fermés.",
    "vol_salon_stop": "Volet arrêté.",
}

# entity_id HA → (service fragment → phrase_id)
_ENTITY_SERVICE_PHRASES: dict[str, dict[str, str]] = {
    "light.living_room": {"turn_on": "lum_salon_on", "turn_off": "lum_salon_off"},
    "light.bedroom": {"turn_on": "lum_chambre_on", "turn_off": "lum_chambre_off"},
    "light.bedside": {"turn_on": "lum_salon_on", "turn_off": "lum_salon_off"},
    "light.hallway": {"turn_on": "lum_salon_on", "turn_off": "lum_salon_off"},
    "light.cuisine": {"turn_on": "lum_cuisine_on", "turn_off": "lum_cuisine_off"},
    "light.chambre": {"turn_on": "lum_chambre_on", "turn_off": "lum_chambre_off"},
    "light.jardin": {"turn_on": "lum_jardin_on", "turn_off": "lum_jardin_off"},
    "climate.living_room": {"turn_on": "clim_salon_on", "turn_off": "clim_salon_off"},
    "climate.salon": {"turn_on": "clim_salon_on", "turn_off": "clim_salon_off"},
    "climate.chambre": {"turn_on": "clim_chambre_on", "turn_off": "clim_chambre_off"},
    "cover.living_room_blind": {"open_cover": "vol_salon_ouvert", "close_cover": "vol_salon_ferme"},
}


def normalize_tts_text(text: str) -> str:
    """Normalise pour lookup manifest (minuscules, sans ponctuation superflue)."""
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = re.sub(r"[^\w\sàâäéèêëïîôùûüç'-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def load_phrase_catalog() -> dict[str, str]:
    """phrase_id → texte canonique."""
    catalog: dict[str, str] = dict(_EXTRA_PHRASES)
    yaml_path = _PHRASES_YAML
    if yaml_path.exists():
        with yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for item in data.get("phrases", []):
            pid = item.get("id")
            text = item.get("text")
            if pid and text:
                catalog[str(pid)] = str(text).strip()
    else:
        logger.warning("[vocal_tts_cache] Fichier introuvable : %s", yaml_path)
    return catalog


@lru_cache(maxsize=1)
def text_to_phrase_id() -> dict[str, str]:
    """texte normalisé → phrase_id."""
    out: dict[str, str] = {}
    for pid, text in load_phrase_catalog().items():
        out[normalize_tts_text(text)] = pid
    return out


def resolve_phrase_id(text: str) -> str | None:
    return text_to_phrase_id().get(normalize_tts_text(text))


def resolve_phrase_id_for_ha_action(entity_id: str, ha_service: str) -> str | None:
    """Mappe entité + service HA vers une phrase pré-enregistrée."""
    action = ha_service.split(".", 1)[-1] if ha_service else ""
    by_service = _ENTITY_SERVICE_PHRASES.get(entity_id, {})
    return by_service.get(action)


def canonical_text_for_ha_action(entity_id: str, ha_service: str) -> str | None:
    pid = resolve_phrase_id_for_ha_action(entity_id, ha_service)
    if not pid:
        return None
    return load_phrase_catalog().get(pid)


def cache_file_for_phrase(phrase_id: str) -> Path | None:
    """Retourne le chemin audio s'il existe (audio_lib/{id}.mp3 ou .wav)."""
    for base in _CACHE_DIRS:
        for ext in (".mp3", ".wav"):
            path = base / f"{phrase_id}{ext}"
            if path.is_file():
                return path
    return None


def sanitize_discussion_tts(text: str, max_sentences: int = 3, max_chars: int = 420) -> str:
    """
    Nettoie une réponse LLM pour le mode Discussion vocal Tab5.
    Supprime le markdown et limite la longueur pour un TTS fluide.
    """
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"#{1,6}\s*", "", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\n+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = re.split(r"(?<=[.!?…])\s+", t)
    if len(parts) > max_sentences:
        t = " ".join(parts[:max_sentences]).strip()
    if len(t) > max_chars:
        cut = t[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")
        t = (cut + ".") if cut else t[:max_chars]
    return t


def enrich_response_with_tts_cache(response: dict) -> dict:
    """
    Ajoute tts_phrase_id / tts_cache_hit si le texte correspond à un MP3 connu.
    """
    text = response.get("response") or ""
    phrase_id = resolve_phrase_id(text)
    if phrase_id and cache_file_for_phrase(phrase_id):
        response["tts_phrase_id"] = phrase_id
        response["tts_cache_hit"] = True
    else:
        response["tts_cache_hit"] = False
    return response
