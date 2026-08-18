"""
api/routes/openai_proxy.py — Endpoint OpenAI-Compatible Proxy pour IDEs (Cline, Continue, Aider).

Créé pour exposer le LLMGateway V11 comme un provider OpenAI standard.
N'importe quel IDE (Cline, Continue.dev, Aider) peut pointer son BaseURL vers :
    http://localhost:8000/v1/chat/completions

Le routage dynamique V12 (circuit breaker, fallback, token tracking) est appliqué
automatiquement à chaque appel entrant, de manière totalement transparente pour l'IDE.

Endpoints :
    GET  /v1/models                — Liste tous les modèles disponibles (format OpenAI)
    POST /v1/chat/completions      — Complétion standard + streaming SSE
    GET  /v1/providers             — Extension: état des providers + circuit breakers

Format retourné : compatible OpenAI API v1 (validé avec Cline, Continue.dev).

Tool-calling : `tools` et `tool_choice` sont transmis au provider, et les
`tool_calls` renvoyés par le modèle ressortent au format OpenAI avec
`finish_reason: "tool_calls"` (non-streaming ET streaming). Les providers CLI
(Antigravity, Claude CLI) ignorent `tools` et répondent en prose : un
avertissement est journalisé quand le catalogue indique `supports_tools = 0`.

    Streaming : `stream=true` émet un vrai flux token-par-token (SSE) pour le
    texte, via `provider.generate_stream()` relayé par un pont threadpool (la
    boucle d'événements n'est jamais bloquée). Les requêtes contenant `tools`
    restent sur le chemin bufferisé : le flux ne portant que du texte, un
    appel d'outil tronqué en morceaux serait ininterprétable par le client.

`usage` (#T357) : le champ ne porte QUE des comptages de tokens réels remontés
par le provider amont ; quand il n'y en a pas, il est entièrement omis (il est
facultatif côté OpenAI). Il n'est jamais estimé — auparavant le chemin
non-streamé y publiait un comptage de MOTS, sur lequel les clients calaient leur
fenêtre de contexte et leur budget.

Limites connues (un agent de code comme OpenCode/Cline s'y heurtera) :
    - `/v1/models` n'expose pas les options par modèle (reasoning effort,
      thinking mode, température imposée).
    - `tool_choice` n'est relayé que par les providers OpenAI-compatibles ; les
      providers natifs (Gemini natif, Anthropic natif) reçoivent `tools` mais
      pas la contrainte de choix.

Auteur : Antigravity IDE + Axel — 2026-06-15 (reconstruit depuis .pyc V12)
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

logger = logging.getLogger(__name__)

# Router FastAPI — préfixe /v1 défini dans gui_server.py lors du include_router
router = APIRouter(tags=["OpenAI Proxy (Cline/Continue)"])


# ── Modèles Pydantic ──────────────────────────────────────────────────────────

class ModeleInconnuError(Exception):
    """Le `model` demandé ne correspond ni à un provider enregistré, ni à un tier."""


# Tiers de routage acceptés comme valeur de `model`, alias inclus (cf.
# LLMGateway._resolve_tier_models). La liste est en dur ici parce que
# _resolve_tier_models rabat silencieusement tout nom inconnu sur "moyen" : elle
# ne peut donc pas servir à distinguer un tier d'un modèle qui n'existe pas.
_TIERS_ROUTAGE = {
    "leger", "moyen", "fort", "automatique",
    "flash", "standard", "reasoner", "pro", "local", "antigravity",
}

# Étiquettes du transcript de repli (providers qui ignorent `messages`).
_ETIQUETTES_ROLE = {
    "user": "Utilisateur",
    "assistant": "Assistant",
    "tool": "Résultat d'outil",
    "function": "Résultat d'outil",
}


class ChatMessage(BaseModel):
    """Message au format OpenAI Chat."""
    role: str
    # `content` peut être une chaîne OU une liste de parts (format multimodal
    # OpenAI, envoyé par plusieurs IDEs). Le typer `str | None` faisait échouer
    # la requête en 422 avant même d'atteindre le moteur.
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """Requête de completion au format OpenAI."""
    model: str = Field(default="deepseek-chat", description="Nom du modèle / provider")
    messages: list[ChatMessage]
    temperature: float | None = 0.7
    max_tokens: int | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: Any | None = None
    n: int | None = 1
    # [#T306] Champ moteur (non-OpenAI) : les clients du proxy (Cline,
    # Continue, OpenCode…) peuvent l'envoyer pour rattacher leur dépense à une
    # session ; sans lui, la ligne token_usage part avec session_id NULL — et
    # avant ce champ, pydantic AVAALAIT silencieusement la clé (extra fields
    # ignorés par défaut), l'attribution était structurellement impossible.
    session_id: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _texte_du_contenu(content: Any) -> str:
    """Aplatit un `content` OpenAI (chaîne, ou liste de parts multimodales)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    morceaux = []
    for part in content:
        if isinstance(part, str):
            morceaux.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            morceaux.append(part.get("text") or "")
        # Les parts non textuelles (image_url…) sont ignorées : aucun provider
        # du moteur ne les consomme par ce chemin.
    return "\n".join(m for m in morceaux if m)


