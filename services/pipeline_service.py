"""
services/pipeline_service.py — Service d'orchestration des pipelines du Moteur.

Créé lors du refactoring Semaine 3 / Phase 4.

Contient la LOGIQUE MÉTIER pure extraite de gui_server.py :
  - run_fast_path()     : Fast-path conversation (casual_chat) avec cache TTL V9 PF-3
  - run_full_pipeline() : Pipeline complet (Planner → DAG → Executor → Reviewer)
  - run_engine_bg()     : Exécution moteur en background (fire-and-forget)
  - stream_tokens()     : Générateur SSE token-par-token pour /api/chat/stream

Les routes API (/api/execute, /api/run, /api/chat/stream) dans gui_server.py
ne sont plus que de légères enveloppes appelant ces fonctions.

Avantages :
  - Testable unitairement sans HTTP (pytest direct sur les fonctions)
  - Réutilisable depuis plusieurs routes ou depuis des agents internes
  - gui_server.py devient < 300 lignes d'ici la prochaine itération

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import asyncio
import hashlib
import logging
from typing import Any, AsyncGenerator, Dict, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Service : Fast-Path (casual_chat) — V9 PF-3 + PF-4
# ══════════════════════════════════════════════════════════════════

FAST_PATH_SYSTEM_PROMPT = (
    "Tu es un assistant IA expert en domotique, code et technologie. "
    "Réponds de manière concise, chaleureuse et en français."
)

FAST_PATH_PROVIDERS = [
    "gemini-3.5-flash-free",
    "deepseek-chat",
    "gemini-2.5-flash-free",
]


async def run_fast_path(
    user_prompt: str,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
) -> Optional[str]:
    """
    Exécute le fast-path (casual_chat) en essayant les providers rapides dans l'ordre.

    Vérifie le cache TTL avant tout appel LLM — cache hit = ~5ms.
    La persistance BDD est déportée en tâche async non-bloquante.

    Args:
        user_prompt     : Texte de la requête utilisateur
        session_id      : ID unique de session pour la persistance BDD
        gateway         : Instance LLMGateway
        token_tracker   : Instance TokenTracker
        fast_path_cache : TTLCache(maxsize=100, ttl=15) partagé depuis AppState

    Returns:
        str  : Réponse du LLM si succès
        None : Si tous les providers ont échoué (déclenche le pipeline complet)

    Raises:
        RuntimeError : Si tous les providers fast path ont échoué
    """
    token_tracker.init_session(session_id, user_prompt)

    # ── Vérification du cache TTL (évite un appel LLM si prompt identique < 15s) ──
    _cache_key = hashlib.md5(f"{FAST_PATH_SYSTEM_PROMPT}||{user_prompt}".encode()).hexdigest()
    _cached = fast_path_cache.get(_cache_key)

    if _cached is not None:
        logger.info(f"[FAST_PATH] ⚡ Cache HIT (clé {_cache_key[:8]}…) — réponse instantanée")
        # Persistance async non-bloquante même sur cache hit
        asyncio.create_task(_persist_fast_path_async(session_id, user_prompt, _cached))
        return _cached

    # ── Tentatives sur les providers rapides ──
    response_text = None
    loop = asyncio.get_event_loop()

    for pname in FAST_PATH_PROVIDERS:
        try:
            fast_provider = gateway.get_provider(pname)
        except ValueError:
            logger.debug(f"[FAST_PATH] Provider {pname} non disponible, skip")
            continue

        try:
            logger.info(f"[FAST_PATH] Tentative → {pname}")
            response_text = await loop.run_in_executor(
                None,
                lambda p=fast_provider: p.generate(
                    FAST_PATH_SYSTEM_PROMPT,
                    user_prompt,
                    session_id=session_id,
                )
            )
            break  # Succès → sortir de la boucle
        except Exception as provider_err:
            logger.warning(f"[FAST_PATH] {pname} échoué : {provider_err}")
            continue

    if response_text is None:
        raise RuntimeError("Tous les providers fast path ont échoué")

    # ── Mise en cache du résultat ──
    fast_path_cache[_cache_key] = response_text

    # ── Persistance BDD async non-bloquante (ne bloque pas la réponse HTTP) ──
    asyncio.create_task(_persist_fast_path_async(session_id, user_prompt, str(response_text)))

    return response_text


async def _persist_fast_path_async(sid: str, prompt: str, result: str, source: str = "") -> None:
    """Tâche async différée : persiste la session fast-path en BDD + EventStore ."""
    try:
        from core.session_history import record_session_start, record_session_end
        record_session_start(sid, prompt, "fast_path")
        record_session_end(
            sid, "success",
            agents_invoked=["fast_path"],
            task_count=1,
            result_summary=result[:500],
        )
    except Exception as _e:
        logger.debug(f"[FAST_PATH] Erreur persistance BDD (non-bloquante) : {_e}")

    # Event Sourcing : log request_received + response_sent
    try:
        from core.event_store import get_event_store
        _es = get_event_store()
        await _es.log("request_received",  source=source, session_id=sid, payload={"prompt": prompt[:200]})
        await _es.log("response_sent",     source=source, agent="fast_path", session_id=sid,
                      payload={"response_len": len(result)})
    except Exception as _ee:
        logger.debug(f"[FAST_PATH] EventStore skip : {_ee}")


def _build_fast_path_response(session_id: str, user_prompt: str, response_text: str) -> Dict[str, Any]:
    """Construit le dict de réponse JSON standardisé pour le fast-path."""
    return {
        "status": "completed",
        "session_id": session_id,
        "response": str(response_text).strip(),
        "history": [{
            "agent_name": "fast_path",
            "status": "success",
            "result_data": str(response_text).strip(),
            "next_agent": "END",
            "error_message": None,
            "new_tasks": [],
            "metadata": {"routing_type": "casual_chat", "model_tier": "leger"},
        }],
        "agents_used": ["fast_path"],
    }


# ══════════════════════════════════════════════════════════════════
# Service : Pipeline complet (Planner → DAG → Executor → Reviewer)
# ══════════════════════════════════════════════════════════════════

async def run_full_pipeline(
    user_prompt: str,
    session_id: str,
    initial_payload: Any,
    starting_agent: str,
    on_event_callback,
    config: Dict,
) -> Dict[str, Any]:
    """
    Exécute le pipeline complet du moteur : Planner → DAG → Executor → Reviewer.

    Assemble toutes les briques : LLMGateway, ToolRegistry, Agents, Engine,
    MCPBridge, WorkflowBridge, Plugins. Timeout 120s maximum.

    Args:
        user_prompt      : Requête utilisateur
        session_id       : ID de session unique
        initial_payload  : Payload de démarrage (depuis Router.analyze_request)
        starting_agent   : Nom de l'agent de démarrage (ex: "planner")
        on_event_callback: Coroutine async appelée à chaque événement moteur
        config           : Configuration du moteur (config.json)

    Returns:
        dict : Réponse JSON normalisée avec status, response, history, agents_used
    """
    from core.llm_gateway import LLMGateway
    from tools.tool_registry import ToolRegistry
    from memory.context_manager import ContextManager
    from core.engine import Engine
    from agents.executor import ExecutorAgent
    from agents.planner import PlannerAgent
    from agents.antigravity_agent import AntigravityAgent
    from agents.ha_agent import HACommandAgent
    from agents.reviewer import ReviewerAgent
    from core.mcp_bridge import MCPBridge
    from core.workflow_bridge import WorkflowBridge
    from tools.system import read_file, write_file, validate_config_yaml
    from tools.terminal import run_terminal_command
    from tools.api import call_api
    from core.serializers import global_state_to_dict, state_update_to_dict
    from core.session_history import record_session_start, record_session_end
    from core import token_tracker

    # ── Construction des dépendances ──
    gateway = LLMGateway()
    registry = ToolRegistry()
    context_manager = ContextManager(llm_gateway=gateway)

    # ── Outils standard ──
    registry.register("read_file", read_file, "Lit le contenu d'un fichier texte local.")
    registry.register("write_file", write_file, "Crée ou modifie un fichier texte local.")
    registry.register("run_terminal_command", run_terminal_command, "Exécute une commande système.")
    registry.register("call_api", call_api, "Effectue une requête HTTP vers une API distante.")
    registry.register("validate_config_yaml", validate_config_yaml, "Valide la syntaxe YAML ESPHome.")

    # ── Outils Git Safety ──
    try:
        from tools.git_safety import git_create_checkpoint, git_rollback_checkpoint, git_apply_checkpoint
        registry.register("git_create_checkpoint", git_create_checkpoint, "Crée un checkpoint Git.")
        registry.register("git_rollback_checkpoint", git_rollback_checkpoint, "Rollback Git.")
        registry.register("git_apply_checkpoint", git_apply_checkpoint, "Valide checkpoint Git.")
    except ImportError:
        logger.debug("[PIPELINE] Outils Git Safety non disponibles")

    # ── MCP Bridge ──
    mcp_bridge = MCPBridge()

    # ── Agents ──
    executor = ExecutorAgent(
        llm_gateway=gateway, tool_registry=registry,
        provider_name=config["executor_model"]
    )
    planner = PlannerAgent(llm_gateway=gateway, provider_name=config["planner_model"])
    antigravity_agent = AntigravityAgent(
        llm_gateway=gateway, tool_registry=registry,
        provider_name=config["antigravity_model"]
    )
    ha_agent = HACommandAgent(
        llm_gateway=gateway, tool_registry=registry,
        provider_name=config["executor_model"]
    )

    reviewer = ReviewerAgent(
        llm_gateway=gateway,
        provider_name=config.get("reviewer_model", "moyen")
    )

    # ── Engine ──
    token_tracker.init_session(session_id, user_prompt)
    engine = Engine(session_id=session_id, context_manager=context_manager)
    engine.register_agent(executor)
    engine.register_agent(planner)
    engine.register_agent(antigravity_agent)
    engine.register_agent(ha_agent)
    engine.register_agent(reviewer)

    # ── Agents custom (WorkflowBridge) ──
    try:
        bridge = WorkflowBridge()
        for custom in bridge.get_custom_agents_config():
            custom_agent = ExecutorAgent(
                llm_gateway=gateway, tool_registry=registry,
                provider_name=custom.get("tier", "automatique")
            )
            custom_agent.name = custom["name"]
            custom_agent.system_prompt = (
                f"Tu es l'agent custom '{custom['name']}' ({custom.get('label', custom['name'])}).\n"
                f"Tu hérites de la boucle ReAct d'ExecutorAgent avec accès à tous les outils.\n"
                f"Exécute la tâche qui t'est assignée de manière rigoureuse et pédagogue."
            )
            engine.register_agent(custom_agent)
    except Exception:
        pass

    # ── Plugins auto-découverts ──
    try:
        from core.plugin_registry import PluginRegistry
        plugin_registry = PluginRegistry()
        for plugin_info in plugin_registry.discover():
            if plugin_info.enabled and plugin_info.agent_class:
                plugin_agent = plugin_registry.create_agent(
                    plugin_info.name, llm_gateway=gateway, tool_registry=registry
                )
                if plugin_agent:
                    engine.register_agent(plugin_agent)
    except Exception:
        pass

    # ── Détection Multi-Intent avant exécution ──
    # Si la requête contient plusieurs intentions séparables, on les distribue
    # en parallèle (asyncio.gather) avec un payload distinct par intent.
    try:
        from core.intent_splitter import IntentSplitter
        _splitter = IntentSplitter()
        _intents = _splitter.split(user_prompt)
        if len(_intents) > 1:
            logger.info(
                f"[PIPELINE] [MULTI-INTENT] Détecté {len(_intents)} sous-intents — "
                f"exécution parallèle"
            )
            await mcp_bridge.start(registry, user_prompt=user_prompt)
            record_session_start(session_id, user_prompt, starting_agent)
            return await run_multi_intent_pipeline(
                intents=_intents,
                session_id=session_id,
                initial_payload=initial_payload,
                starting_agent=starting_agent,
                engine=engine,
                on_event_callback=on_event_callback,
                mcp_bridge=mcp_bridge,
            )
    except Exception as _mi_err:
        logger.debug(f"[PIPELINE] IntentSplitter non disponible : {_mi_err}")

    # ── Callback SSE (diffusion temps réel pendant l'exécution) ──
    async def handle_engine_event(event_type: str, data):
        try:
            await on_event_callback(event_type, data, engine)
        except Exception:
            pass

    engine.on_event = handle_engine_event

    # ── Exécution avec timeout 120s ──
    await mcp_bridge.start(registry, user_prompt=user_prompt)
    record_session_start(session_id, user_prompt, starting_agent)

    try:
        final_state = await asyncio.wait_for(
            engine.run(initial_payload, starting_agent),
            timeout=120.0
        )
    except asyncio.TimeoutError:
        await _cleanup_mcp(mcp_bridge)
        record_session_end(session_id, "error", error_message="Timeout 120s")
        return {
            "status": "error",
            "error": "⏱️ Timeout : l'exécution a dépassé 120 secondes. Essayez une requête plus simple.",
        }
    except Exception as e:
        await _cleanup_mcp(mcp_bridge)
        record_session_end(session_id, "error", error_message=str(e))
        logger.error(f"[PIPELINE] Erreur : {e}")
        return {"status": "error", "error": f"❌ {str(e)}"}

    # ── Extraction du résultat ──
    history_dicts = [state_update_to_dict(h) for h in final_state.history]
    results = [h for h in history_dicts if h.get("status") == "success" and h.get("result_data")]
    response_text = "\n\n".join(str(r["result_data"]) for r in results) if results else ""

    agents_used = list({h.agent_name for h in final_state.history if h.agent_name})
    record_session_end(
        session_id, "success",
        agents_invoked=agents_used,
        task_count=len(final_state.history),
        result_summary=response_text[:500] if response_text else None,
    )

    await _cleanup_mcp(mcp_bridge)

    # Scanner les tokens CLI à la fin pour s'assurer du tracking temps réel
    try:
        from core.cli_token_collector import collect_all_cli_tokens
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(hours=2)).isoformat()
        # Exécuter dans un thread en tâche de fond pour ne pas bloquer le retour de l'API
        asyncio.create_task(asyncio.to_thread(collect_all_cli_tokens, since_date=since, persist_to_db=True))
    except Exception as cli_err:
        logger.debug(f"[PIPELINE] Scan CLI final ignoré : {cli_err}")

    # Event Sourcing : log request + response pour audit trail
    try:
        from core.event_store import get_event_store
        _es     = get_event_store()
        _source = initial_payload.metadata.get("source", "") if hasattr(initial_payload, "metadata") else ""
        asyncio.create_task(_es.log(
            "response_sent", source=_source, agent="|".join(agents_used),
            session_id=session_id,
            payload={"response_len": len(response_text), "agents": agents_used},
        ))
    except Exception as _ee:
        logger.debug(f"[PIPELINE] EventStore skip : {_ee}")

    return {
        "status": "completed",
        "session_id": session_id,
        "response": response_text or "✅ Tâche terminée.",
        "history": history_dicts,
        "agents_used": agents_used,
        "engine_state": global_state_to_dict(final_state),
    }


async def run_multi_intent_pipeline(
    intents: list,
    session_id: str,
    initial_payload: Any,
    starting_agent: str,
    engine,
    on_event_callback,
    mcp_bridge,
) -> Dict[str, Any]:
    """
    Exécute plusieurs sous-intents en parallèle via asyncio.gather.

    Chaque intent est exécuté dans engine.run() avec un payload clOné et un
    objectif réécrit. Les réponses sont fus ionnées en post-processing.

    Args:
        intents:          Liste de sous-requêtes (strings)
        session_id:       ID de session
        initial_payload:  Payload de référence (cloné par intent)
        starting_agent:   Agent de démarrage
        engine:           Instance Engine (déjà configurée)
        on_event_callback: Callback SSE
        mcp_bridge:       MCPBridge déjà démarré

    Returns:
        Dict normalisé : {status, response, agents_used, multi_intent: True, ...}
    """
    from core.serializers import state_update_to_dict

    logger.info(
        f"[PIPELINE] [MULTI-INTENT] Démarrage parallèle — {len(intents)} intents : "
        + " | ".join(f'"{i[:40]}"' for i in intents)
    )

    # Notifier le client SSE du démarrage multi-intent
    try:
        await on_event_callback(
            "multi_intent_start",
            {"intents": intents, "count": len(intents)},
            engine,
        )
    except Exception:
        pass

    async def _run_single_intent(intent: str, index: int):
        """Exécute un intent isolé avec timeout 45s."""
        try:
            # Cloner le payload et remplacer l'objectif
            import copy
            sub_payload = copy.deepcopy(initial_payload)
            # Adapter selon le type de payload (dataclass ou dict)
            if hasattr(sub_payload, 'task_objective'):
                sub_payload.task_objective = intent
            elif isinstance(sub_payload, dict):
                sub_payload['task_objective'] = intent

            sub_state = await asyncio.wait_for(
                engine.run(sub_payload, starting_agent),
                timeout=45.0,
            )

            # Notifier fin de cet intent
            try:
                await on_event_callback(
                    "intent_done",
                    {"intent_index": index, "intent": intent[:60]},
                    engine,
                )
            except Exception:
                pass

            # Extraire la réponse
            history_dicts = [state_update_to_dict(h) for h in sub_state.history]
            results = [
                h for h in history_dicts
                if h.get("status") == "success" and h.get("result_data")
            ]
            response_text = "\n".join(str(r["result_data"]) for r in results)
            agents_used   = list({h.agent_name for h in sub_state.history if h.agent_name})

            return {
                "intent":      intent,
                "index":       index,
                "response":    response_text or "✅ Fait.",
                "agents_used": agents_used,
                "status":      "success",
            }

        except asyncio.TimeoutError:
            logger.warning(f"[PIPELINE] [MULTI-INTENT] Timeout intent[{index}] : {intent[:50]}")
            return {"intent": intent, "index": index, "response": "⏱️ Timeout 45s.", "agents_used": [], "status": "timeout"}
        except Exception as e:
            logger.error(f"[PIPELINE] [MULTI-INTENT] Erreur intent[{index}] : {e}")
            return {"intent": intent, "index": index, "response": f"❌ {str(e)[:100]}", "agents_used": [], "status": "error"}

    # Lancement en parallèle (return_exceptions=True : un échec ne tue pas les autres)
    tasks = [_run_single_intent(intent, i) for i, intent in enumerate(intents)]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Normaliser (en cas d'exception non attrapée par return_exceptions)
    intent_results = []
    for r in raw_results:
        if isinstance(r, Exception):
            intent_results.append({"response": f"❌ {str(r)[:100]}", "agents_used": [], "status": "error"})
        else:
            intent_results.append(r)

    # Fusion des réponses
    all_agents   = []
    all_responses = []
    success_count = 0

    for i, res in enumerate(intent_results):
        if res.get("status") == "success":
            success_count += 1
        all_agents.extend(res.get("agents_used", []))
        label = f"[Intent {i+1}]"
        all_responses.append(f"{label}\n{res.get('response', '')}")

    # Si un seul intent a réussi, réponse directe sans label
    if success_count == 1:
        ok_results = [r for r in intent_results if r.get("status") == "success"]
        merged_response = ok_results[0]["response"]
    elif success_count == 0:
        merged_response = "❌ Aucun intent n'a pu être traité."
    else:
        merged_response = "\n\n".join(all_responses)

    await _cleanup_mcp(mcp_bridge)

    logger.info(
        f"[PIPELINE] [MULTI-INTENT] Terminé : {success_count}/{len(intents)} succès, "
        f"agents={list(set(all_agents))}"
    )

    return {
        "status":          "completed",
        "session_id":      session_id,
        "response":        merged_response,
        "agents_used":     list(set(all_agents)),
        "multi_intent":    True,
        "intents_count":   len(intents),
        "intents_results": intent_results,
        "history":         [],
    }


async def _cleanup_mcp(mcp_bridge) -> None:
    """Arrête proprement le MCP Bridge (ignore les erreurs de fermeture)."""
    try:
        await mcp_bridge.stop()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# Service : Exécution moteur en background (fire-and-forget)
# ══════════════════════════════════════════════════════════════════

async def run_engine_background(objective: str, on_event_callback, session_id: Optional[str] = None) -> None:
    """
    Lance le moteur en arrière-plan pour /api/run (fire-and-forget).
    Met à jour execution_state via AppState pendant l'exécution.

    Args:
        objective        : Objectif de la tâche
        on_event_callback: Coroutine async appelée à chaque événement moteur
        session_id       : ID de session optionnel pré-généré (V12 B3-Fix)
    """
    from core.app_state import get_app_state
    from core.llm_gateway import LLMGateway
    from core.llm_gateway import load_config  # config_loader n'existe pas — load_config est dans llm_gateway

    state = get_app_state()

    try:
        config = load_config()
        # [P1-2.1] Router canonique (factory) au lieu d'un Router nu : active le
        # slow-path LLM, le RAG et le classement Elo sur le flux prod /api/run.
        router = state.get_shared_router()
        initial_payload, starting_agent = await router.analyze_request(objective)

        if not session_id:
            import uuid as _uuid  # uuid4 évite les collisions d'ID session
            session_id = f"bg_{_uuid.uuid4().hex[:8]}"
        gateway = LLMGateway()

        result = await run_full_pipeline(
            user_prompt=objective,
            session_id=session_id,
            initial_payload=initial_payload,
            starting_agent=starting_agent,
            on_event_callback=on_event_callback,
            config=config,
        )

        async with state.execution_lock:
            state.execution_state["status"] = result.get("status", "success")
            state.execution_state["engine_state"] = result.get("engine_state")

    except Exception as e:
        logger.error(f"[ENGINE_BG] Erreur : {e}")
        async with state.execution_lock:
            state.execution_state["status"] = "error"
            state.execution_state["error_message"] = str(e)


# ══════════════════════════════════════════════════════════════════
# Service : Streaming token-par-token (/api/chat/stream)
# ══════════════════════════════════════════════════════════════════

async def stream_chat_tokens(
    prompt: str,
    system_prompt: str,
    provider_name: str,
) -> AsyncGenerator[str, None]:
    """
    Générateur async de tokens SSE pour /api/chat/stream.

    Appelle gateway.stream() et formate chaque chunk en `data: {...}\\n\\n`.
    En cas d'erreur, envoie un chunk {"error": "...", "done": true}.

    Args:
        prompt        : Texte de la requête utilisateur
        system_prompt : System prompt du provider
        provider_name : Nom du provider (ex: "deepseek-chat")

    Yields:
        str : Lignes SSE formatées (data: {...}\\n\\n)
    """
    import json
    from core.llm_gateway import LLMGateway

    gateway = LLMGateway()

    try:
        for chunk in gateway.stream(provider_name, system_prompt, prompt):
            data = json.dumps(chunk, ensure_ascii=False)
            yield f"data: {data}\n\n"
            if chunk.get("done"):
                break
    except Exception as e:
        logger.error(f"[STREAM] Erreur streaming {provider_name} : {e}")
        import json as _json
        error_data = _json.dumps({"error": str(e), "done": True})
        yield f"data: {error_data}\n\n"
