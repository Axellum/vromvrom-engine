"""
tests/unit/test_openai_proxy_usage_t357.py — Le `usage` du proxy /v1 (#T357).

Le proxy annonçait un `usage` FAUX : sur le chemin non-streamé il publiait un
comptage de MOTS (`response_text.split()`) dans des champs nommés
`prompt_tokens` / `completion_tokens`, et sur `stream=true` + `tools` — le cas
normal d'un client d'IDE agentique — il n'émettait aucun usage du tout. Le
client cale sa fenêtre de contexte et son budget là-dessus ; 630 appels mesurés
le 17/08 avaient un prompt médian de 67 523 tokens réels.

Le contrat vérifié ici tient en une phrase : **usage réel du provider, ou pas de
champ `usage` du tout**. Aucune estimation de repli, quelle qu'en soit la
formule — un chiffre faux qui rassure est pire qu'un chiffre absent.

Aucun appel réseau : les providers sont des doubles, le LLMGateway est
monkeypatché.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import openai_proxy
from api.routes.openai_proxy import _extraire_usage_reel, _normaliser_usage

USAGE_OPENAI = {"prompt_tokens": 67523, "completion_tokens": 412, "total_tokens": 67935}


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


class _ProviderFactice:
    """Provider de test : `generate()` et `generate_stream()` scriptables."""

    def __init__(self):
        self.reponse_generate = "réponse du modèle"
        self.chunks = [{"token": "salut", "done": True, "usage": None}]

    def generate(self, system_prompt, user_prompt, **kwargs):
        return self.reponse_generate

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
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


def _message_outil(usage: dict | None = None) -> dict:
    """Message OpenAI d'appel d'outil, tel que `generate()` le retourne."""
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
        }],
    }
    if usage is not None:
        message["usage"] = usage
    return message


# ──────────────────────────────────────────────────────────────────
# Chemin non-streamé
# ──────────────────────────────────────────────────────────────────

def test_non_streame_transmet_l_usage_amont_a_l_identique(client):
    """Usage réel remonté par le provider → transmis TEL QUEL, sans retouche."""
    client.provider.reponse_generate = {
        "role": "assistant", "content": "bonjour", "usage": USAGE_OPENAI,
    }

    reponse = client.post("/v1/chat/completions", json=_requete(stream=False))

    assert reponse.status_code == 200
    assert reponse.json()["usage"] == USAGE_OPENAI


def test_non_streame_sans_usage_amont_n_a_aucune_cle_usage(client):
    """LE garde-fou : pas d'usage amont → la clé `usage` est ABSENTE de la
    réponse (elle est facultative côté OpenAI), et surtout pas remplie par une
    estimation quelconque."""
    client.provider.reponse_generate = "réponse du modèle"

    reponse = client.post("/v1/chat/completions", json=_requete(stream=False))

    corps = reponse.json()
    assert "usage" not in corps, f"champ usage inventé : {corps.get('usage')!r}"
    # Le reste de l'enveloppe OpenAI est intact.
    assert set(corps) == {"id", "object", "created", "model", "choices"}
    assert corps["choices"][0]["message"]["content"] == "réponse du modèle"


def test_aucune_estimation_ne_remplace_l_usage_absent(client):
    """Anti-contournement : remplacer le comptage en mots par une AUTRE
    estimation (heuristique 4 caractères/token, tiktoken…) est exactement le
    défaut à corriger. Une réponse volumineuse sans usage amont ne doit
    toujours produire aucun compteur."""
    client.provider.reponse_generate = "mot " * 5000

    corps = client.post("/v1/chat/completions", json=_requete(stream=False)).json()

    assert "usage" not in corps
    # Aucun compteur de tokens ne doit s'être glissé ailleurs dans l'enveloppe.
    assert "token" not in json.dumps(corps).lower()


def test_non_streame_avec_outils_transmet_l_usage_amont(client):
    """Un appel d'outil ne fait pas perdre l'usage réel."""
    client.provider.reponse_generate = _message_outil(usage=USAGE_OPENAI)

    corps = client.post("/v1/chat/completions", json=_requete(stream=False, outils=True)).json()

    assert corps["choices"][0]["finish_reason"] == "tool_calls"
    assert corps["usage"] == USAGE_OPENAI


# ──────────────────────────────────────────────────────────────────
# Chemin streamé + outils (le cas normal d'un client agentique)
# ──────────────────────────────────────────────────────────────────

def test_streame_avec_outils_porte_l_usage_dans_le_chunk_final(client):
    """`stream=true` + `tools` passe par le chemin bufferisé : si la réponse du
    provider porte un usage réel, il doit arriver au client sur le chunk final
    (celui qui porte `finish_reason`)."""
    client.provider.reponse_generate = _message_outil(usage=USAGE_OPENAI)

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True, outils=True))

    assert reponse.status_code == 200
    chunks = _chunks_sse(reponse.text)
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "tool_calls"
    assert final["usage"] == USAGE_OPENAI
    # Le chunk de contenu, lui, ne porte pas d'usage (contrat OpenAI).
    assert "usage" not in chunks[0]
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "read_file"


def test_streame_avec_outils_sans_usage_amont_n_invente_rien(client):
    """Même chemin, sans usage amont : aucun chunk ne porte de champ `usage`."""
    client.provider.reponse_generate = _message_outil()

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True, outils=True))

    chunks = _chunks_sse(reponse.text)
    assert chunks, "le flux doit contenir au moins un chunk"
    assert all("usage" not in c for c in chunks)


def test_streame_texte_sans_usage_amont_n_invente_rien(client):
    """Streaming token-par-token sans usage remonté : aucun chunk ne porte de
    champ `usage` (le provider a le droit de ne pas supporter
    `stream_options.include_usage`)."""
    client.provider.chunks = [
        {"token": "Bon", "done": False, "usage": None},
        {"token": "jour", "done": False, "usage": None},
        {"token": "", "done": True, "usage": None},
    ]

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True))

    chunks = _chunks_sse(reponse.text)
    assert len(chunks) >= 3
    assert all("usage" not in c for c in chunks)


def test_streame_texte_traduit_l_usage_gemini_natif(client):
    """gemini_native.generate_stream() remonte un `usageMetadata` (schéma
    Gemini) sur son chunk final. Transmis tel quel, le client OpenAI ne
    trouverait AUCUNE des clés qu'il lit : les compteurs sont renommés."""
    client.provider.chunks = [
        {"token": "oui", "done": False, "usage": None},
        {"token": "", "done": True, "usage": {
            "promptTokenCount": 67523,
            "candidatesTokenCount": 412,
            "totalTokenCount": 67935,
        }},
    ]

    reponse = client.post("/v1/chat/completions", json=_requete(stream=True))

    final = _chunks_sse(reponse.text)[-1]
    assert final["usage"] == USAGE_OPENAI


