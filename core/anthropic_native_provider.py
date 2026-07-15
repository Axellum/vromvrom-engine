"""
core/anthropic_native_provider.py — Provider Anthropic natif (API Messages directe).

Accès direct à l'API Anthropic (POST /v1/messages) via ANTHROPIC_API_KEY, INDÉPENDANT
des quotas de l'abonnement Claude Pro CLI utilisé par ClaudeCLIProvider (core/llm/
providers/deepseek.py). Ce provider consomme le budget payant à l'usage de la clé API.

Wire format natif Anthropic (x-api-key + anthropic-version), PAS le format
OpenAI-compatible utilisé par OpenAICompatibleProvider — d'où une classe dédiée,
sur le même modèle que GeminiNativeProvider (core/gemini_native.py) pour l'API
Google native.

Auteur : Antigravity IDE + Axel
Date : 2026-07-03
"""

import logging
from typing import Any, Dict

from core.llm_timeouts import get_timeout

logger = logging.getLogger(__name__)

try:
    from core.llm_gateway import LLMProvider
except ImportError:
    # Fallback pour les tests unitaires isolés
    from abc import ABC, abstractmethod

    class LLMProvider(ABC):
        @abstractmethod
        def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any: pass
        @abstractmethod
        def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]: pass

ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicNativeProvider(LLMProvider):
    """
    Appelle directement POST /v1/messages avec ANTHROPIC_API_KEY (x-api-key).

    Distinct de ClaudeCLIProvider : celui-ci facture à l'usage sur la clé API,
    indépendamment des quotas glissants (5h) de l'abonnement Claude Pro/Max.
    """

    def __init__(self, api_key: str, model: str, max_tokens: int = 8192, timeout: tuple = None):
        timeout = timeout if timeout is not None else get_timeout("anthropic")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.provider_name = f"AnthropicNative({model})"
        self.headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

    def _record_usage(self, usage: dict, session_id: str = None) -> None:
        if not usage:
            return
        try:
            from core.token_tracker import record_usage
            record_usage(
                self.model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                session_id=session_id,
            )
        except Exception as e:
            logger.debug(f"[{self.provider_name}] Erreur token_tracker : {e}")

    def _build_payload(self, system_prompt: str, user_prompt: str, **kwargs) -> dict:
        messages = kwargs.get("messages")
        if not messages:
            messages = [{"role": "user", "content": user_prompt}]

        payload = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "messages": messages,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs:
            payload["tool_choice"] = kwargs["tool_choice"]
        return payload

    @staticmethod
    def _extract_content(resp_json: dict, provider_name: str) -> Any:
        """Extrait le texte (ou les tool_use) d'une réponse Messages API, en gérant le refus."""
        if resp_json.get("stop_reason") == "refusal":
            logger.warning(f"[{provider_name}] Requête refusée par les classifieurs de sécurité Anthropic")
            return ""

        content_blocks = resp_json.get("content", [])
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        if tool_use_blocks:
            return {"tool_calls": tool_use_blocks}

        return "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

    # ──────────────────────────────────────────────────────────────
    # generate() — Appel standard (non-streaming, synchrone)
    # ──────────────────────────────────────────────────────────────

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        from core.openai_compat_provider import SharedHTTPPool

        payload = self._build_payload(system_prompt, user_prompt, **kwargs)
        logger.debug(f"Appel API {self.provider_name} (generate)")
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            ANTHROPIC_MESSAGES_URL, headers=self.headers, json=payload, timeout=self.timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        self._record_usage(resp_json.get("usage"), session_id=kwargs.get("session_id"))
        return self._extract_content(resp_json, self.provider_name)

    async def generate_async(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """[D5] Génération asynchrone native via httpx.AsyncClient (I/O non bloquante)."""
        import asyncio
        import httpx
        from core.openai_compat_provider import SharedAsyncHTTPPool

        payload = self._build_payload(system_prompt, user_prompt, **kwargs)
        logger.debug(f"Appel API {self.provider_name} (generate_async)")
        _to = self.timeout
        _timeout = httpx.Timeout(_to[1], connect=_to[0]) if isinstance(_to, (tuple, list)) else _to
        _client = SharedAsyncHTTPPool.get_client()
        response = await _client.post(
            ANTHROPIC_MESSAGES_URL, headers=self.headers, json=payload, timeout=_timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        await asyncio.to_thread(
            self._record_usage, resp_json.get("usage"), kwargs.get("session_id")
        )
        return self._extract_content(resp_json, self.provider_name)

    # ──────────────────────────────────────────────────────────────
    # generate_structured() — Sortie JSON forcée via tool_use unique
    # (approche native Anthropic — plus fiable qu'un prompt "réponds en JSON")
    # ──────────────────────────────────────────────────────────────

    def generate_structured(
        self, system_prompt: str, user_prompt: str,
        schema: Dict[str, Any], **kwargs,
    ) -> Dict[str, Any]:
        from core.openai_compat_provider import SharedHTTPPool

        tool_name = "respond_with_json"
        call_kwargs = dict(kwargs)
        call_kwargs["tools"] = [{
            "name": tool_name,
            "description": "Retourne la réponse structurée selon le schéma demandé.",
            "input_schema": schema,
        }]
        call_kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

        payload = self._build_payload(system_prompt, user_prompt, **call_kwargs)
        logger.debug(f"Appel API {self.provider_name} (generate_structured)")
        _http = SharedHTTPPool.get_session()
        response = _http.post(
            ANTHROPIC_MESSAGES_URL, headers=self.headers, json=payload, timeout=self.timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        self._record_usage(resp_json.get("usage"), session_id=kwargs.get("session_id"))

        for block in resp_json.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                return block.get("input", {})

        logger.error(f"{self.provider_name} n'a pas retourné de tool_use pour generate_structured")
        return {}
