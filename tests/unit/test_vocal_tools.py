"""Tests unitaires allowlist outils vocaux (#discussion Cerebras)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.vocal_tools import (
    _tool_ha_call_service,
    dispatch_vocal_tool,
    provider_supports_openai_tools,
)


def test_ha_call_service_refuse_homeassistant_restart():
    out = _tool_ha_call_service("light.salon", "homeassistant.restart", {})
    assert "Refusé" in out


def test_ha_call_service_refuse_lock():
    out = _tool_ha_call_service("lock.porte", "lock.unlock", {})
    assert "Refusé" in out


def test_ha_call_service_allow_light(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "[]"

    with patch("requests.post", return_value=mock_resp) as post:
        with patch.dict("os.environ", {"HASS_TOKEN": "tok", "HASS_URL": "http://ha", "HA_VERIFY_TLS": "false"}):
            out = _tool_ha_call_service("light.chambre", "light.turn_on", {"brightness": 120})
    assert out.startswith("OK:")
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0].endswith("/api/services/light/turn_on")
    assert kwargs["json"]["entity_id"] == "light.chambre"
    assert kwargs["json"]["brightness"] == 120
    assert kwargs.get("verify") is False


def test_provider_supports_unwrap_fallback():
    class OpenAICompatibleProvider:
        pass

    from core.llm.providers.deepseek import ClaudeInstructionsWrapper, FallbackProvider

    inner = OpenAICompatibleProvider()
    wrapped = ClaudeInstructionsWrapper(inner)
    fb = FallbackProvider([("gpt-oss-120b", wrapped)])
    assert provider_supports_openai_tools(fb) is True
    assert provider_supports_openai_tools(wrapped) is True
    assert provider_supports_openai_tools(inner) is True


@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    out = await dispatch_vocal_tool("run_terminal", {}, session_id="t")
    assert "non autorisé" in out.lower() or "inconnu" in out.lower()
