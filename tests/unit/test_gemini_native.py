"""
test_gemini_native.py — Tests unitaires pour le GeminiNativeProvider.

Couvre :
- Construction des payloads (systemInstruction, contents, tools)
- Traduction des tool_calls Google → format OpenAI
- Gestion du cache explicite (GeminiCacheManager)
- Détection du grounding dans les metadata du Router
- JSON mode natif (generate_structured)
- Fallback si cache expiré
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Ajout du répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.gemini_native import GeminiCacheManager, GeminiNativeProvider

# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def provider():
    """Provider natif Gemini avec cache désactivé (tests rapides)."""
    return GeminiNativeProvider(
        api_key="test-key-fake",
        model="gemini-3.5-flash",
        enable_explicit_cache=False
    )


@pytest.fixture
def provider_with_cache():
    """Provider natif Gemini avec cache explicite activé."""
    return GeminiNativeProvider(
        api_key="test-key-fake",
        model="gemini-3.5-flash",
        enable_explicit_cache=True,
        cache_ttl_seconds=3600
    )


@pytest.fixture
def provider_with_grounding():
    """Provider natif Gemini avec Search Grounding débloqué."""
    return GeminiNativeProvider(
        api_key="test-key-paid",
        model="gemini-3.5-flash",
        search_grounding_available=True,
        enable_explicit_cache=False
    )


@pytest.fixture
def cache_manager():
    """Gestionnaire de cache isolé."""
    return GeminiCacheManager(api_key="test-key-fake", model="gemini-3.5-flash")


# ──────────────────────────────────────────────────────────────────
# Tests : Construction des payloads
# ──────────────────────────────────────────────────────────────────

class TestPayloadConstruction:
    """Vérifie que les payloads sont correctement construits au format natif Gemini."""

    def test_build_contents_simple(self, provider):
        """Un simple user_prompt doit produire un contenu avec role=user."""
        contents = provider._build_contents("Bonjour le monde")
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert contents[0]["parts"][0]["text"] == "Bonjour le monde"

    def test_build_contents_from_messages(self, provider):
        """Les messages OpenAI-style doivent être traduits au format natif."""
        messages = [
            {"role": "system", "content": "Tu es un assistant."},
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Salut !"},
            {"role": "user", "content": "Comment ça va ?"},
        ]
        contents = provider._build_contents("", messages=messages)
        # Le system message est exclu (géré par systemInstruction)
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"  # assistant → model
        assert contents[2]["role"] == "user"

    def test_build_system_instruction(self, provider):
        """Le systemInstruction doit encapsuler le prompt dans parts."""
        result = provider._build_system_instruction("Tu es un expert domotique.")
        assert result is not None
        assert result["parts"][0]["text"] == "Tu es un expert domotique."

    def test_build_system_instruction_empty(self, provider):
        """Un system prompt vide retourne None."""
        assert provider._build_system_instruction("") is None
        assert provider._build_system_instruction("   ") is None


# ──────────────────────────────────────────────────────────────────
# Tests : Google Search Grounding
# ──────────────────────────────────────────────────────────────────

class TestSearchGrounding:
    """Vérifie la construction des outils de grounding."""

    def test_no_grounding_by_default(self, provider):
        """Sans flag, pas d'outils ajoutés."""
        tools = provider._build_tools(use_search_grounding=False)
        assert tools is None

    def test_grounding_requires_paid_key(self, provider):
        """Le grounding ne s'active pas si search_grounding_available=False."""
        # provider a search_grounding_available=False
        tools = provider._build_tools(use_search_grounding=True)
        assert tools is None  # Pas de grounding car clé gratuite

    def test_grounding_with_paid_key(self, provider_with_grounding):
        """Le grounding s'active si search_grounding_available=True et flag=True."""
        tools = provider_with_grounding._build_tools(use_search_grounding=True)
        assert tools is not None
        assert {"google_search": {}} in tools

    def test_grounding_with_function_declarations(self, provider_with_grounding):
        """Le grounding peut coexister avec des function declarations."""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Lire un fichier",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}
                }
            }
        ]
        tools = provider_with_grounding._build_tools(
            use_search_grounding=True,
            tools=openai_tools
        )
        assert len(tools) == 2
        assert {"google_search": {}} in tools
        # Vérifier les function declarations
        func_tool = [t for t in tools if "function_declarations" in t][0]
        assert func_tool["function_declarations"][0]["name"] == "read_file"


# ──────────────────────────────────────────────────────────────────
# Tests : Traduction des tool_calls
# ──────────────────────────────────────────────────────────────────

