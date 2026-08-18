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
import os
import threading
from typing import Any

import httpx  # [D5] client HTTP async natif
import requests
from requests.adapters import HTTPAdapter

from core.llm.providers.base import LLMProvider
from core.llm_timeouts import get_timeout

logger = logging.getLogger(__name__)

# Au-delà, l'extrait est tronqué : un corps d'erreur d'API tient en quelques lignes,
# et certains renvoient le prompt en écho — inutile d'en inonder le journal.
_MAX_CORPS_ERREUR = 500

# [T316] Repli du plafond de sortie par défaut des providers OpenAI-compatibles.
# Une génération en boucle peut partir jusqu'au plafond par défaut du provider
# (Cerebras 40 000 tokens — mesuré en prod le 16/08) sans qu'aucun appelant n'ait
# posé de `max_tokens`. On applique donc un plafond par défaut, dérivé du
# `context_output` du catalogue (core.models_db.get_model) ; ce repli ne sert que
# pour les modèles absents du catalogue ou à `context_output` nul. Surchargeable
# par la variable d'environnement `MOTEUR_MAX_OUTPUT_TOKENS` (entier > 0).
_DEFAULT_MAX_OUTPUT_TOKENS = 8192


def _repli_max_output_tokens() -> int:
    """Plafond de sortie de repli des providers OpenAI-compatibles (#T316).

    Lit `MOTEUR_MAX_OUTPUT_TOKENS` (entier strictement positif) ; sinon
    `_DEFAULT_MAX_OUTPUT_TOKENS`. N'est utilisé que quand le modèle est absent du
    catalogue ou à `context_output` nul — jamais quand l'appelant fournit
    `max_tokens`.
    """
    brut = os.environ.get("MOTEUR_MAX_OUTPUT_TOKENS", "").strip()
    if brut.isdigit() and int(brut) > 0:
        return int(brut)
    return _DEFAULT_MAX_OUTPUT_TOKENS


def _tokens_cache_hit(usage: dict) -> int:
    """Compteur de tokens d'entrée servis par le cache (#T347).

    L'API le renvoie sous deux formes : `prompt_cache_hit_tokens` (DeepSeek) ou
    `prompt_tokens_details.cached_tokens` (forme normalisée OpenAI, renvoyée par
    d'autres providers compatibles). La première a la priorité ; à défaut la
    seconde ; absent → 0. On ne suppose JAMAIS un taux de cache : seul le
    compteur renvoyé par l'API fait foi.
    """
    if not isinstance(usage, dict):
        return 0
    cache_hit = usage.get("prompt_cache_hit_tokens")
    if cache_hit is not None:
        return int(cache_hit) if cache_hit else 0
    details = usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens", 0) or 0)


def lever_pour_statut(response, *, provider: str = "?", modele: str = "?") -> None:
    """
    [#T309] `raise_for_status()` qui n'avale plus l'explication de l'API.

    `requests` lève une `HTTPError` dont le message se limite au code et à l'URL —
    « 400 Client Error: Bad Request for url: … ». Le corps de la réponse, qui dit
    POURQUOI la requête est refusée (champ inconnu, rôle invalide, quota…), était
    perdu à chaque fois. Sur un 400, la différence est celle entre un ticket
    « Cerebras refuse » et un correctif.

    Mesuré le 11/08 en exerçant la boucle d'outils vocaux : un HTTP 400 a ouvert le
    circuit breaker de `gpt-oss-120b` sans qu'aucune trace ne dise ce que l'API
    reprochait — impossible de diagnostiquer autrement qu'en devinant.
    """
    # Objet réponse non standard (double de test, provider exotique) : on ne tente
    # rien de plus que le comportement d'origine. Journaliser n'est jamais une raison
    # de changer ce que l'appelant reçoit.
    try:
        code = int(response.status_code)
    except (TypeError, ValueError, AttributeError):
        response.raise_for_status()
        return

    if code < 400:
        return
    try:
        corps = (response.text or "").strip()
    except Exception:
        corps = ""
    extrait = corps[:_MAX_CORPS_ERREUR]
    if len(corps) > _MAX_CORPS_ERREUR:
        extrait += f"… (+{len(corps) - _MAX_CORPS_ERREUR} car.)"
    logger.error(
        f"[{provider}] HTTP {response.status_code} sur {modele} — "
        f"réponse de l'API : {extrait or '(corps vide)'}"
    )
    response.raise_for_status()


