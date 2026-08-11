"""test_cascade_erreur_structuree.py — #T265.

Une erreur de provider ne doit JAMAIS devenir un succès vide.

Mesuré en prod le 10/08/2026 : `generate_structured()` de gemini_native avalait
toute exception et renvoyait `{}`. `FallbackProvider` recevait une valeur de
retour, donc concluait au succès : pas de bascule sur le modèle suivant, et un
succès enregistré au circuit breaker (le modèle restait « sain » et était
re-choisi). Un 403 sur `gemini-3.5-flash-free` a ainsi tué un plan complet en
2 ms — « Le Planner a généré un DAG vide (plan: []) » — alors que 6 clés Gemini
et toute la cascade attendaient derrière.

Ces tests couvrent les deux niveaux :
- le provider lève bien au lieu de renvoyer {} (les 4 sorties en échec) ;
- la cascade bascule réellement et compte l'échec au circuit breaker.
"""
from unittest.mock import Mock, patch

import pytest
import requests

from core.anthropic_native_provider import AnthropicNativeProvider
from core.gemini_native import GeminiNativeProvider, GeminiStructuredError
from core.llm.circuit_breaker import CircuitBreaker
from core.llm.providers.base import LLMProvider
from core.llm.providers.deepseek import FallbackProvider


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    CircuitBreaker._registry.clear()
    yield
    CircuitBreaker._registry.clear()


@pytest.fixture
def provider():
    return GeminiNativeProvider(
        api_key="test-key-fake",
        model="gemini-3.5-flash",
        enable_explicit_cache=False,
    )


def _reponse(payload: dict) -> Mock:
    """Réponse HTTP 200 mockée portant le corps JSON donné."""
    reponse = Mock()
    reponse.status_code = 200
    reponse.json.return_value = payload
    reponse.raise_for_status = Mock()
    return reponse


# ──────────────────────────────────────────────────────────────────
# Niveau 1 — le provider lève au lieu de renvoyer {}
# ──────────────────────────────────────────────────────────────────

