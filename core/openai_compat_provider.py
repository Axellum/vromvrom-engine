"""
core/openai_compat_provider.py — Classe factorisée pour tous les providers OpenAI-compatibles.

Créé pour éliminer ~1500 lignes de duplication entre 9 providers
qui partageaient exactement le même code (generate, generate_structured, generate_stream).

Seule la configuration diffère entre eux (base_url, api_key, headers, provider_name).
Les cas spéciaux (ex: OpenRouter extra headers) sont gérés via le paramètre extra_headers.

Providers factorisés : DeepSeek, Mistral, Cohere, Cerebras, OpenRouter,
DeepInfra, xAI, MiniMax.

Providers NON factorisés (comportement spécial) :
- LMStudioProvider : pas de support tools, endpoint local
- GeminiProvider / GeminiNativeProvider : API Google non-OpenAI, caching/grounding
- ClaudeCLIProvider / GeminiCLIProvider : binaires CLI locaux
- FallbackProvider : logique de secours sans API

Auteur : Antigravity IDE + Axel
Créé le : 2026-06-04 (Audit V9 P0.1)
"""

import asyncio
import json
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from typing import Dict, Any

import httpx  # [D5] client HTTP async natif

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Pool de connexions HTTP persistantes (Singleton thread-safe)
#
# Problème : requests.post() crée une nouvelle connexion TCP+TLS à chaque appel.
# Avec HTTPS + négociation TLS, chaque handshake prend 80-200ms supplémentaires.
#
# Solution : requests.Session() avec HTTPAdapter (keep-alive + pool de connexions).
# Une fois la connexion TLS établie, elle reste ouverte pour les appels suivants.
# Gain estimé : -100 à -200ms par appel LLM.
# ──────────────────────────────────────────────────────────────────