def filtrer_champs_prives(messages: list) -> list:
    """
    Retire les champs PRIVÉS (préfixe "_") des messages avant envoi HTTP.

    Le round-trip d'outils Gemini porte la thought_signature dans un champ
    privé des tool_calls (cf. core/gemini_native.py, #T263). Ce champ est
    interne au moteur : il ne doit JAMAIS partir dans le payload des
    providers OpenAI-compatibles, qui peuvent rejeter un champ inconnu
    (erreur 400). Le préfixe "_" n'est utilisé par aucun champ standard.
    """
    nettoyes = []
    for msg in messages:
        msg_propre = {k: v for k, v in msg.items() if not k.startswith("_")}
        tool_calls = msg_propre.get("tool_calls")
        if isinstance(tool_calls, list):
            msg_propre["tool_calls"] = [
                {k: v for k, v in tc.items() if not k.startswith("_")}
                for tc in tool_calls
            ]
        nettoyes.append(msg_propre)
    return nettoyes


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

        [#T268] Se réarme après close() : close() remet `_session` à None sans
        détruire `_instance`, donc `SharedHTTPPool()` seul ne suffisait plus à
        relancer l'initialisation (le __new__ rend l'instance existante sans
        rappeler _init_session). On recrée ici sous verrou (double-check),
        comme SharedAsyncHTTPPool.get_client().
        """
        if cls._session is None:
            with cls._lock:
                if cls._session is None:
                    cls._init_session()
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


class OpenAICompatibleProvider(LLMProvider):
    """
    Classe générique pour tout provider exposant une API OpenAI-compatible.

    Factorise les 3 méthodes (generate, generate_structured, generate_stream)
    qui étaient copier-collées dans 9 providers distincts.

    [#T240] Hérite de `LLMProvider` — la classe n'héritait de rien et lui manquait
    donc `generate_structured_async()`, que `FallbackProvider` appelle pourtant sur
    chaque provider de sa cascade (`core/llm/providers/deepseek.py:686`). Sur les
    ~10 providers factorisés ici, l'appel levait un `AttributeError` avalé par le
    `except Exception` de la cascade : chaque génération structurée échouait
    silencieusement, enregistrait un échec au circuit breaker du modèle et passait au
    suivant. `generate_async()` reste surchargée ci-dessous (vrai httpx async) ; seul
    l'équivalent structuré est désormais fourni par la base (via `asyncio.to_thread`).

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
        extra_headers: dict[str, str] = None,
        timeout: tuple = None,
    ):
        self.provider_name = provider_name
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout if timeout is not None else get_timeout("openai_compat")

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
                cache_hit_tokens=_tokens_cache_hit(usage),
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

    def _max_tokens_par_defaut(self) -> int:
        """Plafond de sortie par défaut du modèle (#T316).

        Dérivé du `context_output` du catalogue (`core.models_db.get_model`),
        avec repli sur `MOTEUR_MAX_OUTPUT_TOKENS` (puis 8192) si le modèle est
        absent du catalogue ou à `context_output` nul. N'est appliqué QUE quand
        l'appelant ne fournit pas `max_tokens` : un appelant qui en pose un n'est
        jamais écrasé (voir les trois méthodes de génération).
        """
        try:
            from core.models_db import get_model
            modele = get_model(self.model)
            if modele:
                ctx_output = modele.get("context_output")
                if ctx_output:
                    return int(ctx_output)
        except Exception as e:
            logger.debug(
                f"[{self.provider_name}] get_model({self.model}) indisponible "
                f"({e}) — repli sur le défaut."
            )
        return _repli_max_output_tokens()

    # ──────────────────────────────────────────────────────────────
    # generate() — Appel standard (non-streaming)
    # ──────────────────────────────────────────────────────────────

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """Génère une réponse complète (non-streaming) via l'API OpenAI-compatible."""
        messages = kwargs.get("messages")
        if messages:
            # Champs privés internes du moteur (ex: _thought_signature Gemini) :
            # jamais transmis aux APIs OpenAI-compatibles (#T263).
            messages = filtrer_champs_prives(messages)
        else:
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
        # `tool_choice` permet au client de FORCER (ou d'interdire) un appel
        # d'outil. Sans ce relais, un client qui l'envoie voyait sa contrainte
        # silencieusement ignorée.
        if kwargs.get("tool_choice") is not None:
            payload["tool_choice"] = kwargs["tool_choice"]

        # [T316] Plafond de sortie : celui de l'appelant s'il en pose un, sinon le
        # défaut dérivé du catalogue (context_output) — pour borner une génération
        # en boucle qui partirait jusqu'au plafond par défaut du provider.
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        else:
            payload["max_tokens"] = self._max_tokens_par_defaut()

        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate)")
        # Utilisation du pool HTTP persistant (réutilisé entre les appels)
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload, timeout=self.timeout,
        )
        lever_pour_statut(response, provider=self.provider_name, modele=self.model)

        resp_json = response.json()
        usage = resp_json.get("usage")
        self._record_usage(usage, session_id=kwargs.get("session_id"))

        # Canal latéral d'usage réel : l'appelant peut passer un dict récepteur
        # via `_usage_sink` (préfixe "_" : jamais transmis au payload HTTP).
        # generate() rend une chaîne dans le cas courant — le type de retour ne
        # peut donc pas porter l'usage, et AUCUN appelant existant ne change :
        # sans sink, le comportement est strictement identique. Le dict est propre
        # à la requête (créé par l'appelant), donc aucune course entre requêtes
        # concurrentes. L'usage est écrit TEL QUE renvoyé par le provider :
        # aucun comptage n'est estimé ni recalculé.
        _usage_sink = kwargs.get("_usage_sink")
        if _usage_sink is not None and isinstance(_usage_sink, dict) and usage:
            _usage_sink["usage"] = usage

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
        if messages:
            # Champs privés internes du moteur (ex: _thought_signature Gemini) :
            # jamais transmis aux APIs OpenAI-compatibles (#T263).
            messages = filtrer_champs_prives(messages)
        else:
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
        if kwargs.get("tool_choice") is not None:
            payload["tool_choice"] = kwargs["tool_choice"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        else:
            payload["max_tokens"] = self._max_tokens_par_defaut()

        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_async)")
        # httpx n'accepte pas le tuple (connect, read) de requests → convertir.
        _to = self.timeout
        _timeout = httpx.Timeout(_to[1], connect=_to[0]) if isinstance(_to, (tuple, list)) else _to
        _client = SharedAsyncHTTPPool.get_client()
        response = await _client.post(
            self.base_url, headers=self.headers, json=payload, timeout=_timeout,
        )
        lever_pour_statut(response, provider=self.provider_name, modele=self.model)

        resp_json = response.json()
        # L'enregistrement d'usage est une écriture SQLite locale rapide : on la
        # déporte en thread pour ne pas bloquer l'event loop.
        await asyncio.to_thread(
            self._record_usage, resp_json.get("usage"), kwargs.get("session_id")
        )

        # Même canal latéral que generate() : miroir fidèle du chemin synchrone,
        # pour que le contrat d'usage soit identique quelle que soit la variante.
        _usage_sink = kwargs.get("_usage_sink")
        if _usage_sink is not None and isinstance(_usage_sink, dict) and resp_json.get("usage"):
            _usage_sink["usage"] = resp_json["usage"]

        message = resp_json["choices"][0]["message"]
        if "tool_calls" in message:
            return message
        return message.get("content", "")

    # ──────────────────────────────────────────────────────────────
    # generate_structured() — Réponse JSON forcée
    # ──────────────────────────────────────────────────────────────

    def generate_structured(
        self, system_prompt: str, user_prompt: str,
        schema: dict[str, Any], **kwargs,
    ) -> dict[str, Any]:
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
        else:
            payload["max_tokens"] = self._max_tokens_par_defaut()

        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_structured)")
        # Pool HTTP persistant
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload, timeout=self.timeout,
        )
        lever_pour_statut(response, provider=self.provider_name, modele=self.model)

        resp_json = response.json()
        self._record_usage(resp_json.get("usage"), session_id=kwargs.get("session_id"))

        content = resp_json["choices"][0]["message"].get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Le thinking DeepSeek peut précéder le JSON — on l'extrait via raw_decode
            try:
                decoder = json.JSONDecoder()
                for m in __import__("re").finditer(r"[{\[]", content):
                    try:
                        obj, _ = decoder.raw_decode(content, m.start())
                        if isinstance(obj, (dict, list)):
                            return obj
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass
            logger.error(f"{self.provider_name} n'a pas retourné un JSON valide: {content[:200]}")
            return {}

    # ──────────────────────────────────────────────────────────────
    # generate_stream() — Streaming SSE natif
    # ──────────────────────────────────────────────────────────────

    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """
        Streaming natif via stream=true (Server-Sent Events).

        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None} pour le texte
            de réponse, ou {"reasoning": str, "done": False, "usage": None} pour
            le raisonnement (#T346). Les deux canaux ne sont JAMAIS mélangés dans
            un même chunk yieldé : ce qui part sous "reasoning" ne doit jamais se
            retrouver dans la réponse rendue ni au TTS.
        """
        messages = kwargs.get("messages")
        if messages:
            # Champs privés internes du moteur (ex: _thought_signature Gemini) :
            # jamais transmis aux APIs OpenAI-compatibles (#T263).
            messages = filtrer_champs_prives(messages)
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
            "stream": True,
            # Extension quasi-universelle chez les APIs OpenAI-compatibles
            # (OpenAI, DeepSeek, Mistral, OpenRouter...) : demande un dernier
            # chunk SSE avec le vrai usage, pour ne PAS avoir à l'estimer.
            "stream_options": {"include_usage": True},
        }

        # [T316] Plafond de sortie : celui de l'appelant s'il en pose un, sinon le
        # défaut dérivé du catalogue (context_output) — même garde-fou anti-boucle
        # que dans generate() / generate_async() / generate_structured().
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        else:
            payload["max_tokens"] = self._max_tokens_par_defaut()

        logger.debug(f"Appel API {self.provider_name} ({self.model}) (generate_stream)")
        # Pool HTTP persistant (stream=True via Session)
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            self.base_url, headers=self.headers, json=payload,
            timeout=self.timeout, stream=True,
        )
        lever_pour_statut(response, provider=self.provider_name, modele=self.model)

        total_tokens = ""
        total_raisonnement = ""
        real_usage = None
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                # Chunk final avec stream_options.include_usage : "choices"
                # vide/absent, "usage" rempli — capturé puis remonté à l'appelant
                # sur le chunk de clôture ci-dessous.
                chunk_usage = chunk.get("usage")
                if chunk_usage:
                    real_usage = chunk_usage
                choices = chunk.get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                # [#T346] Canal raisonnement dédié : DeepSeek émet la pensée sous
                # `delta.reasoning_content` (deepseek-reasoner), d'autres compatibles
                # sous `delta.reasoning`. Ignoré jusque-là, le raisonnement était
                # perdu à la lecture du chunk. On le yield sur son propre canal —
                # JAMAIS mélangé à `token`, sinon il finirait au TTS dans le salon.
                raisonnement = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if raisonnement:
                    total_raisonnement += raisonnement
                    yield {"reasoning": raisonnement, "done": False, "usage": None}
                token = delta.get("content", "")
                if token:
                    total_tokens += token
                    yield {"token": token, "done": False, "usage": None}
            except json.JSONDecodeError:
                continue

        # L'écriture en base AVANT le yield de clôture : les consommateurs
        # (proxy, pipeline, IHM) cassent leur boucle dès `done=True` et ne
        # relancent jamais le générateur — une écriture placée après le yield
        # ne serait pas exécutée de manière fiable.
        if real_usage:
            # Le provider a bien renvoyé l'usage réel via stream_options —
            # pas besoin d'estimer.
            from core.token_tracker import record_usage
            record_usage(
                self.model,
                real_usage.get("prompt_tokens", 0),
                real_usage.get("completion_tokens", 0),
                session_id=kwargs.get("session_id"),
                cache_hit_tokens=_tokens_cache_hit(real_usage),
            )
        else:
            # Provider ignore stream_options.include_usage (pas tous ne le
            # supportent) : repli sur l'estimation chars/4, EN BASE UNIQUEMENT.
            # Le raisonnement compte dans les tokens de sortie facturés : il
            # entre dans l'estimation (sans jamais entrer dans la réponse).
            self._estimate_and_record_stream_usage(
                messages, total_tokens + total_raisonnement, session_id=kwargs.get("session_id"),
            )

        # Chunk de clôture : il porte l'usage RÉEL quand le provider l'a
        # renvoyé via stream_options.include_usage, et None sinon. L'éventuelle
        # estimation de repli calculée pour la base n'y figure JAMAIS :
        # aucun comptage n'est estimé ni recalculé pour le client.
        yield {"token": "", "done": True, "usage": real_usage}