class TestGeminiStructuredLeve:
    """Les 4 sorties en échec de generate_structured() lèvent."""

    @patch("core.gemini_native.requests.post")
    def test_erreur_http_403_est_relevee(self, mock_post, provider):
        """Le cas mesuré en prod : un 403 ne doit pas se transformer en {}."""
        mock_post.side_effect = requests.exceptions.HTTPError(
            "403 Client Error: Forbidden for url: https://...?key=***"
        )

        with pytest.raises(requests.exceptions.HTTPError):
            provider.generate_structured("Sys", "User", schema={})

    @patch("core.gemini_native.requests.post")
    def test_message_429_preserve_pour_la_cascade(self, mock_post, provider):
        """La re-levée doit être à l'identique : la cascade lit le message.

        `FallbackProvider` cherche « 429 » / « rate limit » dans le message pour
        décider de retenter le MÊME modèle avant de basculer. Envelopper
        l'exception dans un type maison casserait cette détection.
        """
        mock_post.side_effect = requests.exceptions.HTTPError(
            "429 Client Error: Too Many Requests"
        )

        with pytest.raises(requests.exceptions.HTTPError) as exc:
            provider.generate_structured("Sys", "User", schema={})

        assert "429" in str(exc.value)

    @patch("core.gemini_native.requests.post")
    def test_json_invalide_leve(self, mock_post, provider):
        mock_post.return_value = _reponse({
            "candidates": [{"content": {"parts": [{"text": "ceci n'est pas du JSON"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        })

        with pytest.raises(GeminiStructuredError, match="JSON invalide"):
            provider.generate_structured("Sys", "User", schema={})

    @patch("core.gemini_native.requests.post")
    def test_aucun_candidat_leve_avec_le_motif(self, mock_post, provider):
        """Prompt bloqué : le motif doit rester diagnosticable dans l'erreur."""
        mock_post.return_value = _reponse({
            "candidates": [],
            "promptFeedback": {"blockReason": "SAFETY"},
        })

        with pytest.raises(GeminiStructuredError, match="SAFETY"):
            provider.generate_structured("Sys", "User", schema={})

    @patch("core.gemini_native.requests.post")
    def test_parts_vides_leve_avec_le_motif(self, mock_post, provider):
        """finishReason=MAX_TOKENS : réponse tronquée, donc inexploitable."""
        mock_post.return_value = _reponse({
            "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}],
        })

        with pytest.raises(GeminiStructuredError, match="MAX_TOKENS"):
            provider.generate_structured("Sys", "User", schema={})

    @patch("core.gemini_native.requests.post")
    def test_reponse_valide_inchangee(self, mock_post, provider):
        """Garde-fou : le chemin nominal ne doit pas être affecté."""
        mock_post.return_value = _reponse({
            "candidates": [{"content": {"parts": [{"text": '{"plan": ["etape"]}'}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        })

        assert provider.generate_structured("Sys", "User", schema={}) == {"plan": ["etape"]}


class TestAnthropicStructuredLeve:
    """Même famille côté Anthropic natif : pas de tool_use → pas un succès."""

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_absence_de_tool_use_leve(self, mock_session):
        reponse = _reponse({"content": [{"type": "text", "text": "blabla"}], "usage": {}})
        mock_session.return_value.post.return_value = reponse

        fournisseur = AnthropicNativeProvider(api_key="test-key-fake", model="claude-fable-5")

        with pytest.raises(RuntimeError, match="tool_use"):
            fournisseur.generate_structured("Sys", "User", schema={})


# ──────────────────────────────────────────────────────────────────
# Niveau 2 — la cascade bascule vraiment (le vrai sujet de #T265)
# ──────────────────────────────────────────────────────────────────

class _ProviderSain(LLMProvider):
    """Provider de secours minimal, deuxième dans la cascade.

    Hérite de `LLMProvider` — comme les vrais providers depuis #T240 : c'est la
    classe de base qui fournit `generate_structured_async()`. Un stub qui ne
    l'hériterait pas rejouerait le bug #T240 (AttributeError avalé) au lieu de
    tester #T265.
    """

    def __init__(self):
        self.appels = 0

    def generate(self, system_prompt, user_prompt, **kwargs):
        return "secours"

    def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
        self.appels += 1
        return {"plan": ["secours"]}


class TestCascadeBascule:

    @patch("core.gemini_native.requests.post")
    def test_403_gemini_fait_basculer_sur_le_modele_suivant(self, mock_post, provider):
        """Le scénario de prod, de bout en bout : 403 → le suivant répond."""
        mock_post.side_effect = requests.exceptions.HTTPError("403 Client Error: Forbidden")
        secours = _ProviderSain()

        cascade = FallbackProvider([
            ("gemini-3.5-flash-free", provider),
            ("modele-de-secours", secours),
        ])

        resultat = cascade.generate_structured("Sys", "User", schema={})

        # Avant #T265 : resultat == {} et secours.appels == 0.
        assert resultat == {"plan": ["secours"]}
        assert secours.appels == 1

    @patch("core.gemini_native.requests.post")
    def test_l_echec_est_compte_au_circuit_breaker(self, mock_post, provider):
        """Sans exception, le CB enregistrait un SUCCÈS pour un appel raté."""
        mock_post.side_effect = requests.exceptions.HTTPError("403 Client Error: Forbidden")

        cascade = FallbackProvider([
            ("gemini-3.5-flash-free", provider),
            ("modele-de-secours", _ProviderSain()),
        ])
        cascade.generate_structured("Sys", "User", schema={})

        cb = CircuitBreaker.get_or_create("gemini-3.5-flash-free")
        assert cb.total_failures >= 1, "l'échec doit être imputé au modèle fautif"
        assert cb.total_calls == 0, "aucun succès ne doit être enregistré"

    @pytest.mark.asyncio
    @patch("core.gemini_native.requests.post")
    async def test_bascule_aussi_sur_le_chemin_async(self, mock_post, provider):
        """C'est le chemin du Planner (`generate_structured_async`)."""
        mock_post.side_effect = requests.exceptions.HTTPError("403 Client Error: Forbidden")
        secours = _ProviderSain()

        cascade = FallbackProvider([
            ("gemini-3.5-flash-free", provider),
            ("modele-de-secours", secours),
        ])

        resultat = await cascade.generate_structured_async("Sys", "User", schema={})

        assert resultat == {"plan": ["secours"]}
        assert secours.appels == 1

    def test_un_dict_vide_ne_declenche_aucune_bascule(self):
        """Caractérisation du mécanisme — la raison d'être du correctif.

        Ce test ne porte pas sur gemini_native mais sur le contrat de la
        cascade : tant qu'un provider RENVOIE quelque chose, même vide, il est
        réputé avoir réussi. C'est pour cela que le correctif devait lever, et
        non renvoyer un dict vide « mieux journalisé ».
        """
        class _ProviderQuiRenvoieVide:
            def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
                return {}

        secours = _ProviderSain()
        cascade = FallbackProvider([
            ("modele-muet", _ProviderQuiRenvoieVide()),
            ("modele-de-secours", secours),
        ])

        assert cascade.generate_structured("Sys", "User", schema={}) == {}
        assert secours.appels == 0
        assert CircuitBreaker.get_or_create("modele-muet").total_calls == 1
