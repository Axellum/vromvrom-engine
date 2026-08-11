"""
tests/unit/test_openai_proxy.py — Proxy OpenAI-compatible (/v1) pour IDEs.

Le fichier `api/routes/openai_proxy.py` n'avait aucun test. Ceux-ci couvrent la
« fondation » sur laquelle repose tout branchement d'un agent de code :

1. Le mapping des messages. Avant, user/assistant/tool étaient concaténés en un
   seul bloc passé comme `user_prompt` : les rôles disparaissaient, donc le
   multi-tours aussi — et le tool-calling, qui exige de renvoyer des messages
   `role: "tool"`, était structurellement impossible.
2. La résolution du modèle. Avant, un modèle inconnu était servi par le PREMIER
   provider du registre, la réponse étant tout de même étiquetée du nom demandé.
3. Le passage par `get_provider()`, donc par le Circuit Breaker / la cascade /
   le cache — contournés par l'accès brut `gw.providers[...]` (même classe de
   bug que #T212 côté Planner).
"""

import pytest

from api.routes import openai_proxy
from api.routes.openai_proxy import (
    ChatMessage,
    ModeleInconnuError,
    _convertir_messages,
    _extraire_reponse,
    _resoudre_provider,
    _texte_du_contenu,
)

# ──────────────────────────────────────────────────────────────────
# Aplatissement du contenu
# ──────────────────────────────────────────────────────────────────

def test_contenu_chaine_simple():
    assert _texte_du_contenu("bonjour") == "bonjour"


def test_contenu_none_devient_chaine_vide():
    assert _texte_du_contenu(None) == ""


def test_contenu_multimodal_en_liste_de_parts():
    """Le typage `str | None` faisait échouer ces requêtes en 422."""
    contenu = [
        {"type": "text", "text": "décris cette image"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
        {"type": "text", "text": "en français"},
    ]
    assert _texte_du_contenu(contenu) == "décris cette image\nen français"


# ──────────────────────────────────────────────────────────────────
# Mapping des messages
# ──────────────────────────────────────────────────────────────────

def test_les_roles_sont_preserves_dans_messages():
    messages = [
        ChatMessage(role="system", content="Tu es un assistant."),
        ChatMessage(role="user", content="salut"),
        ChatMessage(role="assistant", content="bonjour"),
        ChatMessage(role="user", content="quelle heure ?"),
    ]
    system_prompt, transcript, messages_provider = _convertir_messages(messages)

    assert system_prompt == "Tu es un assistant."
    assert [m["role"] for m in messages_provider] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in messages_provider] == [
        "Tu es un assistant.", "salut", "bonjour", "quelle heure ?",
    ]
    # Le transcript de repli distingue les rôles au lieu de tout aplatir.
    assert "Utilisateur : salut" in transcript
    assert "Assistant : bonjour" in transcript


def test_message_tool_conserve_son_role_et_son_id():
    """Prérequis du tool-calling : un `role: tool` doit rester un `role: tool`."""
    messages = [
        ChatMessage(role="user", content="météo ?"),
        ChatMessage(role="assistant", content=None, tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
        ]),
        ChatMessage(role="tool", content="21°C", tool_call_id="call_1", name="get_weather"),
    ]
    _, transcript, messages_provider = _convertir_messages(messages)

    assert messages_provider[1]["role"] == "assistant"
    assert messages_provider[1]["tool_calls"][0]["id"] == "call_1"
    assert messages_provider[2]["role"] == "tool"
    assert messages_provider[2]["tool_call_id"] == "call_1"
    assert messages_provider[2]["name"] == "get_weather"
    # Même le repli textuel mentionne l'appel d'outil au lieu de le perdre.
    assert "get_weather" in transcript


def test_plusieurs_messages_system_sont_fusionnes():
    messages = [
        ChatMessage(role="system", content="Règle A."),
        ChatMessage(role="system", content="Règle B."),
        ChatMessage(role="user", content="ok"),
    ]
    system_prompt, _, messages_provider = _convertir_messages(messages)

    assert system_prompt == "Règle A.\nRègle B."
    assert messages_provider[0] == {"role": "system", "content": "Règle A.\nRègle B."}
    assert len([m for m in messages_provider if m["role"] == "system"]) == 1


def test_sans_message_system_aucune_entree_system():
    _, _, messages_provider = _convertir_messages([ChatMessage(role="user", content="salut")])
    assert [m["role"] for m in messages_provider] == ["user"]


def test_conversation_sans_tour_utilisateur_donne_un_transcript_vide():
    """C'est ce que l'endpoint traduit en 400."""
    _, transcript, _ = _convertir_messages([ChatMessage(role="system", content="Tu es un assistant.")])
    assert transcript == ""


