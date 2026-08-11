"""
tests/unit/test_openai_proxy_streaming.py — Streaming token-par-token (#T251).

Avant #T251, `stream=true` attendait la génération complète puis renvoyait la
réponse en UN SEUL chunk SSE — écran figé côté client, limite documentée dans
le docstring depuis le 15/06. Depuis, la route itère `provider.generate_stream()`
et émet un chunk SSE par token, relayé par `iterate_in_threadpool` : la boucle
d'événements n'est jamais bloquée (pont threadpool).

Deux chemins, assumés : le flux ne porte que du texte — une requête avec
`tools` reste sur le chemin bufferisé, car un appel d'outil tronqué en morceaux
serait ininterprétable par le client.
"""

import asyncio
import json
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import openai_proxy
from api.routes.openai_proxy import _streamer_texte

# ──────────────────────────────────────────────────────────────────
# Helpers de parsing SSE
# ──────────────────────────────────────────────────────────────────

def _contenus_sse(corps: str) -> list[str]:
    """Extrait les deltas `content` non vides des chunks SSE de la réponse."""
    contenus = []
    for ligne in corps.splitlines():
        if not ligne.startswith("data: "):
            continue
        donnees = ligne[len("data: "):]
        if donnees == "[DONE]":
            continue
        chunk = json.loads(donnees)
        delta = chunk["choices"][0]["delta"]
        if delta.get("content"):
            contenus.append(delta["content"])
    return contenus


def _chunks_sse(corps: str) -> list[dict]:
    """Tous les chunks JSON de la réponse SSE (hors [DONE])."""
    chunks = []
    for ligne in corps.splitlines():
        if not ligne.startswith("data: "):
            continue
        donnees = ligne[len("data: "):]
        if donnees != "[DONE]":
            chunks.append(json.loads(donnees))
    return chunks


# ──────────────────────────────────────────────────────────────────
# Provider factice + client FastAPI minimal
# ──────────────────────────────────────────────────────────────────

class _ProviderFactice:
    """Provider de test : generate() bufferisé, generate_stream() scriptable."""

    def __init__(self, chunks=None, generate_reponse="réponse du modèle"):
        self.chunks = list(chunks or [])
        self.generate_reponse = generate_reponse
        self.appels_generate = []
        self.appels_stream = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.appels_generate.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.generate_reponse

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        self.appels_stream.append({"system": system_prompt, "user": user_prompt, **kwargs})
        yield from self.chunks


@pytest.fixture
def client(monkeypatch):
    """App FastAPI minimale portant uniquement le routeur du proxy."""
    provider = _ProviderFactice()

    class _Gateway:
        def __init__(self):
            self.providers = {"modele-a": object()}

        def get_provider(self, name):
            if name.lower() not in self.providers:
                raise ValueError(f"Provider LLM inconnu : {name}")
            return provider

        def get_provider_for_tier(self, tier, config):
            return f"tier-{tier}", provider

    monkeypatch.setattr("core.llm_gateway.LLMGateway", _Gateway, raising=False)
    monkeypatch.setattr("core.llm_gateway.load_config", lambda: {}, raising=False)

    app = FastAPI()
    app.include_router(openai_proxy.router)
    test_client = TestClient(app)
    test_client.provider = provider
    return test_client


def _requete(stream: bool, outils: bool = False) -> dict:
    corps = {
        "model": "modele-a",
        "messages": [{"role": "user", "content": "salut"}],
        "stream": stream,
    }
    if outils:
        corps["tools"] = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    return corps


# ──────────────────────────────────────────────────────────────────
# Streaming token-par-token
# ──────────────────────────────────────────────────────────────────

def test_streaming_texte_emmet_un_chunk_sse_par_token(client):
    """LE test de la tâche : 5 tokens rendus par le provider → au moins 5
    chunks de contenu distincts dans le flux, pas un seul bloc bufferisé."""
    tokens = ["Bon", "jour", " ", "mon", "de"]
    client.provider.chunks = (
        [{"token": t, "done": False, "usage": None} for t in tokens]
        + [{"token": "", "done": True, "usage": None}]
    )

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True))

    assert reponse.status_code == 200
    contenus = _contenus_sse(reponse.text)
    assert len(contenus) >= 5
    assert len(set(contenus)) == 5, "chaque chunk doit porter du texte distinct"
    assert contenus == tokens, "l'ordre et le contenu des tokens doivent être préservés"
    assert reponse.text.rstrip().endswith("data: [DONE]")

    # La preuve que le chemin bufferisé n'est PLUS utilisé pour le texte.
    assert client.provider.appels_generate == []
    assert len(client.provider.appels_stream) == 1


