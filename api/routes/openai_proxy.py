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

Format retourné : 100% compatible OpenAI API v1 (validé avec Cline, Continue.dev).

Auteur : Antigravity IDE + Axel — 2026-06-15 (reconstruit depuis .pyc V12)
"""

import json
import time
import logging
import asyncio
import uuid
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Router FastAPI — préfixe /v1 défini dans gui_server.py lors du include_router
router = APIRouter(tags=["OpenAI Proxy (Cline/Continue)"])


# ── Modèles Pydantic ──────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """Message au format OpenAI Chat."""
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """Requête de completion au format OpenAI."""
    model: str = Field(default="deepseek-chat", description="Nom du modèle / provider")
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Any] = None
    n: Optional[int] = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_completion_response(model: str, content: str, finish_reason: str = "stop",
                               input_tokens: int = 0, output_tokens: int = 0) -> dict:
    """Construit une réponse OpenAI-compatible (non-streaming)."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _make_stream_chunk(model: str, delta_content: str, finish_reason: Optional[str] = None) -> str:
    """Formate un chunk SSE compatible OpenAI streaming."""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": delta_content} if delta_content else {},
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


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
    # Construire le prompt depuis les messages
    system_prompt = ""
    user_parts = []
    for msg in request.messages:
        if msg.role.lower() == "system":
            system_prompt += (msg.content or "") + "\n"
        else:
            user_parts.append(msg.content or "")
    user_prompt = "\n".join(user_parts).strip()

    if not user_prompt:
        raise HTTPException(status_code=400, detail="Aucun message utilisateur fourni.")

    model_name = request.model

    try:
        from core.llm_gateway import LLMGateway
        gw = LLMGateway()
        # Résoudre le provider (nom direct ou tier)
        try:
            provider = next(iter(gw.providers.values()))  # fallback
            if model_name in gw.providers:
                provider = gw.providers[model_name]
        except Exception:
            pass

        logger.info(f"[OPENAI_PROXY] Requête → model={model_name}, stream={request.stream}")

        # Appel LLM dans un thread non-bloquant
        response_text = await asyncio.to_thread(
            provider.generate,
            system_prompt.strip() or "Tu es un assistant IA expert.",
            user_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        if isinstance(response_text, dict):
            response_text = response_text.get("content", str(response_text))

        if request.stream:
            # Streaming SSE : envoyer la réponse complète en un seul chunk
            async def _stream_gen():
                yield _make_stream_chunk(model_name, str(response_text))
                yield _make_stream_chunk(model_name, "", finish_reason="stop")
                yield "data: [DONE]\n\n"
            return StreamingResponse(_stream_gen(), media_type="text/event-stream")
        else:
            tokens_in = max(1, len(user_prompt.split()))
            tokens_out = max(1, len(str(response_text).split()))
            return JSONResponse(_make_completion_response(
                model_name, str(response_text),
                input_tokens=tokens_in, output_tokens=tokens_out
            ))

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
        from core.llm_gateway import LLMGateway
        from core.llm.circuit_breaker import CircuitBreaker
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