# ──────────────────────────────────────────────────────────────────
# Résolution du modèle
# ──────────────────────────────────────────────────────────────────

class _FauxGateway:
    """Gateway minimal : `providers` brut + get_provider() qui lève sur inconnu."""

    def __init__(self):
        self.providers = {"modele-a": "PROVIDER_A", "modele-b": "PROVIDER_B"}
        self.appels_get_provider = []
        self.appels_tier = []

    def get_provider(self, name):
        self.appels_get_provider.append(name)
        cible = self.providers.get(name.lower())
        if cible is None:
            raise ValueError(f"Provider LLM inconnu : {name}")
        return f"CASCADE({cible})"  # get_provider enveloppe dans un FallbackProvider

    def get_provider_for_tier(self, tier, config):
        self.appels_tier.append(tier)
        return f"tier-{tier}", "CASCADE(TIER)"


def test_modele_connu_passe_par_get_provider_pas_par_le_registre_brut():
    """Le wrapping FallbackProvider (Circuit Breaker, retry 429, cache) en dépend."""
    gw = _FauxGateway()
    assert _resoudre_provider(gw, "modele-a") == "CASCADE(PROVIDER_A)"
    assert gw.appels_get_provider == ["modele-a"]


def test_modele_inconnu_leve_au_lieu_de_servir_un_provider_arbitraire():
    """Régression centrale : avant, on répondait avec le premier provider du
    registre tout en étiquetant la réponse du nom demandé."""
    gw = _FauxGateway()
    with pytest.raises(ModeleInconnuError):
        _resoudre_provider(gw, "modele-qui-nexiste-pas")
    assert gw.appels_tier == [], "un modèle inconnu ne doit pas être traité comme un tier"


def test_casse_du_nom_de_modele_toleree():
    """`get_provider` normalise en minuscules ; le test d'appartenance d'avant non."""
    gw = _FauxGateway()
    assert _resoudre_provider(gw, "Modele-A") == "CASCADE(PROVIDER_A)"


@pytest.mark.parametrize("tier", ["leger", "moyen", "fort", "automatique", "pro", "FLASH"])
def test_les_tiers_sont_resolus_comme_tiers(tier, monkeypatch):
    monkeypatch.setattr("core.llm_gateway.load_config", lambda: {}, raising=False)
    gw = _FauxGateway()
    assert _resoudre_provider(gw, tier) == "CASCADE(TIER)"
    assert gw.appels_tier == [tier]
    assert gw.appels_get_provider == []


def test_les_tiers_annonces_sont_ceux_acceptes():
    """Le message de la 404 renvoie vers /v1/models : les tiers doivent y figurer."""
    tiers_listes = {"leger", "moyen", "fort", "automatique"}
    assert tiers_listes <= openai_proxy._TIERS_ROUTAGE


# ──────────────────────────────────────────────────────────────────
# Format d'erreur
# ──────────────────────────────────────────────────────────────────

def test_erreur_au_format_openai():
    import json

    reponse = openai_proxy._erreur_openai(404, "pas trouvé", code="model_not_found", param="model")
    assert reponse.status_code == 404
    corps = json.loads(bytes(reponse.body))
    assert corps["error"]["code"] == "model_not_found"
    assert corps["error"]["param"] == "model"
    assert corps["error"]["type"] == "invalid_request_error"
    assert corps["error"]["message"] == "pas trouvé"


# ──────────────────────────────────────────────────────────────────
# Endpoint complet (POST /v1/chat/completions)
# ──────────────────────────────────────────────────────────────────

class _ProviderEspion:
    """Capture les arguments reçus par generate() / generate_stream()."""

    def __init__(self, reponse="réponse du modèle"):
        self.reponse = reponse
        self.appels = []
        self.appels_stream = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.appels.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.reponse

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        # Streaming minimal : toute la réponse en un token (contrat du moteur :
        # dict {"token", "done", "usage"}). Le vrai token-par-token est couvert
        # par test_openai_proxy_streaming.py.
        self.appels_stream.append({"system": system_prompt, "user": user_prompt, **kwargs})
        yield {"token": str(self.reponse), "done": True, "usage": None}


