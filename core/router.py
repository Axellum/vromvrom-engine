"""
core/router.py — Routeur hybride multi-couches du tab5-engine.

Analyse l'intention utilisateur et détermine le premier agent à invoquer.
Le pipeline de routage comporte 4 niveaux (du plus rapide au plus lent) :

1. Fast-path mots-clés (0ms) : scoring déterministe par catégorie
2. ML Router sklearn (V10.1, ~0ms) : LogisticRegression sur embeddings si entraîné
3. Elo meta-scoring (V6/V11) : ajustement temps réel par scoring Elo
4. LLM slow-path (~200ms) : classification sémantique via LLM léger (fallback)

Historique :
- V5.5  : Routeur hybride fast/slow path + IntentSplitter multi-intent
- V7    : Google Search Grounding (détection données fraîches)
- V9    : Commandes HA externalisées (ha_commands.json), filtrage RAG par catégorie
- V10.1 : ML Router sklearn pré-classificateur (économise le slow-path LLM)
- V11   : Elo scoring par domaine, source-aware routing
- PERF-1: Rendu asynchrone pour éviter le blocage de l'event loop FastAPI
"""
import asyncio
import json
import logging
import os
import re
import time

# Scoring Elo pour routage prédictif des LLM
from core.agent_trace import agent_courant
from core.elo_scorer import get_ranked_models as elo_get_ranked
from core.intent_splitter import IntentSplitter
from core.router_context_compressor import RouterContextCompressor
from core.routing_metrics import record_routing_decision
from core.state import TaskPayload
from memory.context_loader import ContextLoader

logger = logging.getLogger(__name__)

# Seuil minimum de score de mots-clés pour éviter le slow path LLM
# En dessous de ce seuil, le Router appelle le LLM pour classification sémantique
MIN_KEYWORD_SCORE_THRESHOLD = 0.05

# Seuil de confiance minimum du LLM-classifier pour une décision directe
# En dessous, la requête est routée vers le Planner par défaut
MIN_LLM_CONFIDENCE = 0.7

# ──────────────────────────────────────────────────────────────────
# [#T273] Garde HITL sur les court-circuits vers l'Executor
# ──────────────────────────────────────────────────────────────────
# L'approbation humaine (#T267) est posée dans `_check_hitl_before_dag`, donc
# UNIQUEMENT sur le chemin DAG. Or le Router court-circuite les catégories
# `files`/`database`/`sysadmin` directement vers l'Executor, qui écrit des
# fichiers et lance des commandes shell — sans jamais croiser ce point de
# contrôle. Mesuré en production le 11/08 : l'objectif « écris le fichier
# /tmp/preuve_t267.txt … » a produit `run_terminal_command : echo … > …` avec
# ZÉRO demande d'approbation, session en `success`, branche fusionnée.
#
# Correctif : une requête qui demande une ACTION (écrire, supprimer, exécuter…)
# ne peut plus emprunter le raccourci. Elle repart vers le Planner, donc vers le
# DAG, donc vers le point de contrôle qui sait désormais suspendre proprement.
# On perd la latence du raccourci sur ces requêtes-là ; c'est le prix d'un
# contrôle qui ne se contourne pas.
#
# Les requêtes de LECTURE (« lis », « affiche », « cherche ») gardent le
# raccourci : elles ne peuvent rien casser, et ce sont les plus fréquentes.
_ACTIONS_A_RISQUE = (
    # Écriture / modification / suppression de fichiers
    "ecris", "ecrire", "ecrit", "cree", "creer", "creation",
    "modifie", "modifier", "edite", "editer", "remplace", "remplacer",
    "supprime", "supprimer", "efface", "effacer", "vide", "vider",
    "renomme", "renommer", "deplace", "deplacer", "copie", "copier",
    "ajoute", "ajouter", "insere", "inserer", "sauvegarde", "sauvegarder",
    "enregistre", "enregistrer", "genere", "generer", "corrige", "corriger",
    "write", "create", "delete", "remove", "rename", "move", "append",
    # Exécution de commandes / administration
    "execute", "executer", "lance", "lancer", "demarre", "demarrer",
    "redemarre", "redemarrer", "arrete", "arreter", "installe", "installer",
    "desinstalle", "desinstaller", "configure", "configurer", "deploie",
    "deployer", "run ", "exec", "chmod", "chown", "mkdir", "rm ", "rmdir",
    "kill", "sudo", "apt ", "pip install", "git commit", "git push",
)

# Kill-switch : `MOTEUR_ROUTER_GARDE_HITL=0` restaure le raccourci d'avant
# #T273 (donc l'exécution d'actions sans approbation possible).
def garde_hitl_routage_active() -> bool:
    """[#T273] La garde qui empêche de contourner l'approbation est-elle active ?"""
    return os.environ.get("MOTEUR_ROUTER_GARDE_HITL", "1").strip().lower() not in ("0", "false", "off")


# ──────────────────────────────────────────────────────────────────
# [#T173] Contexte projet LÉGER sur le chat rapide (casual_chat)
# ──────────────────────────────────────────────────────────────────
# Le chat rapide est le chemin le plus utilisé (865 échanges vocaux mesurés sur
# vocal_audit_log) mais partait SANS aucun contexte projet, là où les autres
# catégories en reçoivent. On lui injecte le strict nécessaire pour situer les
# projets d'Axel : le profil utilisateur (catégorie "core" du ContextLoader,
# déjà chargée en mémoire — §1 de rules_global.md, 3 445 chars mesurés le 11/08).
# La latence est LE critère de ce chemin (p50 1,6 s / p90 3,6 s) : un contexte
# trop gros la dégraderait, et le remède serait pire que le mal. Le plafond
# couvre donc le profil complet + une marge, pas la documentation entière.
CASUAL_CHAT_CONTEXT_CATEGORIES = ["core"]  # Profil utilisateur (rules_global.md)
CASUAL_CHAT_MAX_CONTEXT_CHARS = 4000  # §1 profil (3 445 chars) + marge ≈ 1 100 tokens

