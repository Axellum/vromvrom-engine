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

import logging
import re

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Patterns de séparation multi-intent (conjonctions, ponctuation)
# ──────────────────────────────────────────────────────────────────

# [#T264] Délimiteurs d'ÉNUMÉRATION uniquement. Deux familles ont été retirées
# le 10/08 après une mesure en production :
#
# - `\.\s+(?=[A-ZÉÈÀ])` (point suivi d'une majuscule) découpait sur CHAQUE phrase.
#   Une consigne structurée de dix lignes partait en cinq « intents », dont
#   « CONTRAINTE : utilise uniquement l'outil read_file » exécutée comme une
#   tâche à part entière, et « dans une tâche finale dépendant des trois »
#   devenue un intent isolé qui ne dépendait plus de rien. 4 fragments sur 5
#   ont fini en timeout.
# `puis` / `ensuite` ont en revanche été CONSERVÉS, après vérification. Ils
# marquent une séquence, donc une dépendance, alors que les intents partent en
# parallèle (`asyncio.gather`) — c'est une imperfection connue. Mais le rejeu
# des 1304 requêtes réelles montre que les retirer ne change rien (mêmes 3
# requêtes découpées), tandis que les garder préserve un comportement
# explicitement testé depuis l'origine (`test_pure_helpers.py`). Sur les 1280
# requêtes courtes du corpus, les 6 seules occurrences de « puis » étaient de la
# prose dictée (« Et puis il y a des œufs d'amour »), déjà neutralisées par la
# garde du verbe orphelin ci-dessous. Retirer ce marqueur aurait donc cassé un
# cas légitime sans rien corriger.
_SPLIT_PATTERNS = [
    # Conjonctions de séquence (cf. note ci-dessus)
    r'\bet\s+(?:aussi|ensuite|après|puis)\b',
    r'\bpuis\b',
    r'\bensuite\b',
    r'\baprès\s+(?:ça|cela)\b',
    # Coordination de deux ordres explicites (le verbe d'action suit le « et »)
    r'\bet\b(?=\s+(?:donne|affiche|montre|allume|éteins|crée|modifie|lis|supprime|lance|vérifie|dis|explique))',
    # Point-virgule : séparateur d'énumération sans ambiguïté
    r'\s*;\s*',
    # Numérotation explicite (1) … 2) … ou 1. … 2. …)
    r'\s*\d+\)\s+',
    r'\s*\d+\.\s+(?=[A-Za-zÉÈÀ])',
]

# [#T264] Longueur au-delà de laquelle un prompt n'est PLUS considéré comme une
# liste de courses mais comme une consigne structurée, qu'il ne faut pas hacher.
# Calibré sur 1304 requêtes réelles de production (vocal_audit_log + sessions) :
# médiane 30 caractères, p90 = 51, p99 = 351. Seules 28 requêtes dépassent 200
# caractères, et ce sont des prompts à contexte injecté (« Question utilisateur :
# … Données récupérées : … ») — jamais des demandes multiples. Le seuil laisse
# donc éligible l'écrasante majorité du trafic réel tout en excluant la prose.
LONGUEUR_MAX_DECOUPAGE = 250

# [#T264] Un fragment qui RENVOIE à un autre n'est pas autonome : le découper le
# prive de son référent. Si l'un des fragments en contient un, le prompt est un
# tout structuré et n'est pas découpé.
# Pas de `\bleurs?\b` nu : en français c'est surtout le possessif (« leur
# anniversaire », « leurs enfants ») et ça bloquait des vrais multi-intents
# après un split légitime sur « et » + verbe. On garde seulement l'anaphore
# « leur(s) résultat(s) », déjà couverte au singulier par « le résultat ».
_MARQUEURS_DEPENDANCE = re.compile(
    r"\b(?:les trois|les deux|ces (?:trois|deux|fichiers|tâches|taches|résultats|resultats)|"
    r"ci-dessus|ci-dessous|précédent|precedent|précédente|precedente|"
    r"cette tâche|cette tache|celles-ci|ceux-ci|celle-ci|celui-ci|"
    r"le résultat|le resultat|leurs?\s+résultats?|leurs?\s+resultats?|"
    r"en découlant|en decoulant|"
    r"dépendant|dependant|une fois (?:que|cela|ceci))\b",
    re.IGNORECASE,
)