class SharedHTTPPool:
    """
    Singleton thread-safe d'un pool de connexions HTTP persistantes.
    Partagé entre toutes les instances de providers OpenAI-compatibles.

    Avantages vs requests.post() brut :
    - Connexions TCP réutilisées (pas de handshake TLS à chaque appel)
    - Pool de 5 connexions par domaine, jusqu'à 20 au total
    - Thread-safe pour accès depuis run_in_executor() et coroutines asyncio
    """

    _instance = None
    _lock = threading.Lock()
    _session: requests.Session = None

    def __new__(cls):
        """Pattern Singleton avec double-check locking thread-safe."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._init_session()
        return cls._instance

    @classmethod
    def _init_session(cls) -> None:
        """Initialise la session HTTP avec le pool de connexions."""
        session = requests.Session()

        adapter = HTTPAdapter(
            pool_connections=5,    # Connexions simultanées par domaine (deepseek.com, etc.)
            pool_maxsize=20,       # Taille maximale du pool total
            max_retries=0,         # Pas de retry automatique (géré par le Circuit Breaker)
        )
        # Monter l'adaptateur pour HTTP et HTTPS
        session.mount('https://', adapter)
        session.mount('http://', adapter)

        # Keep-alive activé par défaut dans requests.Session
        cls._session = session
        logger.info("[SharedHTTPPool] Pool de connexions HTTP initialisé "
                    "(5 connexions/domaine, 20 max, keep-alive activé).")

    @classmethod
    def get_session(cls) -> requests.Session:
        """
        Retourne la session partagée (crée le singleton si besoin).
        Thread-safe — peut être appelé depuis n'importe quel thread.
        """
        if cls._session is None:
            SharedHTTPPool()  # Déclenche l'initialisation lazy
        return cls._session

    @classmethod
    def close(cls) -> None:
        """Ferme proprement toutes les connexions du pool (utilisé au shutdown)."""
        if cls._session is not None:
            cls._session.close()
            cls._session = None
            logger.info("[SharedHTTPPool] Pool de connexions fermé proprement.")


class SharedAsyncHTTPPool:
    """
    [D5] Équivalent async de SharedHTTPPool : un httpx.AsyncClient partagé.

    Mêmes bénéfices (connexions TCP/TLS réutilisées, pool borné, pas de retry
    auto — géré par le Circuit Breaker), mais I/O réellement non bloquante pour
    l'event loop (au lieu de requests + asyncio.to_thread).

    Le client est créé paresseusement et lié à l'event loop courante.
    """

    _client: "httpx.AsyncClient | None" = None
    _lock = threading.Lock()

    @classmethod
    def get_client(cls) -> "httpx.AsyncClient":
        """Retourne le client httpx.AsyncClient partagé (création lazy)."""
        if cls._client is None or cls._client.is_closed:
            with cls._lock:
                if cls._client is None or cls._client.is_closed:
                    limits = httpx.Limits(
                        max_connections=20,            # plafond global (= pool_maxsize sync)
                        max_keepalive_connections=5,   # keep-alive par hôte (= pool_connections sync)
                    )
                    cls._client = httpx.AsyncClient(limits=limits)
                    logger.info("[SharedAsyncHTTPPool] Client httpx.AsyncClient initialisé "
                                "(20 connexions max, 5 keep-alive).")
        return cls._client

    @classmethod
    async def aclose(cls) -> None:
        """Ferme proprement le client async (utilisé au shutdown)."""
        if cls._client is not None and not cls._client.is_closed:
            await cls._client.aclose()
            logger.info("[SharedAsyncHTTPPool] Client async fermé proprement.")
        cls._client = None


class OpenAICompatibleProvider:
    """
    Classe générique pour tout provider exposant une API OpenAI-compatible.
    
    Factorise les 3 méthodes (generate, generate_structured, generate_stream)
    qui étaient copier-collées dans 9 providers distincts.
    
    Paramètres de configuration :
        provider_name : Nom humain du provider (pour les logs)
        base_url      : URL de l'endpoint /chat/completions
        api_key       : Clé d'API (Bearer token)
        model         : Nom du modèle par défaut
        extra_headers : Dict de headers HTTP supplémentaires (ex: OpenRouter)
        timeout       : Tuple (connect_timeout, read_timeout) en secondes
    """
    
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        model: str,
        extra_headers: Dict[str, str] = None,
        timeout: tuple = (5.0, 120.0),
    ):
        self.provider_name = provider_name
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        
        # Construction des headers HTTP standard + optionnels
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            self.headers.update(extra_headers)
    
    def _record_usage(self, usage: dict, session_id: str = None):
        """Enregistre la consommation de tokens dans le tracker centralisé."""
        if not usage:
            return
        try:
            from core.token_tracker import record_usage
            record_usage(
                self.model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                session_id=session_id,
            )
        except Exception as e:
            logger.debug(f"[{self.provider_name}] Erreur token_tracker : {e}")
    
    def _estimate_and_record_stream_usage(self, messages: list, total_text: str, session_id: str = None):
        """Estime les tokens en mode streaming (l'API ne renvoie pas l'usage en stream)."""
        try:
            from core.token_tracker import record_usage
            prompt_len = sum(len(m.get("content", "")) for m in messages) // 4
            record_usage(
                self.model,
                max(1, prompt_len),
                max(1, len(total_text) // 4),
                session_id=session_id,
            )
        except Exception as e:
            logger.debug(f"[{self.provider_name}] Erreur estimation streaming : {e}")
    
    # ──────────────────────────────────────────────────────────────
    # generate() — Appel standard (non-streaming)
    # ──────────────────────────────────────────────────────────────
    
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """Génère une réponse complète (non-streaming) via l'API OpenAI-compatible."""
        messages = kwargs.get("messages")
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
        }
        
        # Support des outils (function calling)
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        
        # Support du max_tokens si fourni
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        
        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate)")
        # Utilisation du pool HTTP persistant (réutilisé entre les appels)
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload, timeout=self.timeout,
        )
        response.raise_for_status()
        
        resp_json = response.json()
        self._record_usage(resp_json.get("usage"), session_id=kwargs.get("session_id"))
        
        message = resp_json["choices"][0]["message"]
        
        # Si le LLM a décidé d'appeler un outil, retourner l'objet message entier
        if "tool_calls" in message:
            return message

        return message.get("content", "")

    async def generate_async(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """
        [D5] Génération asynchrone NATIVE via httpx.AsyncClient (I/O non bloquante).

        Reflète generate() mais sans asyncio.to_thread : l'appel réseau est awaité
        directement sur l'event loop. Même contrat de retour (str ou message avec
        tool_calls) et même enregistrement d'usage.
        """
        messages = kwargs.get("messages")
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
        }
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_async)")
        # httpx n'accepte pas le tuple (connect, read) de requests → convertir.
        _to = self.timeout
        _timeout = httpx.Timeout(_to[1], connect=_to[0]) if isinstance(_to, (tuple, list)) else _to
        _client = SharedAsyncHTTPPool.get_client()
        response = await _client.post(
            self.base_url, headers=self.headers, json=payload, timeout=_timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        # L'enregistrement d'usage est une écriture SQLite locale rapide : on la
        # déporte en thread pour ne pas bloquer l'event loop.
        await asyncio.to_thread(
            self._record_usage, resp_json.get("usage"), kwargs.get("session_id")
        )

        message = resp_json["choices"][0]["message"]
        if "tool_calls" in message:
            return message
        return message.get("content", "")

    # ──────────────────────────────────────────────────────────────
    # generate_structured() — Réponse JSON forcée
    # ──────────────────────────────────────────────────────────────
    
    def generate_structured(
        self, system_prompt: str, user_prompt: str,
        schema: Dict[str, Any], **kwargs,
    ) -> Dict[str, Any]:
        """Génère une réponse JSON structurée via response_format json_object."""
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict."
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": kwargs.get("temperature", 0.0),
        }
        
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        
        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_structured)")
        # Pool HTTP persistant
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload, timeout=self.timeout,
        )
        response.raise_for_status()
        
        resp_json = response.json()
        self._record_usage(resp_json.get("usage"), session_id=kwargs.get("session_id"))
        
        content = resp_json["choices"][0]["message"].get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"{self.provider_name} n'a pas retourné un JSON valide: {content[:200]}")
            return {}
    
    # ──────────────────────────────────────────────────────────────
    # generate_stream() — Streaming SSE natif
    # ──────────────────────────────────────────────────────────────
    
    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """
        Streaming natif via stream=true (Server-Sent Events).
        
        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None}
        """
        messages = kwargs.get("messages")
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
            "stream": True,
        }
        
        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_stream)")
        # Pool HTTP persistant (stream=True via Session)
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload,
            timeout=self.timeout, stream=True,
        )
        response.raise_for_status()
        
        total_tokens = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                yield {"token": "", "done": True, "usage": None}
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    total_tokens += token
                    yield {"token": token, "done": False, "usage": None}
            except json.JSONDecodeError:
                continue
        
        # Estimation des tokens en streaming (l'API ne fournit pas l'usage)
        self._estimate_and_record_stream_usage(
            messages, total_tokens, session_id=kwargs.get("session_id"),
        )


