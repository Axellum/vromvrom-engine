"""
source_router.py — Routing source-aware pour le tab5-engine.

Gère les comportements différenciés selon l'origine de la requête :
- tab5/ha       : domotique, Zero-LLM si possible, réponse ultra-courte (TTS)
- tab5/chat     : conversationnel fluide, accès HA en outil, TTS-friendly
- ide/default   : pipeline complet V9 (Planner → DAG → Reviewer)

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Enums et Dataclasses
# ──────────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    """Type de device/interface source de la requête."""
    TAB5      = "tab5"       # Tablette tactile M5Stack Tab5
    WHATSAPP  = "whatsapp"   # Bot WhatsApp
    IDE       = "ide"        # Antigravity IDE (PC)
    VOICE     = "voice"      # Assistant vocal natif
    WEB       = "web"        # Interface web du moteur
    UNKNOWN   = "unknown"    # Source non identifiée (comportement défaut)


class ModeType(str, Enum):
    """Mode actif sur la source (pertinent surtout pour Tab5)."""
    HA           = "ha"           # Domotique — rapide, déterministe
    CHAT         = "chat"         # Conversationnel — fluide, TTS
    FILES        = "files"        # Fichiers — délégation PC (à implémenter plus tard)
    DEFAULT      = "default"      # Comportement pipeline complet


class ResponseStyle(str, Enum):
    """Style de réponse attendu selon le mode."""
    IMMEDIATE  = "immediate"   # 1 phrase max, action pure (ha)
    NATURAL    = "natural"     # TTS-friendly, ton conversationnel (chat)
    DETAILED   = "detailed"    # Réponse complète, structurée (ide)


@dataclass
class RequestSource:
    """
    Métadonnées de source attachées à chaque requête.

    Envoyées par le client (Tab5, WhatsApp, IDE...) dans le champ
    `source` du corps JSON de /api/execute.

    Attributs optionnels : le moteur applique des valeurs par défaut
    raisonnables si le champ est absent ou incomplet.
    """
    type: SourceType = SourceType.UNKNOWN
    mode: ModeType   = ModeType.DEFAULT
    tts_enabled: bool = False          # Adapter la longueur/style pour TTS
    device_id: str | None = None    # Identifiant unique du device (futur multi-Tab5)
    conversation_id: str | None = None  # Session HA Assist multi-tour (Sprint A3)

    @classmethod
    def from_dict(cls, data: dict) -> "RequestSource":
        """Construit un RequestSource depuis un dict JSON (champ 'source')."""
        if not data:
            return cls()
        try:
            src_type = SourceType(data.get("type", "unknown"))
        except ValueError:
            src_type = SourceType.UNKNOWN
        try:
            mode = ModeType(data.get("mode", "default"))
        except ValueError:
            mode = ModeType.DEFAULT
        return cls(
            type=src_type,
            mode=mode,
            tts_enabled=bool(data.get("tts_enabled", False)),
            device_id=data.get("device_id"),
            conversation_id=data.get("conversation_id"),
        )

    def get_response_style(self) -> ResponseStyle:
        """Détermine le style de réponse selon la source et le mode."""
        if self.mode == ModeType.HA:
            return ResponseStyle.IMMEDIATE
        if self.mode == ModeType.CHAT or self.tts_enabled:
            return ResponseStyle.NATURAL
        return ResponseStyle.DETAILED

    def get_timeout(self) -> float:
        """Timeout en secondes selon le mode (recommandation DeepSeek)."""
        timeouts = {
            ModeType.HA:      2.0,   # Ultra-rapide : déterministe ou abort
            ModeType.CHAT:   20.0,   # Discussion vocale (LLM léger)
            ModeType.FILES:   5.0,   # Délégation PC
            ModeType.DEFAULT: 120.0, # Pipeline complet
        }
        return timeouts.get(self.mode, 120.0)

    def get_model_tier(self) -> str:
        """Tier de modèle LLM recommandé selon le mode."""
        tiers = {
            ModeType.HA:      "leger",   # Flash Lite ou local — le moins cher
            ModeType.CHAT:    "leger",   # Flash Lite — fluidité > puissance
            ModeType.DEFAULT: "automatique",
        }
        return tiers.get(self.mode, "automatique")

    def should_skip_planner(self) -> bool:
        """
        Indique si le pipeline doit sauter le Planner (réponse directe).
        True pour les modes qui ne bénéficient pas d'un plan multi-étapes.
        """
        return self.mode in (ModeType.HA, ModeType.CHAT)

    def get_system_prompt_suffix(self) -> str:
        """
        Suffixe injecté dans le system prompt selon le mode.
        Permet à l'agent d'adapter son style de réponse.
        """
        suffixes = {
            ResponseStyle.IMMEDIATE: (
                "\n\n[MODE VOCAL DOMOTIQUE] "
                "Réponds en UNE SEULE PHRASE courte et directe. "
                "Exemple : 'Lumière allumée.' ou 'Volet fermé.' "
                "Aucune explication, aucun titre, aucune liste."
            ),
            ResponseStyle.NATURAL: (
                "\n\n[MODE VOCAL CONVERSATION — DISCUSSION] "
                "Tu es l'assistant vocal sur sa tablette Tab5, en conversation libre. "
                "Réponds en français, naturellement et brièvement : 2 à 3 phrases maximum. "
                "Pas de markdown, pas de listes, pas de titres, pas d'emojis, pas de code. "
                "Pas de noms d'entités techniques Home Assistant sauf si Axel le demande. "
                "Si tu ne sais pas, dis-le en une phrase."
            ),
            ResponseStyle.DETAILED: "",  # Pas de contrainte
        }
        return suffixes.get(self.get_response_style(), "")

    def __repr__(self) -> str:
        return (
            f"RequestSource(type={self.type.value}, mode={self.mode.value}, "
            f"tts={self.tts_enabled}, device={self.device_id})"
        )


# ──────────────────────────────────────────────────────────────────
# Helpers globaux
# ──────────────────────────────────────────────────────────────────

def parse_source(source_data: dict | None) -> RequestSource:
    """
    Point d'entrée principal : parse le champ 'source' du body JSON.

    Retourne toujours un RequestSource valide (avec valeurs par défaut si absent).
    """
    if not source_data or not isinstance(source_data, dict):
        return RequestSource()
    return RequestSource.from_dict(source_data)


def log_source_decision(source: RequestSource, routing_type: str):
    """Log standardisé de la décision de routing source."""
    logger.info(
        f"[SOURCE ROUTER] {source} → routing_type={routing_type} | "
        f"style={source.get_response_style().value} | "
        f"timeout={source.get_timeout()}s | "
        f"tier={source.get_model_tier()} | "
        f"skip_planner={source.should_skip_planner()}"
    )
