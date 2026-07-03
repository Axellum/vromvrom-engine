"""
core/intent_splitter.py — Décomposition des requêtes multi-intent.

Détecte et sépare les requêtes utilisateur contenant plusieurs intentions
distinctes (ex: "allume la lumière ET donne-moi la météo") en sous-requêtes
indépendantes, chacune pouvant être routée vers un agent différent.

Stratégie hybride :
1. Détection par conjonctions et délimiteurs (rapide, zero-LLM)
2. Validation par heuristiques de changement de domaine (HA vs code vs info)
3. Fallback LLM si les heuristiques sont ambiguës (optionnel)

Créé dans le cadre de l'audit V5.5 (Axe R1 — multi-intent missing).
"""

import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Patterns de séparation multi-intent (conjonctions, ponctuation)
# ──────────────────────────────────────────────────────────────────

# Conjonctions et délimiteurs indiquant un changement d'intention
_SPLIT_PATTERNS = [
    # Conjonctions coordonnantes fortes
    r'\bet\s+(?:aussi|ensuite|après|puis)\b',
    r'\bpuis\b',
    r'\bensuite\b',
    r'\baprès\s+(?:ça|cela)\b',
    # Conjonctions avec changement de verbe d'action
    r'\bet\b(?=\s+(?:donne|affiche|montre|allume|éteins|crée|modifie|lis|supprime|lance|vérifie|dis|explique))',
    # Délimiteurs de ponctuation (point-virgule, tirets de liste)
    r'\s*;\s*',
    r'\s*\.\s+(?=[A-ZÉÈÀ])',  # Point suivi d'une majuscule (nouvelle phrase)
    # Numérotation (1. ... 2. ... ou - ... - ...)
    r'\s*\d+\)\s+',
    r'\s*\d+\.\s+(?=[A-Za-zÉÈÀ])',
]

# Domaines sémantiques pour valider que les sous-requêtes sont réellement
# des intents distincts (et pas juste des détails du même intent)
_DOMAIN_KEYWORDS = {
    "domotique": {
        "allume", "éteins", "lumière", "lampe", "volet", "thermostat",
        "chauffage", "climatisation", "capteur", "température", "humidité",
        "alarme", "caméra", "porte", "serrure", "prise", "switch",
        "home assistant", "ha", "domotique", "automation",
    },
    "meteo": {
        "météo", "meteo", "temps", "pluie", "soleil", "température extérieure",
        "prévision", "neige", "vent", "orage",
    },
    "code": {
        "code", "script", "python", "yaml", "esphome", "fichier", "variable",
        "fonction", "classe", "module", "compiler", "debug", "erreur",
        "modifier", "créer", "écrire", "lire",
    },
    "info": {
        "explique", "raconte", "donne-moi", "c'est quoi", "pourquoi",
        "comment", "résume", "synthèse", "analyse", "compare",
    },
    "calendrier": {
        "calendrier", "agenda", "rendez-vous", "événement", "planning",
        "rappel", "date", "heure",
    },
}


class IntentSplitter:
    """
    Décompose une requête multi-intent en sous-requêtes indépendantes.

    Usage:
        splitter = IntentSplitter()
        intents = splitter.split("Allume la lumière du salon et donne-moi la météo")
        # → ["Allume la lumière du salon", "donne-moi la météo"]
    """

    def __init__(self, min_intent_length: int = 8, max_intents: int = 5):
        """
        Args:
            min_intent_length: Longueur minimale d'un intent valide (caractères)
            max_intents: Nombre maximum de sous-intents à extraire
        """
        self._min_length = min_intent_length
        self._max_intents = max_intents
        # Compilation des patterns en un seul regex (performance)
        self._split_regex = re.compile(
            "|".join(f"(?:{p})" for p in _SPLIT_PATTERNS),
            re.IGNORECASE,
        )

    def split(self, user_prompt: str) -> List[str]:
        """
        Tente de décomposer le prompt en sous-intents distincts.

        Returns:
            Liste de sous-requêtes. Si le prompt est mono-intent,
            retourne une liste contenant uniquement le prompt original.
        """
        if not user_prompt or len(user_prompt) < self._min_length * 2:
            return [user_prompt]

        # Étape 1 : Découper par les patterns de conjonction
        candidates = self._split_regex.split(user_prompt)

        # Nettoyer les fragments vides ou trop courts
        candidates = [
            c.strip() for c in candidates
            if c and len(c.strip()) >= self._min_length
        ]

        if len(candidates) <= 1:
            return [user_prompt]

        # Étape 2 : Valider que les fragments appartiennent à des domaines différents
        validated = self._validate_domain_separation(candidates)

        if len(validated) <= 1:
            return [user_prompt]

        # Limiter le nombre d'intents
        result = validated[: self._max_intents]

        logger.info(
            f"[INTENT SPLITTER] Requête décomposée en {len(result)} sous-intents : "
            + " | ".join(r[:50] for r in result)
        )

        return result

    def _validate_domain_separation(self, fragments: List[str]) -> List[str]:
        """
        Vérifie que les fragments appartiennent à des domaines sémantiques
        différents. Si tous les fragments sont du même domaine, c'est
        probablement un seul intent avec des détails.

        Returns:
            Les fragments validés comme intents distincts.
        """
        fragment_domains: List[Tuple[str, Optional[str]]] = []

        for frag in fragments:
            domain = self._detect_domain(frag)
            fragment_domains.append((frag, domain))

        # Si tous les fragments sont du même domaine, on ne sépare pas
        domains = [d for _, d in fragment_domains if d is not None]
        unique_domains = set(domains)

        if len(unique_domains) <= 1 and len(domains) == len(fragments):
            # Tous les fragments sont du même domaine → mono-intent
            return []

        # Regrouper les fragments consécutifs du même domaine
        merged = []
        current_group = [fragment_domains[0][0]]
        current_domain = fragment_domains[0][1]

        for frag, domain in fragment_domains[1:]:
            if domain == current_domain and domain is not None:
                # Même domaine → fusionner
                current_group.append(frag)
            else:
                # Domaine différent → nouveau groupe
                merged.append(" ".join(current_group))
                current_group = [frag]
                current_domain = domain

        merged.append(" ".join(current_group))

        return [m for m in merged if len(m) >= self._min_length]

    def _detect_domain(self, text: str) -> Optional[str]:
        """Détecte le domaine sémantique dominant d'un fragment de texte."""
        text_lower = text.lower()
        scores = {}

        for domain, keywords in _DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[domain] = score

        if not scores:
            return None

        return max(scores, key=scores.get)

    def is_multi_intent(self, user_prompt: str) -> bool:
        """Vérifie rapidement si un prompt est multi-intent sans le décomposer."""
        return len(self.split(user_prompt)) > 1