# ──────────────────────────────────────────────────────────────────
# Registre de configuration des 9 providers OpenAI-compatibles
# ──────────────────────────────────────────────────────────────────

# Ce dictionnaire permet de créer n'importe quel provider OpenAI-compatible
# avec une seule ligne de configuration au lieu de ~130 lignes de classe.
OPENAI_COMPAT_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/chat/completions",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "description": "DeepSeek API — Ratio coût/intelligence optimal",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "env_key": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
        "description": "Mistral AI — Plan Experiment gratuit, excellent en français",
    },
    "cohere": {
        "base_url": "https://api.cohere.com/compatibility/v1/chat/completions",
        "env_key": "COHERE_API_KEY",
        "default_model": "command-r-plus-latest",
        "description": "Cohere — Trial Key, champion RAG / Tooling",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1/chat/completions",
        "env_key": "CEREBRAS_API_KEY",
        "default_model": "llama3.3-70b",
        "description": "Cerebras — Free Tier d'inférence Wafer Scale ultra-rapide",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "description": "OpenRouter — Accès modèles gratuits et payants",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/AuxFilsDesIdees/moteur_agents",
            "X-Title": "Moteur Agents V9",
        },
    },
    "deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai/chat/completions",
        "env_key": "DEEPINFRA_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "description": "DeepInfra — Backup payant pour modèles open-weights",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1/chat/completions",
        "env_key": "XAI_API_KEY",
        "default_model": "grok-3-latest",
        "description": "xAI — Modèles Grok, fort raisonnement",
    },
    "minimax": {
        # Mise à jour URL : ancienne URL minimaxi.chat (endpoint CN non-standard)
        # → api.minimax.io/v1 (endpoint international, OpenAI-compatible officiel, juin 2026)
        "base_url": "https://api.minimax.io/v1/chat/completions",
        "env_key": "MINIMAX_API_KEY",
        "default_model": "MiniMax-M3",
        "description": "MiniMax — MoE multimodal (texte/image/vidéo), contexte 1M tokens, optimisé agents",
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com/chat/completions",
        "env_key": "GITHUB_TOKEN",
        "default_model": "gpt-4o-mini",
        "description": "GitHub Models — Inférence gratuite pour développeurs via Azure AI",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "env_key": "ZHIPU_API_KEY",
        "default_model": "glm-5-turbo",
        "description": "Zhipu AI (Z.ai) — Modèles GLM-5, excellent en code et FR",
    },
    "ollama_local": {
        "base_url": "http://127.0.0.1:11434/v1/chat/completions",
        "env_key": "OLLAMA_API_KEY",  # Pas de clé requise pour l'instance locale
        "default_model": "qwen2.5-coder:7b",
        "description": "Ollama Local PC — Inférence locale ultra-rapide sur RTX 5070 Ti",
    },
}


