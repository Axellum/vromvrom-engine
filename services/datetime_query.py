"""
services/datetime_query.py — Questions d'heure et de date vocales.

Répond en Zero-LLM aux questions sur l'heure, la date et le jour de la semaine.
Détection déterministe → lecture de l'horloge système → phrase TTS courte.

Appelé par le mode Discussion (`core/vocal_host.py`) et par le mode domotique
(`api/routes/agents.py`, étage « questions du quotidien ») : le 19/10/2026,
l'agent « Domotique » de HA répondait « Je n'ai pas compris la commande
domotique. » à « Quelle heure est-il ? » faute de cet étage.

Les noms des jours et des mois sont écrits ici en français : `strftime('%A')` /
`strftime('%B')` suivent la locale du processus, qui est l'anglais sur le Deck
(« Nous sommes le 19 October 2026. » mesuré en prod le 19/10/2026).

Ce module est conçu pour être appelé de manière synchrone car il ne réalise
aucune opération d'E/S bloquante (lecture mémoire uniquement).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime

# Configuration du logger pour le suivi des requêtes temporelles
logger = logging.getLogger(__name__)

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

# Les motifs portent sur la phrase normalisée par `_normaliser` : minuscules,
# SANS accents, apostrophes et traits d'union remplacés par des espaces
# (« Quelle heure est-il ? » → « quelle heure est il »).
#
# Heure ACTUELLE. Un simple « heure » ne suffit plus : il captait « je suis
# heureux », « un quart d'heure » (bruit de télévision réellement transcrit en
# prod) et « à quelle heure ferme la pharmacie », qui demande l'heure d'un
# événement, pas l'heure qu'il est.
_MOTIFS_HEURE = (
    re.compile(r"\bquel(le)? heure\b"),  # quelle heure est-il, il est quelle heure
    re.compile(r"\bl heure\b(?! (du|de|des|d)\b)"),  # tu as l'heure, donne-moi l'heure
)
_MOTIFS_PAS_HEURE = (
    re.compile(r"\ba quel(le)? heure\b"),  # à quelle heure… : un événement
    re.compile(r"\ba l heure\b"),  # le train est à l'heure
)

# Date du jour. Les formes en « quel jour » sont ancrées en fin de phrase pour
# ne pas répondre la date du jour à « c'est quel jour la fête des mères ? ».
_MOTIFS_DATE = (
    re.compile(r"\bquel(le)? (est la )?date\b"),
    re.compile(r"\b(donne|dis|donnez|dites)( moi)? la date\b"),
    re.compile(r"\bc est quoi la date\b"),
    re.compile(r"\bla date d aujourd hui\b"),
    re.compile(r"\b(on est|nous sommes|c est) le combien\b"),
    re.compile(r"\b(on est|nous sommes|c est) quel jour( aujourd hui)?$"),
    re.compile(r"\bquel jour (on est|sommes nous|nous sommes|est on|est ce|est il|c est)( aujourd hui)?$"),
    re.compile(r"\bquel jour (de la semaine )?(on est |sommes nous |nous sommes )?aujourd hui$"),
    re.compile(r"\bquel jour de la semaine (on est|sommes nous|nous sommes|est on|c est)$"),
)

# Garde-fou : les questions d'agenda (« à quelle heure je commence demain ? »)
# ne sont pas traitées ici et restent au spécialiste calendrier.
_MARQUEURS_AGENDA = (
    "agenda",
    "calendrier",
    "travail",
    "commence",
    "finis",
    "termine",
    "rdv",
    "rendez vous",
    "reunion",
)


def _normaliser(texte: str) -> str:
    """Minuscules, sans accents, ponctuation et apostrophes → espaces."""
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    t = t.replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def match_datetime_query(prompt: str) -> str | None:
    """
    Détecte si la phrase utilisateur est une question temporelle.
    Retourne le type de question ('time' ou 'date') ou None.
    """
    norm = _normaliser(prompt)
    if not norm:
        return None

    if any(m in norm for m in _MARQUEURS_AGENDA):
        return None

    if any(m.search(norm) for m in _MOTIFS_HEURE) and not any(m.search(norm) for m in _MOTIFS_PAS_HEURE):
        return "time"
    if any(m.search(norm) for m in _MOTIFS_DATE):
        return "date"
    return None


def formater_heure(dt: datetime) -> str:
    """« Il est 15 heures 23. », « Il est 1 heure 05. », « Il est 15 heures. »"""
    unite = "heure" if dt.hour < 2 else "heures"
    if dt.minute:
        return f"Il est {dt.hour} {unite} {dt.minute:02d}."
    return f"Il est {dt.hour} {unite}."


def formater_date(dt: datetime) -> str:
    """« Nous sommes le lundi 19 octobre 2026. » (« 1er » le premier du mois)."""
    jour = "1er" if dt.day == 1 else str(dt.day)
    return f"Nous sommes le {_JOURS[dt.weekday()]} {jour} {_MOIS[dt.month - 1]} {dt.year}."


def resolve_datetime_query(prompt: str, now: datetime | None = None) -> str | None:
    """
    Résout la question temporelle en générant une phrase TTS.

    Args:
        prompt: La phrase utilisateur.
        now: Instant injectable pour faciliter les tests unitaires.

    Returns:
        Une phrase TTS courte ou None si la question n'est pas temporelle.
    """
    query_type = match_datetime_query(prompt)
    if not query_type:
        return None

    # Utilise l'instant fourni ou l'heure système actuelle
    dt = now or datetime.now()

    if query_type == "time":
        return formater_heure(dt)
    return formater_date(dt)