# ──────────────────────────────────────────────────────────────────
# Registre de configuration des 9 providers OpenAI-compatibles
# ──────────────────────────────────────────────────────────────────

# Modèle réellement chargé par AJEAN (llama.cpp b10451, llama-server.exe).
# Constante unique : le nom est celui que llama-server expose sur /v1/models,
# c'est-à-dire le nom du fichier .gguf sans l'extension. À ne pas disperser.
AJEAN_DEFAULT_MODEL = "Qwen2.5-14B-Instruct-1M-Q4_K_M"

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
        "default_model": "openrouter/auto",
        "description": "OpenRouter — Accès modèles gratuits et payants",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/Axellum/vromvrom-engine",
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
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "env_key": "ZHIPU_API_KEY",
        "default_model": "glm-5-turbo",
        "description": "Zhipu AI (Z.ai) — Modèles GLM-5, excellent en code et FR",
    },
    "dashscope": {
        # Coding Plan Alibaba (clé sk-sp-*, endpoint dédié — PAS le pay-as-you-go).
        # Intl par défaut ; surcharge possible via DASHSCOPE_BASE_URL.
        "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions",
        "env_key": "DASHSCOPE_API_KEY",
        "default_model": "qwen3-coder-next",
        "description": (
            "Alibaba DashScope Coding Plan Lite — forfait requêtes "
            "(Qwen/GLM/Kimi/MiniMax). Endpoint coding-intl, clé sk-sp-."
        ),
    },
    "ollama_local": {
        "base_url": "http://127.0.0.1:11434/v1/chat/completions",
        "env_key": "OLLAMA_API_KEY",  # Pas de clé requise pour l'instance locale
        "default_model": "qwen2.5-coder:7b",
        "description": "Ollama Local PC — Inférence locale ultra-rapide sur GPU locale",
    },
    "ollama_pc": {
        # Même IP LAN que LMStudioProvider (192.168.1.10, carte "Ethernet 4") — contrairement
        # à ollama_local (127.0.0.1), joignable depuis le Deck en prod. Prérequis côté PC :
        # Ollama démarré avec OLLAMA_HOST=0.0.0.0 (ou au moins .84) + pare-feu Windows ouvert
        # sur 11434 pour le LAN, sinon connect timeout (repli cloud silencieux, pas d'erreur bruyante).
        "base_url": "http://192.168.1.10:11434/v1/chat/completions",
        "env_key": "OLLAMA_API_KEY",  # Pas de clé requise pour l'instance locale
        "default_model": "domotique-qwen7b:q4",
        "description": "Ollama PC via LAN — joignable depuis le Deck (GPU locale, fine-tune domotique)",
    },
    # === AJEAN (llama.cpp / llama-server) — déclaratif, NON branché en cascade ===
    # Runtime llama.cpp du PC d'Axel (b10451, Qwen2.5-14B-Instruct-1M-Q4_K_M), API
    # OpenAI-compatible sur le port 8080. Deux entrées symétriques : le PC d'Axel
    # (192.168.1.10, joignable depuis le Deck) et le Deck lui-même (127.0.0.1, pour sa
    # propre instance à venir). Hôtes déclarés par IP privée/loopback, JAMAIS par nom
    # DNS : `LLMGateway._est_hote_local` (core/llm_gateway.py) classe toute IP privée
    # comme locale (#T337, mode privacy_level=local_only) alors qu'un nom d'hôte serait
    # traité comme externe/cloud. Pas de clé d'API : llama.cpp n'authentifie pas.
    # ⚠️ PRÉREQUIS RÉSEAU (action d'Axel, hors périmètre) : llama-server écoute encore
    # --host 127.0.0.1, donc 192.168.1.10:8080 est injoignable depuis le Deck. Il faut
    # basculer sur 0.0.0.0 et ouvrir le pare-feu Windows sur 8080. Tant que ce n'est
    # pas fait, ces providers échouent en connect timeout (repli cloud silencieux).
    "ajean_pc": {
        "base_url": "http://192.168.1.10:8080/v1/chat/completions",
        "env_key": "AJEAN_API_KEY",  # Pas de clé requise : llama.cpp n'authentifie pas
        "default_model": AJEAN_DEFAULT_MODEL,
        "description": "AJEAN PC via LAN — llama.cpp 14B (RTX, contexte 1M), joignable depuis le Deck",
    },
    "ajean_deck": {
        "base_url": "http://127.0.0.1:8080/v1/chat/completions",
        "env_key": "AJEAN_API_KEY",  # Pas de clé requise : llama.cpp n'authentifie pas
        "default_model": AJEAN_DEFAULT_MODEL,
        "description": "AJEAN Deck — llama.cpp 14B local (127.0.0.1), instance à venir",
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