def _convertir_messages(messages: list[ChatMessage]) -> tuple[str, str, list[dict]]:
    """
    Traduit la conversation entrante vers ce qu'attendent les providers.

    Avant, TOUS les messages non-`system` (user, assistant ET tool) étaient
    concaténés avec des `\\n` en un seul bloc passé comme `user_prompt` : le
    modèle ne pouvait plus distinguer ses propres réponses de celles de
    l'utilisateur, et un historique multi-tours perdait tout son sens.

    Retourne :
      - `system_prompt` : concaténation des messages `system` ;
      - `transcript` : repli textuel pour les providers qui ignorent `messages`
        (GeminiCLIProvider, ClaudeCLIProvider…), avec les rôles ÉTIQUETÉS ;
      - `messages_provider` : la liste OpenAI complète, message système inclus —
        c'est la convention déjà utilisée par `agents/executor.py` pour sa
        boucle ReAct multi-tours.
    """
    system_parts: list[str] = []
    tours: list[ChatMessage] = []

    for msg in messages:
        if msg.role.lower() == "system":
            texte = _texte_du_contenu(msg.content)
            if texte:
                system_parts.append(texte)
        else:
            tours.append(msg)

    system_prompt = "\n".join(system_parts).strip()

    lignes_transcript = []
    messages_provider: list[dict] = []
    if system_prompt:
        messages_provider.append({"role": "system", "content": system_prompt})

    for msg in tours:
        role = msg.role.lower()
        texte = _texte_du_contenu(msg.content)

        entree: dict[str, Any] = {"role": role, "content": texte}
        if msg.name:
            entree["name"] = msg.name
        if msg.tool_calls:
            entree["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            entree["tool_call_id"] = msg.tool_call_id
        messages_provider.append(entree)

        etiquette = _ETIQUETTES_ROLE.get(role, role.capitalize())
        if texte:
            lignes_transcript.append(f"{etiquette} : {texte}")
        elif msg.tool_calls:
            # Message d'assistant qui ne porte QUE des appels d'outils.
            noms = [
                (tc.get("function") or {}).get("name", "?")
                for tc in msg.tool_calls
                if isinstance(tc, dict)
            ]
            lignes_transcript.append(f"{etiquette} : [appel d'outil {', '.join(noms)}]")

    return system_prompt, "\n\n".join(lignes_transcript).strip(), messages_provider


def _resoudre_provider(gw, model_name: str):
    """
    Résout le champ `model` en provider utilisable.

    Deux corrections par rapport à l'accès brut `gw.providers[model_name]` :

    1. On passe par `get_provider()` / `get_provider_for_tier()`, donc par le
       wrapping FallbackProvider — Circuit Breaker, retry 429, cascade et cache
       sémantique. C'est exactement le contournement corrigé sous #T212 côté
       Planner, resté ouvert ici alors que le docstring du module annonce que
       « le routage dynamique V12 est appliqué automatiquement ».
    2. Un modèle inconnu lève `ModeleInconnuError` au lieu d'être servi par le
       PREMIER provider du registre — `next(iter(gw.providers.values()))` —
       tout en étant étiqueté du nom demandé dans la réponse. La réponse était
       alors silencieusement produite par un autre modèle que celui facturé et
       affiché à l'utilisateur.

    Note : `get_provider()` normalise déjà la casse (`name.lower()`), là où le
    test d'appartenance précédent était sensible à la casse.
    """
    if model_name.lower() in _TIERS_ROUTAGE:
        from core.llm_gateway import load_config
        _, provider = gw.get_provider_for_tier(model_name, load_config())
        return provider
    try:
        return gw.get_provider(model_name)
    except ValueError as e:
        raise ModeleInconnuError(str(e)) from e


def _avertir_si_outils_non_supportes(model_name: str) -> None:
    """
    Journalise un avertissement si le modèle demandé est catalogué sans support
    des outils (`supports_tools = 0` dans models_registry.db).

    On n'échoue PAS sur ce signal : le catalogue peut être incomplet, et les
    providers CLI (Antigravity, Claude CLI) ignorent `tools` de toute façon en
    retournant de la prose. Mieux vaut une trace exploitable côté moteur qu'un
    refus sur une métadonnée qui n'est pas la source de vérité.
    """
    if model_name.lower() in _TIERS_ROUTAGE:
        return  # un tier n'est pas un identifiant de modèle du catalogue
    try:
        from core.models_db import get_model
        modele = get_model(model_name)
    except Exception as e:
        logger.debug(f"[OPENAI_PROXY] Catalogue indisponible pour '{model_name}' : {e}")
        return
    if modele and not modele.get("supports_tools"):
        logger.warning(
            f"[OPENAI_PROXY] Le client a envoyé des outils mais le modèle "
            f"'{model_name}' est catalogué sans support du tool-calling "
            f"(supports_tools=0) : le modèle répondra probablement en texte."
        )


def _erreur_openai(status: int, message: str, code: str, param: str | None = None) -> JSONResponse:
    """Erreur au format de l'API OpenAI, que les clients savent afficher."""
    return JSONResponse(
        status_code=status,
        content={"error": {
            "message": message,
            "type": "invalid_request_error",
            "param": param,
            "code": code,
        }},
    )


def _normaliser_tool_calls(tool_calls: list | None) -> list | None:
    """
    Traduit les appels d'outils des providers vers le format attendu par les
    clients OpenAI.

    Deux formes circulent dans le moteur :

    - **OpenAI** — `{id, type: "function", function: {name, arguments}}`, produit
      par `openai_compat_provider`, `LMStudioProvider` et
      `gemini_native._translate_tool_calls`. Il y manque `index`, dont les
      clients se servent pour recoller les fragments en streaming.
    - **Anthropic** — `{type: "tool_use", id, name, input}`, renvoyé tel quel par
      `AnthropicNativeProvider._extract_content` (`{"tool_calls": blocs}`). Sans
      traduction, le client recevrait un appel de type `tool_use`, sans nom
      d'outil et avec des arguments vides, tout en voyant
      `finish_reason: "tool_calls"` — donc un appel inexploitable.
    """
    if not tool_calls:
        return None
    normalises = []
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            continue

        if tc.get("type") == "tool_use" or ("input" in tc and "function" not in tc):
            # Bloc Anthropic : le nom est à la racine et les arguments sont dans
            # `input` (un dict, pas une chaîne JSON).
            entree = {
                "index": i,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("input"),
                },
            }
        else:
            entree = dict(tc)
            entree.setdefault("index", i)
            entree.setdefault("type", "function")
            entree.setdefault("id", f"call_{uuid.uuid4().hex[:12]}")
            entree["function"] = dict(entree.get("function") or {})

        fonction = entree["function"]
        # `arguments` DOIT être une chaîne JSON côté OpenAI ; les providers
        # natifs renvoient un dict.
        arguments = fonction.get("arguments")
        if isinstance(arguments, (dict, list)):
            fonction["arguments"] = json.dumps(arguments, ensure_ascii=False)
        elif arguments is None:
            fonction["arguments"] = "{}"
        normalises.append(entree)
    return normalises or None


