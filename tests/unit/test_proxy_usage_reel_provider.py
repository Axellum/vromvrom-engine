"""
tests/unit/test_proxy_usage_reel_provider.py — L'usage RÉEL du provider arrive
au client du proxy /v1 (complète #T357).

#T357 a posé le contrat côté proxy : le champ `usage` ne porte que des
comptages réels, sinon il est omis. Restait le défaut en amont :
`OpenAICompatibleProvider.generate_stream()` capturait l'usage réel
(`stream_options.include_usage`) pour l'écrire en base… puis yieldait
`usage: None` ; et `generate()` l'enregistrait en base avant de rendre une
simple chaîne — l'information était jetée juste en dessous de là où le proxy
aurait pu la lire.

Ce fichier vérifie les deux chemins de bout en bout, avec un VRAI
`OpenAICompatibleProvider` dont la couche HTTP est simulée (aucun appel
réseau), servi par la route `/v1/chat/completions` réelle :

- streaming : le chunk final porte l'usage réel TEL QUEL (aucune valeur
  recalculée), et rien quand le provider ignore `include_usage` — l'estimation
  de repli calculée pour la base ne remonte JAMAIS au client ;
- bufferisé : l'usage réel transite par le canal latéral `_usage_sink` (un
  dict propre à la requête, jamais d'état partagé sur l'instance), sans aucun
  changement pour les appelants qui attendent une chaîne ;
- `record_usage` est appelé EXACTEMENT UNE FOIS par requête : faire remonter
  l'usage ne doit pas provoquer une seconde écriture en base.

Aucun comptage n'est estimé.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ajout du répertoire parent au PYTHONPATH (motif des tests existants)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.routes import openai_proxy
from core.llm.providers.deepseek import FallbackProvider
from core.openai_compat_provider import OpenAICompatibleProvider

USAGE_REEL = {
    "prompt_tokens": 67523,
    "completion_tokens": 412,
    "total_tokens": 67935,
    "prompt_cache_hit_tokens": 65000,
}


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _chunks_sse(corps: str) -> list[dict]:
    """Tous les chunks JSON d'une réponse SSE (hors [DONE])."""
    chunks = []
    for ligne in corps.splitlines():
        if not ligne.startswith("data: "):
            continue
        donnees = ligne[len("data: "):]
        if donnees != "[DONE]":
            chunks.append(json.loads(donnees))
    return chunks


def _lignes_sse(usage_final: dict | None, avec_done: bool = True) -> list[str]:
    """Corps SSE OpenAI-compatible simulé : deux deltas de contenu, un chunk
    final d'usage (si le provider honore `include_usage`), puis [DONE]."""
    lignes = [
        'data: {"id": "c1", "choices": [{"delta": {"content": "Bon"}}]}',
        'data: {"id": "c2", "choices": [{"delta": {"content": "jour"}, '
        '"finish_reason": "stop"}]}',
    ]
    if usage_final is not None:
        # Forme réelle du chunk include_usage : choices vide, usage rempli.
        lignes.append(
            'data: {"id": "c3", "choices": [], "usage": '
            + json.dumps(usage_final) + "}"
        )
    if avec_done:
        lignes.append("data: [DONE]")
    return lignes


def _reponse_stream(lignes: list[str]) -> MagicMock:
    reponse = MagicMock()
    reponse.status_code = 200
    reponse.iter_lines.return_value = iter(lignes)
    return reponse


def _reponse_json(corps: dict) -> MagicMock:
    reponse = MagicMock()
    reponse.status_code = 200
    reponse.json.return_value = corps
    return reponse


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_name="TestProvider",
        base_url="http://fake.local/v1/chat/completions",
        api_key="cle-test-fake",
        model="modele-test",
    )


@pytest.fixture
def compteur_record_usage(monkeypatch):
    """Compte les écritures en base au lieu de les faire (isolation totale).

    `record_usage` est importé paresseusement DANS les méthodes du provider
    (`from core.token_tracker import record_usage`) : patcher l'attribut du
    module intercepte donc chaque appel.
    """
    appels = []

    def _faux_record_usage(model, prompt_tokens, completion_tokens, **kwargs):
        appels.append({
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            **kwargs,
        })

    monkeypatch.setattr("core.token_tracker.record_usage", _faux_record_usage)
    return appels


@pytest.fixture
def client_proxy(monkeypatch):
    """App FastAPI portant le routeur réel du proxy, dont le gateway renvoie
    un VRAI FallbackProvider autour du provider OpenAI-compatible simulé : la
    chaîne de wrappers complète (ClaudeInstructionsWrapper → FallbackProvider)
    est exercée, comme en production."""
    provider = _provider()

    class _Gateway:
        def __init__(self):
            self.providers = {"modele-test": object()}

        def get_provider(self, name):
            if name.lower() not in self.providers:
                raise ValueError(f"Provider LLM inconnu : {name}")
            from core.llm.providers.deepseek import ClaudeInstructionsWrapper
            return FallbackProvider(
                [(name.lower(), ClaudeInstructionsWrapper(provider))]
            )

        def get_provider_for_tier(self, tier, config):
            raise NotImplementedError

    monkeypatch.setattr("core.llm_gateway.LLMGateway", _Gateway, raising=False)

    app = FastAPI()
    app.include_router(openai_proxy.router)
    test_client = TestClient(app)
    test_client.provider_reel = provider
    return test_client


