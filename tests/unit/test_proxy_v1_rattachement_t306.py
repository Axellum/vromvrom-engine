"""
tests/unit/test_proxy_v1_rattachement_t306.py — le proxy /v1 n'avale plus
`session_id` (#T306).

Audit des chemins d'appel (12/08) : direct, cascade (FallbackProvider),
wrappers, async natif et synchrone, vocal, fast-path, fond — tous transmettent
`session_id` jusqu'à `record_usage()`. Le SEUL trou trouvé est le proxy
OpenAI-compatible `/v1/chat/completions` (api/routes/openai_proxy.py) :

  - chemin bufferisé : `provider.generate(...)` sans `session_id` ;
  - chemin streaming : `_streamer_texte(...)` → `generate_stream(...)` sans
    `session_id` ;
  - et le champ était structurellement INTRANSMISSIBLE : `ChatCompletionRequest`
    ne le déclarait pas, pydantic avalait silencieusement la clé envoyée par un
    client (Cline, Continue, OpenCode…).

La dépense du proxy était donc toujours anonyme (`token_usage.session_id`
NULL), et `agent_name` aussi : personne ne posait l'étiquette — le proxy
consomme hors de tout agent (même motif que `coding_front`, #T308).

Correctif : champ `session_id` optionnel sur la requête (les clients existants
qui ne l'envoient pas gardent exactement le comportement d'avant), transmission
aux deux chemins, étiquette `agent_courant("proxy_v1")` autour de l'appel.

Aucun réseau : le provider résolu est un faux, `generate`/`generate_stream`
sont mockés et capturent les kwargs reçus.
"""
import pytest

from api.routes.openai_proxy import (
    ChatCompletionRequest,
    ChatMessage,
    _streamer_texte,
    chat_completions,
)


class _FauxProvider:
    """Provider factice : enregistre les kwargs reçus, zéro réseau."""

    def __init__(self):
        self.vus = {}
        self.reponses = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        from core.agent_trace import lire_agent_courant
        self.vus["kwargs"] = kwargs
        self.vus["agent_courant"] = lire_agent_courant()
        return self.reponses.pop(0) if self.reponses else "bonjour"

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        self.vus["stream_kwargs"] = kwargs
        yield {"token": "bonjour", "done": True, "usage": None}


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


# ── Le trou : le session_id envoyé par le client devait atteindre le provider ─

@pytest.mark.asyncio
async def test_chemin_bufferise_transmet_le_session_id(faux_provider):
    """Un client qui envoie session_id voit sa dépense rattachée."""
    await chat_completions(_requete(session_id="s_proxy_v1"))

    assert faux_provider.vus["kwargs"].get("session_id") == "s_proxy_v1"


@pytest.mark.asyncio
async def test_chemin_streaming_transmet_le_session_id(faux_provider):
    """Le streaming (SSE token-par-token) transmet aussi le session_id."""
    chunks = []
    async for chunk in _streamer_texte(
        faux_provider, "deepseek-chat", "sys", "transcript", [],
        temperature=0.7, max_tokens=256, session_id="s_proxy_stream",
    ):
        chunks.append(chunk)

    assert faux_provider.vus["stream_kwargs"].get("session_id") == "s_proxy_stream"
    assert chunks, "le flux doit produire des chunks"


@pytest.mark.asyncio
async def test_agent_name_etiquete_pendant_l_appel(faux_provider):
    """La dépense du proxy est visible par agent (proxy_v1), comme coding_front."""
    await chat_completions(_requete(session_id="s_proxy_v1"))

    assert faux_provider.vus["agent_courant"] == "proxy_v1"


# ── Non-régression : sans session_id, comportement inchangé ──────────────────

@pytest.mark.asyncio
async def test_sans_session_id_aucun_changement(faux_provider):
    """Les clients existants (qui n'envoient rien) gardent le comportement d'avant."""
    await chat_completions(_requete())

    assert faux_provider.vus["kwargs"].get("session_id") is None
    assert faux_provider.vus["kwargs"]["messages"] is not None
    assert faux_provider.vus["kwargs"]["temperature"] == 0.7