def _extraire_reponse(brut: Any) -> tuple[str, list | None]:
    """
    Sépare le texte et les appels d'outils de ce que retourne un provider.

    Contrat du moteur (openai_compat_provider, gemini_native, LMStudioProvider) :
    `generate()` retourne soit une chaîne, soit le `message` OpenAI complet quand
    le modèle a décidé d'appeler un outil.

    Ce second cas était écrasé par :

        response_text = response_text.get("content", str(response_text))

    qui renvoie `None` quand la clé `content` EXISTE avec la valeur `None` — soit
    exactement la forme d'un appel d'outil. L'IDE recevait donc la chaîne
    littérale "None" à la place de l'appel.
    """
    if isinstance(brut, dict):
        tool_calls = _normaliser_tool_calls(brut.get("tool_calls"))
        contenu = brut.get("content")
        if contenu is None and not tool_calls:
            # Dict sans contenu ni outil : on sérialise plutôt que de perdre l'info.
            return str(brut), None
        return (contenu or ""), tool_calls
    return ("" if brut is None else str(brut)), None


def _entier_de_comptage(valeur: Any) -> int | None:
    """Retourne `valeur` si c'est un comptage de tokens exploitable, sinon None."""
    # `bool` est un `int` en Python : un True s'inviterait comme « 1 token ».
    if isinstance(valeur, bool) or not isinstance(valeur, int):
        return None
    return valeur


