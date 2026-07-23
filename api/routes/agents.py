"""
api/routes/agents.py — Routes API d'exécution et de contrôle des agents.

Extrait de gui_server.py dans le cadre du refactoring v12.1.0.
Contient :
- /api/run : Exécution asynchrone en arrière-plan d'une tâche.
- /api/execute : Exécution synchrone (conversationnelle) avec routage sémantique.
"""

import asyncio
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.app_state import broadcast_event, get_app_state
from core.auth import optional_auth
from core.llm_gateway import LLMGateway, load_config
from core.serializers import global_state_to_dict
from core.source_router import ModeType, log_source_decision, parse_source
from core.vocal_audit import VocalAuditTimer, log_vocal_request, log_vocal_response
from services.execute_service import (
    apply_source_config_overrides,
    build_chat_mode_failure_response,
    build_ha_fast_path_response,
    build_ha_mode_failure_response,
    execute_ha_service,
    get_execute_timeout,
    match_ha_command,
    prompt_has_domotic_action,
    resolve_ha_command_for_execute,
    should_block_full_pipeline,
)
from services.ha_vocal_fallback import resolve_ha_via_llm
from services.pipeline_service import (
    _build_fast_path_response,
    run_fast_path,
    run_full_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agents"])

# Salutations / small-talk évidents en mode domotique (évite le pipeline Planner ~30s).
_HA_GREETING_TOKENS = frozenset({
    "bonjour", "salut", "hello", "hey", "coucou", "bonsoir", "bonne", "nuit", "journée", "journee",
})
_HA_THANKS_TOKENS = frozenset({"merci", "thanks"})
_HA_JOKE_MARKERS = ("blague", "raconte", "une blague", "dis une blague")


def _normalize_ha_prompt(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\sàâäéèêëïîôùûüç'-]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _ha_conversational_response(prompt: str) -> str | None:
    """
    Réponse courte fixe pour le small-talk évident en mode HA.
    Retourne None si la phrase ressemble à une commande domotique ou est ambiguë.
    """
    norm = _normalize_ha_prompt(prompt)
    if not norm:
        return None

    # Ne jamais intercepter une commande domotique (STT peut ajouter "bonjour" etc.)
    if prompt_has_domotic_action(prompt):
        return None

    words = set(norm.split())

    if words & _HA_GREETING_TOKENS or any(
        p in norm for p in ("comment vas", "ca va", "ça va", "qui es tu", "qui es-tu")
    ):
        return "Bonjour Axel, que veux-tu contrôler ?"

    if words & _HA_THANKS_TOKENS or norm.startswith("merci"):
        return "De rien. Que veux-tu contrôler ?"

    if any(marker in norm for marker in _HA_JOKE_MARKERS):
        return "Je n'ai pas compris la commande domotique."

    return None


class RunRequestBody(BaseModel):
    objective: str


class ExecuteRequestBody(BaseModel):
    """
    Corps de requête pour /api/execute (mode chat synchrone).
    Champ 'source' optionnel pour le routing source-aware.
    [#T194] 'tier' (leger/moyen/fort/automatique) et 'model' (id littéral,
    prioritaire sur tier) : override par requête de la force du workload,
    appliqué à executor/ha_agent via apply_workload_override().
    """
    user_prompt: str
    source: dict = {}  # Ex: {"type": "tab5", "mode": "ha", "tts_enabled": true}
    tier: str | None = None
    model: str | None = None


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


async def _try_ha_state_query_shortcut(
    body: "ExecuteRequestBody",
    request_source,
    session_id: str,
) -> dict | None:
    """
    Question d'état domotique (« la clim est allumée ? », « le volet est ouvert ? »,
    « il fait combien dans le salon ? »), AVANT le verrou global et le routeur.

    Lecture live HA en Zero-LLM. Doit passer AVANT le court-circuit d'action car
    « la lumière est allumée ? » contient un marqueur d'action (« allumée ») qui
    serait sinon interprété comme un turn_on.

    Retourne None si ce n'est pas une question d'état : la cascade continue.
    """
    from services.ha_state_query import resolve_ha_state_query

    with VocalAuditTimer() as timer:
        response_text = await resolve_ha_state_query(body.user_prompt)
    if response_text is None:
        return None

    log_vocal_request(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    logger.info("[EXECUTE] 🏠🔎 HA état → %s", response_text)
    result = build_ha_fast_path_response(
        session_id,
        response_text,
        "ha_state_query",
        {"routing_type": "ha_state_query"},
    )
    log_vocal_response(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        routing_type="ha_state_query",
        agents_used=["ha_state_query"],
        response_text=response_text,
        latency_ms=timer.elapsed_ms,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    return result


async def _try_ha_climate_relative_shortcut(
    body: "ExecuteRequestBody",
    request_source,
    session_id: str,
) -> dict | None:
    """
    Réglage clim RELATIF (« monte la clim de 2 degrés », « un peu plus chaud »),
    AVANT le court-circuit d'action car « monte » serait pris pour un allumage.

    Lit la consigne actuelle + applique le delta (Zero-LLM). None si ce n'est pas
    un réglage relatif : la cascade continue.
    """
    from services.ha_climate_control import resolve_climate_relative

    with VocalAuditTimer() as timer:
        response_text = await resolve_climate_relative(body.user_prompt)
    if response_text is None:
        return None

    log_vocal_request(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    logger.info("[EXECUTE] 🏠🌡️ HA clim relatif → %s", response_text)
    result = build_ha_fast_path_response(
        session_id,
        response_text,
        "ha_climate_relative",
        {"routing_type": "ha_climate_relative"},
    )
    log_vocal_response(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        routing_type="ha_climate_relative",
        agents_used=["ha_climate_relative"],
        response_text=response_text,
        latency_ms=timer.elapsed_ms,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    return result


async def _try_ha_deterministic_shortcut(
    body: "ExecuteRequestBody",
    request_source,
    session_id: str,
) -> dict | None:
    """
    Court-circuit domotique déterministe, AVANT le verrou global et le routeur.

    Tente le match ha_commands.json (exact/fuzzy) + matchers volet/clim/pièce.
    Si une commande est reconnue, exécute le service HA et renvoie la réponse
    SANS prendre state.execution_lock (fini les 409 concurrents IHM ↔ vocal) et
    sans passer par le slow-path LLM du routeur (~200 ms économisés).

    Retourne None si aucune commande déterministe n'est reconnue : l'appelant
    poursuit alors la cascade normale (fuzzy entité, repli LLM, small-talk).
    """
    ha_cmd = await resolve_ha_command_for_execute(body.user_prompt)
    if not ha_cmd:
        return None

    log_vocal_request(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    logger.info(
        "[EXECUTE] 🏠⚡ HA court-circuit → %s(%s) data=%s phrase=%s",
        ha_cmd.service, ha_cmd.entity_id, ha_cmd.service_data, ha_cmd.matched_phrase,
    )
    with VocalAuditTimer() as timer:
        ok, response_text = await execute_ha_service(
            ha_cmd.service,
            ha_cmd.entity_id,
            service_data=ha_cmd.service_data,
        )
    if ok:
        result = build_ha_fast_path_response(
            session_id,
            response_text,
            "ha_command",
            {
                "routing_type": "ha_command",
                "service": ha_cmd.service,
                "entity_id": ha_cmd.entity_id,
                "service_data": ha_cmd.service_data,
                "matched_phrase": ha_cmd.matched_phrase,
                "shortcut": True,
            },
        )
        routing_type, agents_used = "ha_command", ["ha_command"]
    else:
        logger.warning("[EXECUTE] HA court-circuit : appel HA échoué")
        result = build_ha_mode_failure_response(session_id)
        response_text = result["response"]
        routing_type, agents_used = "ha_command_failed", ["ha_command_failed"]

    log_vocal_response(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        routing_type=routing_type,
        agents_used=agents_used,
        response_text=response_text,
        latency_ms=timer.elapsed_ms,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )
    return result


@router.post("/api/execute")
async def execute_chat(body: ExecuteRequestBody, _auth=Depends(optional_auth)):
    """
    Point d'entrée synchrone pour le chat conversationnel (IHM + vocal Tab5).
    """
    state = get_app_state()
    session_id = f"chat_{uuid.uuid4().hex[:10]}"
    request_source = parse_source(body.source)

    # ── Fast paths domotiques (hors verrou global + hors routeur) ──
    # Ni la lecture d'état ni une commande reconnue ne doivent bloquer l'IHM
    # (verrou global → 409) ou payer le slow-path LLM du routeur.
    if request_source.mode == ModeType.HA:
        # 1) Question d'état AVANT l'action (« la lumière est allumée ? » contient
        #    « allumée », qui serait sinon pris pour un turn_on).
        state_reply = await _try_ha_state_query_shortcut(body, request_source, session_id)
        if state_reply is not None:
            return state_reply
        # 2) Réglage clim relatif (« monte la clim de 2° ») AVANT l'action, sinon
        #    « monte » serait pris pour un allumage.
        relative_reply = await _try_ha_climate_relative_shortcut(body, request_source, session_id)
        if relative_reply is not None:
            return relative_reply
        # 3) Commande domotique déterministe.
        shortcut = await _try_ha_deterministic_shortcut(body, request_source, session_id)
        if shortcut is not None:
            return shortcut

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

    suffix = request_source.get_system_prompt_suffix()
    execute_timeout = get_execute_timeout(request_source, "default")

    log_vocal_request(
        session_id=session_id,
        user_prompt=body.user_prompt,
        source_type=request_source.type.value,
        source_mode=request_source.mode.value,
        tts_enabled=request_source.tts_enabled,
        device_id=request_source.device_id,
    )

    routing_type = "default"
    agents_used: list[str] = []
    response_text = ""

    try:
        with VocalAuditTimer() as timer:
            router_instance = state.get_shared_router()
            initial_payload, starting_agent = await router_instance.analyze_request(body.user_prompt)
            routing_type = initial_payload.metadata.get("routing_type", "default")

            initial_payload.metadata["request_source"] = {
                "type": request_source.type.value,
                "mode": request_source.mode.value,
                "tts_enabled": request_source.tts_enabled,
                "device_id": request_source.device_id,
            }
            initial_payload.metadata["system_prompt_suffix"] = suffix
            log_source_decision(request_source, routing_type)

            # ── Mode Discussion (chat) : vocal_host → chat sync ou job async ──
            if request_source.mode == ModeType.CHAT:
                logger.info("[EXECUTE] 💬 Mode discussion → vocal_host")
                execute_timeout = get_execute_timeout(request_source, "casual_chat")
                conv_id = request_source.conversation_id
                if conv_id:
                    from core.vocal_session import record_vocal_turn
                    record_vocal_turn(
                        conv_id, "user", body.user_prompt,
                        source_mode="chat", device_id=request_source.device_id,
                    )
                try:
                    from core import token_tracker
                    from core.vocal_host import handle_discussion

                    host_result = await asyncio.wait_for(
                        handle_discussion(
                            user_prompt=body.user_prompt,
                            session_id=session_id,
                            gateway=LLMGateway(),
                            token_tracker=token_tracker,
                            fast_path_cache=state.fast_path_cache,
                            system_prompt_suffix=suffix,
                            conversation_id=conv_id,
                            device_id=request_source.device_id,
                            tier_override=body.tier,
                            model_override=body.model,
                        ),
                        timeout=execute_timeout,
                    )
                    response_text = host_result.response_text
                    if conv_id and not host_result.async_job_id:
                        from core.vocal_session import record_vocal_turn
                        record_vocal_turn(
                            conv_id, "assistant", response_text,
                            source_mode="chat", device_id=request_source.device_id,
                        )
                    agents_used = host_result.agents_used
                    routing_type = host_result.routing_type
                    async with state.execution_lock:
                        state.execution_state["status"] = "success"
                    result = _build_fast_path_response(session_id, body.user_prompt, response_text)
                    result["history"][0]["agent_name"] = agents_used[0]
                    result["history"][0]["metadata"] = {
                        "routing_type": routing_type,
                        "model_tier": "leger",
                        **host_result.metadata,
                    }
                    result["agents_used"] = agents_used
                    log_vocal_response(
                        session_id=session_id,
                        user_prompt=body.user_prompt,
                        source_type=request_source.type.value,
                        source_mode=request_source.mode.value,
                        routing_type=routing_type,
                        agents_used=agents_used,
                        response_text=response_text,
                        latency_ms=timer.elapsed_ms,
                        tts_enabled=request_source.tts_enabled,
                        device_id=request_source.device_id,
                    )
                    return result
                except Exception as chat_err:
                    logger.warning(f"[EXECUTE] Discussion fast path échoué : {chat_err}")
                    async with state.execution_lock:
                        state.execution_state["status"] = "success"
                    result = build_chat_mode_failure_response(session_id)
                    log_vocal_response(
                        session_id=session_id,
                        user_prompt=body.user_prompt,
                        source_type=request_source.type.value,
                        source_mode=request_source.mode.value,
                        routing_type="discussion_chat_failure",
                        agents_used=["discussion_chat"],
                        response_text=result["response"],
                        latency_ms=timer.elapsed_ms,
                        tts_enabled=request_source.tts_enabled,
                        device_id=request_source.device_id,
                    )
                    return result

            if routing_type == "casual_chat" and request_source.mode == ModeType.HA:
                logger.info("[EXECUTE] mode=ha bloque casual_chat → fast paths HA")
                routing_type = "default"

            # NB : le match déterministe ha_commands.json (exact/fuzzy) est désormais
            # traité en amont par _try_ha_deterministic_shortcut() — avant le verrou
            # global et le routeur. Ici on ne garde que les paliers suivants de la
            # cascade HA (fuzzy entité, repli LLM, small-talk).

            # ── Fast path discussion (avec RAG léger #T173) ──
            if routing_type == "casual_chat":
                logger.info(f"[EXECUTE] ⚡ Fast Path → {body.user_prompt[:60]}")
                try:
                    from core import token_tracker
                    response_text = await asyncio.wait_for(
                        run_fast_path(
                            user_prompt=body.user_prompt,
                            session_id=session_id,
                            gateway=LLMGateway(),
                            token_tracker=token_tracker,
                            fast_path_cache=state.fast_path_cache,
                            tier_override=body.tier,
                            model_override=body.model,
                            system_prompt_suffix=suffix,
                            inject_project_context=True,
                        ),
                        timeout=execute_timeout,
                    )
                    agents_used = ["fast_path"]
                    async with state.execution_lock:
                        state.execution_state["status"] = "success"
                    result = _build_fast_path_response(session_id, body.user_prompt, response_text)
                    log_vocal_response(
                        session_id=session_id,
                        user_prompt=body.user_prompt,
                        source_type=request_source.type.value,
                        source_mode=request_source.mode.value,
                        routing_type="casual_chat",
                        agents_used=agents_used,
                        response_text=response_text,
                        latency_ms=timer.elapsed_ms,
                        tts_enabled=request_source.tts_enabled,
                        device_id=request_source.device_id,
                    )
                    return result
                except Exception as fast_err:
                    logger.warning(f"[EXECUTE] Fast path échoué : {fast_err}")
                    if should_block_full_pipeline(request_source):
                        async with state.execution_lock:
                            state.execution_state["status"] = "success"
                        result = build_ha_mode_failure_response(session_id)
                        log_vocal_response(
                            session_id=session_id,
                            user_prompt=body.user_prompt,
                            source_type=request_source.type.value,
                            source_mode=request_source.mode.value,
                            routing_type="ha_mode_failure",
                            agents_used=["ha_mode_blocked"],
                            response_text=result["response"],
                            latency_ms=timer.elapsed_ms,
                            tts_enabled=request_source.tts_enabled,
                            device_id=request_source.device_id,
                        )
                        return result

            # ── HA déterministe (ha_commands.json) ──
            if routing_type == "ha_deterministic":
                direct_call = initial_payload.metadata.get("direct_tool_call", {})
                ha_service = direct_call.get("arguments", {}).get("service", "")
                ha_entity = direct_call.get("arguments", {}).get("entity_id", "")
                ha_service_data = direct_call.get("arguments", {}).get("service_data")
                logger.info(f"[EXECUTE] 🏠 HA Déterministe → {ha_service}({ha_entity}) data={ha_service_data}")
                try:
                    ok, response_text = await execute_ha_service(
                        ha_service, ha_entity, service_data=ha_service_data,
                    )
                    if ok:
                        agents_used = ["ha_deterministic"]
                        async with state.execution_lock:
                            state.execution_state["status"] = "success"
                        result = build_ha_fast_path_response(
                            session_id,
                            response_text,
                            "ha_deterministic",
                            {
                                "routing_type": "ha_deterministic",
                                "service": ha_service,
                                "entity_id": ha_entity,
                            },
                        )
                        log_vocal_response(
                            session_id=session_id,
                            user_prompt=body.user_prompt,
                            source_type=request_source.type.value,
                            source_mode=request_source.mode.value,
                            routing_type="ha_deterministic",
                            agents_used=agents_used,
                            response_text=response_text,
                            latency_ms=timer.elapsed_ms,
                            tts_enabled=request_source.tts_enabled,
                            device_id=request_source.device_id,
                        )
                        return result
                    logger.warning("[EXECUTE] HA déterministe : appel HA échoué")
                except Exception as ha_err:
                    logger.warning(f"[EXECUTE] HA déterministe échoué : {ha_err}")

            # ── Fuzzy match HA ──
            if request_source.mode == ModeType.HA:
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
                            ok, response_text = await execute_ha_service(
                                fuzzy_result.service,
                                fuzzy_result.entity_id,
                                friendly_name=fuzzy_result.friendly_name,
                            )
                            if ok:
                                agents_used = ["ha_fuzzy"]
                                async with state.execution_lock:
                                    state.execution_state["status"] = "success"
                                result = build_ha_fast_path_response(
                                    session_id,
                                    response_text,
                                    "ha_fuzzy",
                                    {
                                        "routing_type": "ha_fuzzy",
                                        "service": fuzzy_result.service,
                                        "entity_id": fuzzy_result.entity_id,
                                        "score": fuzzy_result.score,
                                    },
                                )
                                log_vocal_response(
                                    session_id=session_id,
                                    user_prompt=body.user_prompt,
                                    source_type=request_source.type.value,
                                    source_mode=request_source.mode.value,
                                    routing_type="ha_fuzzy",
                                    agents_used=agents_used,
                                    response_text=response_text,
                                    latency_ms=timer.elapsed_ms,
                                    tts_enabled=request_source.tts_enabled,
                                    device_id=request_source.device_id,
                                )
                                return result
                            logger.warning("[EXECUTE] Fuzzy match mais appel HA échoué")
                except Exception as fuzz_err:
                    logger.warning(f"[EXECUTE] Fuzzy match HA échoué : {fuzz_err}")

            # ── Repli LLM léger (tier leger, ~8s max) ──
            if request_source.mode == ModeType.HA and prompt_has_domotic_action(body.user_prompt):
                try:
                    from core.vocal_stt_normalize import normalize_vocal_stt
                    vocal_prompt = normalize_vocal_stt(body.user_prompt)
                    llm_intent = await resolve_ha_via_llm(vocal_prompt or body.user_prompt, session_id)
                    if llm_intent:
                        ok, response_text = await execute_ha_service(
                            llm_intent.service,
                            llm_intent.entity_id,
                        )
                        if ok:
                            agents_used = ["ha_llm_fallback"]
                            async with state.execution_lock:
                                state.execution_state["status"] = "success"
                            result = build_ha_fast_path_response(
                                session_id,
                                response_text,
                                "ha_llm_fallback",
                                {
                                    "routing_type": "ha_llm_fallback",
                                    "service": llm_intent.service,
                                    "entity_id": llm_intent.entity_id,
                                },
                            )
                            log_vocal_response(
                                session_id=session_id,
                                user_prompt=body.user_prompt,
                                source_type=request_source.type.value,
                                source_mode=request_source.mode.value,
                                routing_type="ha_llm_fallback",
                                agents_used=agents_used,
                                response_text=response_text,
                                latency_ms=timer.elapsed_ms,
                                tts_enabled=request_source.tts_enabled,
                                device_id=request_source.device_id,
                            )
                            return result
                except Exception as llm_err:
                    logger.warning(f"[EXECUTE] Repli LLM HA échoué : {llm_err}")

            # ── Small-talk HA ──
            if request_source.mode == ModeType.HA:
                conv_reply = _ha_conversational_response(body.user_prompt)
                if conv_reply:
                    logger.info(f"[EXECUTE] 💬 HA conversational → {conv_reply[:60]}")
                    agents_used = ["ha_conversational"]
                    async with state.execution_lock:
                        state.execution_state["status"] = "success"
                    result = build_ha_fast_path_response(
                        session_id,
                        conv_reply,
                        "ha_conversational",
                        {"routing_type": "ha_conversational"},
                    )
                    log_vocal_response(
                        session_id=session_id,
                        user_prompt=body.user_prompt,
                        source_type=request_source.type.value,
                        source_mode=request_source.mode.value,
                        routing_type="ha_conversational",
                        agents_used=agents_used,
                        response_text=conv_reply,
                        latency_ms=timer.elapsed_ms,
                        tts_enabled=request_source.tts_enabled,
                        device_id=request_source.device_id,
                    )
                    return result

                # Mode domotique : ne jamais lancer le pipeline complet
                if should_block_full_pipeline(request_source):
                    logger.info("[EXECUTE] mode=ha — pipeline bloqué, réponse d'échec courte")
                    async with state.execution_lock:
                        state.execution_state["status"] = "success"
                    result = build_ha_mode_failure_response(session_id)
                    log_vocal_response(
                        session_id=session_id,
                        user_prompt=body.user_prompt,
                        source_type=request_source.type.value,
                        source_mode=request_source.mode.value,
                        routing_type="ha_mode_failure",
                        agents_used=["ha_mode_blocked"],
                        response_text=result["response"],
                        latency_ms=timer.elapsed_ms,
                        tts_enabled=request_source.tts_enabled,
                        device_id=request_source.device_id,
                    )
                    return result

            # ── Pipeline complet (discussion complexe / IDE) ──
            if request_source.should_skip_planner() and routing_type == "casual_chat":
                logger.info("[EXECUTE] skip_planner actif mais fast path déjà tenté")

            config = apply_source_config_overrides(
                load_config(),
                request_source,
                tier_override=body.tier,
                model_override=body.model,
            )
            if body.tier or body.model:
                initial_payload.metadata["workload_override"] = {
                    "tier": body.tier, "model": body.model
                }

            pipeline_timeout = execute_timeout if request_source.mode == ModeType.CHAT else 120.0

            async def _on_event(event_type: str, data: Any, engine=None):
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
                timeout_seconds=pipeline_timeout,
            )

            async with state.execution_lock:
                state.execution_state["status"] = result.get("status", "success")
                state.execution_state["engine_state"] = result.get("engine_state")

            agents_used = result.get("agents_used") or []
            response_text = result.get("response", "")
            log_vocal_response(
                session_id=session_id,
                user_prompt=body.user_prompt,
                source_type=request_source.type.value,
                source_mode=request_source.mode.value,
                routing_type=routing_type,
                agents_used=agents_used,
                response_text=response_text,
                latency_ms=timer.elapsed_ms,
                tts_enabled=request_source.tts_enabled,
                device_id=request_source.device_id,
            )
            return result

    except Exception as e:
        async with state.execution_lock:
            state.execution_state["status"] = "error"
            state.execution_state["error_message"] = str(e)
        logger.error(f"[EXECUTE] Erreur inattendue : {e}")
        return {"status": "error", "error": f"❌ {str(e)}"}


# ── Routes additionnelles (évite de patcher gui_server.py sur le Deck) ──

from core.vocal_audit import get_recent_vocal_logs as _get_vocal_logs


@router.get("/api/vocal/audit")
async def vocal_audit_recent(limit: int = 50, _auth=Depends(optional_auth)):
    """Derniers événements vocal_audit_log (diagnostic STT → moteur)."""
    safe_limit = max(1, min(limit, 200))
    logs = _get_vocal_logs(safe_limit)
    return {"count": len(logs), "logs": logs}


class VocalAbortBody(BaseModel):
    """Corps pour /api/vocal/abort (barge-in Sprint A4)."""
    conversation_id: str | None = None
    session_id: str | None = None
    device_id: str | None = None


@router.post("/api/vocal/abort")
async def vocal_abort(body: VocalAbortBody, _auth=Depends(optional_auth)):
    """Interrompt les streams vocaux Discussion en cours (barge-in Tab5)."""
    from core.vocal_abort import abort_vocal_streams, list_active_vocal_streams

    state = get_app_state()
    count = abort_vocal_streams(
        session_id=body.session_id,
        conversation_id=body.conversation_id,
        device_id=body.device_id,
    )
    async with state.execution_lock:
        if state.execution_state.get("status") == "running":
            state.execution_state["status"] = "success"
            state.execution_state["error_message"] = "vocal_abort"
    return {
        "aborted": count,
        "active_before": list_active_vocal_streams(),
        "status": "ok",
    }


class CodeRequestBody(BaseModel):
    prompt: str
    tier: str | None = None
    context_files: list[str] = []


@router.post("/api/code")
async def run_code_task(body: CodeRequestBody, _auth=Depends(optional_auth)) -> dict[str, Any]:
    """Coding front HTTP (#T204) — équivalent `python -m tools.code_front`."""
    from services.coding_front import run_coding_task

    file_contents: dict[str, str] = {}
    for path in body.context_files[:5]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                file_contents[path] = f.read()[:40_000]
        except OSError as exc:
            logger.warning("[API_CODE] Fichier ignoré %s : %s", path, exc)

    return await run_coding_task(
        body.prompt,
        file_contents=file_contents or None,
        force_tier=body.tier if body.tier in ("moyen", "fort") else None,
    )