def _requete(stream: bool, outils: bool = False) -> dict:
    corps = {
        "model": "modele-test",
        "messages": [{"role": "user", "content": "salut"}],
        "stream": stream,
    }
    if outils:
        corps["tools"] = [{
            "type": "function",
            "function": {"name": "read_file", "parameters": {}},
        }]
    return corps


# ──────────────────────────────────────────────────────────────────
# Contrats du provider seul (generate_stream / generate)
# ──────────────────────────────────────────────────────────────────

class TestGenerateStreamContratUsage:

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_chunk_final_porte_l_usage_reel_tel_quel(self, mock_session,
                                                    compteur_record_usage):
        """Le provider renvoie un chunk final d'usage → le chunk de clôture du
        générateur porte EXACTEMENT ces valeurs, sans rien de recalculé."""
        mock_session.return_value.post.return_value = _reponse_stream(
            _lignes_sse(USAGE_REEL)
        )

        chunks = list(_provider().generate_stream("", "salut"))

        cloture = chunks[-1]
        assert cloture["done"] is True
        assert cloture["usage"] == USAGE_REEL
        # Les chunks de contenu ne portent jamais d'usage (contrat OpenAI).
        assert all(c["usage"] is None for c in chunks[:-1])
        # Une seule écriture en base, sur les valeurs RÉELLES du provider.
        assert len(compteur_record_usage) == 1
        assert compteur_record_usage[0]["prompt_tokens"] == 67523
        assert compteur_record_usage[0]["completion_tokens"] == 412

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sans_usage_le_chunk_final_reste_none(self, mock_session,
                                                  compteur_record_usage):
        """Provider ignorant `include_usage` → le chunk de clôture porte None.
        L'estimation chars/4 calculée pour la base est écrite en base (une
        fois), mais elle ne remonte JAMAIS au client."""
        mock_session.return_value.post.return_value = _reponse_stream(
            _lignes_sse(None)
        )

        chunks = list(_provider().generate_stream("", "salut"))

        assert chunks[-1] == {"token": "", "done": True, "usage": None}
        assert all(c["usage"] is None for c in chunks)
        # Le repli d'estimation reste en base : exactement un appel.
        assert len(compteur_record_usage) == 1

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_flux_sans_done_emet_quand_meme_la_cloture(self, mock_session,
                                                       compteur_record_usage):
        """Certains providers terminent le flux sans [DONE] : la clôture doit
        partir quand même, avec l'usage réel s'il a été reçu."""
        mock_session.return_value.post.return_value = _reponse_stream(
            _lignes_sse(USAGE_REEL, avec_done=False)
        )

        chunks = list(_provider().generate_stream("", "salut"))

        assert chunks[-1] == {"token": "", "done": True, "usage": USAGE_REEL}


class TestGenerateCanalLateralUsage:

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sink_porte_l_usage_reel(self, mock_session, compteur_record_usage):
        """Réponse bufferisée avec usage → le sink le reçoit TEL QUEL, la
        valeur de retour (chaîne) ne change pas, et la base n'est écrite
        qu'une fois."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
            "usage": USAGE_REEL,
        })

        sink: dict = {}
        resultat = _provider().generate("", "salut", _usage_sink=sink)

        assert resultat == "bonjour"
        assert sink == {"usage": USAGE_REEL}
        assert len(compteur_record_usage) == 1

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sans_sink_comportement_inchange(self, mock_session,
                                             compteur_record_usage):
        """Non-régression : aucun appelant existant ne passe de sink — le
        comportement est strictement celui d'avant (chaîne, une écriture)."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
            "usage": USAGE_REEL,
        })

        assert _provider().generate("", "salut") == "bonjour"
        assert len(compteur_record_usage) == 1

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sink_vide_quand_le_provider_ne_renvoie_pas_d_usage(
            self, mock_session, compteur_record_usage):
        """Pas d'usage dans la réponse HTTP → le sink reste VIDE. Aucune
        estimation ne vient le remplir."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
        })

        sink: dict = {}
        _provider().generate("", "salut", _usage_sink=sink)

        assert sink == {}
        assert len(compteur_record_usage) == 0

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sink_ne_part_jamais_dans_le_payload(self, mock_session,
                                                 compteur_record_usage):
        """Le préfixe \"_\" marque un champ interne : il ne doit jamais se
        retrouver dans le corps HTTP envoyé au provider amont."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
            "usage": USAGE_REEL,
        })

        _provider().generate("", "salut", _usage_sink={})

        payload = mock_session.return_value.post.call_args.kwargs["json"]
        assert "_usage_sink" not in json.dumps(payload)

    @patch("core.openai_compat_provider.SharedAsyncHTTPPool.get_client")
    def test_generate_async_meme_contrat(self, mock_client,
                                         compteur_record_usage):
        """Le miroir async honore le même canal latéral."""
        import asyncio

        async def _run():
            mock_client.return_value.post = AsyncMock(return_value=_reponse_json({
                "choices": [{"message": {"content": "bonjour"}}],
                "usage": USAGE_REEL,
            }))
            sink: dict = {}
            resultat = await _provider().generate_async("", "salut", _usage_sink=sink)
            assert resultat == "bonjour"
            assert sink == {"usage": USAGE_REEL}
            assert len(compteur_record_usage) == 1

        asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────