# Schémas d'usage non-OpenAI qui circulent dans le moteur, et le nom OpenAI de
# chaque compteur. RENOMMAGE STRICT : aucune valeur n'est dérivée ni calculée.
_SCHEMAS_USAGE_ETRANGERS = (
    # Gemini natif — `usageMetadata`, réellement remonté par
    # gemini_native.generate_stream() sur son chunk final.
    {
        "prompt_tokens": "promptTokenCount",
        "completion_tokens": "candidatesTokenCount",
        "total_tokens": "totalTokenCount",
    },
    # Anthropic natif.
    {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
    },
)


def _normaliser_usage(usage_brut: Any) -> dict | None:
    """
    Rend un `usage` exploitable par un client OpenAI, ou None s'il n'y en a pas.

    [#T357] Règle centrale : **on ne fabrique jamais de chiffre**. Soit le
    provider amont a renvoyé un comptage réel de tokens, soit le champ `usage`
    est entièrement omis de la réponse — il est facultatif dans l'API OpenAI, et
    un chiffre faux est pire qu'un chiffre absent puisque le client pilote sa
    fenêtre de contexte et son budget avec. Le comptage en MOTS qui remplissait
    ce champ annonçait plusieurs fois moins que la réalité (prompt médian mesuré
    le 17/08 sur 630 appels : 67 523 tokens réels).

    Trois schémas existent côté providers :
      - OpenAI (`prompt_tokens`…) : transmis **tel quel**, extras compris
        (`prompt_cache_hit_tokens`, `prompt_tokens_details`…) ;
      - Gemini natif (`usageMetadata`) et Anthropic natif (`input_tokens`…) :
        les compteurs sont RENOMMÉS vers les noms OpenAI. `total_tokens` n'est
        posé que si le provider l'a fourni — il n'est pas recalculé.
    """
    if not isinstance(usage_brut, dict):
        return None

    # Déjà au format OpenAI : rien à traduire, on ne touche à rien.
    if (_entier_de_comptage(usage_brut.get("prompt_tokens")) is not None
            or _entier_de_comptage(usage_brut.get("completion_tokens")) is not None):
        return usage_brut

    for schema in _SCHEMAS_USAGE_ETRANGERS:
        traduit = {}
        for cle_openai, cle_source in schema.items():
            valeur = _entier_de_comptage(usage_brut.get(cle_source))
            if valeur is not None:
                traduit[cle_openai] = valeur
        # Un `total_tokens` seul ne dit pas au client ce qu'il a consommé en
        # entrée : on exige au moins un des deux compteurs principaux.
        if "prompt_tokens" in traduit or "completion_tokens" in traduit:
            return traduit

    return None