class TestToolCallsTranslation:
    """Vérifie la traduction des function_calls du format Gemini natif vers OpenAI."""

    def test_translate_simple_tool_call(self, provider):
        """Un functionCall Gemini natif doit être traduit au format OpenAI."""
        gemini_parts = [
            {
                "functionCall": {
                    "name": "write_file",
                    "args": {"filepath": "test.py", "content": "print('hello')"}
                }
            }
        ]
        result = provider._translate_tool_calls(gemini_parts)
        assert result is not None
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "write_file"
        # Les arguments doivent être un JSON string (format OpenAI)
        args = json.loads(result[0]["function"]["arguments"])
        assert args["filepath"] == "test.py"

    def test_translate_multiple_tool_calls(self, provider):
        """Plusieurs functionCalls dans la même réponse."""
        gemini_parts = [
            {"functionCall": {"name": "read_file", "args": {"path": "a.py"}}},
            {"functionCall": {"name": "read_file", "args": {"path": "b.py"}}},
        ]
        result = provider._translate_tool_calls(gemini_parts)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "read_file"
        assert result[1]["function"]["name"] == "read_file"
        # IDs uniques
        assert result[0]["id"] != result[1]["id"]

    def test_no_tool_calls(self, provider):
        """Des parts sans functionCall retournent None."""
        gemini_parts = [{"text": "Bonjour !"}]
        result = provider._translate_tool_calls(gemini_parts)
        assert result is None


# ──────────────────────────────────────────────────────────────────
# Tests : round-trip de la thought_signature (#T263)
# ──────────────────────────────────────────────────────────────────

class TestThoughtSignatureRoundTrip:
    """
    Vérifie la capture puis la ré-émission de la thought_signature.

    L'API REST Gemini renvoie le champ "thoughtSignature" (camelCase, observé
    en conditions réelles le 10/08/2026) au niveau de la part functionCall.
    Il doit être ré-émis à l'identique au tour suivant, sinon l'API répond
    400. Le champ interne porte la clé privée "_thought_signature" (filtrée
    avant envoi chez les providers OpenAI-compatibles).
    """

    SIG_A = "signature-AAAA"
    SIG_B = "signature-BBBB"

    def test_translate_captures_signature(self, provider):
        """Une part avec thoughtSignature doit la porter dans le tool_call."""
        gemini_parts = [{
            "functionCall": {"name": "get_weather", "args": {"city": "Paris"}},
            "thoughtSignature": self.SIG_A,
        }]
        result = provider._translate_tool_calls(gemini_parts)
        assert result[0]["_thought_signature"] == self.SIG_A
        # Le format OpenAI standard reste intact
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"

    def test_translate_multiple_calls_keep_own_signature(self, provider):
        """Chaque functionCall doit retrouver SA signature (pas celle du voisin)."""
        gemini_parts = [
            {"functionCall": {"name": "read_file", "args": {"path": "a.py"}},
             "thoughtSignature": self.SIG_A},
            {"functionCall": {"name": "read_file", "args": {"path": "b.py"}},
             "thoughtSignature": self.SIG_B},
        ]
        result = provider._translate_tool_calls(gemini_parts)
        assert len(result) == 2
        assert result[0]["_thought_signature"] == self.SIG_A
        assert result[1]["_thought_signature"] == self.SIG_B

    def test_translate_missing_signature_tolerated(self, provider):
        """Une part sans signature (modèle ancien) ne doit rien casser."""
        gemini_parts = [
            {"functionCall": {"name": "read_file", "args": {"path": "a.py"}}},
            {"functionCall": {"name": "read_file", "args": {"path": "b.py"}},
             "thoughtSignature": self.SIG_A},
        ]
        result = provider._translate_tool_calls(gemini_parts)
        assert len(result) == 2
        assert "_thought_signature" not in result[0]
        assert result[1]["_thought_signature"] == self.SIG_A

    def test_build_contents_reemits_signature(self, provider):
        """Le tour suivant doit ré-émettre la signature au niveau de la part."""
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_gemini_0_123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": json.dumps({"city": "Paris"})
                },
                "_thought_signature": self.SIG_A,
            }],
        }]
        contents = provider._build_contents("", messages=messages)
        part = contents[0]["parts"][0]
        assert "functionCall" in part
        assert part["functionCall"]["name"] == "get_weather"
        # La signature est une sœur de functionCall, comme dans la réponse réelle
        assert part["thoughtSignature"] == self.SIG_A

    def test_build_contents_multiple_calls_each_own_signature(self, provider):
        """Deux tool_calls → deux parts, chacune avec SA signature."""
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"},
                 "_thought_signature": self.SIG_A},
                {"id": "c2", "type": "function",
                 "function": {"name": "read_file", "arguments": "{\"path\": \"b.py\"}"},
                 "_thought_signature": self.SIG_B},
            ],
        }]
        contents = provider._build_contents("", messages=messages)
        parts = contents[0]["parts"]
        assert len(parts) == 2
        assert parts[0]["thoughtSignature"] == self.SIG_A
        assert parts[1]["thoughtSignature"] == self.SIG_B

    def test_build_contents_missing_signature_tolerated(self, provider):
        """Sans signature interne, la part est émise sans thoughtSignature."""
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_gemini_0_123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
            }],
        }]
        contents = provider._build_contents("", messages=messages)
        part = contents[0]["parts"][0]
        assert "functionCall" in part
        assert "thoughtSignature" not in part

    def test_full_round_trip_preserves_signature(self, provider):
        """Traduction → exécution → ré-émission : la part du tour 2 est fidèle."""
        # Tour 1 : réponse Gemini avec signature
        gemini_parts = [{
            "functionCall": {"name": "get_weather", "args": {"city": "Paris"}},
            "thoughtSignature": self.SIG_A,
        }]
        tool_calls = provider._translate_tool_calls(gemini_parts)

        # Le moteur exécute l'outil et reconstruit les messages du tour 2
        messages = [
            {"role": "user", "content": "Météo à Paris ?"},
            {"role": "assistant", "content": "", "tool_calls": tool_calls},
            {"role": "tool", "tool_call_id": tool_calls[0]["id"],
             "name": "get_weather", "content": "{\"temperature\": 21}"},
        ]
        contents = provider._build_contents("", messages=messages)

        # La part du tour 2 doit porter la même signature que le tour 1
        model_parts = [p for m in contents if m["role"] == "model"
                       for p in m["parts"]]
        fc_parts = [p for p in model_parts if "functionCall" in p]
        assert len(fc_parts) == 1
        assert fc_parts[0]["thoughtSignature"] == self.SIG_A
        assert fc_parts[0]["functionCall"] == {
            "name": "get_weather",
            "args": {"city": "Paris"},
        }