# Bout en bout : route /v1/chat/completions réelle
# ──────────────────────────────────────────────────────────────────

class TestBoutEnBoutStreaming:

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_stream_usage_reel_livre_tel_quel(self, mock_session, client_proxy,
                                              compteur_record_usage):
        """LE test central : un provider simulé renvoie un chunk final
        `usage` réel → la requête streamée livre au client un `usage` portant
        EXACTEMENT les valeurs du provider, sans aucune valeur recalculée."""
        mock_session.return_value.post.return_value = _reponse_stream(
            _lignes_sse(USAGE_REEL)
        )

        reponse = client_proxy.post("/v1/chat/completions",
                                    json=_requete(stream=True))

        assert reponse.status_code == 200
        chunks = _chunks_sse(reponse.text)
        porteurs = [c for c in chunks if "usage" in c]
        assert len(porteurs) == 1, "un seul chunk doit porter l'usage"
        assert porteurs[0]["usage"] == USAGE_REEL
        assert porteurs[0] is chunks[-1], "l'usage part sur le chunk final"
        # Le flux de contenu est intact.
        deltas = [
            c["choices"][0]["delta"].get("content")
            for c in chunks if c["choices"][0]["delta"].get("content")
        ]
        assert deltas == ["Bon", "jour"]
        # Pas de double-compte : une seule écriture en base.
        assert len(compteur_record_usage) == 1

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_stream_sans_usage_champ_absent(self, mock_session, client_proxy,
                                            compteur_record_usage):
        """Provider ignorant `include_usage` → aucun chunk ne porte `usage` :
        pas de zéro, pas d'estimation — le repli calculé pour la base n'en
        sort jamais."""
        mock_session.return_value.post.return_value = _reponse_stream(
            _lignes_sse(None)
        )

        reponse = client_proxy.post("/v1/chat/completions",
                                    json=_requete(stream=True))

        chunks = _chunks_sse(reponse.text)
        assert chunks, "le flux doit contenir au moins un chunk"
        assert all("usage" not in c for c in chunks), (
            f"usage inventé : {[c.get('usage') for c in chunks if 'usage' in c]!r}"
        )
        # L'estimation de repli reste en base, exactement une fois.
        assert len(compteur_record_usage) == 1


class TestBoutEnBoutBufferise:

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_reponse_texte_usage_transmis_via_sink(self, mock_session,
                                                   client_proxy,
                                                   compteur_record_usage):
        """Chemin bufferisé, réponse CHAÎNE : l'usage réel transite par le
        canal latéral et arrive dans la réponse OpenAI — le cas qui était
        structurellement perdu avant cette PR."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
            "usage": USAGE_REEL,
        })

        reponse = client_proxy.post("/v1/chat/completions",
                                    json=_requete(stream=False))

        assert reponse.status_code == 200
        assert reponse.json()["usage"] == USAGE_REEL
        assert len(compteur_record_usage) == 1

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_reponse_texte_sans_usage_champ_omis(self, mock_session,
                                                 client_proxy,
                                                 compteur_record_usage):
        """Pas d'usage amont → la clé est absente de la réponse (jamais de
        zéro ni d'estimation)."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {"content": "bonjour"}}],
        })

        corps = client_proxy.post("/v1/chat/completions",
                                  json=_requete(stream=False)).json()

        assert "usage" not in corps
        assert corps["choices"][0]["message"]["content"] == "bonjour"
        assert len(compteur_record_usage) == 0

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_stream_avec_outils_usage_reel_sur_chunk_final(self, mock_session,
                                                           client_proxy,
                                                           compteur_record_usage):
        """`stream=true` + `tools` — le cas normal d'un client d'IDE
        agentique : la requête reste bufferisée et le chunk final porte
        l'usage réel remonté par le sink."""
        mock_session.return_value.post.return_value = _reponse_json({
            "choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            }}],
            "usage": USAGE_REEL,
        })

        reponse = client_proxy.post("/v1/chat/completions",
                                    json=_requete(stream=True, outils=True))

        chunks = _chunks_sse(reponse.text)
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "tool_calls"
        assert final["usage"] == USAGE_REEL
        assert all("usage" not in c for c in chunks[:-1])
        assert len(compteur_record_usage) == 1