def _extraire_usage_reel(brut: Any, usage_sink: dict | None = None) -> dict | None:
    """
    Récupère l'usage réel porté par la réponse d'un provider (chemin bufferisé).

    `generate()` retourne aujourd'hui soit une chaîne, soit le `message` OpenAI
    quand le modèle appelle un outil. Dans le cas chaîne, l'usage réel de la
    réponse HTTP est enregistré en base par `_record_usage()` côté provider mais
    ne peut pas être porté par le type de retour : les providers
    OpenAI-compatibles le déposent alors dans le dict `_usage_sink` fourni par
    l'appelant (canal latéral propre à la requête — jamais d'état partagé sur
    l'instance). `brut` reste prioritaire (cas tool_calls, où le dict porte
    déjà l'usage) ; à défaut on lit le sink. Aucun des deux ne porte rien →
    None, et le champ sera omis — surtout pas une estimation de repli.
    """
    if isinstance(brut, dict):
        usage = _normaliser_usage(brut.get("usage"))
        if usage is not None:
            return usage
    if isinstance(usage_sink, dict):
        return _normaliser_usage(usage_sink.get("usage"))
    return None


def _make_completion_response(model: str, content: str, finish_reason: str = "stop",
                               tool_calls: list | None = None,
                               usage: dict | None = None) -> dict:
    """
    Construit une réponse OpenAI-compatible (non-streaming).

    [#T357] `usage` n'est posé que si le provider amont a fourni un comptage
    réel (cf. `_normaliser_usage`) ; sinon la clé est absente de la réponse.
    """
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    reponse: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
    }
    if usage:
        reponse["usage"] = usage
    return reponse


def _make_stream_chunk(model: str, delta_content: str, finish_reason: str | None = None,
                       tool_calls: list | None = None, usage: dict | None = None) -> str:
    """Formate un chunk SSE compatible OpenAI streaming."""
    delta: dict[str, Any] = {}
    if delta_content:
        delta["content"] = delta_content
    if tool_calls:
        # Les clients recollent les fragments par `index` (posé par
        # _normaliser_tool_calls) ; ici tout arrive d'un coup.
        delta["tool_calls"] = tool_calls
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    if usage:
        # [#T357] Usage réel uniquement (déjà passé par `_normaliser_usage`) :
        # aucun chunk ne porte de comptage inventé, le champ est simplement
        # absent quand le provider n'a rien remonté.
        chunk["usage"] = usage
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _streamer_texte(provider, model_name: str, system_prompt: str, transcript: str,
                          messages_provider: list[dict], temperature: float | None,
                          max_tokens: int | None, session_id: str | None = None):
    """
    Générateur SSE token-par-token (texte uniquement) pour `stream=true`.

    `provider.generate_stream()` est un générateur SYNCHRONE : itéré nu dans la
    route async, il gèlerait la boucle d'événements de toute l'application
    (tous les autres clients bloqués pendant la génération). Le pont
    `iterate_in_threadpool` délègue chaque `next()` à un thread du pool : la
    boucle d'événements reste libre, chaque token part dès qu'il est produit.

    Une exception survenant APRÈS le premier token ne peut plus être
    transformée en réponse d'erreur HTTP (les en-têtes sont déjà partis) : on
    termine le flux proprement (chunk final + [DONE]) et on journalise —
    l'erreur n'est pas avalée en silence.

    [#T343-C] L'étiquette d'agent est posée ICI, pas sur la route : la route
    rend `StreamingResponse` immédiatement et c'est Starlette qui consomme le
    générateur APRÈS, hors du bloc `with`. Posée autour de l'itération, elle
    atteint le thread du pool via le pont `iterate_in_threadpool`
    (anyio.to_thread.run_sync copie le contexte, vérifié à la mesure) —
    c'est là que `record_usage` écrit la ligne de dépense.
    """
    usage_reel = None
    tokens_emis = 0
    from core.agent_trace import agent_courant
    with agent_courant("proxy_v1"):
        try:
            async for item in iterate_in_threadpool(provider.generate_stream(
                system_prompt, transcript,
                messages=messages_provider,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=session_id,
            )):
                token = (item or {}).get("token") or ""
                if token:
                    tokens_emis += 1
                    yield _make_stream_chunk(model_name, token)
                # [#T357] Le contrat `generate_stream` est {"token", "done",
                # "usage"} mais tous les providers ne parlent pas le schéma
                # OpenAI : gemini_native remonte un `usageMetadata`
                # (promptTokenCount…), qui transmis tel quel donnerait au client
                # un `usage` dont aucune clé attendue n'existe.
                usage_candidat = _normaliser_usage(item.get("usage"))
                if usage_candidat:
                    usage_reel = usage_candidat
                if item.get("done"):
                    break
        except Exception as e:
            # En-têtes HTTP déjà partis : impossible de répondre en erreur HTTP. On
            # ferme le flux proprement pour que le client ne reste pas en attente
            # d'un chunk qui ne viendra jamais.
            logger.error(
                f"[OPENAI_PROXY] Streaming interrompu après {tokens_emis} token(s) "
                f"émis : {e}"
            )
            yield _make_stream_chunk(model_name, "", finish_reason="stop", usage=usage_reel)
            yield "data: [DONE]\n\n"
            return
    yield _make_stream_chunk(model_name, "", finish_reason="stop", usage=usage_reel)
    yield "data: [DONE]\n\n"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/v1/models")
