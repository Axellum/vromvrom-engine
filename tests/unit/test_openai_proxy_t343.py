"""
tests/unit/test_openai_proxy_t343.py — #T343-C : le chemin streaming du proxy
/v1 porte l'étiquette d'agent `proxy_v1`.

Mesuré le 16/08 sur la base de la machine où le proxy sert réellement :
13 lignes `token_usage` étiquetées `proxy_v1`, toutes issues du chemin
bufferisé, ZÉRO du chemin streaming. Or le streaming est le mode par défaut
de Cline / Continue : leur dépense partait anonyme.

Pourquoi le `with agent_courant(...)` de la route ne suffit pas : la route
rend `StreamingResponse` immédiatement et c'est Starlette qui consomme le
générateur APRÈS, hors du bloc `with`. Le contexte doit être posé là où les
tokens sont réellement consommés, dans `_streamer_texte` — et le test doit
vérifier le contexte VU PAR LE PROVIDER (dans son thread, via le pont
`iterate_in_threadpool`), pas celui de la route : l'enregistrement d'usage se
fait pendant le `next()` du générateur, dans le thread du pool.

Vérifié à la mesure (anyio 4, `_backends/_asyncio.py` l. 2510) : le pont
`iterate_in_threadpool` → `anyio.to_thread.run_sync` fait `copy_context()`
et le passe au worker — l'étiquette posée dans `_streamer_texte` atteint donc
bien le thread du provider. Aucun réseau : le provider résolu est un faux.
"""
import json
import threading

import pytest

from api.routes.openai_proxy import (
    ChatCompletionRequest,
    ChatMessage,
    _streamer_texte,
    chat_completions,
)


class _FauxProvider:
    """Provider factice : lit la ContextVar DEPUIS SON THREAD d'exécution,
    comme le vrai code (record_usage tourne pendant le next() du pool)."""

    def __init__(self, nb_tokens=5):
        self.nb_tokens = nb_tokens
        self.agent_vu_en_stream = "<jamais appelé>"
        self.agent_vu_bufferise = "<jamais appelé>"
        self.stream_dans_thread_pool = None

    def generate(self, system_prompt, user_prompt, **kwargs):
        from core.agent_trace import lire_agent_courant
        self.agent_vu_bufferise = lire_agent_courant()
        return "réponse factice"

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        from core.agent_trace import lire_agent_courant
        for i in range(self.nb_tokens):
            # Exécuté dans le thread du pool (chaque next() via
            # iterate_in_threadpool) : c'est ICI que record_usage écrirait.
            self.agent_vu_en_stream = lire_agent_courant()
            self.stream_dans_thread_pool = (
                threading.current_thread() is not threading.main_thread()
            )
            yield {"token": f"tok{i}", "done": i == self.nb_tokens - 1, "usage": None}


@pytest.fixture
def faux_provider(monkeypatch):
    provider = _FauxProvider()
    monkeypatch.setattr(
        "api.routes.openai_proxy._resoudre_provider",
        lambda gateway, name: provider,
    )
    return provider


def _requete(**champs):
    return ChatCompletionRequest(
        model="deepseek-chat",
        messages=[ChatMessage(role="user", content="salut")],
        **champs,
    )


# ── Test central : l'étiquette atteint le thread du provider en streaming ────

@pytest.mark.asyncio
async def test_streaming_etiquette_proxy_v1_vue_par_le_provider(faux_provider):
    """`stream=true` sans outils : au moment où le double produit ses tokens,
    `lire_agent_courant()` vaut `proxy_v1` — VU DEPUIS LE THREAD du provider."""
    reponse = await chat_completions(_requete(stream=True))

    async for _ in reponse.body_iterator:
        pass  # consomme le flux : c'est là que le générateur tourne

    assert faux_provider.stream_dans_thread_pool, (
        "le provider doit s'exécuter dans le thread du pool — sinon ce test "
        "ne prouve rien sur la propagation du contexte (cf. #T301)"
    )
    assert faux_provider.agent_vu_en_stream == "proxy_v1", (
        f"étiquette perdue sur le chemin streaming : "
        f"{faux_provider.agent_vu_en_stream!r} — c'est le symptôme d'un "
        "contexte posé sur la route (Starlette consomme le générateur après "
        "le bloc with) au lieu de `_streamer_texte`"
    )


# ── Non-régression : le chemin bufferisé garde son étiquette ─────────────────

@pytest.mark.asyncio
async def test_chemin_bufferise_garde_l_etiquette_proxy_v1(faux_provider):
    """`stream=false` (ou outils en streaming) : l'étiquette reste posée —
    le correctif ne touche pas au chemin bufferisé de #T306."""
    await chat_completions(_requete())

    assert faux_provider.agent_vu_bufferise == "proxy_v1"


# ── Non-régression : le streaming réel (5 tokens → ≥ 5 chunks) ───────────────

@pytest.mark.asyncio
async def test_streaming_5_tokens_produit_au_moins_5_chunks(faux_provider):
    """Le streaming token-par-token de #T251 n'est pas cassé : 5 tokens
    rendus par le provider → au moins 5 chunks de contenu dans le flux."""
    chunks = []
    async for chunk in _streamer_texte(
        faux_provider, "deepseek-chat", "sys", "transcript", [],
        temperature=0.7, max_tokens=256,
    ):
        chunks.append(chunk)

    contenus = []
    for c in chunks:
        if not c.startswith("data: "):
            continue
        donnees = c[len("data: "):].strip()
        if donnees == "[DONE]":
            continue
        delta = json.loads(donnees)["choices"][0]["delta"]
        if delta.get("content"):
            contenus.append(delta["content"])
    assert len(contenus) >= 5, (
        f"{len(contenus)} chunk(s) de contenu pour 5 tokens — le streaming "
        "réel de #T251 serait cassé"
    )
    assert contenus == [f"tok{i}" for i in range(5)], "ordre des tokens préservé"
    assert chunks[-1].startswith("data: [DONE]")
