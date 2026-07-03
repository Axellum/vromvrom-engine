"""
tests/unit/test_ollama_deck.py
Tests unitaires pour OllamaDeckProvider et la méthode get_deck_provider() de LLMGateway.
Les tests mockent requests pour ne pas dépendre du réseau.
"""
import pytest
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ollama_provider():
    """Crée un OllamaDeckProvider avec l'URL par défaut."""
    from core.llm_gateway import OllamaDeckProvider
    return OllamaDeckProvider()


@pytest.fixture
def mock_response_ok():
    """Simule une réponse HTTP 200 d'Ollama avec un token de réponse."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "choices": [{"message": {"content": "Réponse de test Ollama"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock.raise_for_status = MagicMock()
    return mock


@pytest.fixture
def mock_tags_ok():
    """Simule une réponse HTTP 200 de /api/tags (Ollama disponible)."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"models": [{"name": "phi3:mini"}]}
    return mock


# ──────────────────────────────────────────────────────────────────
# Tests OllamaDeckProvider
# ──────────────────────────────────────────────────────────────────

class TestOllamaDeckProvider:
    """Tests unitaires pour OllamaDeckProvider."""

    def test_init_url_defaut(self, ollama_provider):
        """Vérifie que l'URL par défaut pointe bien vers le Deck (IP Ethernet)."""
        assert "${OLLAMA_HOST:-localhost}" in ollama_provider.base_url
        assert "11434" in ollama_provider.base_url
        assert ollama_provider.model_name == "phi3:mini"

    def test_init_url_custom(self):
        """Vérifie qu'on peut surcharger l'hôte et le modèle."""
        from core.llm_gateway import OllamaDeckProvider
        p = OllamaDeckProvider(host="192.168.0.139", port=11434, model_name="gemma2:2b")
        assert "192.168.0.139" in p.base_url
        assert p.model_name == "gemma2:2b"

    def test_ping_available_deck_joignable(self, ollama_provider, mock_tags_ok):
        """Test ping_available() quand Ollama répond correctement."""
        with patch("requests.get", return_value=mock_tags_ok) as mock_get:
            result = ollama_provider.ping_available()
        assert result is True

    def test_ping_available_deck_hors_ligne(self, ollama_provider):
        """Test ping_available() quand le Deck est éteint (timeout)."""
        import requests
        with patch("requests.get", side_effect=requests.exceptions.ConnectTimeout):
            result = ollama_provider.ping_available()
        assert result is False

    def test_ping_available_basculement_wifi(self):
        """Test que ping_available() bascule automatiquement vers l'IP Wi-Fi si Ethernet échoue."""
        import requests
        from core.llm_gateway import OllamaDeckProvider
        p = OllamaDeckProvider(host="${OLLAMA_HOST:-localhost}")

        mock_tags_wifi = MagicMock()
        mock_tags_wifi.status_code = 200

        def mock_get_side_effect(url, **kwargs):
            if "${OLLAMA_HOST:-localhost}" in url:
                raise requests.exceptions.ConnectTimeout
            return mock_tags_wifi  # Wi-Fi répond

        with patch("requests.get", side_effect=mock_get_side_effect):
            result = p.ping_available()

        assert result is True
        # Vérifier que l'IP a bien basculé vers le Wi-Fi
        assert "192.168.0.139" in p.base_url

    def test_generate_reponse_ok(self, ollama_provider, mock_response_ok):
        """Test generate() avec une réponse Ollama valide."""
        # On mock la session HTTP (SharedHTTPPool ou requests direct)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response_ok
        with patch("core.llm.providers.deepseek.SharedHTTPPool") as mock_pool:
            mock_pool.get_session.return_value = mock_session
            with patch("core.llm.providers.deepseek._USE_HTTP_POOL", True):
                with patch("core.token_tracker.record_usage"):
                    result = ollama_provider.generate("Système", "Question test")
        assert result == "Réponse de test Ollama"

    def test_generate_deck_hors_ligne(self, ollama_provider):
        """Test que generate() lève une RuntimeError si le Deck est hors ligne."""
        import requests
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.ConnectTimeout
        with patch("core.llm.providers.deepseek.SharedHTTPPool") as mock_pool:
            mock_pool.get_session.return_value = mock_session
            with patch("core.llm.providers.deepseek._USE_HTTP_POOL", True):
                with pytest.raises(RuntimeError, match="Timeout de connexion"):
                    ollama_provider.generate("Système", "Question test")

    def test_generate_structured_json_valide(self, ollama_provider):
        """Test generate_structured() avec une réponse JSON valide d'Ollama."""
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {
            "choices": [{"message": {"content": '{"result": "ok", "score": 0.95}'}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 12},
        }
        mock_session = MagicMock()
        mock_session.post.return_value = mock
        with patch("core.llm.providers.deepseek.SharedHTTPPool") as mock_pool:
            mock_pool.get_session.return_value = mock_session
            with patch("core.llm.providers.deepseek._USE_HTTP_POOL", True):
                with patch("core.token_tracker.record_usage"):
                    result = ollama_provider.generate_structured("Sys", "Question", {})
        assert result == {"result": "ok", "score": 0.95}

    def test_generate_structured_json_invalide(self, ollama_provider):
        """Test que generate_structured() retourne {} si Ollama ne retourne pas de JSON valide."""
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {
            "choices": [{"message": {"content": "Réponse non-JSON du modèle"}}],
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.post.return_value = mock
        with patch("core.llm.providers.deepseek.SharedHTTPPool") as mock_pool:
            mock_pool.get_session.return_value = mock_session
            with patch("core.llm.providers.deepseek._USE_HTTP_POOL", True):
                result = ollama_provider.generate_structured("Sys", "Question", {})
        assert result == {}

    def test_modele_transmis_dans_payload(self, ollama_provider, mock_response_ok):
        """Vérifie que le model_name est bien transmis dans le payload Ollama."""
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response_ok
        with patch("core.llm.providers.deepseek.SharedHTTPPool") as mock_pool:
            mock_pool.get_session.return_value = mock_session
            with patch("core.llm.providers.deepseek._USE_HTTP_POOL", True):
                with patch("core.token_tracker.record_usage"):
                    ollama_provider.generate("Sys", "Test")
        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        assert payload.get("model") == "phi3:mini"


# ──────────────────────────────────────────────────────────────────
# Tests LLMGateway.get_deck_provider()
# ──────────────────────────────────────────────────────────────────

class TestGetDeckProvider:
    """Tests pour la méthode get_deck_provider() de LLMGateway."""

    def test_get_deck_provider_disponible(self):
        """Test que get_deck_provider() retourne un provider quand le Deck répond."""
        from core.llm_gateway import OllamaDeckProvider
        # Créer un mock de la Gateway minimal
        gw = MagicMock()
        gw.providers = {
            "deck_ollama": OllamaDeckProvider(),
            "deck_ollama_phi3": OllamaDeckProvider(model_name="phi3:mini"),
            "deck_ollama_gemma": OllamaDeckProvider(model_name="gemma2:2b"),
            "deck_ollama_llama": OllamaDeckProvider(model_name="llama3.2:3b"),
        }
        # Remplacer la méthode par la vraie
        from core.llm_gateway import LLMGateway
        gw.get_deck_provider = LLMGateway.get_deck_provider.__get__(gw)

        with patch.object(OllamaDeckProvider, "ping_available", return_value=True):
            result = gw.get_deck_provider()

        assert result is not None
        assert isinstance(result, OllamaDeckProvider)

    def test_get_deck_provider_hors_ligne(self):
        """Test que get_deck_provider() retourne None quand le Deck est hors ligne."""
        from core.llm_gateway import OllamaDeckProvider, LLMGateway
        gw = MagicMock()
        gw.providers = {"deck_ollama": OllamaDeckProvider()}
        gw.get_deck_provider = LLMGateway.get_deck_provider.__get__(gw)

        with patch.object(OllamaDeckProvider, "ping_available", return_value=False):
            result = gw.get_deck_provider()

        assert result is None