@pytest.fixture
def client(monkeypatch):
    """App FastAPI minimale portant uniquement le routeur du proxy."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    espion = _ProviderEspion()

    class _Gateway:
        def __init__(self):
            self.providers = {"modele-a": object(), "modele-b": object()}

        def get_provider(self, name):
            if name.lower() not in self.providers:
                raise ValueError(f"Provider LLM inconnu : {name}")
            return espion

        def get_provider_for_tier(self, tier, config):
            return f"tier-{tier}", espion

    monkeypatch.setattr("core.llm_gateway.LLMGateway", _Gateway, raising=False)
    monkeypatch.setattr("core.llm_gateway.load_config", lambda: {}, raising=False)

    app = FastAPI()
    app.include_router(openai_proxy.router)
    client = TestClient(app)
    client.espion = espion
    return client


def test_endpoint_transmet_la_conversation_avec_ses_roles(client):
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [
            {"role": "system", "content": "Tu es un assistant."},
            {"role": "user", "content": "écris une fonction"},
            {"role": "assistant", "content": "def f(): pass"},
            {"role": "user", "content": "ajoute un test"},
        ],
    })
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["choices"][0]["message"]["content"] == "réponse du modèle"
    assert corps["model"] == "modele-a"

    appel = client.espion.appels[0]
    assert [m["role"] for m in appel["messages"]] == ["system", "user", "assistant", "user"]
    assert appel["messages"][2]["content"] == "def f(): pass"


def test_endpoint_404_sur_modele_inconnu(client):
    """Avant : 200 avec la réponse d'un autre provider, étiquetée du nom demandé."""
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-qui-nexiste-pas",
        "messages": [{"role": "user", "content": "salut"}],
    })
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "model_not_found"
    assert client.espion.appels == [], "aucun provider ne doit être appelé"


def test_endpoint_400_sans_message_utilisateur(client):
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "system", "content": "Tu es un assistant."}],
    })
    assert reponse.status_code == 400


def test_endpoint_accepte_le_contenu_multimodal(client):
    """Ces requêtes échouaient en 422 (validation Pydantic) avant."""
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "salut"}]}],
    })
    assert reponse.status_code == 200
    assert client.espion.appels[0]["messages"][0]["content"] == "salut"


def test_endpoint_accepte_un_tier_comme_modele(client):
    reponse = client.post("/v1/chat/completions", json={
        "model": "fort",
        "messages": [{"role": "user", "content": "salut"}],
    })
    assert reponse.status_code == 200
    assert reponse.json()["model"] == "fort"


def test_v1_models_liste_providers_et_tiers(client):
    reponse = client.get("/v1/models")
    assert reponse.status_code == 200
    ids = {m["id"] for m in reponse.json()["data"]}
    assert {"modele-a", "modele-b"} <= ids
    assert {"leger", "moyen", "fort", "automatique"} <= ids


def test_endpoint_streaming_reste_fonctionnel(client):
    """Contrat SSE : le contenu part en chunks via generate_stream, et le flux
    se termine par [DONE] (le token-par-token est couvert par
    test_openai_proxy_streaming.py)."""
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "salut"}],
        "stream": True,
    })
    assert reponse.status_code == 200
    corps = reponse.text
    assert "réponse du modèle" in corps
    assert corps.rstrip().endswith("data: [DONE]")


# ──────────────────────────────────────────────────────────────────
# Tool-calling
# ──────────────────────────────────────────────────────────────────

def _message_avec_outil():
    """Ce que renvoie un provider quand le modèle décide d'appeler un outil."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
        }],
    }


def test_appel_doutil_nest_plus_transforme_en_texte_none():
    """Régression centrale : `.get("content", defaut)` renvoie None quand la clé
    EXISTE avec la valeur None — la forme exacte d'un appel d'outil. L'IDE
    recevait la chaîne littérale "None" à la place de l'appel."""
    texte, tool_calls = _extraire_reponse(_message_avec_outil())

    assert texte == ""
    assert tool_calls is not None
    assert tool_calls[0]["function"]["name"] == "read_file"


def test_reponse_texte_simple_inchangee():
    assert _extraire_reponse("bonjour") == ("bonjour", None)


def test_reponse_dict_avec_contenu_et_outils():
    brut = {"role": "assistant", "content": "je regarde", "tool_calls": _message_avec_outil()["tool_calls"]}
    texte, tool_calls = _extraire_reponse(brut)
    assert texte == "je regarde"
    assert len(tool_calls) == 1


def test_dict_sans_contenu_ni_outil_est_serialise_pas_perdu():
    texte, tool_calls = _extraire_reponse({"inattendu": 1})
    assert "inattendu" in texte
    assert tool_calls is None


def test_normalisation_ajoute_index_et_type():
    """Les clients recollent les fragments de streaming par `index`."""
    normalises = openai_proxy._normaliser_tool_calls([
        {"function": {"name": "a", "arguments": "{}"}},
        {"function": {"name": "b", "arguments": "{}"}},
    ])
    assert [tc["index"] for tc in normalises] == [0, 1]
    assert all(tc["type"] == "function" for tc in normalises)
    assert all(tc["id"] for tc in normalises)


