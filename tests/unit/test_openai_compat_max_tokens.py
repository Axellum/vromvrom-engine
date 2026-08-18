"""
test_openai_compat_max_tokens.py — Plafond de sortie par défaut (#T316).

Mesuré en prod le 16/08 : sur `moteur_runtime.db`, six lignes de `token_usage`
portent EXACTEMENT 40000 `completion_tokens`, toutes sur `gemma-4-31b` (Cerebras).
L'écart de temps avec l'appel précédent (22 s / 14 s / 19 s / 57 s / 19 s) prouve
que le modèle a VRAIMENT produit 40000 tokens : il est parti en boucle et n'a été
arrêté que par le plafond par défaut du provider. Le moteur, lui, n'envoie aucun
`max_tokens` aux providers OpenAI-compatibles (core/llm_gateway.py n'en pose
jamais) : rien ne borne la génération.

On pose donc un plafond de sortie PAR DÉFAUT sur les trois méthodes de
`core/openai_compat_provider.py`, dérivé du `context_output` du catalogue
(core.models_db.get_model), avec un repli configurable par la variable
d'environnement `MOTEUR_MAX_OUTPUT_TOKENS` pour les modèles absents du catalogue
ou à `context_output` nul.

Règles vérifiées ici :
  - SANS `max_tokens` fourni → le payload CONTIENT `max_tokens`, égal au
    `context_output` du catalogue pour ce modèle.
  - AVEC `max_tokens=30000` → le payload porte bien 30000, jamais le défaut
    (le proxy /v1 relaie le `max_tokens` de son client : on ne l'écrase pas).
  - Les trois méthodes sont couvertes : generate, generate_async, streaming.
  - Modèle ABSENT du catalogue → le repli s'applique, aucune exception.

Aucun appel réseau réel : le payload est capturé via un double de session HTTP.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ajout du répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.openai_compat_provider as oacp
from core.openai_compat_provider import OpenAICompatibleProvider

# Modèle du catalogue : `context_output` = 4096 (miroir de gemma-4-31b-cerebras).
_MODELE_CATALOGUE = "gemma-4-31b-cerebras"
_CONTEXT_OUTPUT = 4096
_MODELE_INCONNU = "modele-absente-du-catalogue"


def _provider(model: str = _MODELE_CATALOGUE) -> OpenAICompatibleProvider:
    """Provider OpenAI-compatible de test, sans clé ni appel réseau réel."""
    return OpenAICompatibleProvider(
        provider_name="TestProvider",
        base_url="http://fake.local/v1/chat/completions",
        api_key="cle-test-fake",
        model=model,
    )


def _reponse_ok():
    """Réponse HTTP factice : un message texte + un usage minimal."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": "Bonjour"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    return resp


# ──────────────────────────────────────────────────────────────────
# Tests : generate()
# ──────────────────────────────────────────────────────────────────

class TestGenerateDefaut:
    """generate() : plafond par défaut dérivé du catalogue."""

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_sans_max_tokens_utilise_context_output_catalogue(
        self, mock_get_model, mock_get_session
    ):
        """SANS max_tokens → le payload porte le context_output du catalogue."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        mock_get_session.return_value.post.return_value = _reponse_ok()

        provider = _provider()
        provider.generate("", "Salut")

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == _CONTEXT_OUTPUT
        mock_get_model.assert_called_once_with(_MODELE_CATALOGUE)

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_avec_max_tokens_ne_ecrase_pas(self, mock_get_model, mock_get_session):
        """AVEC max_tokens=30000 → le payload porte 30000, pas le défaut."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        mock_get_session.return_value.post.return_value = _reponse_ok()

        provider = _provider()
        provider.generate("", "Salut", max_tokens=30000)

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 30000
        # Le catalogue ne doit PAS être consulté quand l'appelant fournit max_tokens.
        mock_get_model.assert_not_called()

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_modele_absent_du_catalogue_utilise_repli(
        self, mock_get_model, mock_get_session
    ):
        """Modèle absent du catalogue → repli MOTEUR_MAX_OUTPUT_TOKENS, sans exception."""
        mock_get_model.return_value = None
        mock_get_session.return_value.post.return_value = _reponse_ok()
        with patch.dict(os.environ, {"MOTEUR_MAX_OUTPUT_TOKENS": "1234"}, clear=False):
            provider = _provider(_MODELE_INCONNU)
            provider.generate("", "Salut")

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 1234

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_repli_par_defaut_sans_env(self, mock_get_model, mock_get_session):
        """Sans env ni catalogue → repli sur _DEFAULT_MAX_OUTPUT_TOKENS."""
        mock_get_model.return_value = None
        mock_get_session.return_value.post.return_value = _reponse_ok()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOTEUR_MAX_OUTPUT_TOKENS", None)
            provider = _provider(_MODELE_INCONNU)
            provider.generate("", "Salut")

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == oacp._DEFAULT_MAX_OUTPUT_TOKENS


# ──────────────────────────────────────────────────────────────────
# Tests : generate_async()
# ──────────────────────────────────────────────────────────────────

