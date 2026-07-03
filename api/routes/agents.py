"""
api/routes/agents.py — Routes API d'exécution et de contrôle des agents.

Extrait de gui_server.py dans le cadre du refactoring v12.1.0.
Contient :
- /api/run : Exécution asynchrone en arrière-plan d'une tâche.
- /api/execute : Exécution synchrone (conversationnelle) avec routage sémantique.
"""

import os
import uuid
import logging
import asyncio
import aiohttp
from typing import Any
from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends

from core.app_state import get_app_state, broadcast_event
from core.llm_gateway import LLMGateway, load_config
from core.auth import optional_auth
from core.serializers import global_state_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agents"])


class RunRequestBody(BaseModel):
    objective: str


class ExecuteRequestBody(BaseModel):
    """
    Corps de requête pour /api/execute (mode chat synchrone).
    Champ 'source' optionnel pour le routing source-aware.
    """
    user_prompt: str
    source: dict = {}  # Ex: {"type": "tab5", "mode": "ha", "tts_enabled": true}


@router.post("/api/run")
async def run_task(body: RunRequestBody):
    """
    Démarre une nouvelle exécution d'agents en arrière-plan (fire-and-forget).
    Logique métier déléguée à services.pipeline_service.run_engine_background()
    """
    state = get_app_state()
    session_id = f"bg_{uuid.uuid4().hex[:8]}"

    async with state.execution_lock:
        if state.execution_state.get("status") == "running":
            raise HTTPException(status_code=400, detail="Une exécution de tâche est déjà en cours.")
        state.execution_state.update({
            "status": "running",
            "objective": body.objective,
            "session_id": session_id,
            "engine_state": None,
            "error_message": None
        })

    # Enregistrer immédiatement le début de session en BDD
    try:
        from core.session_history import record_session_start
        record_session_start(session_id, body.objective, "planner")
    except Exception as e:
        logger.warning(f"[API_RUN] Impossible d'enregistrer le début de session : {e}")

    async def _on_event(event_type: str, data: Any, engine=None):
        """Callback SSE pour diffusion temps réel."""
        state.execution_state["engine_state"] = global_state_to_dict(engine.state) if engine else None
        if event_type == "orchestration_completed":
            state.execution_state["status"] = data.get("status", "success")
        await broadcast_event(event_type, data)

    from services.pipeline_service import run_engine_background
    asyncio.create_task(run_engine_background(body.objective, _on_event, session_id=session_id))
    return {
        "message": "Tâche démarrée en arrière-plan.",
        "status": "running",
        "session_id": session_id
    }