def test_normalisation_serialise_les_arguments_dict():
    """`arguments` doit être une chaîne JSON côté OpenAI ; certains providers
    renvoient un dict."""
    import json as _json

    normalises = openai_proxy._normaliser_tool_calls([
        {"function": {"name": "a", "arguments": {"path": "a.py"}}},
    ])
    assert _json.loads(normalises[0]["function"]["arguments"]) == {"path": "a.py"}


def test_arguments_absents_deviennent_objet_json_vide():
    normalises = openai_proxy._normaliser_tool_calls([{"function": {"name": "a"}}])
    assert normalises[0]["function"]["arguments"] == "{}"


def test_endpoint_transmet_les_outils_au_provider(client):
    outils = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "lis a.py"}],
        "tools": outils,
        "tool_choice": "auto",
    })
    assert reponse.status_code == 200

    appel = client.espion.appels[0]
    assert appel["tools"] == outils, "les outils étaient déclarés puis jetés avant"
    assert appel["tool_choice"] == "auto"


def test_endpoint_nenvoie_pas_de_cle_tools_sans_outils(client):
    """Un `tools=None` transmis casserait les providers qui testent `\"tools\" in kwargs`."""
    client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "salut"}],
    })
    assert "tools" not in client.espion.appels[0]
    assert "tool_choice" not in client.espion.appels[0]


def test_endpoint_remonte_les_tool_calls_au_format_openai(client):
    client.espion.reponse = _message_avec_outil()
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "lis a.py"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    })
    assert reponse.status_code == 200

    choix = reponse.json()["choices"][0]
    assert choix["finish_reason"] == "tool_calls"
    assert choix["message"]["content"] is None
    assert choix["message"]["tool_calls"][0]["function"]["name"] == "read_file"


def test_endpoint_streaming_porte_les_tool_calls(client):
    client.espion.reponse = _message_avec_outil()
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "lis a.py"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        "stream": True,
    })
    assert reponse.status_code == 200
    corps = reponse.text
    assert "read_file" in corps
    assert '"finish_reason": "tool_calls"' in corps
    assert corps.rstrip().endswith("data: [DONE]")


def test_endpoint_finish_reason_stop_sans_outil(client):
    reponse = client.post("/v1/chat/completions", json={
        "model": "modele-a",
        "messages": [{"role": "user", "content": "salut"}],
    })
    assert reponse.json()["choices"][0]["finish_reason"] == "stop"


def test_bloc_tool_use_anthropic_traduit_en_format_openai():
    """`AnthropicNativeProvider._extract_content` renvoie `{"tool_calls": blocs}`
    au format Anthropic (`type: tool_use`, `name`/`input` à la racine). Sans
    traduction, le client recevait un appel sans nom d'outil ni arguments."""
    import json as _json

    blocs = [{
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "read_file",
        "input": {"path": "a.py"},
    }]
    normalises = openai_proxy._normaliser_tool_calls(blocs)

    assert len(normalises) == 1
    appel = normalises[0]
    assert appel["type"] == "function"
    assert appel["id"] == "toolu_abc"
    assert appel["index"] == 0
    assert appel["function"]["name"] == "read_file"
    assert _json.loads(appel["function"]["arguments"]) == {"path": "a.py"}
    # Les champs Anthropic bruts ne doivent pas fuiter vers le client.
    assert "input" not in appel
    assert "name" not in appel


def test_reponse_anthropic_complete_ressort_exploitable():
    """Bout en bout depuis ce que renvoie réellement le provider Anthropic."""
    import json as _json

    brut = {"tool_calls": [
        {"type": "tool_use", "id": "toolu_1", "name": "write_file", "input": {"path": "b.py", "content": "x"}},
    ]}
    texte, tool_calls = _extraire_reponse(brut)

    assert texte == ""
    assert tool_calls[0]["function"]["name"] == "write_file"
    assert _json.loads(tool_calls[0]["function"]["arguments"])["path"] == "b.py"


def test_bloc_anthropic_sans_type_mais_avec_input_est_traduit():
    """Détection de repli quand `type` manque."""
    normalises = openai_proxy._normaliser_tool_calls([{"id": "x", "name": "f", "input": {"a": 1}}])
    assert normalises[0]["function"]["name"] == "f"
    assert normalises[0]["type"] == "function"


def test_une_seule_ligne_de_log_par_requete(client, caplog):
    """Deux `logger.info` quasi identiques étaient émis par appel."""
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="api.routes.openai_proxy"):
        client.post("/v1/chat/completions", json={
            "model": "modele-a",
            "messages": [{"role": "user", "content": "salut"}],
        })

    lignes = [r for r in caplog.records if "Requête →" in r.getMessage()]
    assert len(lignes) == 1