# Plafond des autres catégories (valeur inchangée, nommée pour ne plus être magique)
CONTEXT_LOADER_MAX_CHARS = 8000

# Kill-switch : `MOTEUR_CHAT_CONTEXTE_PROJET=0` restaure le comportement d'avant
# #T173 (aucun contexte projet injecté sur casual_chat, à l'identique).
def contexte_projet_chat_rapide_active() -> bool:
    """[#T173] Le chat rapide reçoit-il son contexte projet léger ?"""
    return os.environ.get("MOTEUR_CHAT_CONTEXTE_PROJET", "1").strip().lower() not in ("0", "false", "off")


def _sans_accents(texte: str) -> str:
    """Normalise les accents — le STT vocal et les clients tapent souvent sans."""
    for source, cible in (
        ("éèêë", "e"), ("àâä", "a"), ("îï", "i"), ("ôö", "o"), ("ûüù", "u"), ("ç", "c"),
    ):
        for c in source:
            texte = texte.replace(c, cible)
    return texte


def requete_demande_une_action(user_prompt: str) -> bool:
    """
    [#T273] La requête demande-t-elle d'AGIR (écrire, supprimer, exécuter) ?

    Volontairement large : un faux positif envoie la requête au Planner (plus
    lent, mais correct et approuvable), tandis qu'un faux négatif laisse une
    action s'exécuter sans supervision. L'asymétrie des conséquences dicte le
    réglage.
    """
    texte = _sans_accents((user_prompt or "").lower())
    return any(mot in texte for mot in _ACTIONS_A_RISQUE)

# ──────────────────────────────────────────────────────────────────
# Mapping : catégorie du Router → catégories du ContextLoader
# ──────────────────────────────────────────────────────────────────
CATEGORY_TO_CONTEXT = {
    "home_assistant": ["home_assistant"],
    "code_generation": ["code_generation"],
    "database":        ["home_assistant"],
    "analysis":        ["analysis"],
    "files":           [],  # Pas de contexte spécifique
    "casual_chat":     CASUAL_CHAT_CONTEXT_CATEGORIES,  # [#T173] profil utilisateur léger
    "sysadmin":        [],  # Pas de contexte spécifique
    "deck_edge":        [],  # Edge AI — routage vers Ollama Deck
    # [#T319] Aucun contexte projet à injecter : ces trois familles interrogent des
    # services externes (Google Workspace, APIs de facturation), pas le dépôt. Leur
    # absence de cette table ferait retomber `context_categories` sur le défaut ;
    # les déclarer vides est explicite et évite d'alourdir le prompt pour rien.
    "calendar":        [],
    "email":           [],
    "accounts":        [],
}