# ──────────────────────────────────────────────────────────────────
# Tests : GeminiCacheManager
# ──────────────────────────────────────────────────────────────────

class TestCacheManager:
    """Vérifie le cycle de vie du cache explicite."""

    def test_initial_state(self, cache_manager):
        """Le cache démarre inactif."""
        status = cache_manager.get_status()
        assert status["active"] is False
        assert status["cache_name"] is None

    @patch("core.gemini_native.requests.post")
    def test_create_cache_success(self, mock_post, cache_manager):
        """Création réussie d'un cache → nom et TTL enregistrés."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "cachedContents/test123",
            "usageMetadata": {"totalTokenCount": 5000}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = cache_manager.create("Un long contenu de test..." * 100, ttl_seconds=1800)

        assert result == "cachedContents/test123"
        assert cache_manager._active_cache_name == "cachedContents/test123"
        status = cache_manager.get_status()
        assert status["active"] is True
        assert status["ttl_remaining_seconds"] > 0

    @patch("core.gemini_native.requests.post")
    def test_create_cache_deduplicate(self, mock_post, cache_manager):
        """Le même contenu ne crée pas un nouveau cache si l'ancien est valide."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "cachedContents/test123",
            "usageMetadata": {"totalTokenCount": 5000}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        content = "Contenu identique pour les deux appels" * 100
        cache_manager.create(content, ttl_seconds=3600)

        # Le deuxième appel ne doit PAS re-créer le cache
        result2 = cache_manager.create(content, ttl_seconds=3600)
        assert result2 == "cachedContents/test123"
        # Un seul appel HTTP POST (le premier)
        assert mock_post.call_count == 1

    def test_cache_expiration(self, cache_manager):
        """Un cache expiré retourne None pour get_active_cache_name()."""
        cache_manager._active_cache_name = "cachedContents/expired"
        cache_manager._cache_expire_time = time.time() - 10  # Expiré il y a 10s

        result = cache_manager.get_active_cache_name()
        assert result is None
        assert cache_manager._active_cache_name is None

    @patch("core.gemini_native.requests.delete")
    def test_delete_cache(self, mock_delete, cache_manager):
        """La suppression nettoie l'état interne."""
        mock_delete.return_value = Mock(status_code=200)
        cache_manager._active_cache_name = "cachedContents/todelete"
        cache_manager._cache_expire_time = time.time() + 3600
        cache_manager._cache_content_hash = "abc123"

        cache_manager.delete()

        assert cache_manager._active_cache_name is None
        assert cache_manager._cache_expire_time is None
        assert cache_manager._cache_content_hash is None


