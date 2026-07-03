# -*- coding: utf-8 -*-
"""Tests de la fondation D5 — chemin async natif des providers (httpx)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.openai_compat_provider import OpenAICompatibleProvider, SharedAsyncHTTPPool
from core.llm.providers.base import LLMProvider


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_name="test",
        base_url="https://api.test/v1/chat/completions",
        api_key="fake-key",
        model="test-model",
    )


def _fake_client(resp_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=resp_json)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


def test_generate_async_returns_content():
    """generate_async natif renvoie le contenu et fait UN appel réseau awaité."""
    prov = _provider()
    prov._record_usage = MagicMock()  # pas d'I/O DB en test
    client = _fake_client({"choices": [{"message": {"content": "bonjour"}}],
                           "usage": {"total_tokens": 5}})
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        out = asyncio.run(prov.generate_async("sys", "user"))
    assert out == "bonjour"
    client.post.assert_awaited_once()


def test_generate_async_returns_tool_calls_message():
    """Si le LLM renvoie des tool_calls, generate_async renvoie le message complet."""
    prov = _provider()
    prov._record_usage = MagicMock()
    msg = {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "f"}}]}
    client = _fake_client({"choices": [{"message": msg}], "usage": {}})
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        out = asyncio.run(prov.generate_async("sys", "user"))
    assert isinstance(out, dict) and "tool_calls" in out


def test_async_pool_singleton():
    """Le pool async renvoie le même client tant qu'il n'est pas fermé."""
    SharedAsyncHTTPPool._client = None
    c1 = SharedAsyncHTTPPool.get_client()
    c2 = SharedAsyncHTTPPool.get_client()
    assert c1 is c2
    asyncio.run(SharedAsyncHTTPPool.aclose())
    assert SharedAsyncHTTPPool._client is None


def test_base_generate_async_fallback_runs_sync():
    """Le fallback de base exécute generate() (sync) dans un thread."""

    class DummyProvider(LLMProvider):
        def generate(self, system_prompt, user_prompt, **kwargs):
            return f"sync:{user_prompt}"

        def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
            return {}

    prov = DummyProvider()
    out = asyncio.run(prov.generate_async("sys", "salut"))
    assert out == "sync:salut"