@router.post("/api/execute")
async def execute_chat(body: ExecuteRequestBody, _auth=Depends(optional_auth)):
    """
    Point d'entrée synchrone pour le chat conversationnel.
    Logique métier déléguée à services.pipeline_service.
    Auth optionnelle via Bearer Token (MOTEUR_API_KEY dans .env).

    Flux :
    1. Router analyse → routing_type (casual_chat vs complexe)
    2. casual_chat → run_fast_path() (< 400ms avec cache TTL)
    3. complexe    → run_full_pipeline() (Planner → DAG → Reviewer)
    """
    from services.pipeline_service import (
        run_fast_path, run_full_pipeline,
        _build_fast_path_response
    )
    state = get_app_state()

    # ── Vérification mutex exécution ──
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

    session_id = f"chat_{int(asyncio.get_event_loop().time())}"

    try:
        # ── Parse de la source (source-aware routing) ──
        from core.source_router import parse_source, log_source_decision
        request_source = parse_source(body.source)

        # ── ÉTAPE 1 : Routage (< 50ms) ──
        # [P1-2.1] Router canonique partagé (gateway/RAG/config câblés).
        router_instance = state.get_shared_router()
        initial_payload, starting_agent = await router_instance.analyze_request(body.user_prompt)
        routing_type = initial_payload.metadata.get("routing_type", "default")

        # Injecter la source et le suffix TTS dans les métadonnées du payload
        initial_payload.metadata["request_source"] = {
            "type": request_source.type.value,
            "mode": request_source.mode.value,
            "tts_enabled": request_source.tts_enabled,
        }
        initial_payload.metadata["system_prompt_suffix"] = request_source.get_system_prompt_suffix()
        log_source_decision(request_source, routing_type)

        # ── ÉTAPE 2A : FAST PATH (casual_chat) ──
        if routing_type == "casual_chat":
            logger.info(f"[EXECUTE] ⚡ Fast Path → {body.user_prompt[:60]}")
            try:
                from core import token_tracker
                response_text = await run_fast_path(
                    user_prompt=body.user_prompt,
                    session_id=session_id,
                    gateway=LLMGateway(),
                    token_tracker=token_tracker,
                    fast_path_cache=state.fast_path_cache,
                )
                async with state.execution_lock:
                    state.execution_state["status"] = "success"
                return _build_fast_path_response(session_id, body.user_prompt, response_text)
            except Exception as fast_err:
                logger.warning(f"[EXECUTE] Fast path échoué, fallback pipeline : {fast_err}")

        # ── ÉTAPE 2A-bis : COURT-CIRCUIT HA DÉTERMINISTE (Zero-LLM, ~100ms) ──
        if routing_type == "ha_deterministic":
            direct_call = initial_payload.metadata.get("direct_tool_call", {})
            ha_service = direct_call.get("arguments", {}).get("service", "")
            ha_entity = direct_call.get("arguments", {}).get("entity_id", "")
            logger.info(f"[EXECUTE] 🏠 HA Déterministe → {ha_service}({ha_entity})")
            try:
                # Récupérer le token et l'URL HA depuis les variables d'environnement ou .env
                ha_token = os.environ.get("HASS_TOKEN", "")
                ha_url = os.environ.get("HASS_URL", "https://${HA_HOST:-192.168.1.x}:8123")
                if not ha_token:
                    # Fallback : lire depuis .env du moteur
                    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
                    if os.path.exists(env_path):
                        with open(env_path, "r", encoding="utf-8") as ef:
                            for line in ef:
                                line = line.strip()
                                if line.startswith("HASS_TOKEN="):
                                    ha_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                                elif line.startswith("HASS_URL="):
                                    ha_url = line.split("=", 1)[1].strip().strip('"').strip("'")

                if ha_token:
                    domain, action = ha_service.split(".", 1)
                    api_url = f"{ha_url}/api/services/{domain}/{action}"
                    headers = {
                        "Authorization": f"Bearer {ha_token}",
                        "Content-Type": "application/json"
                    }
                    payload_ha = {"entity_id": ha_entity}
                    ssl_ctx = ha_ssl_context()  # [P0-1.5] TLS HA centralisé
                    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                    async with aiohttp.ClientSession(connector=connector) as http_session:
                        async with http_session.post(api_url, json=payload_ha, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                entity_name = ha_entity.split(".")[-1].replace("_", " ").title()
                                action_verb = "allumé" if "turn_on" in ha_service else "éteint" if "turn_off" in ha_service else "exécuté"
                                if "open" in ha_service:
                                    action_verb = "ouvert"
                                elif "close" in ha_service:
                                    action_verb = "fermé"
                                response_text = f"{entity_name} {action_verb}."
                            else:
                                resp_text = await resp.text()
                                response_text = f"Erreur HA ({resp.status}): {resp_text[:100]}"
                else:
                    response_text = "Token HA non configuré. Commande non exécutée."

                async with state.execution_lock:
                    state.execution_state["status"] = "success"
                return {
                    "status": "completed",
                    "session_id": session_id,
                    "response": response_text,
                    "history": [{
                        "agent_name": "ha_deterministic",
                        "status": "success",
                        "result_data": response_text,
                        "next_agent": "END",
                        "error_message": None,
                        "new_tasks": [],
                        "metadata": {"routing_type": "ha_deterministic", "service": ha_service, "entity_id": ha_entity},
                    }],
                    "agents_used": ["ha_deterministic"],
                }
            except Exception as ha_err:
                logger.warning(f"[EXECUTE] HA déterministe échoué, fallback pipeline : {ha_err}")

        # ── ÉTAPE 2A-ter : FUZZY MATCH HA (mode tab5/ha sans match déterministe) ──
        from core.source_router import ModeType
        if (request_source.mode == ModeType.HA
                and routing_type in ("ha_direct", "default", "home_assistant")):
            try:
                from core.ha_fuzzy_matcher import get_fuzzy_matcher
                matcher = get_fuzzy_matcher()
                if matcher:
                    fuzzy_result = await matcher.find_entity(body.user_prompt)
                    if fuzzy_result:
                        logger.info(
                            f"[EXECUTE] 🔍 Fuzzy match → {fuzzy_result.entity_id} "
                            f"(score={fuzzy_result.score:.2f})"
                        )
                        _ha_token = os.environ.get("HASS_TOKEN", "")
                        _ha_url = os.environ.get("HASS_URL", "https://${HA_HOST:-192.168.1.x}:8123")
                        if not _ha_token:
                            _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
                            if os.path.exists(_env_path):
                                with open(_env_path, "r", encoding="utf-8") as _ef:
                                    for _ln in _ef:
                                        _ln = _ln.strip()
                                        if _ln.startswith("HASS_TOKEN="):
                                            _ha_token = _ln.split("=", 1)[1].strip().strip('"').strip("'")
                                        elif _ln.startswith("HASS_URL="):
                                            _ha_url = _ln.split("=", 1)[1].strip().strip('"').strip("'")
                        if _ha_token:
                            _domain, _action = fuzzy_result.service.split(".", 1)
                            _api_url = f"{_ha_url}/api/services/{_domain}/{_action}"
                            _ssl_ctx = ha_ssl_context()  # [P0-1.5] TLS HA centralisé
                            _connector = aiohttp.TCPConnector(ssl=_ssl_ctx)
                            async with aiohttp.ClientSession(connector=_connector) as _s:
                                async with _s.post(
                                    _api_url,
                                    json={"entity_id": fuzzy_result.entity_id},
                                    headers={"Authorization": f"Bearer {_ha_token}", "Content-Type": "application/json"},
                                    timeout=aiohttp.ClientTimeout(total=5.0)
                                ) as _resp:
                                    if _resp.status == 200:
                                        _response_text = fuzzy_result.to_response_text()
                                    else:
                                        _resp_body = await _resp.text()
                                        _response_text = f"Erreur HA ({_resp.status}): {_resp_body[:80]}"
                            async with state.execution_lock:
                                state.execution_state["status"] = "success"
                            return {
                                "status": "completed",
                                "session_id": session_id,
                                "response": _response_text,
                                "history": [{
                                    "agent_name": "ha_fuzzy",
                                    "status": "success",
                                    "result_data": _response_text,
                                    "next_agent": "END",
                                    "error_message": None,
                                    "new_tasks": [],
                                    "metadata": {
                                        "routing_type": "ha_fuzzy",
                                        "service": fuzzy_result.service,
                                        "entity_id": fuzzy_result.entity_id,
                                        "score": fuzzy_result.score,
                                    },
                                }],
                                "agents_used": ["ha_fuzzy"],
                            }
            except Exception as _fuzz_err:
                logger.warning(f"[EXECUTE] Fuzzy match HA échoué, fallback pipeline : {_fuzz_err}")

        # ── ÉTAPE 2B : PIPELINE COMPLET ──
        config = load_config()

        async def _on_event(event_type: str, data: Any, engine=None):
            """Callback SSE pour diffusion temps réel pendant pipeline."""
            if engine:
                state.execution_state["engine_state"] = global_state_to_dict(engine.state)
            if event_type == "orchestration_completed":
                state.execution_state["status"] = data.get("status", "success")
            try:
                await broadcast_event(event_type, data)
            except Exception:
                pass

        result = await run_full_pipeline(
            user_prompt=body.user_prompt,
            session_id=session_id,
            initial_payload=initial_payload,
            starting_agent=starting_agent,
            on_event_callback=_on_event,
            config=config,
        )

        async with state.execution_lock:
            state.execution_state["status"] = result.get("status", "success")
            state.execution_state["engine_state"] = result.get("engine_state")

        return result

    except Exception as e:
        async with state.execution_lock:
            state.execution_state["status"] = "error"
            state.execution_state["error_message"] = str(e)
        logger.error(f"[EXECUTE] Erreur inattendue : {e}")
        return {"status": "error", "error": f"❌ {str(e)}"}