# ──────────────────────────────────────────────────────────────────
# Tests : generate() avec mock HTTP
# ──────────────────────────────────────────────────────────────────

class TestGenerate:
    """Vérifie generate() avec des réponses HTTP mockées."""

    @patch("core.gemini_native.requests.post")
    def test_generate_text_response(self, mock_post, provider):
        """generate() retourne le texte de la réponse."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Bonjour ! Je suis Gemini natif."}]
                }
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 8,
                "cachedContentTokenCount": 0,
            }
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = provider.generate("Tu es un assistant.", "Bonjour")
        assert isinstance(result, str)
        assert "Gemini natif" in result

        # Vérifier que systemInstruction est dans le payload envoyé
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "systemInstruction" in payload
        assert payload["systemInstruction"]["parts"][0]["text"] == "Tu es un assistant."

    @patch("core.gemini_native.requests.post")
    def test_generate_tool_call_response(self, mock_post, provider):
        """generate() retourne un dict avec tool_calls si le modèle appelle une fonction."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "write_file",
                            "args": {"filepath": "test.py", "content": "print('hello')"}
                        }
                    }]
                }
            }],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 15}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = provider.generate("Tu es un assistant.", "Cree test.py")
        assert isinstance(result, dict)
        assert "tool_calls" in result
        assert result["tool_calls"][0]["function"]["name"] == "write_file"

    @patch("core.gemini_native.requests.post")
    def test_generate_with_grounding_flag(self, mock_post, provider_with_grounding):
        """generate() avec use_search_grounding=True ajoute google_search dans tools."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Il fait 25 degres."}]}}],
            "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 10}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        provider_with_grounding.generate(
            "Tu es un assistant meteo.", "Quelle meteo a Paris ?",
            use_search_grounding=True
        )

        # Vérifier que tools contient google_search dans le payload
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "tools" in payload
        assert {"google_search": {}} in payload["tools"]


# ──────────────────────────────────────────────────────────────────
# Tests : generate_structured() avec mock HTTP
# ──────────────────────────────────────────────────────────────────

class TestGenerateStructured:
    """Vérifie generate_structured() avec JSON mode natif."""

    @patch("core.gemini_native.requests.post")
    def test_json_mode_native(self, mock_post, provider):
        """generate_structured() utilise responseMimeType dans le payload."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '{"category": "domotique", "confidence": 0.95}'}]
                }
            }],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 15}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = provider.generate_structured(
            "Classifie.", "Allume la lumiere",
            schema={"type": "object", "properties": {"category": {"type": "string"}}}
        )

        assert isinstance(result, dict)
        assert result["category"] == "domotique"
        assert result["confidence"] == 0.95

        # Vérifier que responseMimeType est dans le payload
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["generationConfig"]["responseMimeType"] == "application/json"

    @patch("core.gemini_native.requests.post")
    def test_json_mode_with_markdown_cleanup(self, mock_post, provider):
        """generate_structured() nettoie le markdown autour du JSON."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '```json\n{"ok": true}\n```'}]
                }
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = provider.generate_structured("Test.", "Test", schema={})
        assert result == {"ok": True}


# ──────────────────────────────────────────────────────────────────
# Tests : Intégration Router (détection grounding)
# ──────────────────────────────────────────────────────────────────

class TestRouterGrounding:
    """Vérifie que le Router détecte les mots-clés de données fraîches."""

    async def test_meteo_triggers_grounding(self):
        """Le mot 'météo' doit activer le flag use_search_grounding."""
        from core.router import Router
        router = Router()
        router.rag_engine = None
        router.context_loader = MagicMock()
        router.context_loader.load_all.return_value = None
        router.context_loader.reload_if_stale.return_value = None
        router.context_loader.get_context_for_categories.return_value = ""

        payload, agent = await router.analyze_request("Quelle est la météo à Paris aujourd'hui ?")
        assert payload.metadata.get("use_search_grounding") is True

    async def test_normal_request_no_grounding(self):
        """Une requête normale ne doit pas activer le grounding."""
        from core.router import Router
        router = Router()
        router.rag_engine = None
        router.context_loader = MagicMock()
        router.context_loader.load_all.return_value = None
        router.context_loader.reload_if_stale.return_value = None
        router.context_loader.get_context_for_categories.return_value = ""

        payload, agent = await router.analyze_request("Explique moi le fonctionnement du routeur")
        assert payload.metadata.get("use_search_grounding") is False