def create_provider(
    provider_id: str, model: str = None, api_key: str = None,
) -> OpenAICompatibleProvider:
    """
    Factory : crée un provider OpenAI-compatible à partir du registre.
    
    Args:
        provider_id : Clé dans OPENAI_COMPAT_PROVIDERS (ex: "deepseek", "mistral")
        model       : Nom du modèle (surcharge le défaut du registre)
        api_key     : Clé API (surcharge la variable d'environnement)
    
    Returns:
        OpenAICompatibleProvider configuré et prêt à l'emploi
    
    Raises:
        ValueError si le provider_id n'existe pas dans le registre
    """
    import os
    
    if provider_id not in OPENAI_COMPAT_PROVIDERS:
        raise ValueError(
            f"Provider '{provider_id}' inconnu. "
            f"Disponibles : {list(OPENAI_COMPAT_PROVIDERS.keys())}"
        )
    
    config = OPENAI_COMPAT_PROVIDERS[provider_id]
    resolved_key = api_key or os.environ.get(config["env_key"], "")
    resolved_model = model or config["default_model"]
    
    return OpenAICompatibleProvider(
        provider_name=provider_id.capitalize(),
        base_url=config["base_url"],
        api_key=resolved_key,
        model=resolved_model,
        extra_headers=config.get("extra_headers"),
    )


# ──────────────────────────────────────────────────────────────────
# [PHASE 2 - D3] Provider MiniMax — sous-classe propre (remplace le monkey-patch)
# ──────────────────────────────────────────────────────────────────
import re as _re


class MiniMaxProvider(OpenAICompatibleProvider):
    """
    Provider MiniMax. Les modèles de raisonnement MiniMax émettent des blocs
    <think>...</think> qu'il faut retirer de la réponse finale.

    [D3] Auparavant géré par un monkey-patch de `provider.generate` à
    l'instanciation (qui cassait le wrapping ClaudeInstructionsWrapper et le
    streaming). Désormais une vraie sous-classe, testable et composable.
    """

    _THINK_RE = _re.compile(r"<think>[\s\S]*?</think>\s*", _re.DOTALL)

    @classmethod
    def strip_think(cls, text: Any) -> Any:
        """Retire les blocs <think>...</think> d'une chaîne ; renvoie tel quel sinon."""
        if isinstance(text, str):
            return cls._THINK_RE.sub("", text).strip()
        return text

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        result = super().generate(system_prompt, user_prompt, **kwargs)
        # Réponse texte simple.
        if isinstance(result, str):
            return self.strip_think(result)
        # Réponse structurée (tool_calls) : nettoyer le champ content textuel.
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            result["content"] = self.strip_think(result["content"])
        return result
