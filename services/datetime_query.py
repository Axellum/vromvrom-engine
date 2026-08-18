"""
services/datetime_query.py — Questions d'heure et date vocales (mode:chat).

Répond en Zero-LLM aux questions sur l'heure, la date et le jour de la semaine.
Détection déterministe → lecture de l'horloge système → phrase TTS courte.

Ce module est conçu pour être appelé de manière synchrone car il ne réalise
aucune opération d'E/S bloquante (lecture mémoire uniquement).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

# Configuration du logger pour le suivi des requêtes temporelles
logger = logging.getLogger(__name__)

# Marqueurs de questions temporelles pour la détection déterministe
_TIME_MARKERS = frozenset({"heure", "quelle heure", "il est quelle heure", "quelle heure est-il"})
_DATE_MARKERS = frozenset({"date", "on est quel jour", "quel jour on est", "quel jour sommes-nous"})
_WEEKDAY_MARKERS = frozenset({"quel jour", "jour de la semaine"})

def match_datetime_query(prompt: str) -> Optional[str]:
    """
    Détecte si la phrase utilisateur est une question temporelle.
    Retourne le type de question ('time', 'date', 'weekday') ou None.
    """
    norm = prompt.lower().strip()
    
    # Garde-fou : les questions d'agenda (« à quelle heure je commence demain ? »)
    # ne doivent pas être traitées ici pour laisser la main au spécialiste calendrier.
    agenda_markers = {"agenda", "calendrier", "travail", "commence", "finis", "rdv", "rendez-vous"}
    if any(m in norm for m in agenda_markers):
        return None

    if any(m in norm for m in _TIME_MARKERS):
        return "time"
    if any(m in norm for m in _DATE_MARKERS):
        return "date"
    if any(m in norm for m in _WEEKDAY_MARKERS):
        return "weekday"
    
    return None

def resolve_datetime_query(prompt: str, now: Optional[datetime] = None) -> Optional[str]:
    """
    Résout la question temporelle en générant une phrase TTS.
    
    Args:
        prompt: La phrase utilisateur normalisée.
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
        return f"Il est {dt.hour} heures {dt.minute:02d}."
    
    if query_type == "date":
        return f"Nous sommes le {dt.day} {dt.strftime('%B')} {dt.year}."
    
    if query_type == "weekday":
        return f"Nous sommes {dt.strftime('%A')}."
        
    return None
