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
# Mapping : catégorie du Router → catégories du ContextLoader
# ──────────────────────────────────────────────────────────────────
CATEGORY_TO_CONTEXT = {
    "home_assistant": ["home_assistant"],
    "code_generation": ["code_generation"],
    "database":        ["home_assistant"],
    "analysis":        ["analysis"],
    "files":           [],  # Pas de contexte spécifique
    "casual_chat":     [],  # Pas de contexte spécifique
    "sysadmin":        [],  # Pas de contexte spécifique
    "deck_edge":        [],  # Edge AI — routage vers Ollama Deck
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
                "keywords": ["ssh", "deck", "steamdeck", "popydeck", "uptime", "journalctl", "syslog", "système", "linux", "free -m", "df -h", "htop", "diagnostic", "vm", "reboot", "ping", "processus", "pid", "daemon", "service", "charge cpu", "ollama", "benchmark", "inferérence locale", "edge ai", "phi3", "gemma", "llama", "rdna2"],
                "weight": 1.5
            },
            # Catégorie deck_edge : requêtes de tâches légères à router vers Ollama sur le Deck
            # Utilisée principalement en interne par le Planner (pas en interactif direct)
            "deck_edge": {
                "keywords": ["parse logs", "reformater yaml", "résumé court", "deck_ollama", "steam deck ia"],
                "weight": 1.0
            },
        }

    def _tokenize(self, text: str) -> list[str]:
        """Tokenisation basique pour l'analyse syntaxique."""
        return re.findall(r'[a-zA-Z0-9_àâéèêëîïôöùûüç\-]+', text.lower())

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
            elif dominant_category in ["files", "database"]:
                # Court-circuit direct vers l'Executor pour des opérations simples
                target_agent = "executor"
                routing_type = "executor_direct"
                model_tier = "moyen"
                logger.info(f"[ROUTER] Opération simple de {dominant_category} → Court-circuit direct vers l'Executor (Tier moyen).")
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

        return target_agent, routing_type, model_tier, payload_metadata

    async def analyze_request(self, user_prompt: str) -> tuple[TaskPayload, str]:
        """
        Détermine la nature de la requête, enrichit le contexte via le RAG local,
        et choisit le premier agent à invoquer (avec possibilité de court-circuit direct).
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
            llm_result = await self._llm_classify(user_prompt)
            if llm_result:
                llm_category = llm_result.get("category", "")
                llm_confidence = llm_result.get("confidence", 0.0)
                llm_complexity = llm_result.get("complexity", "complex")

                if llm_confidence >= MIN_LLM_CONFIDENCE and llm_category in self.categories:
                    dominant_category = llm_category
                    # Le LLM surcharge la détection heuristique de complexité
                    if llm_complexity == "simple":
                        is_complex = False
                    logger.info(
                        f"[ROUTER] [LLM SLOW PATH] Classification sémantique : "
                        f"{llm_category} (confiance: {llm_confidence:.0%})"
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
        structured_context = ""
        if context_categories and dominant_category != "casual_chat":
            # Rechargement si des fichiers ont changé
            self.context_loader.reload_if_stale()

            structured_context = self.context_loader.get_context_for_categories(
                context_categories, max_chars=8000  # Limiter pour ne pas exploser le prompt
            )
            if structured_context:
                logger.info(f"[ROUTER] Contexte 3-Layers chargé : {len(structured_context):,} chars")

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
            )
            logger.info(f"[ROUTER] Métriques enregistrées (latence: {_routing_latency_ms:.1f}ms)")
        except Exception as _rm_err:
            logger.warning(f"[ROUTER] Erreur de logging métriques : {_rm_err}")

        return payload, target_agent

    async def _llm_classify(self, user_prompt: str) -> dict | None:
        """
        Slow path : appel au LLM léger pour classification sémantique.
        
        Appelé uniquement quand le scoring par mots-clés est insuffisant (max_score < 0.05).
        Utilise le tier "leger" (local LM Studio → Gemini Flash Free → ...) pour minimiser
        la latence (~200ms) et le coût (~0.001$/requête).
        
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

            if hasattr(provider, "generate_structured_async"):
                result = await provider.generate_structured_async(
                    system_prompt="Tu es un classificateur d'intentions. Réponds uniquement en JSON.",
                    user_prompt=classification_prompt,
                    schema={},  # Schéma libre, on parse manuellement
                    temperature=0.0,
                )
            else:
                result = await asyncio.to_thread(
                    provider.generate_structured,
                    system_prompt="Tu es un classificateur d'intentions. Réponds uniquement en JSON.",
                    user_prompt=classification_prompt,
                    schema={},
                    temperature=0.0,
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
