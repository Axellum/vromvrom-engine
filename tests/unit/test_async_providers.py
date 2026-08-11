"""Tests de la fondation D5 — chemin async natif des providers (httpx)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm.providers.base import LLMProvider
from core.openai_compat_provider import OpenAICompatibleProvider, SharedAsyncHTTPPool


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_name="test",
        base_url="https://api.test/v1/chat/completions",
        api_key="fake-key",
        model="test-model",
    )


def _fake_client(resp_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=resp_json)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


def test_generate_async_returns_content():
    """generate_async natif renvoie le contenu et fait UN appel réseau awaité."""
    prov = _provider()
    prov._record_usage = MagicMock()  # pas d'I/O DB en test
    client = _fake_client({"choices": [{"message": {"content": "bonjour"}}],
                           "usage": {"total_tokens": 5}})
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        out = asyncio.run(prov.generate_async("sys", "user"))
    assert out == "bonjour"
    client.post.assert_awaited_once()


def test_generate_async_returns_tool_calls_message():
    """Si le LLM renvoie des tool_calls, generate_async renvoie le message complet."""
    prov = _provider()
    prov._record_usage = MagicMock()
    msg = {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "f"}}]}
    client = _fake_client({"choices": [{"message": msg}], "usage": {}})
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        out = asyncio.run(prov.generate_async("sys", "user"))
    assert isinstance(out, dict) and "tool_calls" in out


def test_async_pool_singleton():
    """Le pool async renvoie le même client tant qu'il n'est pas fermé."""
    SharedAsyncHTTPPool._client = None
    c1 = SharedAsyncHTTPPool.get_client()
    c2 = SharedAsyncHTTPPool.get_client()
    assert c1 is c2
    asyncio.run(SharedAsyncHTTPPool.aclose())
    assert SharedAsyncHTTPPool._client is None


def test_http_pool_se_rearme_apres_close():
    """[#T268] get_session() -> close() -> get_session() doit rendre une session
    utilisable : le pool ne doit pas rester définitivement mort après un close()."""
    from core.openai_compat_provider import SharedHTTPPool

    SharedHTTPPool.get_session()
    SharedHTTPPool.close()
    session = SharedHTTPPool.get_session()
    assert session is not None
    assert hasattr(session, "post")


def test_http_pool_singleton():
    """Deux get_session() consécutifs rendent LA MÊME session (keep-alive TLS)."""
    from core.openai_compat_provider import SharedHTTPPool

    SharedHTTPPool.close()
    s1 = SharedHTTPPool.get_session()
    s2 = SharedHTTPPool.get_session()
    assert s1 is s2


def test_base_generate_async_fallback_runs_sync():
    """Le fallback de base exécute generate() (sync) dans un thread."""

    class DummyProvider(LLMProvider):
        def generate(self, system_prompt, user_prompt, **kwargs):
            return f"sync:{user_prompt}"

        def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
            return {}

    prov = DummyProvider()
    out = asyncio.run(prov.generate_async("sys", "salut"))
    assert out == "sync:salut"


def test_generate_async_relaye_tool_choice():
    """`tool_choice` (forcer/interdire un outil) était ignoré : seul `tools`
    était recopié dans le payload, la contrainte du client se perdait."""
    prov = _provider()
    prov._record_usage = MagicMock()
    client = _fake_client({"choices": [{"message": {"content": "ok"}}], "usage": {}})
    outils = [{"type": "function", "function": {"name": "f"}}]
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        asyncio.run(prov.generate_async("sys", "user", tools=outils, tool_choice="required"))

    payload = client.post.await_args.kwargs["json"]
    assert payload["tools"] == outils
    assert payload["tool_choice"] == "required"


def test_generate_sync_relaye_tool_choice():
    """Même relais sur le chemin synchrone (celui qu'emprunte le proxy /v1)."""
    from core.openai_compat_provider import SharedHTTPPool

    prov = _provider()
    prov._record_usage = MagicMock()
    session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    session.post = MagicMock(return_value=resp)
    outils = [{"type": "function", "function": {"name": "f"}}]
    with patch.object(SharedHTTPPool, "get_session", return_value=session):
        prov.generate("sys", "user", tools=outils, tool_choice={"type": "function", "function": {"name": "f"}})

    payload = session.post.call_args.kwargs["json"]
    assert payload["tools"] == outils
    assert payload["tool_choice"]["function"]["name"] == "f"


def test_pas_de_tool_choice_sans_valeur():
    """Un `tool_choice=None` ne doit pas apparaître dans le payload."""
    prov = _provider()
    prov._record_usage = MagicMock()
    client = _fake_client({"choices": [{"message": {"content": "ok"}}], "usage": {}})
    with patch.object(SharedAsyncHTTPPool, "get_client", return_value=client):
        asyncio.run(prov.generate_async("sys", "user", tool_choice=None))

    assert "tool_choice" not in client.post.await_args.kwargs["json"]


# ──────────────────────────────────────────────────────────────────────
# ClaudeInstructionsWrapper — le wrapper ne doit pas annuler la fondation D5
# ──────────────────────────────────────────────────────────────────────


class _EspionChemin(LLMProvider):
    """Provider factice qui note le chemin emprunté (sync ou async natif)."""

    def __init__(self):
        self.chemins: list[str] = []
        self.systemes: list[str] = []
        self.kwargs: list[dict] = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.chemins.append("sync")
        self.systemes.append(system_prompt)
        self.kwargs.append(kwargs)
        return "sync"

    def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
        self.chemins.append("sync_structured")
        self.systemes.append(system_prompt)
        self.kwargs.append(kwargs)
        return {}

    async def generate_async(self, system_prompt, user_prompt, **kwargs):
        self.chemins.append("async_natif")
        self.systemes.append(system_prompt)
        self.kwargs.append(kwargs)
        return "async"

    async def generate_structured_async(self, system_prompt, user_prompt, schema, **kwargs):
        self.chemins.append("async_natif_structured")
        self.systemes.append(system_prompt)
        self.kwargs.append(kwargs)
        return {"ok": True}


def _wrapper_avec_instructions(espion, instructions="\n\n=== CONVENTIONS ==="):
    """Wrapper dont l'injection est figée (indépendante de la présence de CLAUDE.md)."""
    import time as _time

    from core.llm.providers.deepseek import ClaudeInstructionsWrapper

    wrap = ClaudeInstructionsWrapper(espion)
    wrap._cached_instructions = instructions
    wrap._last_loaded = _time.time()
    return wrap


def test_wrapper_emprunte_le_chemin_async_natif():
    """Régression : le wrapper n'implémentait aucune méthode async, il héritait
    donc du fallback `to_thread(self.generate)` de LLMProvider — et comme
    LLMGateway._get_raw_provider() enveloppe TOUS les providers, le chemin
    httpx natif de D5 n'était jamais emprunté par le moteur."""
    espion = _EspionChemin()
    out = asyncio.run(_wrapper_avec_instructions(espion).generate_async("sys", "user"))

    assert espion.chemins == ["async_natif"], (
        f"chemin sync emprunté malgré un provider async natif : {espion.chemins}"
    )
    assert out == "async"


def test_wrapper_structured_emprunte_le_chemin_async_natif():
    espion = _EspionChemin()
    out = asyncio.run(
        _wrapper_avec_instructions(espion).generate_structured_async("sys", "user", {})
    )

    assert espion.chemins == ["async_natif_structured"]
    assert out == {"ok": True}


def test_wrapper_async_conserve_injection_claude_md():
    """Sync et async doivent enrichir le prompt système à l'identique — sinon
    un agent de code recevrait les conventions sur un chemin et pas sur l'autre."""
    espion = _EspionChemin()
    wrap = _wrapper_avec_instructions(espion)

    wrap.generate("sys", "user", conventions_projet=True)
    asyncio.run(wrap.generate_async("sys", "user", conventions_projet=True))

    assert espion.systemes[0] == espion.systemes[1] == "sys\n\n=== CONVENTIONS ==="


# ──────────────────────────────────────────────────────────────────────
# [T287] Les conventions CLAUDE.md ne partent QUE sur demande
# ──────────────────────────────────────────────────────────────────────


def test_aucune_injection_par_defaut():
    """Le contrat s'inverse : l'injection était inconditionnelle sur TOUS les
    appels de TOUS les providers (+5056 caractères mesurés, ~1264 tokens), y
    compris le chat, le vocal et le routage. Elle est désormais sur demande."""
    espion = _EspionChemin()
    asyncio.run(_wrapper_avec_instructions(espion).generate_async("sys", "user"))

    assert espion.systemes == ["sys"], (
        f"conventions injectées sans que l'appelant les demande : {espion.systemes}"
    )


def test_le_drapeau_ne_descend_jamais_dans_le_provider():
    """`conventions_projet` est un drapeau interne au moteur : s'il atteignait un
    provider, il partirait dans le payload HTTP et pourrait être rejeté."""
    espion = _EspionChemin()
    asyncio.run(
        _wrapper_avec_instructions(espion).generate_async(
            "sys", "user", conventions_projet=True
        )
    )

    assert "conventions_projet" not in espion.kwargs[0]


def test_injection_atteint_le_message_systeme_de_messages():
    """Le point qui rendait l'injection inutile pour les agents de code : les
    providers OpenAI-compatibles IGNORENT `system_prompt` quand `messages=` est
    fourni (cas de la boucle ReAct de l'Executor). Mesuré avant correctif :
    5067 caractères de prompt système sur un appel simple, 12 sur un `messages=`."""
    espion = _EspionChemin()
    messages = [
        {"role": "system", "content": "SYS-EXECUTOR"},
        {"role": "user", "content": "écris le fichier"},
    ]
    asyncio.run(
        _wrapper_avec_instructions(espion).generate_async(
            "sys", "user", messages=messages, conventions_projet=True
        )
    )

    envoyes = espion.kwargs[0]["messages"]
    assert envoyes[0]["content"] == "SYS-EXECUTOR\n\n=== CONVENTIONS ==="
    assert envoyes[1] == {"role": "user", "content": "écris le fichier"}


def test_la_liste_de_messages_de_lappelant_nest_pas_modifiee():
    """LE piège : la boucle ReAct réutilise sa liste d'un tour à l'autre. Une
    modification en place empilerait les conventions à chaque itération jusqu'à
    saturer la fenêtre de contexte."""
    espion = _EspionChemin()
    wrap = _wrapper_avec_instructions(espion)
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u"},
    ]

    for _ in range(3):  # trois tours ReAct sur la MÊME liste
        asyncio.run(wrap.generate_async("sys", "user", messages=messages, conventions_projet=True))

    assert messages[0]["content"] == "SYS", "la liste de l'appelant a été mutée"
    for envoi in espion.kwargs:
        assert envoi["messages"][0]["content"] == "SYS\n\n=== CONVENTIONS ==="


def test_messages_sans_role_system_recoit_un_message_systeme():
    espion = _EspionChemin()
    asyncio.run(
        _wrapper_avec_instructions(espion).generate_async(
            "sys", "user", messages=[{"role": "user", "content": "u"}], conventions_projet=True
        )
    )

    envoyes = espion.kwargs[0]["messages"]
    assert envoyes[0]["role"] == "system"
    assert envoyes[0]["content"] == "=== CONVENTIONS ==="
    assert envoyes[1]["role"] == "user"


def test_sans_claude_md_le_drapeau_ne_casse_rien():
    """Un dépôt sans CLAUDE.md (cas du CI) : demander les conventions doit être
    un no-op, pas une erreur."""
    espion = _EspionChemin()
    wrap = _wrapper_avec_instructions(espion, instructions="")
    out = asyncio.run(wrap.generate_async("sys", "user", conventions_projet=True))

    assert out == "async"
    assert espion.systemes == ["sys"]
    assert "conventions_projet" not in espion.kwargs[0]


def test_wrapper_async_supporte_un_provider_sans_async_natif():
    """Un provider qui n'implémente que le synchrone (CLI, LM Studio…) doit
    continuer à fonctionner via le fallback to_thread de la classe de base."""

    class _SyncSeul(LLMProvider):
        def generate(self, system_prompt, user_prompt, **kwargs):
            return f"sync:{system_prompt}"

        def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
            return {}

    out = asyncio.run(
        _wrapper_avec_instructions(_SyncSeul()).generate_async(
            "sys", "user", conventions_projet=True
        )
    )
    assert out == "sync:sys\n\n=== CONVENTIONS ==="