# ──────────────────────────────────────────────────────────────────
# Normalisation : renommage strict, jamais de calcul
# ──────────────────────────────────────────────────────────────────

def test_normaliser_usage_laisse_le_schema_openai_intact():
    """Passthrough total, extras compris (cache DeepSeek, détails OpenAI) :
    on ne réécrit pas ce que le provider a compté."""
    usage = {
        "prompt_tokens": 67523,
        "completion_tokens": 412,
        "total_tokens": 67935,
        "prompt_cache_hit_tokens": 65000,
        "prompt_tokens_details": {"cached_tokens": 65000},
    }
    assert _normaliser_usage(usage) == usage


def test_normaliser_usage_traduit_le_schema_anthropic():
    assert _normaliser_usage({"input_tokens": 120, "output_tokens": 8}) == {
        "prompt_tokens": 120, "completion_tokens": 8,
    }


def test_normaliser_usage_ne_calcule_jamais_le_total():
    """`total_tokens` n'est posé que si le provider l'a fourni : on ne dérive
    aucune valeur, même par une somme."""
    traduit = _normaliser_usage({"promptTokenCount": 100, "candidatesTokenCount": 20})
    assert traduit == {"prompt_tokens": 100, "completion_tokens": 20}
    assert "total_tokens" not in traduit


@pytest.mark.parametrize("brut", [
    None,
    {},
    "12 tokens",
    {"total_tokens": 42},              # un total seul ne dit rien de l'entrée
    {"prompt_tokens": None},           # clé présente mais vide
    {"prompt_tokens": True},           # bool : `isinstance(True, int)` est vrai
    {"tokens_utilises": 900},          # schéma inconnu
])
def test_normaliser_usage_refuse_ce_qui_n_est_pas_un_comptage(brut):
    """Tout ce qui n'est pas un comptage réel exploitable donne None — donc une
    omission du champ en aval, jamais une valeur de remplacement."""
    assert _normaliser_usage(brut) is None


def test_extraire_usage_reel_ignore_une_reponse_texte():
    """`generate()` renvoie une chaîne dans le cas courant : aucun usage à en
    tirer, et rien à inventer."""
    assert _extraire_usage_reel("réponse du modèle") is None
    assert _extraire_usage_reel({"content": "bonjour"}) is None
    assert _extraire_usage_reel({"content": "bonjour", "usage": USAGE_OPENAI}) == USAGE_OPENAI