# [#T264] Une contrainte n'est pas une tâche. Un fragment impératif de contrainte
# exécuté seul produit du travail parasite — mesuré : « CONTRAINTE : utilise
# UNIQUEMENT l'outil read_file » est partie en exécution indépendante.
_MARQUEURS_CONTRAINTE = re.compile(
    r"\b(?:contrainte|interdiction|interdit|obligatoire|attention|important|"
    r"règle ?:|regle ?:|n'écris|necris|n'exécute|nexecute|ne modifie|ne crée|ne cree|"
    r"uniquement|exclusivement|surtout pas|en aucun cas)\b",
    re.IGNORECASE,
)

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

    def split(self, user_prompt: str) -> list[str]:
        """
        Tente de décomposer le prompt en sous-intents distincts.

        Returns:
            Liste de sous-requêtes. Si le prompt est mono-intent,
            retourne une liste contenant uniquement le prompt original.
        """
        if not user_prompt or len(user_prompt) < self._min_length * 2:
            return [user_prompt]

        # [#T264] Étape 0 — trois refus AVANT tout découpage. Chacun correspond à
        # un dégât mesuré en production le 10/08 (cf. #T264).
        if len(user_prompt) > LONGUEUR_MAX_DECOUPAGE:
            logger.debug(
                f"[INTENT SPLITTER] Prompt de {len(user_prompt)} caractères "
                f"(> {LONGUEUR_MAX_DECOUPAGE}) : consigne structurée, pas de découpage."
            )
            return [user_prompt]

        if _MARQUEURS_CONTRAINTE.search(user_prompt):
            logger.debug(
                "[INTENT SPLITTER] Contrainte détectée dans le prompt : "
                "découpage refusé (une contrainte n'est pas une tâche)."
            )
            return [user_prompt]

        # Étape 1 : Découper par les marqueurs d'énumération
        candidates = self._split_regex.split(user_prompt)

        # Nettoyer les fragments vides ou trop courts
        candidates = [
            c.strip() for c in candidates
            if c and len(c.strip()) >= self._min_length
        ]

        if len(candidates) <= 1:
            return [user_prompt]

        # [#T264] Étape 1bis — un fragment doit être une PHRASE D'ACTION, pas un
        # verbe orphelin. Mesuré sur le trafic réel : « Recherche et liste les
        # fichiers du projet » produisait le fragment « Recherche », qui n'a
        # aucun objet et part quand même en exécution. Un intent vaut au moins
        # un verbe et son complément.
        if any(len(fragment.split()) < 3 for fragment in candidates):
            logger.debug(
                "[INTENT SPLITTER] Fragment trop court pour être une action autonome : "
                "pas de découpage."
            )
            return [user_prompt]

        # [#T264] Étape 1ter — un fragment qui renvoie à un autre n'est pas
        # autonome : le séparer le prive de son référent, et le Planner le
        # traite alors hors contexte (c'est ainsi qu'un contrat a exigé la
        # création d'un fichier que la consigne interdisait).
        for fragment in candidates:
            if _MARQUEURS_DEPENDANCE.search(fragment):
                logger.debug(
                    f"[INTENT SPLITTER] Fragment non autonome (« {fragment[:40]}… ») : "
                    "le prompt est un tout structuré, pas de découpage."
                )
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

    def _validate_domain_separation(self, fragments: list[str]) -> list[str]:
        """
        Vérifie que les fragments appartiennent à des domaines sémantiques
        différents. Si tous les fragments sont du même domaine, c'est
        probablement un seul intent avec des détails.

        Returns:
            Les fragments validés comme intents distincts.
        """
        fragment_domains: list[tuple[str, str | None]] = []

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

    def _detect_domain(self, text: str) -> str | None:
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
