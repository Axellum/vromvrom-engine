"""
test_openai_compat_provider.py — Tests du filtrage des champs privés (#T263).

Le round-trip d'outils Gemini porte la thought_signature dans un champ privé
"_thought_signature" des tool_calls. Ce champ est interne au moteur : il ne
doit JAMAIS partir dans le payload des providers OpenAI-compatibles, qui
peuvent rejeter un champ inconnu (erreur 400). Vérifie que le filtre est
appliqué avant l'envoi HTTP.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ajout du répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.openai_compat_provider import OpenAICompatibleProvider, filtrer_champs_prives

# ──────────────────────────────────────────────────────────────────
# Tests : filtrer_champs_prives (fonction pure)
# ──────────────────────────────────────────────────────────────────

class TestFiltrerChampsPrives:
    """Vérifie le retrait des champs privés (préfixe \"_\") des messages."""

    def test_strip_private_field_from_message(self):
        """Un champ privé au niveau du message est retiré, le reste est intact."""
        messages = [{
            "role": "assistant",
            "content": "Bonjour",
            "_metadata_interne": {"cache": "abc"},
        }]
        nettoyes = filtrer_champs_prives(messages)
        assert "_metadata_interne" not in nettoyes[0]
        assert nettoyes[0]["role"] == "assistant"
        assert nettoyes[0]["content"] == "Bonjour"

    def test_strip_private_field_from_tool_calls(self):
        """La _thought_signature des tool_calls ne doit pas partir à l'extérieur."""
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_gemini_0_123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
                "_thought_signature": "signature-AAAA",
            }],
        }]
        nettoyes = filtrer_champs_prives(messages)
        tc = nettoyes[0]["tool_calls"][0]
        assert "_thought_signature" not in tc
        assert tc["id"] == "call_gemini_0_123"
        assert tc["function"]["name"] == "get_weather"

    def test_strip_does_not_alter_standard_fields(self):
        """Aucun champ standard OpenAI ne commence par \"_\" : rien ne bouge."""
        messages = [{
            "role": "user",
            "content": "Salut",
            "tool_calls": None,
        }]
        nettoyes = filtrer_champs_prives(messages)
        assert nettoyes == messages

    def test_empty_list(self):
        """Une liste vide reste vide, sans exception."""
        assert filtrer_champs_prives([]) == []


# ──────────────────────────────────────────────────────────────────
# Tests : application dans generate() (pas de fuite dans le payload)
# ──────────────────────────────────────────────────────────────────

class TestNoLeakInPayload:
    """Vérifie que le payload HTTP ne contient jamais le champ privé."""

    def _provider(self):
        return OpenAICompatibleProvider(
            provider_name="TestProvider",
            base_url="http://fake.local/v1/chat/completions",
            api_key="cle-test-fake",
            model="modele-test",
        )

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_generate_payload_without_private_field(self, mock_get_session):
        """generate() n'envoie pas la _thought_signature dans le payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Bonjour"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_get_session.return_value.post.return_value = mock_response

        messages = [
            {"role": "user", "content": "Météo à Paris ?"},
            {"role": "assistant", "content": "",
             "tool_calls": [{
                 "id": "call_gemini_0_123",
                 "type": "function",
                 "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
                 "_thought_signature": "signature-AAAA",
             }]},
        ]
        provider = self._provider()
        provider.generate("", "Météo à Paris ?", messages=messages)

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        # Le payload sérialisé ne doit JAMAIS contenir la signature privée
        assert "_thought_signature" not in json.dumps(payload)
        # Le reste du tool_call est bien transmis (format OpenAI standard)
        tc = payload["messages"][1]["tool_calls"][0]
        assert tc["function"]["name"] == "get_weather"

    @patch("core.openai_compat_provider.SharedAsyncHTTPPool.get_client")
    def test_generate_async_payload_without_private_field(self, mock_get_client):
        """generate_async() n'envoie pas la _thought_signature non plus."""
        import asyncio

        async def _run():
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Bonjour"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
            mock_get_client.return_value.post = AsyncMock(return_value=mock_response)

            messages = [{
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_gemini_0_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                    "_thought_signature": "signature-AAAA",
                }],
            }]
            provider = self._provider()
            await provider.generate_async("", "Météo ?", messages=messages)

            payload = mock_get_client.return_value.post.call_args.kwargs["json"]
            assert "_thought_signature" not in json.dumps(payload)

        asyncio.run(_run())
