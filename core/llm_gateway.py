"""
core/llm_gateway.py — Passerelle unifiée d'accès aux ~18 providers LLM.

Instancie et orchestre tous les providers (DeepSeek, Gemini natif/compat,
Claude natif/CLI, Mistral, Cohere, Cerebras, OpenRouter, xAI, MiniMax,
DeepInfra, GitHub Models, LM Studio/Ollama locaux...) derrière une interface
unique. Sélectionne le provider par tier via ProviderScorer (core/provider_scorer.py :
coût + quota + solde + latence live), applique le Circuit Breaker par modèle
(core/llm/circuit_breaker.py) et bascule en cascade sur échec.

Voir aussi : contexte_ia/03_Software/CARTOGRAPHIE_MOTEUR.md §3.2 pour le détail
des relations avec les autres modules de ce groupe (elo_scorer, budget_guard,
key_pool, semantic_cache...).
"""
import copy
import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Import du pool HTTP partagé (connexions TLS persistantes)
try:
    from core.openai_compat_provider import SharedHTTPPool
    _USE_HTTP_POOL = True
except ImportError:
    _USE_HTTP_POOL = False
    SharedHTTPPool = None

# Imports des modules découpés [v12.1.0]
from core.llm.circuit_breaker import CircuitBreaker
from core.llm.providers.base import LLMProvider
from core.llm.providers.deepseek import (
    ClaudeInstructionsWrapper,
    FallbackProvider,
    LMStudioProvider,
    OllamaDeckProvider,
    _make_claude,
)
from core.llm.providers.gemini import GeminiCLIProvider, GeminiProvider

# Import du provider natif Gemini (caching + grounding)
try:
    from core.gemini_native import GeminiNativeProvider
except ImportError:
    GeminiNativeProvider = None


# [#T118] Poids de la latence live (CircuitBreaker.avg_latency_ms) dans le score
# de routage — un tie-breaker en complément du cascade_priority statique, pas un
# remplacement : plafonné pour ne jamais dominer un écart de cascade_priority
# entre providers réellement différents.
LIVE_LATENCY_WEIGHT = 0.3
LIVE_LATENCY_MAX_PENALTY = 3.0


def get_live_latency_penalty(model_name: str) -> float:
    """
    [#T118] Pénalité de score dérivée de la latence live moyenne (EMA) du modèle,
    mesurée par son CircuitBreaker (`core/llm/circuit_breaker.py`). Plafonnée à
    `LIVE_LATENCY_MAX_PENALTY` pour rester un tie-breaker, jamais un remplacement
    du `cascade_priority` statique.

    Retourne 0.0 tant qu'aucun appel réussi n'a encore mesuré de latence.
    """
    try:
        avg_latency_ms = CircuitBreaker.get_or_create(model_name).avg_latency_ms
    except Exception:
        return 0.0
    if not avg_latency_ms:
        return 0.0
    return min((avg_latency_ms / 1000.0) * LIVE_LATENCY_WEIGHT, LIVE_LATENCY_MAX_PENALTY)