class Router:
    """
    Analyse l'intention utilisateur brute, extrait le contexte via RAG et
    les fichiers 3-Layers (ContextLoader), détermine la complexité et
    formate le premier TaskPayload pour l'agent adéquat.
    
    Routeur hybride (A2 Audit) :
    - Fast path (0ms) : mots-clés déterministes + courts-circuits HA
    - Slow path (~200ms) : LLM-classifier si aucun match déterministe
    """
    def __init__(self, default_agent: str = "planner", rag_engine=None,
                 context_loader: ContextLoader = None, llm_gateway=None, config: dict = None):
        self.default_agent = default_agent
        self.rag_engine = rag_engine
        # Gateway LLM pour le slow path de classification sémantique
        self.llm_gateway = llm_gateway
        self.config = config or {}

        # Initialisation du ContextLoader (charge les fichiers contexte_ia/)
        if context_loader:
            self.context_loader = context_loader
        else:
            self.context_loader = ContextLoader()
        self.context_loader.load_all()

        # Initialisation de la mémoire épisodique et sémantique
        from memory.episodes import EpisodeStore
        from memory.facts import FactStore
        self.episode_store = EpisodeStore()
        self.fact_store = FactStore()

        # [v12.3.0] Compresseur de contexte multi-sources
        self.context_compressor = RouterContextCompressor(llm_gateway=self.llm_gateway)

        # IntentSplitter pour décomposer les requêtes multi-intent
        self._intent_splitter = IntentSplitter()

        # Flag pour activer/désactiver le scoring Elo.
        # [P1-2.1] Piloté par la config (défaut True) au lieu d'être codé en dur,
        # pour rester cohérent avec la relecture `getattr(self, '_elo_enabled', ...)`.
        self._elo_enabled = bool(self.config.get("elo_enabled", True))

        # Chargement de la table de commandes HA déterministes
        # Externalisé dans ha_commands.json au lieu d'être codé en dur
        self._ha_commands = []
        ha_cmds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ha_commands.json",
        )
        if os.path.exists(ha_cmds_path):
            try:
                with open(ha_cmds_path, encoding="utf-8") as f:
                    ha_data = json.load(f)
                    self._ha_commands = ha_data.get("commands", [])
                logger.info(f"[ROUTER] {len(self._ha_commands)} commandes HA déterministes chargées depuis ha_commands.json")
            except Exception as e:
                logger.warning(f"[ROUTER] Erreur chargement ha_commands.json : {e}")

        # Dictionnaire des catégories et de leurs mots-clés associés
        self.categories = {
            "casual_chat": {
                "keywords": ["bonjour", "salut", "hello", "hi", "merci", "thanks", "de rien", "ok", "cool", "parfait", "ça va", "comment vas-tu"],
                "weight": 1.0
            },
            "home_assistant": {
                "keywords": ["lumière", "lumiere", "clim", "climatisation", "volet", "switch", "sensor", "esphome", "tab5", "micro_wake_word", "ha", "automation", "température", "temperature", "humidité", "humidite", "yeelight", "sonnette", "bouton", "dalle", "tactile", "allume", "allumer", "eteins", "eteindre", "ouvre", "ouvrir", "ferme", "fermer"],
                "weight": 1.5
            },
            "code_generation": {
                "keywords": ["code", "python", "javascript", "c++", "cpp", "fonction", "classe", "algorithme", "bug", "exception", "asyncio", "coroutine", "compilation", "script", "refactoring", "implémenter"],
                "weight": 1.2
            },
            "database": {
                "keywords": ["sqlite", "bdd", "base de données", "table", "sql", "requête", "select", "insert", "db", "recorder", "recorder.db"],
                "weight": 1.4
            },
            "files": {
                "keywords": ["fichier", "dossier", "directory", "lire", "écrire", "créer", "delete", "remove", "copier", "mv", "cp", "rm", "path", "chemin"],
                "weight": 1.0
            },
            "analysis": {
                "keywords": ["analyse", "audit", "rapport", "comparaison", "benchmark", "performance", "optimisation", "token", "coût", "facture", "pricing", "tarif"],
                "weight": 1.2
            },
            "sysadmin": {
                "keywords": ["ssh", "deck", "steamdeck", "remote-host", "uptime", "journalctl", "syslog", "système", "linux", "free -m", "df -h", "htop", "diagnostic", "vm", "reboot", "ping", "processus", "pid", "daemon", "service", "charge cpu", "ollama", "benchmark", "inferérence locale", "edge ai", "phi3", "gemma", "llama", "rdna2"],
                "weight": 1.5
            },
            # Catégorie deck_edge : requêtes de tâches légères à router vers Ollama sur le Deck
            # Utilisée principalement en interne par le Planner (pas en interactif direct)
            "deck_edge": {
                "keywords": ["parse logs", "reformater yaml", "résumé court", "deck_ollama", "steam deck ia"],
                "weight": 1.0
            },
            # [#T319] Les trois familles que le classifieur LLM identifiait déjà à 95 %
            # et que le routeur jetait faute de catégorie correspondante (cf. docstring
            # de `_normaliser_categorie_llm`). Mots-clés choisis pour ne recouvrir aucune
            # catégorie existante : « coût », « facture », « tarif » restent à `analysis`.
            "calendar": {
                "keywords": ["agenda", "calendrier", "rendez-vous", "rdv", "planning",
                             "prévu", "prevu", "réunion", "reunion", "disponibilité",
                             "disponibilite", "créneau", "creneau", "événement", "evenement"],
                "weight": 1.4
            },
            "email": {
                "keywords": ["mail", "mails", "email", "emails", "courriel", "courriels",
                             "gmail", "boîte", "boite", "messagerie", "expéditeur",
                             "expediteur", "destinataire", "pièce jointe", "piece jointe"],
                "weight": 1.4
            },
            "accounts": {
                "keywords": ["crédit", "credit", "crédits", "credits", "solde", "quota",
                             "quotas", "abonnement", "consommation", "forfait",
                             "plafond", "clé api", "cle api"],
                "weight": 1.4
            },
        }

    def _tokenize(self, text: str) -> list[str]:
        """Tokenisation basique pour l'analyse syntaxique."""
        return re.findall(r'[a-zA-Z0-9_àâéèêëîïôöùûüç\-]+', text.lower())

    def _normaliser_categorie_llm(self, categorie: str) -> str:
        """
        [#T319] Ramène le libellé libre du classifieur sur le vocabulaire fermé du routeur.

        Le classifieur reçoit la liste des catégories mais écrit ce qu'il veut. Mesuré le
        12/08/2026 en l'interrogeant directement sur les demandes de la campagne :

            « il me reste combien de crédit… »  → account_balance      (confiance 95 %)
            « qu'est-ce que j'ai de prévu… »    → calendar_query       (confiance 95 %)
            « résume-moi les mails… »           → email_summarization  (confiance 95 %)

        Les trois classifications sont **justes**. Toutes les trois étaient jetées par le
        test `llm_category in self.categories`, et la requête repartait sans catégorie —
        donc vers le Planner, donc sur le chemin DAG, donc dans le mur HITL.

        La normalisation est **conservatrice** : correspondance exacte d'abord, puis une
        table d'alias explicite, puis un préfixe `<canonique>_*`. Rien d'approximatif :
        une catégorie fausse envoie la requête au mauvais tier, ce qui coûte plus cher
        qu'une catégorie absente (#T294).
        """
        if not categorie:
            return ""
        brut = categorie.strip().lower().replace("-", "_").replace(" ", "_")

        # 1) Déjà dans le vocabulaire : rien à faire.
        if brut in self.categories:
            return brut

        # 2) Alias explicites — libellés réellement observés ou proches immédiats.
        alias = {
            "calendar_query": "calendar", "calendar_event": "calendar",
            "calendar_lookup": "calendar", "agenda": "calendar", "schedule": "calendar",
            "email_summarization": "email", "email_query": "email",
            "email_summary": "email", "mail": "email", "gmail": "email",
            "account_balance": "accounts", "account_query": "accounts",
            "billing": "accounts", "quota": "accounts", "credits": "accounts",
            "file_management": "files", "filesystem": "files",
            "code": "code_generation", "coding": "code_generation",
            "system": "sysadmin", "system_diagnostic": "sysadmin",
            "smalltalk": "casual_chat", "greeting": "casual_chat",
        }
        if brut in alias:
            return alias[brut]

        # 3) Préfixe canonique : « calendar_something » → « calendar ». On exige un
        #    préfixe suivi de « _ » pour ne pas confondre deux catégories voisines.
        for canonique in self.categories:
            if brut.startswith(f"{canonique}_"):
                return canonique

        # 4) Inconnu : on rend le libellé tel quel, l'appelant le rejettera et le
        #    journalisera comme candidat à l'ajout.
        return brut

    # ──────────────────────────────────────────────────────────────────
    # [PHASE 2 - D4] Couches de classification extraites (pures, testables)
    # ──────────────────────────────────────────────────────────────────
    def _score_categories(self, prompt_words: list[str], prompt_word_set: set) -> tuple[dict, str | None, float]:
        """
        Couche 1 — scoring déterministe par mots-clés.

        Retourne (scores_par_catégorie, catégorie_dominante, score_max).
        Méthode pure : ne dépend que de self.categories et des entrées.
        """
        scores = {}
        for category, cat_data in self.categories.items():
            cat_score = 0.0
            for kw in cat_data["keywords"]:
                if kw in prompt_word_set:
                    cat_score += cat_data["weight"]
            # Normalisation par la longueur des mots du prompt
            scores[category] = cat_score / max(1, len(prompt_words))

        dominant_category = None
        max_score = 0.0
        for category, score in scores.items():
            if score > max_score:
                max_score = score
                dominant_category = category

        logger.info(
            f"[ROUTER] Scores d'intention : "
            f"{ {c: round(s, 4) for c, s in scores.items() if s > 0} } (Dominant: {dominant_category})"
        )
        return scores, dominant_category, max_score

    def _detect_complexity(self, user_prompt: str) -> bool:
        """Heuristique de complexité (longueur + mots-clés). Surchargeable par le LLM."""
        is_complex = len(user_prompt) > 220
        complexity_keywords = [
            "refactor", "architecture", "audit", "self-healing", "parallèle",
            "multithreading", "race condition", "circuit breaker", "moteur",
            "dag", "optimiser", "migration",
        ]
        if any(kw in user_prompt.lower() for kw in complexity_keywords):
            is_complex = True
        return is_complex

    def _detect_grounding(self, user_prompt: str) -> bool:
        """Détecte un besoin de données fraîches → Google Search Grounding."""
        grounding_keywords = [
            "météo", "meteo", "temps qu'il fait", "température actuelle", "prévisions",
            "actualité", "dernières nouvelles", "breaking news", "news",
            "dernière version", "mise à jour", "changelog", "release", "update",
            "cours", "bourse", "action", "bitcoin", "crypto",
            "cve", "vulnérabilité", "faille", "sécurité",
            "prix", "tarif actuel", "combien coûte",
            "aujourd'hui", "en ce moment", "récemment", "cette semaine",
            "dernièrement", "tout à l'heure", "ce matin", "ce soir",
        ]
        is_grounding_needed = any(kw in user_prompt.lower() for kw in grounding_keywords)
        if is_grounding_needed:
            logger.info("[ROUTER] 🔍 Données fraîches détectées → Search Grounding sera activé")
        return is_grounding_needed

    def _resolve_target_agent(
        self, user_prompt: str, dominant_category: str | None, is_complex: bool
    ) -> tuple[str, str, str, dict]:
        """
        Couche de résolution — mappe la catégorie dominante vers un agent cible.

        Retourne (target_agent, routing_type, model_tier, payload_metadata).
        Les requêtes complexes (ou sans catégorie) tombent sur l'agent par défaut.
        Inclut les court-circuits déterministes Home Assistant (zero-LLM via ha_commands.json).
        """
        target_agent = self.default_agent
        routing_type = "default"
        model_tier = "automatique"
        payload_metadata: dict = {}

        if not is_complex and dominant_category:
            if dominant_category == "casual_chat":
                # Court-circuit direct vers l'Executor en tier léger pour de la simple discussion
                target_agent = "executor"
                routing_type = "casual_chat"
                model_tier = "leger"
                logger.info("[ROUTER] Requête de conversation simple → Court-circuit direct vers l'Executor (Tier léger).")
            elif dominant_category == "home_assistant":
                # Analyse de commande directe déterministe (Zero-LLM Latency)
                clean_prompt = user_prompt.lower().strip()
                # Retrait des accents basiques
                clean_prompt = clean_prompt.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a").replace("ï", "i").replace("î", "i")
                # Retrait de la ponctuation finale
                clean_prompt = re.sub(r'[?.!,;]+$', '', clean_prompt).strip()

                direct_tool = None
                direct_args: dict = {}

                # Recherche dans la table de commandes externalisée (ha_commands.json)
                ha_cmds = getattr(self, "_ha_commands", [])
                for cmd in ha_cmds:
                    if clean_prompt in cmd.get("phrases", []):
                        direct_tool = "mcp_ha_custom_call_service"
                        direct_args = {
                            "service": cmd["service"],
                            "entity_id": cmd.get("entity_id", ""),
                        }
                        if cmd.get("service_data"):
                            direct_args["service_data"] = cmd["service_data"]
                        break

                if direct_tool:
                    target_agent = "ha_agent"
                    routing_type = "ha_deterministic"
                    model_tier = "leger"
                    # Injection du direct call
                    payload_metadata = {
                        "direct_tool_call": {
                            "name": direct_tool,
                            "arguments": direct_args
                        },
                        "routing_type": routing_type,
                        "model_tier": model_tier
                    }
                    logger.info(f"[ROUTER] Commande domotique déterministe détectée : {clean_prompt} -> Execution directe de {direct_tool}({direct_args}) sans LLM.")
                else:
                    # Commande domotique simple non-déterministe
                    target_agent = "ha_agent"
                    routing_type = "ha_direct"
                    model_tier = "leger"  # Passage sur le tier léger pour réduire la latence
                    payload_metadata = {
                        "is_direct_command": True,
                        "max_turns": 2,
                        "routing_type": routing_type,
                        "model_tier": model_tier
                    }
                    logger.info("[ROUTER] Commande simple Home Assistant → Handoff rapide vers l'HA Agent (Tier léger Gemini Flash, max 2 tours).")
            elif dominant_category in ["files", "database", "sysadmin",
                                       "calendar", "email", "accounts"]:
                # [#T319] `calendar`, `email` et `accounts` rejoignent ce raccourci pour
                # exactement la même raison que `files` : ce sont des demandes de LECTURE
                # servies par un outil unique (get_calendar_events, search_gmail,
                # call_api). Sans catégorie, elles partaient au Planner, donc sur le
                # chemin DAG, donc dans le mur HITL — mesuré le 12/08 sur « il me reste
                # combien de crédit chez Anthropic et Gemini ? », bloquée en attente
                # d'approbation sans qu'aucun outil n'ait tourné.
                #
                # [#T273] Le raccourci vers l'Executor saute le point d'approbation
                # HITL (posé sur le chemin DAG). On ne l'autorise donc plus quand la
                # requête demande d'AGIR : elle repart au Planner pour devenir un
                # plan approuvable. La lecture, elle, garde le raccourci. Cette garde
                # s'applique telle quelle aux trois nouvelles catégories : « envoie un
                # mail à… » repart au Planner, « résume-moi mes mails » non.
                if garde_hitl_routage_active() and requete_demande_une_action(user_prompt):
                    # target_agent reste self.default_agent ("planner")
                    routing_type = "planner_pour_approbation"
                    payload_metadata = {
                        "hitl_garde_routage": True,
                        "categorie_dominante": dominant_category,
                    }
                    logger.info(
                        f"[ROUTER] [T273] Action détectée sur '{dominant_category}' → "
                        f"raccourci Executor REFUSÉ, passage par le Planner pour que le plan "
                        f"soit approuvable (le HITL ne couvre que le chemin DAG)."
                    )
                elif dominant_category == "sysadmin":
                    # SysAdminAgent supprimé — routage vers Executor avec contexte sysadmin
                    target_agent = "executor"
                    routing_type = "sysadmin_direct"
                    model_tier = "moyen"
                    payload_metadata = {
                        "is_direct_command": True,
                        "routing_type": routing_type,
                        "model_tier": model_tier
                    }
                    logger.info("[ROUTER] Commande SysAdmin Linux détectée → Handoff vers l'Executor (outils terminal).")
                else:
                    # Court-circuit direct vers l'Executor pour des opérations simples
                    target_agent = "executor"
                    routing_type = "executor_direct"
                    model_tier = "moyen"
                    logger.info(f"[ROUTER] Opération simple de {dominant_category} → Court-circuit direct vers l'Executor (Tier moyen).")

        return target_agent, routing_type, model_tier, payload_metadata

    async def analyze_request(self, user_prompt: str, session_id: str = "") -> tuple[TaskPayload, str]:
        """
        Détermine la nature de la requête, enrichit le contexte via le RAG local,
        et choisit le premier agent à invoquer (avec possibilité de court-circuit direct).

        [#T296] `session_id` n'est pas utilisé par le routage lui-même : il sert
        uniquement à horodater la décision dans `routing_decisions`, où la colonne
        était vide sur 68/68 lignes. Sans lui, une décision de routage ne pouvait
        être rapprochée d'aucune consommation réelle. Paramètre explicite plutôt
        que ContextVar : les six appelants connaissent tous leur session, la
        magie n'apporterait rien ici.
        """
        relevant_context = "Nouvelle requête utilisateur (Initiale)."
        up_prompt = user_prompt.upper()
        # Début de la mesure de latence du routage
        _routing_start = time.perf_counter()

        # Détection multi-intent : si la requête contient plusieurs intentions,
        # on signale les sous-intents dans les metadata pour traitement parallèle
        intent_splitter = getattr(self, "_intent_splitter", None)
        if intent_splitter is None:
            intent_splitter = IntentSplitter()
            self._intent_splitter = intent_splitter
        sub_intents = intent_splitter.split(user_prompt)
        _is_multi_intent = len(sub_intents) > 1

        # 1. Interception automatique de la routine de Fin de Session (Directive Critique)
        if "FIN DE SESSION" in up_prompt or "SAUVEGARDE" in up_prompt or "ENREGISTRE" in up_prompt:
            rules_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "contexte_ia", "01_Core", "rules_global.md"))
            rules_text = ""
            if os.path.exists(rules_path):
                try:
                    with open(rules_path, encoding="utf-8") as f:
                        rules_text = f.read()
                except Exception as _e:
                    from core.error_reporter import report_swallowed
                    report_swallowed("router.read_rules_global", _e, level="warning")

            relevant_context += (
                "\n\n*** DIRECTIVE CRITIQUE DU tab5-engine (FIN DE SESSION) ***\n"
                "L'utilisateur a déclenché une routine de sauvegarde systémique.\n"
                "Le planificateur DOIT OBLIGATOIREMENT générer un plan strict pour exécuter les 7 étapes de la section "
                "'Règle Absolue Tier 1 : FIN DE SESSION / SAUVEGARDE' décrite dans les règles globales ci-dessous.\n"
                "Important: Tu es le moteur local 'moteur_agents', tu peux donc ignorer la sauvegarde des artefacts IDE (Règle 6).\n"
                "Voici le contenu du fichier de règles globales pour te guider :\n\n"
                f"{rules_text}"
            )

        # [#T307] Synchronisation Markdown de fin de session, en tâche de fond :
        # le routeur ne doit JAMAIS attendre l'écriture (échec silencieux, écriture
        # en AJOUT uniquement dans contexte_ia/historique/ — jamais dans 04_Projets).
        # Déclenchée uniquement quand la session est connue : sans session_id, on ne
        # résume pas « la session la plus récente » au hasard.
        if session_id:
            try:
                from tools.sync_db_to_markdown import sync_db_to_markdown

                if not hasattr(self, "_sync_tasks"):
                    self._sync_tasks = []
                _tache = asyncio.create_task(
                    asyncio.to_thread(sync_db_to_markdown, session_id=session_id)
                )
                self._sync_tasks.append(_tache)
                logger.info("[ROUTER] Sync Markdown de fin de session déclenchée en arrière-plan (#T307).")
            except Exception as _sync_err:
                logger.debug(f"[ROUTER] Sync Markdown non déclenchée (non bloquant) : {_sync_err}")

        # 2. Classification cognitive par scoring de mots-clés normalisé
        # (DÉPLACÉ AVANT le RAG pour permettre le filtrage par catégories)
        prompt_words = self._tokenize(user_prompt)
        prompt_word_set = set(prompt_words)

        # Couche 1 — scoring déterministe par mots-clés.
        scores, dominant_category, max_score = self._score_categories(prompt_words, prompt_word_set)

        # Slow path LLM : si aucune catégorie n'est suffisamment dominante,
        # appeler le LLM-classifier pour une classification sémantique
        # La détection de complexité est faite AVANT le slow path
        # pour que le LLM puisse la surcharger si sa confiance est >= 70%
        is_complex = self._detect_complexity(user_prompt)

        # ML Router : pré-classificateur sklearn avant le slow-path LLM
        # Économise ~200ms si le modèle est entraîné (confidence >= 0.75)
        _ml_predicted = None
        try:
            from core.ml_router import get_ml_router
            _ml_router = get_ml_router()
            if _ml_router.is_trained and max_score < MIN_KEYWORD_SCORE_THRESHOLD:
                _ml_type, _ml_conf = _ml_router.predict(user_prompt)
                if _ml_type and _ml_type in self.categories:
                    dominant_category = _ml_type
                    _ml_predicted = _ml_type
                    logger.info(
                        f"[ROUTER] [ML FAST PATH] ⚡ {_ml_type} "
                        f"(conf={_ml_conf:.3f}, ~0ms vs LLM ~200ms)"
                    )
        except Exception as _ml_err:
            logger.debug(f"[ROUTER] MLRouter non disponible : {_ml_err}")

        if max_score < MIN_KEYWORD_SCORE_THRESHOLD and self.llm_gateway and _ml_predicted is None:
            llm_result = await self._llm_classify(user_prompt, session_id=session_id)
            if llm_result:
                llm_category = llm_result.get("category", "")
                llm_confidence = llm_result.get("confidence", 0.0)
                llm_complexity = llm_result.get("complexity", "complex")

                # [#T319] Le classifieur écrit un libellé LIBRE ; le routeur travaille sur
                # un vocabulaire FERMÉ. Sans normalisation, une classification correcte à
                # 95 % est jetée au seul motif qu'elle ne s'appelle pas pareil.
                llm_category = self._normaliser_categorie_llm(llm_category)

                if llm_confidence >= MIN_LLM_CONFIDENCE and llm_category in self.categories:
                    dominant_category = llm_category
                    # Le LLM surcharge la détection heuristique de complexité
                    if llm_complexity == "simple":
                        is_complex = False
                    logger.info(
                        f"[ROUTER] [LLM SLOW PATH] Classification sémantique : "
                        f"{llm_category} (confiance: {llm_confidence:.0%})"
                    )
                elif llm_confidence >= MIN_LLM_CONFIDENCE:
                    # [#T319] Confiance suffisante mais libellé hors vocabulaire même après
                    # normalisation : on le journalise tel quel. C'est le signal qui dit
                    # quelle catégorie il manque — c'est comme ça que #T319 a été trouvée.
                    logger.info(
                        f"[ROUTER] [LLM SLOW PATH] Catégorie '{llm_result.get('category', '')}' "
                        f"hors vocabulaire du routeur (confiance {llm_confidence:.0%}) → "
                        f"défaut vers Planner. Candidate à l'ajout dans self.categories."
                    )
                else:
                    logger.info(
                        f"[ROUTER] [LLM SLOW PATH] Confiance insuffisante "
                        f"({llm_confidence:.0%} < {MIN_LLM_CONFIDENCE:.0%}). Défaut vers Planner."
                    )

        # 2bis. Détection de mots-clés ESPHome/LVGL/Tab5 pour enrichissement spécifique
        esphome_keywords = ["esphome", "tab5", "lvgl", "esp32", "i2c", "spi", "ota", "yaml", "on_boot", "lambda", "c++", "cpp", "mipi", "gpio"]
        is_esphome = any(kw in user_prompt.lower() for kw in esphome_keywords)

        # Détection de mots-clés moteur/agents
        moteur_keywords = ["moteur", "engine", "planner", "executor", "gateway", "llm", "token", "pricing", "agent", "tier", "routage"]
        is_moteur = any(kw in user_prompt.lower() for kw in moteur_keywords)

        # Détection de besoin de données fraîches → Google Search Grounding
        # Si détecté, le GeminiNativeProvider payant sera utilisé avec
        # tools: [{google_search: {}}] pour ancrer la réponse dans des données temps réel.
        is_grounding_needed = self._detect_grounding(user_prompt)

        # Pré-construire context_categories AVANT l'appel RAG pour le filtrage
        context_categories = []
        if dominant_category and dominant_category in CATEGORY_TO_CONTEXT:
            # [#T173] Kill-switch : `MOTEUR_CHAT_CONTEXTE_PROJET=0` → aucun contexte
            # projet sur le chat rapide (comportement d'avant, strictement identique).
            if dominant_category == "casual_chat" and not contexte_projet_chat_rapide_active():
                logger.info(
                    "[ROUTER] [T173] Kill-switch MOTEUR_CHAT_CONTEXTE_PROJET=0 : "
                    "aucun contexte projet sur le chat rapide."
                )
            else:
                context_categories.extend(CATEGORY_TO_CONTEXT[dominant_category])
        if is_esphome:
            context_categories.append("esphome")
        if is_moteur:
            context_categories.append("moteur")

        # 3. RAG local : Récupération des sections de contexte technique pertinentes
        # Le RAG reçoit les catégories détectées pour filtrer le bruit vectoriel
        rag_result = ""
        if self.rag_engine and dominant_category != "casual_chat":
            try:
                if hasattr(self.rag_engine, "query_async"):
                    rag_result = await self.rag_engine.query_async(
                        user_prompt, top_n=3,
                        allowed_categories=context_categories if context_categories else None
                    )
                else:
                    rag_result = await asyncio.to_thread(
                        self.rag_engine.query,
                        user_prompt, top_n=3,
                        allowed_categories=context_categories if context_categories else None
                    )
            except Exception as rag_err:
                logger.warning(f"[ROUTER] Erreur RAG local : {rag_err}")

        # is_complex déjà calculé plus haut (avant le slow path LLM)
        # pour permettre la surcharge par le classificateur LLM

        # 5. Sélection de l'agent (couche de résolution extraite — D4).
        target_agent, routing_type, model_tier, payload_metadata = self._resolve_target_agent(
            user_prompt, dominant_category, is_complex
        )

        # 5bis. Collecte des contextes pour compression (v12.3.0)
        # [#T173] Le chat rapide est désormais couvert : il reçoit un contexte
        # projet LÉGER, plafonné explicitement pour ne pas dégrader sa latence.
        structured_context = ""
        if context_categories:
            # Rechargement si des fichiers ont changé
            self.context_loader.reload_if_stale()

            max_chars = (
                CASUAL_CHAT_MAX_CONTEXT_CHARS
                if dominant_category == "casual_chat"
                else CONTEXT_LOADER_MAX_CHARS  # Limiter pour ne pas exploser le prompt
            )
            structured_context = self.context_loader.get_context_for_categories(
                context_categories, max_chars=max_chars
            )
            if structured_context:
                # Garantie en dur : le plafond ne doit JAMAIS être dépassé, même si
                # le loader déborde de quelques caractères (marqueur de troncature).
                if len(structured_context) > max_chars:
                    structured_context = structured_context[:max_chars]
                logger.info(
                    f"[ROUTER] Contexte 3-Layers chargé : {len(structured_context):,} chars "
                    f"(plafond {max_chars:,})"
                )

        # Collecte mémoire épisodique
        episodic_context = ""
        try:
            if hasattr(self.episode_store, "query_relevant_episodes_async"):
                episodic_context = await self.episode_store.query_relevant_episodes_async(user_prompt, max_results=3)
            else:
                episodic_context = await asyncio.to_thread(
                    self.episode_store.query_relevant_episodes,
                    user_prompt, max_results=3
                )
            if episodic_context:
                logger.info(f"[ROUTER] Mémoire épisodique chargée : {len(episodic_context):,} chars")
        except Exception as ep_err:
            logger.warning(f"[ROUTER] Erreur mémoire épisodique : {ep_err}")

        # Collecte mémoire sémantique
        facts_context = ""
        try:
            # Utiliser les mots-clés détectés + la catégorie dominante
            fact_keywords = list(prompt_word_set)[:10]
            if dominant_category:
                fact_keywords.append(dominant_category)

            if hasattr(self.fact_store, "get_facts_for_context_async"):
                facts_context = await self.fact_store.get_facts_for_context_async(fact_keywords, max_chars=1500)
            else:
                facts_context = await asyncio.to_thread(
                    self.fact_store.get_facts_for_context,
                    fact_keywords, max_chars=1500
                )
            if facts_context:
                logger.info(f"[ROUTER] Mémoire sémantique chargée : {len(facts_context):,} chars")
        except Exception as fact_err:
            logger.warning(f"[ROUTER] Erreur mémoire sémantique : {fact_err}")

        # Compression et déduplication sémantique multi-sources
        contexts_to_compress = {
            "facts": facts_context or "",
            "rag": rag_result or "",
            "episodes": episodic_context or "",
            "context_loader": structured_context or ""
        }
        compressor = getattr(self, "context_compressor", None)
        if compressor is None:
            compressor = RouterContextCompressor(llm_gateway=getattr(self, "llm_gateway", None))
            self.context_compressor = compressor

        if hasattr(compressor, "compress_async"):
            compressed_context = await compressor.compress_async(contexts_to_compress)
        else:
            compressed_context = await asyncio.to_thread(compressor.compress, contexts_to_compress)

        if compressed_context:
            relevant_context += "\n\n" + compressed_context

        # Fusionner les métadonnées système et spécifiques au routage
        final_metadata = {
            "routing_type": routing_type,
            "model_tier": model_tier,
            "dominant_category": dominant_category,
            "is_complex": is_complex,
            "context_categories": context_categories,
            "use_search_grounding": is_grounding_needed,  # Active le Google Search Grounding
        }
        # Injecter les sous-intents si multi-intent détecté
        if _is_multi_intent:
            final_metadata["multi_intent"] = True
            final_metadata["sub_intents"] = sub_intents
            logger.info(f"[ROUTER] Multi-intent détecté : {len(sub_intents)} sous-requêtes")

        # Injection du classement Elo pour le domaine détecté
        # Le LLMGateway utilisera cet ordre à la place de l'ordre statique config.json
        if getattr(self, '_elo_enabled', True) and dominant_category:
            try:
                # Récupérer les modèles du tier depuis la config
                _cfg = self.config
                if not _cfg:
                    try:
                        _cfg_path = os.path.join(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config.json",
                        )
                        if os.path.exists(_cfg_path):
                            with open(_cfg_path, encoding='utf-8') as f:
                                _cfg = json.load(f)
                    except Exception:
                        _cfg = {}
                _tier_models = _cfg.get("tiers", {}).get(model_tier, [])
                if _tier_models:
                    elo_ranked = elo_get_ranked(dominant_category, _tier_models)
                    final_metadata["elo_ranked_models"] = [
                        {"model": m, "elo": round(s, 1)} for m, s in elo_ranked
                    ]
                    logger.info(
                        f"[ROUTER] [ELO] Classement pour '{dominant_category}' : "
                        + " > ".join(f"{m}({s:.0f})" for m, s in elo_ranked[:5])
                    )
            except Exception as _elo_err:
                logger.warning(f"[ROUTER] [ELO] Erreur de classement : {_elo_err}")

        final_metadata.update(payload_metadata)

        payload = TaskPayload(
            task_objective=user_prompt,
            relevant_context=relevant_context.strip(),
            metadata=final_metadata
        )

        # Enregistrement de la décision de routage dans SQLite
        try:
            _routing_latency_ms = (time.perf_counter() - _routing_start) * 1000
            # Calcul du tier r?ellement utilis? (planner_model config vs tier local)
            try:
                from core.llm_gateway import load_config as _lc
                _log_tier = _lc().get('planner_model', model_tier) if routing_type == 'default' and is_complex else model_tier
            except Exception:
                _log_tier = model_tier
            record_routing_decision(
                user_prompt=user_prompt,
                dominant_category=dominant_category,
                routing_type=routing_type,
                target_agent=target_agent,
                model_tier=_log_tier,
                is_complex=is_complex,
                fast_path_used=(max_score >= MIN_KEYWORD_SCORE_THRESHOLD),
                llm_classifier_used=(max_score < MIN_KEYWORD_SCORE_THRESHOLD and self.llm_gateway is not None),
                context_categories=context_categories,
                latency_ms=_routing_latency_ms,
                session_id=session_id or "",
            )
            logger.info(f"[ROUTER] Métriques enregistrées (latence: {_routing_latency_ms:.1f}ms)")
        except Exception as _rm_err:
            logger.warning(f"[ROUTER] Erreur de logging métriques : {_rm_err}")

        return payload, target_agent

    async def _llm_classify(self, user_prompt: str, session_id: str = "") -> dict | None:
        """
        Slow path : appel au LLM léger pour classification sémantique.

        Appelé uniquement quand le scoring par mots-clés est insuffisant (max_score < 0.05).
        Utilise le tier "leger" (local LM Studio → Gemini Flash Free → ...) pour minimiser
        la latence (~200ms) et le coût (~0.001$/requête).

        [#T312] L'appel est étiqueté « router » et rattaché à sa session. Il ne
        l'était pas : mesuré le 11/08 sur une campagne de 8 demandes réelles,
        5 lignes de `token_usage` sur 39 (13 %) n'avaient NI `agent_name` NI
        `session_id` — exactement une par demande passée par ce slow-path. C'était
        le dernier consommateur anonyme du chemin interactif, alors qu'il tourne
        sur toute requête dont les mots-clés ne tranchent pas.

        `#T296` avait classé cette absence comme légitime (« routeur,
        classificateur, script direct »), mais `#T308` a depuis étiqueté tous les
        autres consommateurs hors agent pour la même raison : ils consomment
        réellement, et anonymement. Le classifieur est le même cas.

        Args:
            user_prompt: La requête à classer.
            session_id: Session à laquelle imputer l'appel (vide = hors session).

        Returns:
            Dict avec keys: category, complexity, target_agent, confidence
            None si l'appel échoue ou si le gateway n'est pas disponible.
        """
        if not self.llm_gateway:
            return None

        # Catégories disponibles pour le classifier
        categories_list = ", ".join(self.categories.keys())

        classification_prompt = (
            "Tu es un routeur de requêtes pour un système multi-agents domotique.\n"
            "Classifie la requête utilisateur suivante en retournant UNIQUEMENT un JSON strict.\n\n"
            f"Catégories disponibles : {categories_list}\n\n"
            "Agents disponibles :\n"
            "- executor : tâches techniques simples (fichiers, scripts, discussion)\n"
            "- ha_agent : commandes domotiques Home Assistant (lumières, volets, capteurs)\n"
            "- planner : tâches complexes nécessitant un plan multi-étapes\n\n"
            f"Requête utilisateur : \"{user_prompt}\"\n\n"
            "FORMAT DE SORTIE (JSON strict, pas de markdown) :\n"
            "{\n"
            '  "category": "<catégorie>",\n'
            '  "complexity": "simple" | "complex",\n'
            '  "target_agent": "executor" | "ha_agent" | "planner",\n'
            '  "confidence": <float entre 0.0 et 1.0>\n'
            "}"
        )

        try:
            # Récupérer le provider du tier léger pour minimiser la latence
            config = self.config
            if not config:
                # Charger la config depuis le fichier si non fournie
                try:
                    cfg_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.json",
                    )
                    if os.path.exists(cfg_path):
                        with open(cfg_path, encoding='utf-8') as f:
                            config = json.load(f)
                except Exception:
                    config = {}

            tier_name, provider = self.llm_gateway.get_provider_for_tier("leger", config)

            logger.info(f"[ROUTER] [LLM SLOW PATH] Classification via tier leger ({tier_name})...")

            # [#T312] `agent_courant` étiquette la ligne de consommation, `session_id`
            # la rattache à la demande. Le context manager restaure l'étiquette de
            # l'appelant en sortant : le routeur est invoqué depuis des chemins déjà
            # étiquetés, et une étiquette qui déborde ment (#T294).
            with agent_courant("router"):
                if hasattr(provider, "generate_structured_async"):
                    result = await provider.generate_structured_async(
                        system_prompt="Tu es un classificateur d'intentions. Réponds uniquement en JSON.",
                        user_prompt=classification_prompt,
                        schema={},  # Schéma libre, on parse manuellement
                        temperature=0.0,
                        session_id=session_id,
                    )
                else:
                    # `to_thread` (et non `run_in_executor`) : la ContextVar ne
                    # franchit que ce pont-là (#T301).
                    result = await asyncio.to_thread(
                        provider.generate_structured,
                        system_prompt="Tu es un classificateur d'intentions. Réponds uniquement en JSON.",
                        user_prompt=classification_prompt,
                        schema={},
                        temperature=0.0,
                        session_id=session_id,
                    )

            # Validation du résultat
            if isinstance(result, dict) and "category" in result:
                # Normaliser les valeurs
                result["confidence"] = float(result.get("confidence", 0.0))
                result["category"] = result.get("category", "").lower().strip()
                result["complexity"] = result.get("complexity", "complex").lower().strip()
                result["target_agent"] = result.get("target_agent", "planner").lower().strip()

                logger.info(
                    f"[ROUTER] [LLM SLOW PATH] Résultat : "
                    f"cat={result['category']}, "
                    f"complexity={result['complexity']}, "
                    f"agent={result['target_agent']}, "
                    f"confidence={result['confidence']:.0%}"
                )
                return result
            else:
                logger.warning(f"[ROUTER] [LLM SLOW PATH] Réponse LLM invalide : {result}")
                return None

        except Exception as e:
            logger.warning(f"[ROUTER] [LLM SLOW PATH] Erreur lors de la classification LLM : {e}")
            return None