def test_la_requete_avec_tools_reste_sur_le_chemin_bufferise(client):
    """Un appel d'outil tronqué en morceaux serait ininterprétable par le
    client : avec `tools`, stream=true garde le chemin bufferisé (tout part en
    un seul chunk) et renvoie `finish_reason: "tool_calls"`."""
    client.provider.generate_reponse = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
        }],
    }

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True, outils=True))

    assert reponse.status_code == 200
    assert '"finish_reason": "tool_calls"' in reponse.text
    assert reponse.text.rstrip().endswith("data: [DONE]")

    # Tout l'appel d'outil d'un coup : chunk de contenu + chunk de fin.
    chunks = _chunks_sse(reponse.text)
    assert len(chunks) == 2
    delta = chunks[0]["choices"][0]["delta"]
    assert delta["tool_calls"][0]["function"]["name"] == "read_file"

    # generate_stream n'a PAS été appelé : la voie bufferisée a été prise.
    assert client.provider.appels_stream == []
    assert len(client.provider.appels_generate) == 1


def test_exception_apres_deux_tokens_termine_le_flux_proprement(client, caplog):
    """Une panne en cours de flux ne peut plus devenir une réponse d'erreur
    HTTP (les en-têtes sont déjà partis) : les tokens déjà émis restent, le
    flux se ferme proprement et l'erreur est journalisée, pas avalée."""

    def _generateur_qui_plante():
        yield {"token": "premier", "done": False, "usage": None}
        yield {"token": " second", "done": False, "usage": None}
        raise RuntimeError("panne réseau simulée")

    client.provider.chunks = _generateur_qui_plante()

    with caplog.at_level(logging.ERROR, logger="api.routes.openai_proxy"):
        reponse = client.post("/v1/chat/completions", json=_requete(stream=True))

    assert reponse.status_code == 200, "aucune exception ne doit fuiter côté serveur"
    assert _contenus_sse(reponse.text) == ["premier", " second"]
    assert reponse.text.rstrip().endswith("data: [DONE]")
    assert any("Streaming interrompu" in r.getMessage() for r in caplog.records)


def test_usage_du_generateur_propage_dans_le_dernier_chunk(client):
    """`generate_stream` peut remonter un usage réel (stream_options
    include_usage) : il ressort sur le dernier chunk, avec finish_reason."""
    client.provider.chunks = [
        {"token": "oui", "done": False, "usage": None},
        {"token": "", "done": True, "usage": {
            "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
        }},
    ]

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True))

    chunks = _chunks_sse(reponse.text)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}


# ──────────────────────────────────────────────────────────────────
# Chemin non-streaming et pont threadpool
# ──────────────────────────────────────────────────────────────────

def test_non_streaming_rend_exactement_le_meme_json_qu_avant(client):
    """Le chemin `stream=false` est resté identique : même forme JSON, même
    comptage approximatif, generate_stream jamais appelé."""
    reponse = client.post("/v1/chat/completions", json=_requete(stream=False))

    assert reponse.status_code == 200
    corps = reponse.json()
    assert set(corps) == {"id", "object", "created", "model", "choices", "usage"}
    assert corps["object"] == "chat.completion"
    assert corps["model"] == "modele-a"
    choix = corps["choices"][0]
    assert set(choix) == {"index", "message", "finish_reason"}
    assert choix["index"] == 0
    assert choix["finish_reason"] == "stop"
    assert choix["message"] == {"role": "assistant", "content": "réponse du modèle"}
    assert corps["usage"]["total_tokens"] == (
        corps["usage"]["prompt_tokens"] + corps["usage"]["completion_tokens"]
    )
    assert client.provider.appels_stream == []


@pytest.mark.asyncio
async def test_streamer_texte_utilise_un_pont_threadpool():
    """Preuve mécanique du pont : pendant que le générateur SYNCHRONE dort
    (0,2 s), la boucle d'événements continue de tourner — un `asyncio.sleep`
    court se termine AVANT le token. Itéré nu, le même générateur gèlerait la
    boucle et inverserait l'ordre."""

    class _ProviderLent:
        def generate_stream(self, system_prompt, user_prompt, **kwargs):
            time.sleep(0.2)  # I/O synchrone simulée
            yield {"token": "enfin", "done": True, "usage": None}

    ordre = []

    async def _tick():
        await asyncio.sleep(0.05)
        ordre.append("tick")

    tache_tick = asyncio.create_task(_tick())
    async for _ in _streamer_texte(_ProviderLent(), "modele", "sys", "user", [], None, None):
        ordre.append("token")
    await tache_tick

    # Sans pont threadpool, "tick" ne pourrait tourner qu'après la fin du
    # générateur (0,2 s de sleep synchrone) : "token" précéderait "tick".
    assert ordre.index("tick") < ordre.index("token")