class LLMGateway:

    """
    Passerelle unifiée permettant d'appeler le LLM approprié de manière transparente.
    """
    def __init__(self, deepseek_key: str = None, gemini_key: str = None):
        # Clé hardcodée supprimée — warning explicite si absente du .env
        ds_key = deepseek_key or os.environ.get("DEEPSEEK_API_KEY")
        if not ds_key:
            logger.warning(
                "[LLMGateway] DEEPSEEK_API_KEY absente du .env — "
                "les providers DeepSeek seront désactivés. "
                "Ajoutez DEEPSEEK_API_KEY=sk-... dans moteur_agents/.env"
            )
        gem_free_key = gemini_key or os.environ.get("GEMINI_API_KEY")
        # Clé payante de secours (s'il y en a une, sinon on fallback sur la clé gratuite si billing activé)
        gem_paid_key = os.environ.get("GEMINI_PAYANT_API_KEY") or gem_free_key

        mistral_key = os.environ.get("MISTRAL_API_KEY")
        if not mistral_key:
            logger.warning(
                "[LLMGateway] MISTRAL_API_KEY absente du .env — "
                "les providers Mistral seront désactivés. "
                "Ajoutez MISTRAL_API_KEY=... dans moteur_agents/.env"
            )

        cohere_key = os.environ.get("COHERE_API_KEY")
        if not cohere_key:
            logger.info(
                "[LLMGateway] COHERE_API_KEY absente du .env — "
                "les providers Cohere seront désactivés. "
                "Ajoutez COHERE_API_KEY=... dans moteur_agents/.env"
            )

        cerebras_key = os.environ.get("CEREBRAS_API_KEY")
        if not cerebras_key:
            logger.info(
                "[LLMGateway] CEREBRAS_API_KEY absente du .env — "
                "les providers Cerebras seront désactivés. "
                "Ajoutez CEREBRAS_API_KEY=... dans moteur_agents/.env"
            )

        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if not openrouter_key:
            logger.info(
                "[LLMGateway] OPENROUTER_API_KEY absente du .env — "
                "les providers OpenRouter seront désactivés. "
                "Ajoutez OPENROUTER_API_KEY=... dans moteur_agents/.env"
            )

        xai_key = os.environ.get("XAI_API_KEY")
        if not xai_key:
            logger.info(
                "[LLMGateway] XAI_API_KEY absente du .env — "
                "les providers xAI seront désactivés. "
                "Ajoutez XAI_API_KEY=... dans moteur_agents/.env"
            )

        minimax_key = os.environ.get("MINIMAX_API_KEY")
        if not minimax_key:
            logger.info(
                "[LLMGateway] MINIMAX_API_KEY absente du .env — "
                "les providers MiniMax seront désactivés. "
                "Ajoutez MINIMAX_API_KEY=... dans moteur_agents/.env"
            )

        deepinfra_key = os.environ.get("DEEPINFRA_API_KEY")
        if not deepinfra_key:
            logger.info(
                "[LLMGateway] DEEPINFRA_API_KEY absente du .env — "
                "les providers DeepInfra seront désactivés. "
                "Ajoutez DEEPINFRA_API_KEY=... dans moteur_agents/.env"
            )

        github_key = os.environ.get("GITHUB_TOKEN")
        if not github_key:
            logger.info(
                "[LLMGateway] GITHUB_TOKEN absente du .env — "
                "les providers GitHub Models seront désactivés. "
                "Ajoutez GITHUB_TOKEN=github_pat_... dans moteur_agents/.env"
            )

        zhipu_key = os.environ.get("ZHIPU_API_KEY")
        if not zhipu_key:
            logger.info(
                "[LLMGateway] ZHIPU_API_KEY absente du .env — "
                "les providers Zhipu AI seront désactivés. "
                "Ajoutez ZHIPU_API_KEY=... dans moteur_agents/.env"
            )

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_key:
            logger.info(
                "[LLMGateway] ANTHROPIC_API_KEY absente du .env — "
                "l'accès direct à l'API Anthropic sera désactivé (seul le CLI "
                "Claude Pro reste disponible via claude_cli). "
                "Ajoutez ANTHROPIC_API_KEY=sk-ant-... dans moteur_agents/.env"
            )



        # Instanciation des 9 providers OpenAI-compatibles via la factory
        # Remplace ~1500 lignes de classes dupliquées par des appels config-driven
        from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS, OpenAICompatibleProvider

        def _make_compat(provider_id: str, model: str, api_key: str, timeout: tuple = None) -> OpenAICompatibleProvider:
            """Crée un provider OpenAI-compatible à partir du registre centralisé."""
            config = OPENAI_COMPAT_PROVIDERS.get(provider_id, {})
            return OpenAICompatibleProvider(
                provider_name=provider_id.capitalize(),
                base_url=config.get("base_url", ""),
                api_key=api_key,
                model=model,
                extra_headers=config.get("extra_headers"),
                timeout=timeout,
            )

        # --- DeepSeek ---
        _ds = {}
        if ds_key:
            _ds = {
                "deepseek": _make_compat("deepseek", "deepseek-chat", ds_key),
                "deepseek-chat": _make_compat("deepseek", "deepseek-chat", ds_key),
                "deepseek-reasoner": _make_compat("deepseek", "deepseek-reasoner", ds_key),
                "deepseek-r1": _make_compat("deepseek", "deepseek-reasoner", ds_key),
                "deepseek-v4-flash": _make_compat("deepseek", "deepseek-chat", ds_key),
                "deepseek-v4-pro": _make_compat("deepseek", "deepseek-chat", ds_key),
            }

        # --- Mistral ---
        _mistral = {}
        if mistral_key:
            _mistral = {
                "mistral": _make_compat("mistral", "mistral-large-latest", mistral_key),
                "mistral-large-latest": _make_compat("mistral", "mistral-large-latest", mistral_key),
                "codestral-latest": _make_compat("mistral", "codestral-latest", mistral_key),
                "open-mistral-nemo": _make_compat("mistral", "open-mistral-nemo", mistral_key),
            }

        # --- Cohere ---
        _cohere = {}
        if cohere_key:
            _cohere = {
                "cohere": _make_compat("cohere", "command-r-plus-08-2024", cohere_key),
                "command-r-plus-08-2024": _make_compat("cohere", "command-r-plus-08-2024", cohere_key),
                "command-r-08-2024": _make_compat("cohere", "command-r-08-2024", cohere_key),
                "command-r-plus-latest": _make_compat("cohere", "command-r-plus-08-2024", cohere_key),
                "command-r-latest": _make_compat("cohere", "command-r-08-2024", cohere_key),
            }

        # --- Cerebras ---
        _cerebras = {}
        if cerebras_key:
            _cerebras = {
                "cerebras": _make_compat("cerebras", "gpt-oss-120b", cerebras_key),
                "gpt-oss-120b": _make_compat("cerebras", "gpt-oss-120b", cerebras_key),
                "zai-glm-4.7": _make_compat("cerebras", "zai-glm-4.7", cerebras_key),
            }

        # --- OpenRouter ---
        _openrouter = {}
        if openrouter_key:
            _openrouter = {
                "openrouter": _make_compat("openrouter", "meta-llama/llama-3.3-70b-instruct:free", openrouter_key),
                "meta-llama/llama-3.3-70b-instruct:free": _make_compat("openrouter", "meta-llama/llama-3.3-70b-instruct:free", openrouter_key),
                "meta-llama/llama-3.2-3b-instruct:free": _make_compat("openrouter", "meta-llama/llama-3.2-3b-instruct:free", openrouter_key),
            }

        # --- xAI (Grok) ---
        _xai = {}
        if xai_key:
            _xai = {
                "grok": _make_compat("xai", "grok-4.3", xai_key),
                "grok-4.3": _make_compat("xai", "grok-4.3", xai_key),
                "grok-4.20-non-reasoning": _make_compat("xai", "grok-4.20-0309-non-reasoning", xai_key),
                "grok-4.20-reasoning": _make_compat("xai", "grok-4.20-0309-reasoning", xai_key),
                "grok-4.20-multi-agent": _make_compat("xai", "grok-4.20-multi-agent-0309", xai_key),
                "grok-build": _make_compat("xai", "grok-build-0.1", xai_key),
            }

        # --- MiniMax ---
        # Gamme complète validée par test live (2026-06-16)
        # Endpoint officiel : https://api.minimax.io/v1/chat/completions
        # Modèles disponibles sur ce compte (5/7 OK) :
        #   OK  : M3, M2.7, M2.7-highspeed, M2.5, M2.5-highspeed, M2.1, M2.1-highspeed, M2
        #   KO  : M1, Text-01 (érreur 500 "plan not supported" — non inclus dans le plan actuel)
        # Note : Les modèles MiniMax retournent des blocs <think>...</think> qu'il faut filtrer.
        _minimax = {}
        if minimax_key:
            def _make_minimax(model: str) -> OpenAICompatibleProvider:
                """[D3] Factory MiniMax via la sous-classe MiniMaxProvider (plus de monkey-patch)."""
                from core.openai_compat_provider import MiniMaxProvider
                mm_config = OPENAI_COMPAT_PROVIDERS.get("minimax", {})
                return MiniMaxProvider(
                    provider_name="Minimax",
                    base_url=mm_config.get("base_url", ""),
                    api_key=minimax_key,
                    model=model,
                    extra_headers=mm_config.get("extra_headers"),
                )

            _minimax = {
                # Alias principal vers le flagship
                "minimax":                    _make_minimax("MiniMax-M3"),
                # --- Gamme M3 (juin 2026, MoE multimodal, 1M tokens, flagship) ---
                "MiniMax-M3":                 _make_minimax("MiniMax-M3"),
                "minimax-m3":                 _make_minimax("MiniMax-M3"),
                # --- Gamme M2.7 (mars 2026, raisonnement haute performance) ---
                "MiniMax-M2.7":               _make_minimax("MiniMax-M2.7"),
                "minimax-m2.7":               _make_minimax("MiniMax-M2.7"),
                "MiniMax-M2.7-highspeed":     _make_minimax("MiniMax-M2.7-highspeed"),
                "minimax-m2.7-highspeed":     _make_minimax("MiniMax-M2.7-highspeed"),
                # --- Gamme M2.5 (fév 2026, équilibre vitesse/intelligence) ---
                "MiniMax-M2.5":               _make_minimax("MiniMax-M2.5"),
                "minimax-m2.5":               _make_minimax("MiniMax-M2.5"),
                "MiniMax-M2.5-highspeed":     _make_minimax("MiniMax-M2.5-highspeed"),
                "minimax-m2.5-highspeed":     _make_minimax("MiniMax-M2.5-highspeed"),
                # --- Gamme M2.1 (déc 2025, modèle léger applicatif) ---
                "MiniMax-M2.1":               _make_minimax("MiniMax-M2.1"),
                "minimax-m2.1":               _make_minimax("MiniMax-M2.1"),
                "MiniMax-M2.1-highspeed":     _make_minimax("MiniMax-M2.1-highspeed"),
                "minimax-m2.1-highspeed":     _make_minimax("MiniMax-M2.1-highspeed"),
                # --- Gamme M2 (oct 2025, modèle agentique fondateur) ---
                "MiniMax-M2":                 _make_minimax("MiniMax-M2"),
                "minimax-m2":                 _make_minimax("MiniMax-M2"),
                # NOTE : MiniMax-M1 et MiniMax-Text-01 non supportés par le plan actuel
            }

        # --- DeepInfra ---
        _deepinfra = {}
        if deepinfra_key:
            _deepinfra = {
                "deepinfra/llama-3.3-70b-instruct": _make_compat("deepinfra", "meta-llama/Llama-3.3-70B-Instruct", deepinfra_key),
                "deepinfra/qwen-2.5-72b-instruct": _make_compat("deepinfra", "Qwen/Qwen2.5-72B-Instruct", deepinfra_key),
                "deepinfra/deepseek-r1": _make_compat("deepinfra", "deepseek-ai/DeepSeek-R1", deepinfra_key),
            }

        # --- GitHub Models ---
        _github = {}
        if github_key:
            _github = {
                "github": _make_compat("github", "gpt-4o-mini", github_key),
                "github/gpt-4o": _make_compat("github", "gpt-4o", github_key),
                "github/gpt-4o-mini": _make_compat("github", "gpt-4o-mini", github_key),
                "github/meta-llama-3.3-70b-instruct": _make_compat("github", "meta-llama-3.3-70b-instruct", github_key),
            }

        # --- Zhipu AI (Z.ai) ---
        _zhipu = {}
        if zhipu_key:
            _zhipu = {
                "zhipu": _make_compat("zhipu", "glm-5-turbo", zhipu_key),
                "z-ai": _make_compat("zhipu", "glm-5.2", zhipu_key),
                "glm-5.2": _make_compat("zhipu", "glm-5.2", zhipu_key),
                "glm-5.1": _make_compat("zhipu", "glm-5.1", zhipu_key),
                "glm-5": _make_compat("zhipu", "glm-5", zhipu_key),
                "glm-5-turbo": _make_compat("zhipu", "glm-5-turbo", zhipu_key),
                "glm-4.7": _make_compat("zhipu", "glm-4.7", zhipu_key),
                "glm-4.6": _make_compat("zhipu", "glm-4.6", zhipu_key),
                "glm-4.5": _make_compat("zhipu", "glm-4.5", zhipu_key),
                "glm-4.5-air": _make_compat("zhipu", "glm-4.5-air", zhipu_key),
            }

        # --- Anthropic API directe (ANTHROPIC_API_KEY, indépendant du CLI Claude Pro) ---
        _anthropic_native = {}
        if anthropic_key:
            from core.anthropic_native_provider import AnthropicNativeProvider

            def _make_anthropic(model: str) -> AnthropicNativeProvider:
                return AnthropicNativeProvider(api_key=anthropic_key, model=model)

            _anthropic_native = {
                # Modèles non disponibles via le CLI Claude Pro (claude_cli)
                "claude-sonnet-5": _make_anthropic("claude-sonnet-5"),
                "claude-fable-5": _make_anthropic("claude-fable-5"),
                # Accès direct alternatif aux modèles déjà exposés via claude_cli — suffixe
                # "-direct" car la clé du dict est un espace de nommage global (self.providers)
                # et "claude-opus-4-8"/"claude-haiku-4-5" pointent déjà vers ClaudeCLIProvider.
                "claude-opus-4-8-direct": _make_anthropic("claude-opus-4-8"),
                "claude-haiku-4-5-direct": _make_anthropic("claude-haiku-4-5"),
            }

        # --- Ollama Local (PC de développement) ---
        # Modèle local PRINCIPAL = fine-tune projet (QLoRA sur le code/HA/Tab5, base
        # Qwen2.5-Coder-7B). Converti via llama.cpp puis quantifié q4_K_M (le converter
        # interne d'Ollama corrompt ce modèle — cf. Modelfile.domotique-gguf).
        # Les modèles de base restent accessibles par leur nom.
        _ollama_local = {
            "ollama_local": _make_compat("ollama_local", "domotique-qwen7b:q4", "ollama"),
            "domotique-qwen7b": _make_compat("ollama_local", "domotique-qwen7b:q4", "ollama"),
            "domotique-qwen7b:q4": _make_compat("ollama_local", "domotique-qwen7b:q4", "ollama"),
            "qwen2.5-coder:7b": _make_compat("ollama_local", "qwen2.5-coder:7b", "ollama"),
            "deepseek-r1:8b": _make_compat("ollama_local", "deepseek-r1:8b", "ollama"),
            # Variante joignable en LAN (192.168.1.x) depuis le Deck — cf. commentaire
            # dans OPENAI_COMPAT_PROVIDERS["ollama_pc"]. Utilisée par le fast path vocal
            # (FAST_PATH_PROVIDERS) pour du local-first même quand le moteur tourne sur le Deck.
            # Timeout dédié (connect 2s, read 15s) — PAS la famille "lmstudio" (120s de read) :
            # mesuré en direct le 16/07, un modèle Ollama "froid" (pas encore chargé en VRAM)
            # met 30s+ à répondre au premier appel. Le budget vocal Discussion est de 20s au
            # total (source_router.py, ModeType.CHAT) : un read_timeout de 15s laisse encore
            # de la marge pour basculer sur le cloud dans le budget, plutôt que de faire
            # attendre l'utilisateur ~30-90s en silence sur un cold start.
            "ollama_pc": _make_compat("ollama_pc", "domotique-qwen7b:q4", "ollama", timeout=(2.0, 15.0)),
        }

        self.providers: dict[str, LLMProvider] = {
            **_ds,
            **_mistral,
            **_cohere,
            **_cerebras,
            **_openrouter,
            **_xai,
            **_minimax,
            **_deepinfra,
            **_github,
            **_zhipu,
            **_anthropic_native,
            **_ollama_local,

            "local": LMStudioProvider(),
            # === STEAM DECK EDGE AI (Ollama RDNA2) ===
            # Endpoint réseau local : http://192.168.1.x:11434
            # Disponibilité vérifiée dynamiquement via ping_available()
            # Tiers recommandés : parsing_logs, yaml_format, resume_court
            "deck_ollama":       OllamaDeckProvider(),                              # phi3:mini par défaut
            "deck_ollama_phi3":  OllamaDeckProvider(model_name="phi3:mini"),       # Parsing rapide
            "deck_ollama_gemma": OllamaDeckProvider(model_name="gemma2:2b"),       # Reformatage YAML
            "deck_ollama_llama": OllamaDeckProvider(model_name="llama3.2:3b"),     # Résumés courts
            # === CLAUDE CODE CLI — Modèles validés le 2026-05-24 ===
            # Accès via abonnement Claude Pro/Max — inclus dans le forfait
            # Le ClaudeCLIProvider passe --model {default_model} au CLI
            "claude":            _make_claude(),                      # défaut = sonnet-4-6
            "claude-opus-4-8":   _make_claude("claude-opus-4-8"),     # ✅ Le plus puissant (depuis 2026-05-28)
            "claude-opus-4-7":   _make_claude("claude-opus-4-7"),     # ✅ Génération stable précédente
            "claude-opus-4-0":   _make_claude("claude-opus-4-8"),     # ✅ Alias vers opus-4-8
            "claude-opus-4-5":   _make_claude("claude-opus-4-5"),     # ✅ Génération stable
            "claude-sonnet-4-6": _make_claude("claude-sonnet-4-6"),   # ✅ Défaut CLI
            "claude-sonnet-4-5": _make_claude("claude-sonnet-4-5"),   # ✅
            "claude-haiku-4-5":  _make_claude("claude-haiku-4-5"),    # ✅ Rapide
            # Anciens alias legacy
            "claude-sonnet-4.6-thinking-cli": _make_claude("claude-sonnet-4-6"),
            "claude-opus-4.6-thinking-cli":   _make_claude("claude-opus-4-8"),


            "gemini-cli": GeminiCLIProvider(),
            "antigravity": GeminiCLIProvider(), # alias
            "gemini-3.5-flash-high-cli": GeminiCLIProvider(mode="chat", model_name="gemini-3.5-flash-high-cli"),
            "gemini-3.5-flash-medium-cli": GeminiCLIProvider(mode="chat", model_name="gemini-3.5-flash-medium-cli"),
        }

        # Choix du provider Gemini : Natif (caching + grounding) ou OpenAI-compatible (fallback)
        _GeminiClass = GeminiNativeProvider if GeminiNativeProvider else GeminiProvider
        _provider_type = "Natif (caching+grounding)" if GeminiNativeProvider else "OpenAI-compatible (legacy)"
        logger.info(f"[LLMGateway] Provider Gemini sélectionné : {_provider_type}")

        # 1. Enregistrement des versions gratuites (clé gratuite AI Studio)
        if gem_free_key:
            if GeminiNativeProvider:
                # Providers natifs avec caching implicite (cache explicite = payant uniquement)
                # Note : le cache EXPLICITE (cachedContents API) n'est PAS disponible en Free Tier
                # (erreur 429: TotalCachedContentStorageTokensPerModelFreeTier limit=0)
                # Le cache IMPLICITE (systemInstruction) reste actif et GRATUIT.
                self.providers["gemini-3.5-flash-free"] = GeminiNativeProvider(
                    api_key=gem_free_key, model="gemini-3.5-flash",
                    search_grounding_available=False,
                    enable_explicit_cache=False,  # Cache explicite = payant uniquement
                    use_key_pool=True  # [Phase 1] Rotation multi-clés Free Tier
                )
                self.providers["gemini-3.1-flash-lite-free"] = GeminiNativeProvider(
                    api_key=gem_free_key, model="gemini-3.1-flash-lite",
                    enable_explicit_cache=False,
                    use_key_pool=True
                )
                self.providers["gemini-2.5-flash-free"] = GeminiNativeProvider(
                    api_key=gem_free_key, model="gemini-2.5-flash",
                    enable_explicit_cache=False,
                    use_key_pool=True
                )
            else:
                # Fallback OpenAI-compatible si gemini_native.py n'est pas disponible
                self.providers["gemini-3.5-flash-free"] = GeminiProvider(api_key=gem_free_key, model="gemini-3.5-flash")
                self.providers["gemini-3.1-flash-lite-free"] = GeminiProvider(api_key=gem_free_key, model="gemini-3.1-flash-lite")
                self.providers["gemini-2.5-flash-free"] = GeminiProvider(api_key=gem_free_key, model="gemini-2.5-flash")

            # Compatibilité et fallbacks historiques vers gratuit par défaut
            self.providers["gemini"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-flash"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-3.5-flash"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-pro"] = self.providers["gemini-3.5-flash-free"]  # Fallback sur 3.5 Flash
            self.providers["gemini-2.5-pro"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-3.1-pro"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-3.1-flash"] = self.providers["gemini-3.5-flash-free"]
            self.providers["gemini-3.1-flash-lite"] = self.providers["gemini-3.1-flash-lite-free"]
            self.providers["gemini-2.5-flash"] = self.providers["gemini-2.5-flash-free"]
            logger.info(f"✅ Provider Gemini Gratuit ({_provider_type}) activé avec succès.")
        else:
            logger.warning("Clé GEMINI_API_KEY (gratuite) non fournie.")

        # 2. Enregistrement des versions payantes (clé payante GCP)
        # Enregistrées uniquement si GEMINI_PAYANT_API_KEY est explicitement définie dans le .env
        gem_paid_key = os.environ.get("GEMINI_PAYANT_API_KEY")
        if gem_paid_key:
            if GeminiNativeProvider:
                # Providers natifs payants avec Search Grounding débloqué
                self.providers["gemini-3.5-flash-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3.5-flash",
                    search_grounding_available=True  # 🔍 Grounding débloqué !
                )
                self.providers["gemini-2.5-pro-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-2.5-pro",
                    search_grounding_available=True
                )
                self.providers["gemini-2.5-flash-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-2.5-flash",
                    search_grounding_available=True
                )
                self.providers["gemini-2.0-flash-tts-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-2.0-flash-tts",
                    enable_explicit_cache=False  # TTS : pas de cache
                )
                self.providers["gemini-3.1-flash-lite-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3.1-flash-lite",
                    enable_explicit_cache=False
                )
                # [P1 FIX] Corrigé : gemini-1.5-pro (retiré/404) → gemini-3-pro-preview
                self.providers["gemini-3-pro-short-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3-pro-preview",
                    search_grounding_available=True
                )
                # [P2] Ajout des providers Pro manquants (testés OK le 26/05/2026)
                self.providers["gemini-3.1-pro-preview-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3.1-pro-preview",
                    search_grounding_available=True  # 🔍 Pro avec Grounding + context 2M
                )
                self.providers["gemini-3.1-pro-customtools-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3.1-pro-preview-customtools",
                    search_grounding_available=True  # 🛠️ Pro optimisé Tool Use
                )
                self.providers["gemini-3-pro-preview-paid"] = GeminiNativeProvider(
                    api_key=gem_paid_key, model="gemini-3-pro-preview",
                    search_grounding_available=True
                )
            else:
                # Fallback OpenAI-compatible
                self.providers["gemini-3.5-flash-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3.5-flash")
                self.providers["gemini-2.5-pro-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-2.5-pro")
                self.providers["gemini-2.5-flash-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-2.5-flash")
                self.providers["gemini-2.0-flash-tts-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-2.0-flash-tts")
                self.providers["gemini-3.1-flash-lite-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3.1-flash-lite")
                # [P1 FIX] Corrigé : gemini-1.5-pro → gemini-3-pro-preview
                self.providers["gemini-3-pro-short-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3-pro-preview")
                # [P2] Ajout Pro manquants (fallback OpenAI-compatible)
                self.providers["gemini-3.1-pro-preview-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3.1-pro-preview")
                self.providers["gemini-3.1-pro-customtools-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3.1-pro-preview-customtools")
                self.providers["gemini-3-pro-preview-paid"] = GeminiProvider(api_key=gem_paid_key, model="gemini-3-pro-preview")
            logger.info(f"✅ Provider Gemini Payant ({_provider_type}) activé avec succès.")
        else:
            logger.info("Clé GEMINI_PAYANT_API_KEY absente du .env — les providers GCP payants sont désactivés.")

    def get_access_map(self) -> dict[str, dict[str, Any]]:
        """
        Retourne la carte d'accès des modèles disponibles (clés configurées).
        """
        access_map = {}
        for name, provider in self.providers.items():
            # Déterminer le provider parent
            provider_type = "unknown"
            if "deepseek" in name:
                provider_type = "deepseek"
            elif "gemini" in name:
                provider_type = "gemini"
            elif "claude" in name:
                provider_type = "claude"
            elif "grok" in name or "xai" in name:
                provider_type = "xai"
            elif "mistral" in name:
                provider_type = "mistral"
            elif "cohere" in name:
                provider_type = "cohere"
            elif "cerebras" in name or "gpt-oss" in name:
                provider_type = "cerebras"
            elif "openrouter" in name:
                provider_type = "openrouter"
            elif "minimax" in name:
                provider_type = "minimax"
            elif "deepinfra" in name:
                provider_type = "deepinfra"
            elif "github" in name:
                provider_type = "github"
            elif "local" in name or "deck_ollama" in name:
                provider_type = "local"

            # Récupérer l'objet provider interne si ClaudeInstructionsWrapper
            actual_provider = provider
            if hasattr(provider, "provider"):
                actual_provider = provider.provider

            model_name = (
                getattr(actual_provider, "model", None)
                or getattr(actual_provider, "model_name", None)
                or name
            )

            access_map[name] = {
                "available": True,
                "provider": provider_type,
                "model_name": model_name
            }
        return access_map

    def _get_raw_provider(self, name: str) -> LLMProvider:
        """
        Résolution interne SANS Circuit Breaker (ClaudeInstructionsWrapper seul).

        Réservée à get_provider_for_tier(), qui applique déjà son propre wrapping
        FallbackProvider/CircuitBreaker sur la liste complète du tier — passer par
        get_provider() ici imbriquerait deux cascades (double CB, double retry).
        """
        provider = self.providers.get(name.lower())
        if not provider:
            raise ValueError(f"Provider LLM inconnu : {name}")
        # Envelopper le provider pour injecter automatiquement CLAUDE.md
        # sauf si c'est un FallbackProvider (qui va déléguer aux providers enveloppés)
        if not isinstance(provider, FallbackProvider) and not isinstance(provider, ClaudeInstructionsWrapper):
            return ClaudeInstructionsWrapper(provider)
        return provider

    def get_provider(self, name: str) -> LLMProvider:
        """
        [#T212] Accès direct par ID littéral (ex: planner_model="gemini-3.5-flash-free").

        Retourne désormais une cascade d'un seul élément (FallbackProvider) : l'appel
        passe ainsi par le MÊME wrapping Circuit Breaker / retry 429 / cache sémantique
        que le chemin get_provider_for_tier(), au lieu de contourner le CB comme avant.
        Le nom de CB utilisé est la clé du registre (name.lower()), soit le même espace
        de nommage que les modèles résolus par tier.

        Lève toujours ValueError si le nom est inconnu (contrat inchangé pour les
        appelants qui s'en servent comme test de disponibilité).
        """
        raw = self._get_raw_provider(name)
        if isinstance(raw, FallbackProvider):
            return raw
        return FallbackProvider([(name.lower(), raw)])

    def stream(self, provider_name: str, system_prompt: str, user_prompt: str, **kwargs):
        """
        Streaming token-par-token via le provider spécifié.
        
        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None}
        """
        provider = self.get_provider(provider_name)
        yield from provider.generate_stream(system_prompt, user_prompt, **kwargs)

    def _resolve_tier_models(self, tier: str, config: dict) -> tuple[str, list]:
        """
        [T133][T183-migration] Résout un nom de tier (avec alias) en (actual_tier, allowed_models).

        Source de vérité : models_registry.db (colonne routing_tier, filtrée sur
        status='active') via core.models_db.get_models_for_tier(). config.json["tiers"]
        et le default_map ne servent plus que de filet de sécurité si la DB ne renvoie
        rien (ex: DB absente, routing_tier pas encore renseigné pour ce tier).
        """
        tier = tier.lower()
        tier_mapping = {
            "flash": "leger",
            "standard": "moyen",
            "reasoner": "fort",
            "pro": "fort",
            "local": "leger",
            "antigravity": "fort"
        }
        actual_tier = tier_mapping.get(tier, tier)
        if actual_tier not in ["leger", "moyen", "fort", "automatique"]:
            actual_tier = "moyen"

        try:
            from core.models_db import get_models_for_tier
            db_models = get_models_for_tier(actual_tier)
            allowed_models = [m["id"] for m in db_models]
        except Exception as e:
            logger.warning(f"[LLMGateway] get_models_for_tier({actual_tier}) indisponible ({e}), repli sur config.json.")
            allowed_models = []

        if not allowed_models:
            tiers_config = config.get("tiers", {})
            allowed_models = tiers_config.get(actual_tier, [])

        if not allowed_models:
            default_map = {
                "leger": ["local", "gemini-3.5-flash"],
                "moyen": ["deepseek-chat", "gemini-3.5-flash"],
                "fort": ["deepseek-reasoner", "claude", "gemini-2.5-pro"],
                "automatique": ["local", "gemini-3.5-flash", "deepseek-chat", "gemini-cli", "deepseek-reasoner", "claude", "gemini-2.5-pro"]
            }
            allowed_models = default_map.get(actual_tier, ["gemini-3.5-flash"])

        # [routing_policy] Exclusions volontaires du routage automatique, quelle
        # que soit la source (config.json OU models_registry.db). Cas d'usage :
        # claude-fable-5 est délibérément hors tiers (décision Axel 07/2026,
        # anti auto-escalade vers le modèle le plus cher) — ce filtre garantit
        # qu'un ajout ultérieur en DB (routing_tier) ne le réintroduira pas.
        # L'accès EXPLICITE via get_provider("claude-fable-5") reste possible.
        excluded = set(config.get("routing_policy", {}).get("excluded_models", []))
        if excluded:
            filtered = [m for m in allowed_models if m not in excluded]
            if len(filtered) != len(allowed_models):
                logger.info(
                    f"[LLMGateway] routing_policy : modèle(s) exclu(s) du tier '{actual_tier}' : "
                    f"{sorted(set(allowed_models) - set(filtered))}"
                )
            allowed_models = filtered

        return actual_tier, allowed_models

    def get_provider_for_tier(self, tier: str, config: dict, elo_order: list = None) -> tuple[str, FallbackProvider]:
        """
        Résout un Tier en un FallbackProvider contenant les modèles configurés pour ce Tier, triés dynamiquement.

        Si elo_order est fourni (liste de dicts {"model": str, "elo": float}),
        les modèles sont triés par Elo décroissant en priorité, puis par coût/quota en
        second critère. Cela permet au routeur de privilégier le modèle le plus fiable
        pour le domaine d'intention détecté.

        [T133] Le scoring coût/quota/latence est délégué à ProviderScorer
        (core/provider_scorer.py, ex God Object de 150+ lignes) — cette méthode ne
        fait plus que résoudre le tier, instancier les providers et trier.
        """
        actual_tier, allowed_models = self._resolve_tier_models(tier, config)

        providers_list = []
        for model in allowed_models:
            try:
                # [#T212] Résolution brute : le FallbackProvider construit ci-dessous
                # applique déjà le Circuit Breaker sur chaque modèle de la liste.
                provider = self._get_raw_provider(model)
                providers_list.append((model, provider))
            except ValueError:
                logger.warning(f"Modèle {model} non disponible ou clé API manquante.")
                continue

        from core.provider_scorer import ProviderScorer
        scorer = ProviderScorer([m for m, _ in providers_list])
        providers_list = scorer.sort_providers(providers_list, elo_order)

        logger.info(f"[LLMGateway] Tier '{tier}' (actual: '{actual_tier}') -> allowed_models: {allowed_models} -> trié (prioritaire d'abord): {[p[0] for p in providers_list]}")

        if not providers_list:
            for fallback_model in ["gemini-3.5-flash", "deepseek-chat", "local"]:
                try:
                    providers_list.append((fallback_model, self._get_raw_provider(fallback_model)))
                    break
                except ValueError:
                    continue

        return f"tier-{actual_tier}", FallbackProvider(providers_list)

    def get_providers_summary(self) -> list:
        """
        Retourne le résumé de tous les providers LLM configurés.
        Méthode requise par la route /api/providers pour l'IHM Models Registry.
        """
        try:
            from core.models_db import get_all_providers
            return get_all_providers()
        except Exception as e:
            logger.error(f"[LLMGateway] Erreur dans get_providers_summary: {e}")
            # Fallback statique si la BDD n'est pas accessible
            return [
                {
                    "id": name,
                    "name": name.capitalize(),
                    "cascade_priority": 5.0
                }
                for name in self.providers
            ]

    def get_circuit_breakers_status(self) -> dict:
        """
        Retourne l'état de tous les Circuit Breakers du registre global.
        Méthode d'instance requise par le serveur MCP Tab5-Engine.
        """
        try:
            cb_status = {}
            if CircuitBreaker is not None:
                with CircuitBreaker._registry_lock:
                    for name, cb in CircuitBreaker._registry.items():
                        cb_status[name] = cb.to_dict()

            providers_info = {}
            for name in self.providers:
                providers_info[name] = {
                    "available": True,
                    "circuit_breaker": cb_status.get(name, {"state": "CLOSED", "failure_count": 0}),
                    "type": type(self.providers[name]).__name__
                }

            return {
                "providers": providers_info,
                "circuit_breakers": cb_status,
                "total_providers": len(providers_info),
                "total_circuit_breakers": len(cb_status)
            }
        except Exception as e:
            logger.error(f"[LLMGateway] Erreur dans get_circuit_breakers_status: {e}")
            return {"error": str(e), "providers": {}, "circuit_breakers": {}}

    def get_deck_provider(self, model_name: str = None) -> "OllamaDeckProvider | None":
        """
        Retourne un OllamaDeckProvider si le Steam Deck est joignable via réseau local.
        Teste automatiquement les deux IPs (Ethernet .43 puis Wi-Fi .139).

        Args:
            model_name: Modèle Ollama à utiliser (phi3:mini, gemma2:2b, llama3.2:3b).
                        Si None, utilise le modèle par défaut configuré.

        Returns:
            OllamaDeckProvider prêt à l'emploi, ou None si le Deck est hors ligne.
        """
        # Sélectionner le bon provider selon le modèle demandé
        provider_map = {
            "phi3:mini":    "deck_ollama_phi3",
            "gemma2:2b":    "deck_ollama_gemma",
            "llama3.2:3b":  "deck_ollama_llama",
        }
        provider_key = provider_map.get(model_name, "deck_ollama")
        provider = self.providers.get(provider_key)

        if provider is None:
            logger.warning(f"[LLMGateway] Provider Deck '{provider_key}' introuvable.")
            return None

        # Test de disponibilité réseau (timeout 2s, non bloquant)
        if provider.ping_available():
            logger.info(f"[LLMGateway] Steam Deck Ollama disponible → {provider.host} (modèle: {provider.model_name})")
            return provider
        else:
            logger.debug("[LLMGateway] Steam Deck hors ligne ou Ollama non démarré.")
            return None

    def get_all_cache_status(self) -> dict:

        """
        Retourne l'état des caches Gemini explicites de tous les providers natifs.
        Utilisé par la route API /api/google-cache-status.
        """
        cache_statuses = {}
        for name, provider in self.providers.items():
            if hasattr(provider, 'get_cache_status'):
                status = provider.get_cache_status()
                if status.get("active"):
                    cache_statuses[name] = status
        # [Cache sémantique] Stats du cache de réponses LLM (hit-rate, entrées).
        try:
            from core.semantic_cache import get_semantic_cache
            semantic_cache_stats = get_semantic_cache().stats()
        except Exception:
            semantic_cache_stats = None
        return {
            "active_caches": len(cache_statuses),
            "caches": cache_statuses,
            "native_provider_available": GeminiNativeProvider is not None,
            "semantic_cache": semantic_cache_stats,
        }

def migrate_old_gemini_id(model_id: str) -> str:
    """Migre les anciens IDs de modèles Gemini vers leurs équivalents opérationnels par défaut."""
    mapping = {
        "gemini-3.5-flash": "gemini-3.5-flash-free",
        "gemini-3.1-pro": "gemini-3.5-flash-free",
        "gemini-3.1-flash": "gemini-3.5-flash-free",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-free",
        "gemini-2.5-pro": "gemini-3.5-flash-free",
        "gemini-2.5-flash": "gemini-2.5-flash-free",
        "gemini-2.0-flash-tts": "gemini-3.5-flash-free"
    }
    return mapping.get(model_id.lower(), model_id)

# [PHASE 1 - D1] Cache de configuration invalidé par mtime.
# Évite la relecture disque + parsing JSON + logique de migration à CHAQUE appel
# (config.json était relu sur le hot-path à chaque requête). Thread-safe.
_CONFIG_CACHE: dict = {"mtime": None, "data": None}
_CONFIG_CACHE_LOCK = threading.Lock()


def load_config(force_reload: bool = False) -> dict:
    """
    Charge la configuration et garantit la rétrocompatibilité pour les tiers.

    [PHASE 1 - D1] Résultat mis en cache et invalidé sur le mtime du fichier.
    L'écriture de migration est protégée par un FileLock pour éviter la corruption
    en cas d'accès concurrent. Retourne toujours une COPIE profonde pour que les
    appelants puissent muter leur dict sans impacter le cache partagé.
    """
    config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

    # Hit de cache : si le fichier n'a pas changé depuis le dernier parsing, on évite l'I/O.
    try:
        current_mtime = os.path.getmtime(config_file) if os.path.exists(config_file) else None
    except OSError:
        current_mtime = None
    if not force_reload:
        with _CONFIG_CACHE_LOCK:
            if _CONFIG_CACHE["data"] is not None and _CONFIG_CACHE["mtime"] == current_mtime:
                return copy.deepcopy(_CONFIG_CACHE["data"])

    default_config = {
        "planner_model": "deepseek-reasoner",
        "executor_model": "gemini-3.5-flash-free",
        "antigravity_model": "fort",
        "tiers": {
            "leger": ["local", "gemini-3.5-flash-free", "deepseek-chat"],
            "moyen": ["deepseek-chat", "gemini-3.5-flash-free", "local"],
            "fort": ["deepseek-reasoner", "claude"],
            "automatique": ["local", "gemini-3.5-flash-free", "deepseek-chat", "deepseek-reasoner", "claude"]
        },
        "persistent_agents": {
            "daemon_model": "leger",
            "daemon_interval_minutes": 10,
            "daemon_enabled": True,
            "dreamer_model": "leger",
            "dreamer_schedule": "02:00",
            "dreamer_enabled": True,
            "dreamer_idle_trigger_hours": 3,
            "routines_model": "fort",
            # [#T202] Clone Git dédié aux tâches DreamCoder (branches task/*).
            # Vide = repli sur le dossier du moteur (poste de dev). Sur le Deck
            # (prod overlay sans dépôt légitime), pointer le clone dédié, ex:
            # /home/deck/dev_station/moteur_agents_dreamcoder
            "dreamcoder_repo_path": "",
        }
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, encoding='utf-8') as f:
                data = json.load(f)

                needs_migration = False

                # Migration des anciens IDs pour les rôles d'agents
                if "planner_model" in data:
                    new_val = migrate_old_gemini_id(data["planner_model"])
                    if new_val != data["planner_model"]:
                        data["planner_model"] = new_val
                        needs_migration = True
                if "executor_model" in data:
                    new_val = migrate_old_gemini_id(data["executor_model"])
                    if new_val != data["executor_model"]:
                        data["executor_model"] = new_val
                        needs_migration = True
                if "antigravity_model" in data:
                    new_val = migrate_old_gemini_id(data["antigravity_model"])
                    if new_val != data["antigravity_model"]:
                        data["antigravity_model"] = new_val
                        needs_migration = True

                # Migration des anciens IDs dans les tiers
                if "tiers" not in data:
                    data["tiers"] = default_config["tiers"]
                    needs_migration = True
                else:
                    for tier_key in ["leger", "moyen", "fort", "automatique"]:
                        if tier_key not in data["tiers"]:
                            data["tiers"][tier_key] = default_config["tiers"][tier_key]
                            needs_migration = True
                        else:
                            new_list = [migrate_old_gemini_id(m) for m in data["tiers"][tier_key]]
                            if new_list != data["tiers"][tier_key]:
                                data["tiers"][tier_key] = new_list
                                needs_migration = True

                # Injection des defaults pour persistent_agents (daemon, dreamer)
                if "persistent_agents" not in data:
                    data["persistent_agents"] = default_config["persistent_agents"]
                    needs_migration = True
                else:
                    # Compléter les clés manquantes avec les defaults
                    for pa_key, pa_default in default_config["persistent_agents"].items():
                        if pa_key not in data["persistent_agents"]:
                            data["persistent_agents"][pa_key] = pa_default
                            needs_migration = True

                if needs_migration:
                    try:
                        # [PHASE 1 - D1] Écriture protégée par FileLock (fichier partagé).
                        from filelock import FileLock
                        with FileLock(config_file + ".lock", timeout=10):
                            with open(config_file, 'w', encoding='utf-8') as wf:
                                json.dump(data, wf, indent=2)
                        logger.info("config.json migré avec succès avec les nouveaux IDs de modèles Gemini Free.")
                    except Exception as we:
                        logger.error(f"Erreur d'écriture de la migration de config.json: {we}")

                # Mise en cache du résultat parsé+migré (clé = mtime courant du fichier).
                try:
                    new_mtime = os.path.getmtime(config_file)
                except OSError:
                    new_mtime = current_mtime
                with _CONFIG_CACHE_LOCK:
                    _CONFIG_CACHE["mtime"] = new_mtime
                    _CONFIG_CACHE["data"] = data
                return copy.deepcopy(data)
        except Exception as e:
            logger.error(f"Erreur lors du chargement de config.json: {e}")
    return copy.deepcopy(default_config)
