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
import time
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import ValidationError

from core.state import TaskPayload

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# [#T194] Override de force de workload (sélecteur de tier IHM)
# ══════════════════════════════════════════════════════════════════

WORKLOAD_TIERS = ("leger", "moyen", "fort", "automatique")


def apply_workload_override(config: dict, tier: str | None = None, model: str | None = None) -> dict:
    """
    Applique l'override par requête du sélecteur IHM (tier léger/moyen/fort/auto
    ou id de modèle explicite) sur une COPIE de la config.

    Cible executor_model et ha_model : ce sont les agents qui produisent la
    réponse — le Planner garde son modèle configuré (fiabilité du plan JSON,
    coût déjà faible). `provider_name` accepte indifféremment un nom de tier
    (résolu par get_provider_for_tier) ou un id littéral (get_provider), donc
    aucune autre plomberie n'est nécessaire.
    """
    if model:
        return {**config, "executor_model": model, "ha_model": model}
    if tier in WORKLOAD_TIERS:
        return {**config, "executor_model": tier, "ha_model": tier}
    return config


# ══════════════════════════════════════════════════════════════════
# Service : Fast-Path (casual_chat) — V9 PF-3 + PF-4
# ══════════════════════════════════════════════════════════════════

FAST_PATH_SYSTEM_PROMPT = (
    "Tu es l'assistant vocal de la maison. "
    "Réponds UNIQUEMENT en français, en phrases naturelles (ou un petit tableau texte "
    "si on te le demande). Chaleureux, concis. "
    "INTERDIT : JSON, code, balises, appels d'outils, MCP, function_call, "
    "ou tout texte technique du type {\"tool\":...}. "
    "Tu n'as PAS d'outils MCP ni de recherche web dans ce mode chat : "
    "si tu ne peux pas répondre (actualité, dernier modèle sorti, photo réelle, etc.), "
    "dis-le clairement en français avec la raison, sans inventer d'appel d'outil. "
    "N'invente jamais d'état domotique, de météo ou d'agenda."
)