async def list_models():
    """
    Liste tous les modèles disponibles au format OpenAI.
    Compatible avec les dropdowns de Cline / Continue.dev / OpenWebUI.
    """
    try:
        from core.llm_gateway import LLMGateway
        gw = LLMGateway()
        models_list = []
        for name in sorted(list(gw.providers.keys())):
            models_list.append({
                "id": name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moteur-agents",
            })
        # Les tiers de routage sont des valeurs de `model` valides (cf.
        # _resoudre_provider) : sans eux dans cette liste, ils resteraient
        # indécouvrables, et le message de la 404 « voir /v1/models » serait faux.
        for tier in ("leger", "moyen", "fort", "automatique"):
            models_list.append({
                "id": tier,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moteur-agents (tier de routage)",
            })
        return {"object": "list", "data": models_list}
    except Exception as e:
        logger.error(f"[OPENAI_PROXY] list_models erreur : {e}")
        return {"object": "list", "data": []}


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """
    Endpoint de complétion chat compatible OpenAI.
    Supporte stream=True (SSE) et stream=False (JSON direct).

    Le champ `model` de la requête est utilisé comme nom de provider
    dans le LLMGateway. Ex: "deepseek-chat", "gemini-3.5-flash-free", etc.
    """
    system_prompt, transcript, messages_provider = _convertir_messages(request.messages)

    if not transcript:
        raise HTTPException(status_code=400, detail="Aucun message utilisateur fourni.")

    model_name = request.model

    try:
        from core.llm_gateway import LLMGateway
        gw = LLMGateway()
        provider = _resoudre_provider(gw, model_name)

        # Outils : transmis au provider seulement s'il y en a. Les providers
        # OpenAI-compatibles les recopient dans leur payload ; gemini_native
        # traduit les function declarations. Avant, `tools`/`tool_choice`
        # étaient déclarés par le schéma puis silencieusement jetés.
        options_outils: dict[str, Any] = {}
        if request.tools:
            options_outils["tools"] = request.tools
            if request.tool_choice is not None:
                options_outils["tool_choice"] = request.tool_choice
            _avertir_si_outils_non_supportes(model_name)

        logger.info(
            f"[OPENAI_PROXY] Requête → model={model_name}, stream={request.stream}, "
            f"{len(messages_provider)} message(s), {len(request.tools or [])} outil(s)"
        )

        # ── Streaming réel token-par-token (texte uniquement) ──
        # Le flux ne porte que des deltas de contenu : une requête AVEC `tools`
        # reste sur le chemin bufferisé ci-dessous, car un appel d'outil tronqué
        # en morceaux serait ininterprétable par le client.
        if request.stream and not request.tools:
            logger.info("[OPENAI_PROXY] Chemin streaming token-par-token (texte)")
            return StreamingResponse(
                _streamer_texte(
                    provider, model_name,
                    system_prompt or "Tu es un assistant IA expert.",
                    transcript, messages_provider,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    session_id=request.session_id,
                ),
                media_type="text/event-stream",
            )

        # ── Chemin bufferisé : non-streaming, ou outils en streaming ──
        logger.info("[OPENAI_PROXY] Chemin bufferisé (non-streaming ou outils en streaming)")

        # [#T306] La dépense du proxy doit être rattachée : session_id (si le
        # client l'envoie) et étiquette d'agent (Cline/Continue consomment hors
        # de tout agent — même motif que coding_front #T308).
        from core.agent_trace import agent_courant
        # [#T357] Canal latéral d'usage réel : un dict propre à la requête,
        # déposé dans les kwargs sous `_usage_sink`. Les providers
        # OpenAI-compatibles y écrivent l'usage de la réponse HTTP quand
        # generate() rend une simple chaîne (le type de retour ne peut pas le
        # porter). Les providers qui ne le connaissent pas l'ignorent —
        # l'usage reste alors omis, jamais estimé.
        usage_sink: dict[str, Any] = {}
        with agent_courant("proxy_v1"):
            brut = await asyncio.to_thread(
                provider.generate,
                system_prompt or "Tu es un assistant IA expert.",
                transcript,
                messages=messages_provider,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                session_id=request.session_id,
                _usage_sink=usage_sink,
                **options_outils,
            )

        response_text, tool_calls = _extraire_reponse(brut)
        # [#T357] Usage réel s'il est porté par la réponse du provider ou par
        # le sink, sinon None : le champ sera omis, jamais estimé.
        usage_reel = _extraire_usage_reel(brut, usage_sink)
        finish_reason = "tool_calls" if tool_calls else "stop"

        if request.stream:
            # Outils en streaming : la réponse complète (appel d'outil compris)
            # part en un seul chunk — le flux ne porte que du texte. C'est le cas
            # normal d'un client agentique : le chunk final doit donc porter
            # l'usage réel quand il existe (#T357).
            async def _stream_gen():
                yield _make_stream_chunk(model_name, response_text, tool_calls=tool_calls)
                yield _make_stream_chunk(
                    model_name, "", finish_reason=finish_reason, usage=usage_reel,
                )
                yield "data: [DONE]\n\n"
            return StreamingResponse(_stream_gen(), media_type="text/event-stream")
        else:
            # [#T357] Plus de comptage de MOTS présenté comme des tokens : ce
            # champ pilotait la fenêtre de contexte et le budget du client avec
            # un chiffre plusieurs fois trop bas. Sans usage amont, pas de clé.
            return JSONResponse(_make_completion_response(
                model_name, response_text,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                usage=usage_reel,
            ))

    except ModeleInconnuError as e:
        # Doit précéder le `except Exception` ci-dessous, sinon un modèle inconnu
        # ressortirait en 500 au lieu d'une 404 exploitable par le client.
        logger.warning(f"[OPENAI_PROXY] Modèle inconnu demandé : {model_name} ({e})")
        return _erreur_openai(
            404,
            f"Le modèle '{model_name}' n'existe pas dans ce moteur. "
            f"Voir GET /v1/models pour la liste des modèles et tiers disponibles.",
            code="model_not_found",
            param="model",
        )
    except Exception as e:
        logger.error(f"[OPENAI_PROXY] chat_completions erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v1/providers")
async def get_providers_status():
    """
    Extension non-standard : état des providers + circuit breakers.
    Utile pour le monitoring depuis l'HMI V12.
    """
    try:
        from core.llm.circuit_breaker import CircuitBreaker
        from core.llm_gateway import LLMGateway
        gw = LLMGateway()
        providers_list = []
        for name, provider in gw.providers.items():
            cb = CircuitBreaker.get_or_create(name)
            providers_list.append({
                "name": name,
                "type": type(provider).__name__,
                "circuit_breaker": {
                    "state": cb.state.value if hasattr(cb.state, "value") else getattr(cb, "state", "UNKNOWN"),
                    "failure_count": getattr(cb, "is_open", 0),
                },
                "available": not cb.is_open if hasattr(cb, "is_open") else True,
            })
        return {"providers": providers_list, "count": len(providers_list)}
    except Exception as e:
        logger.error(f"[OPENAI_PROXY] get_providers_status erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))
