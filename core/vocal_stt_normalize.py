"""
vocal_stt_normalize.py — Nettoyage des transcriptions Whisper avant matching HA.

Les erreurs STT typiques Tab5 (logs vocal_audit) :
- « est-elle » / « et tel » au lieu de « éteins »
- « al lumière » / « l'alumière » au lieu de « la lumière »
- « de la somme » au lieu de « de la chambre »
"""

from __future__ import annotations

import re
import unicodedata


def _strip_accents(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    return "".join(c for c in t if not unicodedata.combining(c))


def normalize_vocal_stt(text: str) -> str:
    """Corrige les erreurs STT courantes sans toucher au reste de la phrase."""
    t = (text or "").strip()
    if not t:
        return t

    # Apostrophes / guillemets parasites
    t = t.replace("'", " ").replace("'", " ").replace("’", " ")
    t = re.sub(r"\s+", " ", t).strip()

    lower = t.lower()
    lower_norm = _strip_accents(lower)

    # Verbes éteindre (STT confond souvent avec « est-elle », « et tel »…)
    off_patterns = (
        (r"\bdescends\b", "descend"),
        (r"\bdescend\b", "descend"),
        (r"\best[\s-]?elle\b", "eteins"),
        (r"\bet[\s-]?(tel|teins|telle|tells|tais)\b", "eteins"),
        (r"\bet t as\b", "eteins"),
        (r"\be[\s-]?ton[\s-]?[aà]?\b", "eteins"),
        (r"\béteint\b", "eteins"),
        (r"\beteint\b", "eteins"),
        (r"\beteindre\b", "eteins"),
    )
    for pattern, repl in off_patterns:
        lower_norm = re.sub(pattern, repl, lower_norm, flags=re.I)

    # Allumer
    on_patterns = (
        (r"\ballumee\b", "allume"),
        (r"\ballume\b", "allume"),
        (r"\bamume\b", "allume"),
    )
    for pattern, repl in on_patterns:
        lower_norm = re.sub(pattern, repl, lower_norm, flags=re.I)

    # Lumière
    light_patterns = (
        (r"\bl[\s']?alumiere\b", "la lumiere"),
        (r"\bal[\s-]?lumiere\b", "la lumiere"),
        (r"\blumiere du\b", "lumiere du"),
        (r"\bgroupe de lumiere\b", "lumiere"),
        (r"\blumieres\b", "lumiere"),
        (r"\blumières\b", "lumiere"),
    )
    for pattern, repl in light_patterns:
        lower_norm = re.sub(pattern, repl, lower_norm, flags=re.I)

    # Pièces mal transcrites
    room_patterns = (
        (r"\bde la somme\b", "de la chambre"),
        (r"\bla somme\b", "la chambre"),
    )
    for pattern, repl in room_patterns:
        lower_norm = re.sub(pattern, repl, lower_norm, flags=re.I)

    return lower_norm.strip()