class TestGenerateAsyncDefaut:
    """generate_async() : même plafond par défaut que generate()."""

    @patch("core.openai_compat_provider.SharedAsyncHTTPPool.get_client")
    @patch("core.models_db.get_model")
    def test_sans_max_tokens_utilise_context_output_catalogue(
        self, mock_get_model, mock_get_client
    ):
        """SANS max_tokens → le payload async porte le context_output du catalogue."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        mock_get_client.return_value.post = AsyncMock(return_value=_reponse_ok())

        async def _run():
            provider = _provider()
            await provider.generate_async("", "Salut")

        asyncio.run(_run())
        payload = mock_get_client.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == _CONTEXT_OUTPUT

    @patch("core.openai_compat_provider.SharedAsyncHTTPPool.get_client")
    @patch("core.models_db.get_model")
    def test_avec_max_tokens_ne_ecrase_pas(self, mock_get_model, mock_get_client):
        """AVEC max_tokens=30000 → le payload async porte 30000, pas le défaut."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        mock_get_client.return_value.post = AsyncMock(return_value=_reponse_ok())

        async def _run():
            provider = _provider()
            await provider.generate_async("", "Salut", max_tokens=30000)

        asyncio.run(_run())
        payload = mock_get_client.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 30000
        mock_get_model.assert_not_called()

    @patch("core.openai_compat_provider.SharedAsyncHTTPPool.get_client")
    @patch("core.models_db.get_model")
    def test_modele_absent_du_catalogue_utilise_repli(
        self, mock_get_model, mock_get_client
    ):
        """Modèle absent du catalogue → repli env, sans exception, en async."""
        mock_get_model.return_value = None
        mock_get_client.return_value.post = AsyncMock(return_value=_reponse_ok())

        async def _run():
            with patch.dict(os.environ, {"MOTEUR_MAX_OUTPUT_TOKENS": "4321"}, clear=False):
                provider = _provider(_MODELE_INCONNU)
                await provider.generate_async("", "Salut")

        asyncio.run(_run())
        payload = mock_get_client.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 4321


# ──────────────────────────────────────────────────────────────────
# Tests : generate_structured()
# ──────────────────────────────────────────────────────────────────

class TestGenerateStructuredDefaut:
    """generate_structured() : même plafond par défaut."""

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_sans_max_tokens_utilise_context_output_catalogue(
        self, mock_get_model, mock_get_session
    ):
        """SANS max_tokens → le payload structuré porte le context_output."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        resp = _reponse_ok()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_get_session.return_value.post.return_value = resp

        provider = _provider()
        provider.generate_structured("", "Salut", {"type": "object"})

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == _CONTEXT_OUTPUT

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_avec_max_tokens_ne_ecrase_pas(self, mock_get_model, mock_get_session):
        """AVEC max_tokens=30000 → le payload structuré porte 30000."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        resp = _reponse_ok()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_get_session.return_value.post.return_value = resp

        provider = _provider()
        provider.generate_structured("", "Salut", {"type": "object"}, max_tokens=30000)

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 30000
        mock_get_model.assert_not_called()

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_modele_absent_du_catalogue_utilise_repli(
        self, mock_get_model, mock_get_session
    ):
        """Modèle absent du catalogue → repli env, sans exception, en structuré."""
        mock_get_model.return_value = None
        resp = _reponse_ok()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_get_session.return_value.post.return_value = resp

        with patch.dict(os.environ, {"MOTEUR_MAX_OUTPUT_TOKENS": "999"}, clear=False):
            provider = _provider(_MODELE_INCONNU)
            provider.generate_structured("", "Salut", {"type": "object"})

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 999


# ──────────────────────────────────────────────────────────────────
# Tests : generate_stream() (chemin streaming)
# ──────────────────────────────────────────────────────────────────

class TestGenerateStreamDefaut:
    """generate_stream() : même plafond par défaut sur le payload stream."""

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_sans_max_tokens_utilise_context_output_catalogue(
        self, mock_get_model, mock_get_session
    ):
        """SANS max_tokens → le payload stream porte le context_output."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        # Flux SSE factice : un chunk de contenu puis [DONE].
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Bon"}}]}',
            "data: [DONE]",
        ]
        mock_get_session.return_value.post.return_value = resp

        provider = _provider()
        list(provider.generate_stream("", "Salut"))

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["stream"] is True
        assert payload["max_tokens"] == _CONTEXT_OUTPUT

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_avec_max_tokens_ne_ecrase_pas(self, mock_get_model, mock_get_session):
        """AVEC max_tokens=30000 → le payload stream porte 30000."""
        mock_get_model.return_value = {"context_output": _CONTEXT_OUTPUT}
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Bon"}}]}',
            "data: [DONE]",
        ]
        mock_get_session.return_value.post.return_value = resp

        provider = _provider()
        list(provider.generate_stream("", "Salut", max_tokens=30000))

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 30000
        mock_get_model.assert_not_called()

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_modele_absent_du_catalogue_utilise_repli(
        self, mock_get_model, mock_get_session
    ):
        """Modèle absent du catalogue → repli env, sans exception, en stream."""
        mock_get_model.return_value = None
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "Bon"}}]}',
            "data: [DONE]",
        ]
        mock_get_session.return_value.post.return_value = resp

        with patch.dict(os.environ, {"MOTEUR_MAX_OUTPUT_TOKENS": "777"}, clear=False):
            provider = _provider(_MODELE_INCONNU)
            list(provider.generate_stream("", "Salut"))

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 777


# ──────────────────────────────────────────────────────────────────
# Tests : le repli ne s'applique jamais quand context_output est nul
# ──────────────────────────────────────────────────────────────────

class TestContextOutputNul:
    """context_output nul → repli, pas de plafond invalide."""

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    @patch("core.models_db.get_model")
    def test_context_output_nul_utilise_repli(self, mock_get_model, mock_get_session):
        """context_output nul → repli MOTEUR_MAX_OUTPUT_TOKENS."""
        mock_get_model.return_value = {"context_output": None}
        mock_get_session.return_value.post.return_value = _reponse_ok()
        with patch.dict(os.environ, {"MOTEUR_MAX_OUTPUT_TOKENS": "2048"}, clear=False):
            provider = _provider()
            provider.generate("", "Salut")

        payload = mock_get_session.return_value.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 2048