# Cerebras gpt-oss-120b en tête : bench local 2026-07-21 ~430 ms TTFT
# vs ~1,8 s Gemini 3.5 Flash free / DeepSeek (SSE mode=chat). Fallback cloud
# si quota Cerebras (30 RPM) ou indispo.
FAST_PATH_PROVIDERS = [
    "gpt-oss-120b",
    "ollama_pc",  # Ollama PC (GPU locale, fine-tune domotique) — local-first ; repli cloud si PC éteint (connect timeout 2s)
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
    tier_override: str | None = None,
    model_override: str | None = None,
    system_prompt_suffix: str = "",
    inject_project_context: bool = False,
    conversation_id: str | None = None,
    enable_vocal_tools: bool = False,
) -> str | None:
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
        enable_vocal_tools : Boucle outils allowlist (HA/calendrier/web/mémoire)

    Returns:
        str  : Réponse du LLM si succès
        None : Si tous les providers ont échoué (déclenche le pipeline complet)

    Raises:
        RuntimeError : Si tous les providers fast path ont échoué
    """
    # [#T301] Le fast-path n'est pas un agent : il appelle le gateway directement,
    # donc l'enveloppe de `BaseAgent.invoke()` (#T296) ne s'applique pas et
    # `token_usage.agent_name` restait NULL. Mesuré en exerçant la prod le 11/08 :
    # une requête réelle « casual_chat » écrit sa ligne de consommation sans aucune
    # étiquette, alors que la route se déclare elle-même `agents_used = ["fast_path"]`.
    # C'est le chemin DOMINANT du moteur (vocal : 6 appelants, dont vocal_jobs et
    # vocal_host), donc sans ça la colonne reste vide là où le moteur sert vraiment.
    #
    # Pose sans restauration, comme `core/llm/fallback_trace.py` (#T288) : chaque
    # requête s'exécute dans sa propre tâche asyncio, qui démarre avec une copie du
    # contexte et meurt avec la requête. Vérifié : aucun des 6 appelants n'invoque
    # le fast-path depuis un agent, il n'y a donc pas de contexte parent à rendre.
    from core.agent_trace import poser_agent_courant
    poser_agent_courant("fast_path")

    token_tracker.init_session(session_id, user_prompt)

    system_prompt = FAST_PATH_SYSTEM_PROMPT
    if system_prompt_suffix:
        system_prompt = f"{system_prompt}{system_prompt_suffix}"
    if conversation_id:
        try:
            from core.vocal_session import build_vocal_session_context
            history_block = build_vocal_session_context(conversation_id)
            if history_block:
                system_prompt = f"{system_prompt}{history_block}"
        except Exception as hist_err:
            logger.debug(f"[FAST_PATH] Historique vocal ignoré : {hist_err}")
    if inject_project_context:
        try:
            from services.execute_service import get_casual_chat_context
            rag_block = await get_casual_chat_context(user_prompt)
            if rag_block:
                system_prompt = f"{system_prompt}{rag_block}"
        except Exception as rag_err:
            logger.debug(f"[FAST_PATH] Contexte projet ignoré : {rag_err}")

    # ── Vérification du cache TTL (évite un appel LLM si prompt identique < 15s) ──
    # [#T194] L'override tier/modèle fait partie de la clé : une même question posée
    # en "fort" ne doit pas resservir la réponse cachée du tier "léger".
    # Désactiver le cache TTL quand les outils sont actifs (commandes HA / web).
    _override_key = (
        f"{tier_override or ''}|{model_override or ''}|{conversation_id or ''}"
        f"|tools={int(enable_vocal_tools)}"
    )
    _cache_key = hashlib.md5(
        f"{system_prompt}||{_override_key}||{user_prompt}".encode()
    ).hexdigest()
    _cached = None if enable_vocal_tools else fast_path_cache.get(_cache_key)

    if _cached is not None:
        logger.info(f"[FAST_PATH] ⚡ Cache HIT (clé {_cache_key[:8]}…) — réponse instantanée")
        # Persistance async non-bloquante même sur cache hit
        asyncio.create_task(_persist_fast_path_async(session_id, user_prompt, _cached))
        return _cached

    # ── Tentatives sur les providers rapides ──
    response_text = None

    # [#T194] Le modèle/tier choisi dans l'IHM passe en tête de cascade ; la
    # liste statique FAST_PATH_PROVIDERS reste le filet de sécurité derrière.
    candidates: list = []
    if model_override:
        candidates.append(model_override)
    elif tier_override in WORKLOAD_TIERS and tier_override != "automatique":
        try:
            from core.llm_gateway import load_config
            _, tier_provider = gateway.get_provider_for_tier(tier_override, load_config())
            candidates.append((f"tier:{tier_override}", tier_provider))
        except Exception as tier_err:
            logger.warning(f"[FAST_PATH] Tier '{tier_override}' non résolu : {tier_err}")
    candidates.extend(FAST_PATH_PROVIDERS)

    from core.vocal_tools import provider_supports_openai_tools, run_vocal_tool_loop

    # [#T352] Résolution préalable des candidats en paires (pname, provider)
    # disponibles. La sonde d'hôte a besoin du provider pour lire son base_url,
    # et de connaître le nombre de candidats restants pour ne JAMAIS vider la
    # cascade : si elle écarterait tous les candidats, le dernier est tenté quand
    # même — mieux vaut payer 2 s que ne rien répondre.
    candidats_resolus: list[tuple[str, object]] = []
    for entry in candidates:
        if isinstance(entry, tuple):
            candidats_resolus.append(entry)
            continue
        try:
            candidats_resolus.append((entry, gateway.get_provider(entry)))
        except ValueError:
            logger.debug(f"[FAST_PATH] Provider {entry} non disponible, skip")

    for index, (pname, fast_provider) in enumerate(candidats_resolus):
        # [#T352] Sonde d'accessibilité d'hôte : avant de tenter un provider dont
        # l'URL pointe vers un hôte du réseau local, on vérifie que la machine
        # décroche et on passe au suivant si ce n'est pas le cas. Jamais sur le
        # dernier candidat (ne pas vider la cascade), jamais sur un hôte cloud
        # (latence variable, DNS, proxys), et toute exception de la sonde = « on
        # ne sait pas » = on tente le provider comme avant.
        if index < len(candidats_resolus) - 1 and await _fast_path_hote_muet(
            gateway, pname, fast_provider
        ):
            continue

        try:
            # Mini allowlist outils (HA/calendrier/web/mémoire) si provider OpenAI-compat
            if enable_vocal_tools and provider_supports_openai_tools(fast_provider):
                logger.info(f"[FAST_PATH] Tentative outils → {pname}")
                tool_text = await run_vocal_tool_loop(
                    fast_provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    session_id=session_id,
                )
                if tool_text:
                    response_text = tool_text
                    break

            logger.info(f"[FAST_PATH] Tentative → {pname}")
            # [#T301] `asyncio.to_thread` et NON `loop.run_in_executor` : les deux
            # déportent dans l'executor par défaut, mais seul `to_thread` propage la
            # ContextVar (vérifié : run_in_executor -> None, to_thread -> valeur).
            # Sans ça, l'étiquette d'agent posée juste au-dessus n'atteint jamais
            # `record_usage()`, qui s'exécute dans le thread du provider.
            response_text = await asyncio.to_thread(
                fast_provider.generate,
                system_prompt,
                user_prompt,
                session_id=session_id,
            )
            # generate() peut renvoyer un message tool_calls si tools passés ailleurs
            if isinstance(response_text, dict):
                response_text = (response_text.get("content") or "").strip() or None
                if not response_text:
                    continue
            break  # Succès → sortir de la boucle
        except Exception as provider_err:
            logger.warning(f"[FAST_PATH] {pname} échoué : {provider_err}")
            continue

    if response_text is None:
        raise RuntimeError("Tous les providers fast path ont échoué")

    # ── Mise en cache du résultat (sauf chemin outils : états HA changeants) ──
    if not enable_vocal_tools:
        fast_path_cache[_cache_key] = response_text

    # ── Persistance BDD async non-bloquante (ne bloque pas la réponse HTTP) ──
    asyncio.create_task(_persist_fast_path_async(session_id, user_prompt, str(response_text)))

    return response_text


async def _fast_path_hote_muet(gateway, pname: str, fast_provider) -> bool:
    """True si l'hôte local du provider ne décroche pas → on le saute (#T352).

    Garde-fous :
    - ne sonde QUE les hôtes locaux, via le critère `_est_hote_local` du gateway
      (loopback + IP privée LAN ; tout nom DNS est traité comme externe) — un
      provider cloud n'est JAMAIS écarté par une sonde TCP ;
    - toute exception de la sonde (socket, DNS, boucle asyncio) = « on ne sait
      pas » = on tente le provider comme avant (le doute ne devient pas un refus).
    """
    try:
        from core.llm import provider_health

        base_url = provider_health.base_url_provider(fast_provider)
        if not base_url:
            return False
        hote_port = provider_health.hote_et_port(base_url)
        if hote_port is None:
            return False
        hote, port = hote_port
        if not gateway._est_hote_local(hote):
            return False  # hôte cloud : jamais sondé
        if await provider_health.hote_joignable(base_url):
            return False  # la machine décroche → on tente
        logger.info(f"[FAST_PATH] {pname} ignoré : {hote}:{port} ne décroche pas")
        return True
    except Exception as _e:
        logger.debug(f"[FAST_PATH] Sonde hôte ignorée pour {pname} : {_e}")
        return False


async def _persist_fast_path_async(sid: str, prompt: str, result: str, source: str = "") -> None:
    """Tâche async différée : persiste la session fast-path en BDD + EventStore ."""
    try:
        from core.session_history import record_session_end, record_session_start
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


def _build_fast_path_response(session_id: str, user_prompt: str, response_text: str) -> dict[str, Any]:
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

def monter_moteur(session_id: str, user_prompt: str, config: dict) -> tuple:
    """
    [#T267] Assemble un moteur complet : gateway, outils, agents, plugins.

    Extrait tel quel du corps de `run_full_pipeline()` (aucun changement de
    logique) pour être réutilisable par la reprise après approbation humaine :
    le DAG approuvé doit se rejouer dans le même environnement que celui qui l'a
    planifié, sinon un outil absent le ferait échouer pour une raison qui n'a
    rien à voir avec le plan.

    Returns:
        (engine, registry, mcp_bridge, gateway)
    """
    from agents.antigravity_agent import AntigravityAgent
    from agents.executor import ExecutorAgent
    from agents.ha_agent import HACommandAgent
    from agents.planner import PlannerAgent
    from agents.reviewer import ReviewerAgent
    from core import token_tracker
    from core.engine import Engine
    from core.llm_gateway import LLMGateway
    from core.mcp_bridge import MCPBridge
    from core.workflow_bridge import WorkflowBridge
    from memory.context_manager import ContextManager
    from tools.registry_setup import register_base_tools, register_extended_tools
    from tools.tool_registry import ToolRegistry

    gateway = LLMGateway()
    registry = ToolRegistry()
    context_manager = ContextManager(llm_gateway=gateway)

    # ── Outils (enregistrement factorisé, #T213) ──
    # Le chemin HTTP principal dispose désormais des mêmes familles d'outils que
    # core/factory.py (avant : base + git seulement — Workspace/Cloud/Imagen
    # manquaient silencieusement ici, alors que compensés en théorie par MCPBridge).
    register_base_tools(registry)
    register_extended_tools(registry)

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
    # [#T202] repo_root optionnel dans la config (posé par DreamCoder pour
    # cibler son clone de travail dédié) — None = comportement historique.
    engine = Engine(
        session_id=session_id,
        context_manager=context_manager,
        repo_root=config.get("repo_root"),
    )
    engine.register_agent(executor)
    engine.register_agent(planner)
    engine.register_agent(antigravity_agent)
    engine.register_agent(ha_agent)
    engine.register_agent(reviewer)

    # ── Agents custom (WorkflowBridge) ──
    try:
        from core.custom_agents import build_custom_agent_prompt
        bridge = WorkflowBridge()
        for custom in bridge.get_custom_agents_config():
            custom_agent = ExecutorAgent(
                llm_gateway=gateway, tool_registry=registry,
                provider_name=custom.get("tier", "automatique"),
                # [#T233] Restrictions d'outils (absent/None = tous, [] = aucun)
                allowed_tools=custom.get("allowed_tools"),
            )
            custom_agent.name = custom["name"]
            custom_agent.system_prompt = build_custom_agent_prompt(
                custom["name"], custom.get("label"), custom.get("allowed_tools")
            )
            engine.register_agent(custom_agent)
    except Exception:
        pass

    # ── [#T159] Agents custom déclarés dans config.json (CRUD /api/agents) ──
    try:
        from core.custom_agents import register_config_custom_agents
        register_config_custom_agents(engine, gateway, registry, config)
    except Exception as e:
        logger.warning(f"[PIPELINE] Agents custom config.json non chargés : {e}")

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

    return engine, registry, mcp_bridge, gateway


def _normaliser_payload_initial(initial_payload: Any) -> TaskPayload:
    """
    Normalise le payload d'entrée de run_full_pipeline en TaskPayload (#T281).

    Contrat de frontière : le moteur (core/engine.py) a le droit d'attendre un
    TaskPayload — c'est ici, à l'entrée du pipeline, que ce contrat est garanti.
    - TaskPayload : passe tel quel (même objet, aucune copie sur le chemin nominal).
    - dict : validé par Pydantic, qui nomme lui-même le champ manquant.
    - autre : refus explicite nommant le paramètre et le type reçu.

    Lève ValueError avec un message actionnable en cas de refus — jamais
    d'AttributeError nu qui remonterait jusqu'à la route HTTP.
    """
    if isinstance(initial_payload, TaskPayload):
        return initial_payload
    if isinstance(initial_payload, dict):
        try:
            return TaskPayload.model_validate(initial_payload)
        except ValidationError as _ve:
            champs = ", ".join(
                f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in _ve.errors()
            )
            raise ValueError(
                f"initial_payload : dict invalide pour TaskPayload — {champs}. "
                "Le champ task_objective (str) est obligatoire."
            ) from _ve
    raise ValueError(
        f"initial_payload : type reçu « {type(initial_payload).__name__} » au lieu d'un "
        "TaskPayload ou d'un dict. Le champ task_objective (str) est obligatoire."
    )


def detecter_echec_sans_resultat(
    history_dicts: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> str | None:
    """
    [#T328] La session a-t-elle échoué sans rien produire ? Retourne le message
    d'erreur à remonter, ou None si la session peut être annoncée comme réussie.

    Critère : au moins une tâche en erreur ET aucun résultat exploitable. Une
    session dont une partie a réussi reste un succès (partiel) — on ne
    transforme pas un demi-résultat en échec, on refuse seulement d'appeler
    « terminée » une session qui n'a rien rendu.

    Motif : les trois sorties du pipeline mentaient de concert — statut de base
    « success » en dur, `status: "completed"` en dur, et « ✅ Tâche terminée. »
    en repli de réponse vide. Mesuré le 12/08 sur `chat_683ea8b2cd` : l'outil
    échoue, le journal écrit `phase: failed`, l'utilisateur lit « ✅ Tâche
    terminée. » après 60,9 s. Double dégât — l'utilisateur croit sa demande
    satisfaite, et la base enregistre un succès qui gonfle les statistiques
    (cf. #T325, 88 « tâches réussies » sans coût ni résultat).
    """
    if results:
        return None
    echecs = [
        h for h in history_dicts
        if h.get("status") == "error" or h.get("error_message")
    ]
    if not echecs:
        return None
    return next(
        (h.get("error_message") for h in reversed(echecs) if h.get("error_message")),
        "aucun détail disponible",
    )


async def run_full_pipeline(
    user_prompt: str,
    session_id: str,
    initial_payload: TaskPayload | dict[str, Any],
    starting_agent: str,
    on_event_callback,
    config: dict,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """
    Exécute le pipeline complet du moteur : Planner → DAG → Executor → Reviewer.

    Assemble toutes les briques : LLMGateway, ToolRegistry, Agents, Engine,
    MCPBridge, WorkflowBridge, Plugins. Timeout 120s maximum.

    Args:
        user_prompt      : Requête utilisateur
        session_id       : ID de session unique
        initial_payload  : TaskPayload (recommandé — passe tel quel, même objet)
                           ou dict validé en TaskPayload par Pydantic (#T281)
        starting_agent   : Nom de l'agent de démarrage (ex: "planner")
        on_event_callback: Coroutine async appelée à chaque événement moteur
        config           : Configuration du moteur (config.json)

    Returns:
        dict : Réponse JSON normalisée avec status, response, history, agents_used
    """
    # ── Contrat de frontière (#T281) : le moteur attend un TaskPayload ──
    # Avant : un dict passait la frontière (type annoncé `Any`) et mourait plus
    # loin dans core/engine.py sur `initial_payload.metadata` — AttributeError
    # qui ne nommait ni le paramètre fautif ni ce qui était attendu.
    try:
        initial_payload = _normaliser_payload_initial(initial_payload)
    except ValueError as _ve:
        logger.error(f"[PIPELINE] Payload initial refusé : {_ve}")
        return {"status": "error", "error": f"❌ {_ve}"}

    from core.serializers import global_state_to_dict, state_update_to_dict
    from core.session_history import record_session_end, record_session_start

    # ── Construction des dépendances ──
    # [#T267] Montage extrait dans `monter_moteur()` pour que la REPRISE après
    # approbation humaine obtienne exactement le même environnement d'exécution
    # (mêmes agents, mêmes outils, même bridge MCP). Une reprise montée
    # différemment exécuterait le plan approuvé dans un moteur qui n'est pas
    # celui qui l'a produit — outils manquants en silence, donc échec inexplicable.
    engine, registry, mcp_bridge, _gateway = monter_moteur(session_id, user_prompt, config)

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

    # ── Exécution avec timeout configurable (source_router en mode vocal) ──
    await mcp_bridge.start(registry, user_prompt=user_prompt)
    record_session_start(session_id, user_prompt, starting_agent)

    try:
        final_state = await asyncio.wait_for(
            engine.run(initial_payload, starting_agent),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _cleanup_mcp(mcp_bridge)
        record_session_end(session_id, "error", error_message=f"Timeout {timeout_seconds}s")
        return {
            "status": "error",
            "error": (
                f"⏱️ Timeout : l'exécution a dépassé {int(timeout_seconds)} secondes. "
                "Essayez une requête plus simple."
            ),
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

    # [#T267] Session suspendue en attente d'approbation humaine : ni « success »
    # ni « error ». Avant, l'opérateur voyait « ⏱️ Timeout : l'exécution a dépassé
    # 120 secondes » alors que le moteur attendait sagement son feu vert — le
    # message désignait la mauvaise cause et l'approbation n'arrivait jamais.
    if getattr(engine, "est_suspendue", lambda: False)():
        request_id = getattr(engine, "_suspension_request_id", None)
        record_session_end(
            session_id, "waiting_approval",
            agents_invoked=agents_used,
            task_count=len(final_state.history),
            result_summary=f"En attente d'approbation ({request_id})",
        )
        await _cleanup_mcp(mcp_bridge)
        return {
            "status": "waiting_approval",
            "session_id": session_id,
            "request_id": request_id,
            "response": (
                "⏸️ Le plan contient des tâches à risque et attend votre approbation. "
                "L'exécution reprendra exactement sur ce plan une fois approuvé."
            ),
            "history": history_dicts,
            "agents_used": agents_used,
            "engine_state": global_state_to_dict(final_state),
        }

    # [#T328] Une session qui n'a produit AUCUN résultat alors que des tâches ont
    # échoué n'est pas un succès. Avant ce correctif, les trois sorties mentaient
    # de concert : `record_session_end(..., "success")` en dur, `status:
    # "completed"` en dur, et « ✅ Tâche terminée. » en repli de réponse vide.
    #
    # Mesuré le 12/08 (session `chat_683ea8b2cd`) : « Quelle est la température
    # actuelle dans le salon ? » → l'outil `call_api` échoue sur une connexion
    # fermée par HA, le journal écrit `[ERROR] Erreur depuis ha_agent` et
    # `phase: failed`… et l'utilisateur reçoit « ✅ Tâche terminée. » après 60,9 s.
    #
    # Double dégât : l'utilisateur croit sa demande satisfaite, ET la base
    # enregistre un succès — c'est ainsi que des « tâches réussies » sans coût ni
    # résultat s'accumulent dans les métriques (cf. #T325).
    derniere_erreur = detecter_echec_sans_resultat(history_dicts, results)
    if derniere_erreur is not None:
        echecs = [h for h in history_dicts if h.get("status") == "error" or h.get("error_message")]
        logger.warning(
            "[PIPELINE] [#T328] Session %s : %d tâche(s) en erreur et aucun résultat "
            "— signalée en échec au lieu de « Tâche terminée ».",
            session_id, len(echecs),
        )
        record_session_end(
            session_id, "error",
            agents_invoked=agents_used,
            task_count=len(final_state.history),
            error_message=str(derniere_erreur)[:500],
        )
        await _cleanup_mcp(mcp_bridge)
        return {
            "status": "error",
            "session_id": session_id,
            "error": str(derniere_erreur),
            "response": f"❌ La tâche n'a pas abouti : {derniere_erreur}",
            "history": history_dicts,
            "agents_used": agents_used,
            "engine_state": global_state_to_dict(final_state),
        }

    record_session_end(
        session_id, "success",
        agents_invoked=agents_used,
        task_count=len(final_state.history),
        result_summary=response_text[:500] if response_text else None,
    )

    await _cleanup_mcp(mcp_bridge)

    # Scanner les tokens CLI à la fin pour s'assurer du tracking temps réel
    try:
        from datetime import datetime, timedelta

        from core.cli_token_collector import collect_all_cli_tokens
        since = (datetime.now() - timedelta(hours=2)).isoformat()
        # Exécuter dans un thread en tâche de fond pour ne pas bloquer le retour de l'API
        asyncio.create_task(asyncio.to_thread(collect_all_cli_tokens, since_date=since, persist_to_db=True))
    except Exception as cli_err:
        logger.debug(f"[PIPELINE] Scan CLI final ignoré : {cli_err}")

    # Event Sourcing : log request + response pour audit trail
    try:
        from core.event_store import get_event_store
        _es     = get_event_store()
        # [#T281] La frontière garantit un TaskPayload : metadata est toujours un
        # dict (défaut Pydantic), le hasattr est devenu inutile.
        _source = initial_payload.metadata.get("source", "")
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
    initial_payload: TaskPayload,
    starting_agent: str,
    engine,
    on_event_callback,
    mcp_bridge,
) -> dict[str, Any]:
    """
    Exécute plusieurs sous-intents en parallèle via asyncio.gather.

    Chaque intent est exécuté dans engine.run() avec un payload clOné et un
    objectif réécrit. Les réponses sont fus ionnées en post-processing.

    Args:
        intents:          Liste de sous-requêtes (strings)
        session_id:       ID de session
        initial_payload:  TaskPayload de référence (cloné par intent) — la
                          frontière de run_full_pipeline garantit ce type (#T281)
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
            # [#T281] La frontière garantit un TaskPayload : plus besoin
            # d'adapter selon le type (dataclass ou dict).
            sub_payload.task_objective = intent

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

        except TimeoutError:
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

async def run_engine_background(
    objective: str,
    on_event_callback,
    session_id: str | None = None,
    execution_key: str | None = None,
) -> None:
    """
    Lance le moteur en arrière-plan pour /api/run (fire-and-forget).
    Met à jour execution_state via AppState pendant l'exécution.

    Args:
        objective        : Objectif de la tâche
        on_event_callback: Coroutine async appelée à chaque événement moteur
        session_id       : ID de session optionnel pré-généré (V12 B3-Fix)
        execution_key    : Clé de concurrence #T324 (entrée du registre à
                           libérer en fin d'exécution, même sur exception).
    """
    from core.app_state import get_app_state
    from core.llm_gateway import (
        LLMGateway,
        load_config,  # config_loader n'existe pas — load_config est dans llm_gateway
    )

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

        # [#T277] Une session de fond héritait du défaut de 120 s, calibré pour
        # le chemin interactif où un humain attend. Ici personne n'attend
        # (fire-and-forget) : la borne n'est là que pour éviter une session
        # immortelle. À 120 s, le Planner servi, il restait quelques dizaines de
        # secondes pour le DAG, la revue et la finalisation — d'où des sessions
        # tuées en cours de route.
        from core.fenetres_execution import duree_session_fond_s

        result = await run_full_pipeline(
            user_prompt=objective,
            session_id=session_id,
            initial_payload=initial_payload,
            starting_agent=starting_agent,
            on_event_callback=on_event_callback,
            config=config,
            timeout_seconds=duree_session_fond_s(),
        )

        final_status = result.get("status", "success")
        final_engine_state = result.get("engine_state")
        final_error = None

    except Exception as e:
        logger.error(f"[ENGINE_BG] Erreur : {e}")
        final_status = "error"
        final_engine_state = None
        final_error = str(e)
    finally:
        # [#T324] Libération systématique de l'entrée de session (try/finally) :
        # aucune fuite dans le registre, même sur exception. La vue agrégée reste
        # "running" si une autre session tourne encore.
        if execution_key:
            await state.end_execution(
                execution_key,
                status=final_status,
                engine_state=final_engine_state,
                error_message=final_error,
            )


# ══════════════════════════════════════════════════════════════════
# Service : Streaming discussion vocal (Sprint A1 — /api/execute/stream)
# ══════════════════════════════════════════════════════════════════

import re as _re

_SENTENCE_BOUNDARY_RE = _re.compile(r"^(.+?[.!?…])(?:\s+|$)", _re.DOTALL)
_CLAUSE_BOUNDARY_RE = _re.compile(r"^(.+?[,;:])(?:\s+|$)", _re.DOTALL)
_EARLY_CHUNK_CHARS = 42


def _pop_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Extrait les phrases/chunks du buffer de streaming (TTS rapide)."""
    sentences: list[str] = []
    rest = buffer
    while True:
        match = _SENTENCE_BOUNDARY_RE.match(rest)
        if match:
            sentence = match.group(1).strip()
            if sentence:
                sentences.append(sentence)
            rest = rest[match.end():]
            continue

        match = _CLAUSE_BOUNDARY_RE.match(rest)
        if match and len(match.group(1).strip()) >= 12:
            sentence = match.group(1).strip()
            if sentence:
                sentences.append(sentence)
            rest = rest[match.end():]
            continue

        if len(rest) >= _EARLY_CHUNK_CHARS:
            cut = rest[:_EARLY_CHUNK_CHARS].rfind(" ")
            if cut < 12:
                cut = _EARLY_CHUNK_CHARS
            chunk = rest[:cut].strip()
            if chunk:
                sentences.append(chunk)
            rest = rest[cut:].lstrip()
            continue

        break
    return sentences, rest


async def _build_fast_path_system_prompt(
    system_prompt_suffix: str = "",
    inject_project_context: bool = False,
    user_prompt: str = "",
    conversation_id: str | None = None,
) -> str:
    system_prompt = FAST_PATH_SYSTEM_PROMPT
    if system_prompt_suffix:
        system_prompt = f"{system_prompt}{system_prompt_suffix}"
    if conversation_id:
        try:
            from core.vocal_session import build_vocal_session_context
            history_block = build_vocal_session_context(conversation_id)
            if history_block:
                system_prompt = f"{system_prompt}{history_block}"
        except Exception as hist_err:
            logger.debug(f"[FAST_PATH] Historique vocal ignoré : {hist_err}")
    if inject_project_context and user_prompt:
        try:
            from services.execute_service import get_casual_chat_context
            rag_block = await get_casual_chat_context(user_prompt)
            if rag_block:
                system_prompt = f"{system_prompt}{rag_block}"
        except Exception as rag_err:
            logger.debug(f"[STREAM_DISCUSSION] Contexte projet ignoré : {rag_err}")
    return system_prompt


def _fast_path_provider_candidates(
    gateway,
    tier_override: str | None,
    model_override: str | None,
) -> list:
    candidates: list = []
    if model_override:
        candidates.append(model_override)
    elif tier_override in WORKLOAD_TIERS and tier_override != "automatique":
        try:
            from core.llm_gateway import load_config
            _, tier_provider = gateway.get_provider_for_tier(tier_override, load_config())
            candidates.append((f"tier:{tier_override}", tier_provider))
        except Exception as tier_err:
            logger.warning(f"[STREAM_DISCUSSION] Tier '{tier_override}' non résolu : {tier_err}")
    candidates.extend(FAST_PATH_PROVIDERS)
    return candidates


async def stream_discussion_fast_path_sse(
    user_prompt: str,
    session_id: str,
    gateway,
    fast_path_cache,
    system_prompt_suffix: str = "",
    inject_project_context: bool = True,
    tier_override: str | None = None,
    model_override: str | None = None,
    conversation_id: str | None = None,
    enable_vocal_tools: bool = True,
) -> AsyncGenerator[str, None]:
    """
    Générateur SSE pour le mode Discussion vocal (mode=chat).
    Émet token / sentence / done — jamais Planner/DAG.

    [#T346] Événements supplémentaires (additifs, un client qui les ignore
    fonctionne comme avant) :
    - thinking      : {"type": "thinking", "text": str} — fragment du
      raisonnement du modèle, JAMAIS mélangé aux tokens ni au TTS ;
    - thinking_done : {"type": "thinking_done", "durationMs": int} — émis à la
      fin de la phase de raisonnement (premier token de réponse ou clôture) ;
    - tool_call     : {"type": "tool_call", "id": str, "toolName": str,
      "args": dict} — annonce d'un appel d'outil (statut « running ») ;
    - tool_result   : {"type": "tool_result", "id": str, "toolName": str,
      "status": "success"|"error", "resultData": str|None,
      "errorMessage": str|None, "executionTimeMs": int} — résultat de l'outil.

    `enable_vocal_tools` : les outils (HA/calendrier/web/mémoire) ne sont
    proposés QU'aux providers qui les supportent
    (`provider_supports_openai_tools`) ; pour les autres, aucun aller-retour
    n'est ajouté — le flux reste strictement celui d'avant.
    """
    import json

    from core.vocal_abort import (
        is_vocal_aborted,
        register_vocal_stream,
        unregister_vocal_stream,
    )
    from core.vocal_tools import provider_supports_openai_tools, run_vocal_tool_loop
    from core.vocal_tts_cache import sanitize_discussion_tts

    register_vocal_stream(session_id, conversation_id=conversation_id)

    try:
        system_prompt = await _build_fast_path_system_prompt(
            system_prompt_suffix, inject_project_context, user_prompt, conversation_id
        )

        _override_key = f"{tier_override or ''}|{model_override or ''}|{conversation_id or ''}"
        _cache_key = hashlib.md5(
            f"{system_prompt}||{_override_key}||{user_prompt}".encode()
        ).hexdigest()
        cached = fast_path_cache.get(_cache_key)
        if cached is not None:
            if is_vocal_aborted(session_id, conversation_id=conversation_id):
                yield f"data: {json.dumps({'type': 'aborted'}, ensure_ascii=False)}\n\n"
                return
            logger.info(f"[STREAM_DISCUSSION] Cache HIT (clé {_cache_key[:8]}…)")
            final = sanitize_discussion_tts(str(cached))
            yield f"data: {json.dumps({'type': 'sentence', 'text': final}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'response': final, 'agents_used': ['discussion_chat']}, ensure_ascii=False)}\n\n"
            return

        accumulated = ""
        sentence_buf = ""
        streamed = False
        # [#T346] Suivi de la phase de raisonnement : None = pas de pensée en
        # cours ; sinon l'instant (monotonic) du premier fragment reçu.
        debut_pensee: float | None = None

        # [#T352] Résolution préalable des candidats en paires (pname, provider),
        # comme dans `run_fast_path` : la sonde d'hôte a besoin du provider pour
        # lire son base_url, et de connaître le nombre de candidats restants pour
        # ne JAMAIS vider la cascade (le dernier est tenté même s'il est muet).
        candidats_resolus: list[tuple[str, object]] = []
        for entry in _fast_path_provider_candidates(gateway, tier_override, model_override):
            if isinstance(entry, tuple):
                # Override de tier : déjà une paire (nom, provider) prête à l'emploi.
                candidats_resolus.append(entry)
                continue
            try:
                candidats_resolus.append((entry, gateway.get_provider(entry)))
            except ValueError:
                logger.debug(f"[STREAM_DISCUSSION] Provider {entry} indisponible, skip")

        for index, (pname, fast_provider) in enumerate(candidats_resolus):
            if is_vocal_aborted(session_id, conversation_id=conversation_id):
                yield f"data: {json.dumps({'type': 'aborted'}, ensure_ascii=False)}\n\n"
                return
            # [#T352] Sonde d'accessibilité d'hôte : même garde-fous que sur le
            # chemin non streamé. Jamais sur le dernier candidat (ne pas vider la
            # cascade), jamais sur un hôte cloud, et toute exception de la sonde =
            # « on ne sait pas » = on tente le provider comme avant.
            if index < len(candidats_resolus) - 1 and await _fast_path_hote_muet(
                gateway, pname, fast_provider
            ):
                continue

            # [#T346] Outils vocaux sur le chemin streamé : on RÉUTILISE la
            # boucle existante `run_vocal_tool_loop` (pas de seconde boucle
            # d'outils). Ses événements de cycle de vie passent par une queue
            # au fil de l'eau ; le sentinel None marque la fin de la boucle.
            # Un provider sans support d'outils ne paie AUCUN surcoût : la
            # vérification est purement locale, zéro aller-retour.
            if enable_vocal_tools and provider_supports_openai_tools(fast_provider):
                queue_evenements: asyncio.Queue = asyncio.Queue()

                # Paramètre par défaut : la clôture est liée à la queue de CE
                # tour de cascade (B023 — variable réassignée à chaque tour).
                async def _pousser_evenement_outil(evenement, _queue=queue_evenements):
                    await _queue.put(evenement)

                tache_outils = asyncio.create_task(run_vocal_tool_loop(
                    fast_provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    session_id=session_id,
                    on_event=_pousser_evenement_outil,
                ))
                tache_outils.add_done_callback(
                    lambda _t, _queue=queue_evenements: _queue.put_nowait(None)
                )

                texte_outils = None
                try:
                    while True:
                        # Un abort vocal doit couper la boucle d'outils comme
                        # il coupe le flux de tokens (barge-in).
                        if is_vocal_aborted(session_id, conversation_id=conversation_id):
                            tache_outils.cancel()
                            yield f"data: {json.dumps({'type': 'aborted'}, ensure_ascii=False)}\n\n"
                            return
                        try:
                            evenement = await asyncio.wait_for(
                                queue_evenements.get(), timeout=0.25
                            )
                        except TimeoutError:
                            continue
                        if evenement is None:
                            break
                        yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"
                    texte_outils = await tache_outils
                except Exception as outils_err:
                    logger.warning(
                        f"[STREAM_DISCUSSION] {pname} boucle outils échouée : {outils_err}"
                    )
                    continue

                if texte_outils:
                    logger.info(f"[STREAM_DISCUSSION] Réponse outils → {pname}")
                    # Le résultat des outils est synthétisé par le modèle : il
                    # passe par token + sentence comme le reste du flux, mais
                    # JAMAIS par le cache TTL (états HA changeants — même règle
                    # que le chemin non streamé `run_fast_path`).
                    final = sanitize_discussion_tts(texte_outils)
                    if not final:
                        final = sanitize_discussion_tts("Désolé, peux-tu reformuler ?")
                    phrases, reste = _pop_complete_sentences(final)
                    if reste.strip():
                        phrases.append(reste.strip())
                    for phrase in phrases:
                        yield f"data: {json.dumps({'type': 'token', 'text': phrase}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'sentence', 'text': phrase}, ensure_ascii=False)}\n\n"
                    asyncio.create_task(
                        _persist_fast_path_async(session_id, user_prompt, final)
                    )
                    yield f"data: {json.dumps({'type': 'done', 'response': final, 'agents_used': ['discussion_chat']}, ensure_ascii=False)}\n\n"
                    return
                # Réponse vide de la boucle d'outils : on retombe sur le stream
                # direct du même provider (comme run_fast_path retombe sur
                # generate() quand la boucle rend None).

            stream_source = fast_provider.generate_stream(
                system_prompt, user_prompt, session_id=session_id
            )

            try:
                logger.info(f"[STREAM_DISCUSSION] Streaming → {pname}")
                for chunk in stream_source:
                    if is_vocal_aborted(session_id, conversation_id=conversation_id):
                        yield f"data: {json.dumps({'type': 'aborted'}, ensure_ascii=False)}\n\n"
                        return
                    # [#T346] Canal raisonnement : émis en événement SSE distinct
                    # (contrat ThinkingBlock). Il n'alimente NI `accumulated` ni
                    # `sentence_buf` : le raisonnement ne doit JAMAIS partir au
                    # TTS ni se retrouver dans la réponse rendue ou mise en cache.
                    raisonnement = chunk.get("reasoning") or ""
                    if raisonnement:
                        if debut_pensee is None:
                            debut_pensee = time.monotonic()
                        yield f"data: {json.dumps({'type': 'thinking', 'text': raisonnement}, ensure_ascii=False)}\n\n"
                    token = chunk.get("token") or ""
                    if token:
                        if debut_pensee is not None:
                            # La réponse commence : la phase de pensée se referme.
                            duree_ms = int((time.monotonic() - debut_pensee) * 1000)
                            debut_pensee = None
                            yield f"data: {json.dumps({'type': 'thinking_done', 'durationMs': duree_ms}, ensure_ascii=False)}\n\n"
                        accumulated += token
                        sentence_buf += token
                        streamed = True
                        yield f"data: {json.dumps({'type': 'token', 'text': token}, ensure_ascii=False)}\n\n"
                        new_sentences, sentence_buf = _pop_complete_sentences(sentence_buf)
                        for raw_sentence in new_sentences:
                            clean = sanitize_discussion_tts(raw_sentence)
                            if clean:
                                yield f"data: {json.dumps({'type': 'sentence', 'text': clean}, ensure_ascii=False)}\n\n"
                    if chunk.get("done"):
                        if debut_pensee is not None:
                            # Pensée refermée sans texte de réponse (modèle qui
                            # n'a rien rendu d'autre) : on notifie quand même.
                            duree_ms = int((time.monotonic() - debut_pensee) * 1000)
                            debut_pensee = None
                            yield f"data: {json.dumps({'type': 'thinking_done', 'durationMs': duree_ms}, ensure_ascii=False)}\n\n"
                        break
                if streamed:
                    fast_path_cache[_cache_key] = accumulated
                    asyncio.create_task(_persist_fast_path_async(session_id, user_prompt, accumulated))
                    break
            except Exception as provider_err:
                logger.warning(f"[STREAM_DISCUSSION] {pname} échoué : {provider_err}")
                accumulated = ""
                sentence_buf = ""
                streamed = False
                debut_pensee = None
                continue

        if not streamed:
            err = "Tous les providers discussion stream ont échoué"
            yield f"data: {json.dumps({'type': 'error', 'message': err}, ensure_ascii=False)}\n\n"
            fallback = sanitize_discussion_tts("Désolé, peux-tu reformuler ?")
            yield f"data: {json.dumps({'type': 'done', 'response': fallback, 'agents_used': ['discussion_chat']}, ensure_ascii=False)}\n\n"
            return

        if sentence_buf.strip():
            tail = sanitize_discussion_tts(sentence_buf.strip())
            if tail:
                yield f"data: {json.dumps({'type': 'sentence', 'text': tail}, ensure_ascii=False)}\n\n"

        final = sanitize_discussion_tts(accumulated)
        if not final:
            final = sanitize_discussion_tts("Désolé, peux-tu reformuler ?")

        yield f"data: {json.dumps({'type': 'done', 'response': final, 'agents_used': ['discussion_chat']}, ensure_ascii=False)}\n\n"
    finally:
        unregister_vocal_stream(session_id, conversation_id=conversation_id)


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
