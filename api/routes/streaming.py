"""
api/routes/streaming.py — Routes SSE (Server-Sent Events) et streaming du Moteur.

Contient :
- /api/stream : Flux global d'événements du moteur.
- /api/chat/stream : Streaming simple de tokens pour un prompt.
- /api/execute/stream : Streaming progressif de l'exécution complète d'un pipeline d'agents.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.routes.agents import ExecuteRequestBody
from core.app_state import get_app_state
from core.auth import optional_auth
from core.serializers import global_state_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Streaming"])


class ChatStreamBody(BaseModel):
    prompt: str
    system_prompt: str = "Tu es un assistant IA expert en domotique et code."
    provider: str = "deepseek-chat"


@router.get("/api/stream")
async def sse_stream():
    """
    Flux Server-Sent Events (SSE) pour diffuser les évènements du moteur en temps réel.
    """
    state = get_app_state()
    queue: asyncio.Queue = asyncio.Queue()

    # Bug trouvé en vérification navigateur (Phase 3) : `sse_clients_list`
    # n'existe plus sur AppState (seul `sse_clients` subsiste) → GET /api/stream
    # levait AttributeError et le flux d'événements du Dashboard était mort.
    async with state.sse_lock:
        state.sse_clients.add(queue)

    async def event_generator():
        try:
            # Ping initial pour stabiliser la connexion EventSource côté JS
            yield f"event: ping\ndata: {json.dumps({'message': 'connected'})}\n\n"
            while True:
                event_data = await queue.get()
                yield f"event: {event_data['event']}\ndata: {json.dumps(event_data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            async with state.sse_lock:
                state.sse_clients.discard(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/api/chat/stream")
async def chat_stream(body: ChatStreamBody):
    """
    Streaming token-par-token via SSE.
    Délégué à services.pipeline_service.stream_chat_tokens()
    """
    from services.pipeline_service import stream_chat_tokens
    return StreamingResponse(
        stream_chat_tokens(body.prompt, body.system_prompt, body.provider),
        media_type="text/event-stream",
    )


async def _execute_stream_generator(
    user_prompt: str,
    source: dict,
    session_id: str,
    tier: str | None = None,
    model: str | None = None,
):
    """
    Générateur SSE pour /api/execute/stream.
    [#T194] tier/model : override par requête de la force du workload (sélecteur IHM).
    """
    import json as _json

    queue: asyncio.Queue = asyncio.Queue()
    pipeline_task = None

    try:
        from core import token_tracker
        from core.llm_gateway import LLMGateway, load_config
        from core.source_router import ModeType, log_source_decision, parse_source
        from services.execute_service import apply_source_config_overrides, get_execute_timeout
        from services.pipeline_service import (
            run_fast_path,
            run_full_pipeline,
        )

        state = get_app_state()

        # ── Routing ──
        # [P1-2.1] Router canonique partagé (gateway/RAG/config câblés).
        router_instance = state.get_shared_router()
        initial_payload, starting_agent = await router_instance.analyze_request(user_prompt)
        routing_type = initial_payload.metadata.get("routing_type", "default")

        request_source = parse_source(source)
        initial_payload.metadata["request_source"] = {
            "type": request_source.type.value,
            "mode": request_source.mode.value,
            "tts_enabled": request_source.tts_enabled,
        }
        suffix = request_source.get_system_prompt_suffix()
        initial_payload.metadata["system_prompt_suffix"] = suffix
        log_source_decision(request_source, routing_type)

        if routing_type == "casual_chat" and request_source.mode == ModeType.HA:
            routing_type = "default"

        # ── Fast path : pas de streaming pour casual_chat (déjà rapide) ──
        if routing_type == "casual_chat":
            response = await run_fast_path(
                user_prompt=user_prompt,
                session_id=session_id,
                gateway=LLMGateway(),
                token_tracker=token_tracker,
                fast_path_cache=state.fast_path_cache,
                tier_override=tier,
                model_override=model,
                system_prompt_suffix=suffix,
                inject_project_context=True,
            )
            async with state.execution_lock:
                state.execution_state["status"] = "success"
            event = _json.dumps({"type": "done", "response": response, "agents_used": ["fast_path"]})
            yield f"data: {event}\n\n"
            return

        # ── Pipeline complet : SSE streaming via Queue ──
        async def _on_event(event_type: str, data: Any, engine=None):
            """Callback du pipeline → inject dans la queue SSE."""
            if engine:
                state.execution_state["engine_state"] = global_state_to_dict(engine.state)
            if event_type == "orchestration_completed":
                state.execution_state["status"] = data.get("status", "success")
            await queue.put({"type": event_type, "data": data})

        # [#T194] Override par requête + source_router tier recommandé.
        config = apply_source_config_overrides(
            load_config(), request_source, tier_override=tier, model_override=model
        )
        if tier or model:
            initial_payload.metadata["workload_override"] = {"tier": tier, "model": model}

        pipeline_timeout = get_execute_timeout(request_source, routing_type)
        if pipeline_timeout < 120:
            deadline = asyncio.get_event_loop().time() + pipeline_timeout

        async def _run_pipeline():
            try:
                result = await run_full_pipeline(
                    user_prompt=user_prompt,
                    session_id=session_id,
                    initial_payload=initial_payload,
                    starting_agent=starting_agent,
                    on_event_callback=_on_event,
                    config=config,
                    timeout_seconds=pipeline_timeout,
                )
                await queue.put({"type": "__done__", "result": result})
            except Exception as e:
                await queue.put({"type": "__error__", "error": str(e)})

        pipeline_task = asyncio.create_task(_run_pipeline())

        # ── Lecture de la queue + yield SSE ──
        deadline = asyncio.get_event_loop().time() + pipeline_timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                err_event = _json.dumps({"type": "error", "message": "Timeout 120s dépassé"})
                yield f"data: {err_event}\n\n"
                if pipeline_task and not pipeline_task.done():
                    pipeline_task.cancel()
                return

            try:
                item = await asyncio.wait_for(queue.get(), timeout=min(5.0, remaining))
            except TimeoutError:
                yield "data: {\"type\":\"heartbeat\"}\n\n"
                continue

            if item["type"] == "__done__":
                result = item.get("result", {})
                async with state.execution_lock:
                    state.execution_state["status"] = result.get("status", "success")
                    state.execution_state["engine_state"] = result.get("engine_state")
                done_event = _json.dumps({
                    "type": "done",
                    "response": result.get("response", ""),
                    "agents_used": result.get("agents_used", []),
                    "status": result.get("status", "completed"),
                })
                yield f"data: {done_event}\n\n"
                return

            elif item["type"] == "__error__":
                async with state.execution_lock:
                    state.execution_state["status"] = "error"
                    state.execution_state["error_message"] = item.get("error", "Erreur inconnue")
                err_event = _json.dumps({"type": "error", "message": item.get("error", "Erreur inconnue")})
                yield f"data: {err_event}\n\n"
                return

            else:
                try:
                    event_payload = _json.dumps({"type": item["type"], "data": item.get("data", {})})
                    yield f"data: {event_payload}\n\n"
                except Exception:
                    pass

    except GeneratorExit:
        logger.info(f"[STREAM] Client déconnecté ({session_id}) → annulation pipeline")
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
        async with state.execution_lock:
            if state.execution_state.get("status") == "running":
                state.execution_state["status"] = "error"
                state.execution_state["error_message"] = "Client déconnecté"
    except Exception as e:
        logger.error(f"[STREAM] Erreur générateur SSE : {e}")
        async with state.execution_lock:
            if state.execution_state.get("status") == "running":
                state.execution_state["status"] = "error"
                state.execution_state["error_message"] = str(e)
        try:
            import json as _j
            yield f"data: {_j.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception:
            pass


@router.post("/api/execute/stream")
async def execute_chat_stream(body: ExecuteRequestBody, _auth=Depends(optional_auth)):
    """
    Streaming SSE du pipeline conversationnel pour l'IHM web.
    """
    state = get_app_state()

    async with state.execution_lock:
        if state.execution_state.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail="Une exécution est déjà en cours. Attendez sa fin ou utilisez /api/stop."
            )
        state.execution_state.update({
            "status": "running", "objective": body.user_prompt,
            "engine_state": None, "error_message": None,
        })

    session_id = f"stream_{int(asyncio.get_event_loop().time())}"
    return StreamingResponse(
        _execute_stream_generator(
            body.user_prompt, body.source, session_id, tier=body.tier, model=body.model
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
