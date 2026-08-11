"""
core/factory.py — Module de fabrique du tab5-engine.

Ce module centralise l'assemblage des composants du moteur (gateway, registry,
agents, engine) en une seule fonction create_engine() réutilisable.

Créé le 2026-05-24 suite au bug "No module named 'core.factory'" détecté lors
du démarrage du serveur MCP Tab5-Engine.
"""

import asyncio
import logging

from agents.antigravity_agent import AntigravityAgent
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.reviewer import ReviewerAgent
from core.engine import Engine
from core.llm_gateway import LLMGateway, load_config
from core.router import Router
from core.workflow_bridge import WorkflowBridge
from memory.context_manager import ContextManager
from memory.rag import RAGEngine
from tools.registry_setup import register_base_tools, register_extended_tools, register_rag_tool
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def create_engine(session_id: str = "factory_session", register_git_tools: bool = True,
                  enregistrer_plugins: bool = True) -> tuple:
    """
    Crée et assemble tous les composants du tab5-engine.
    
    Args:
        session_id:          Identifiant unique de la session d'exécution.
        register_git_tools:  Si True, enregistre les outils Git Safety dans le registry.
        enregistrer_plugins: [#T231 étape 2] Si True, enregistre les plugins provider
            internes au catalogue (idempotent). Les tests qui montent un moteur
            complet passent False pour ne pas écrire dans le catalogue partagé.
    
    Returns:
        Tuple (engine: Engine, router: Router, config: dict)
    """
    # 1. Chargement de la configuration depuis config.json
    config = load_config()
    logger.info(f"[FACTORY] Chargement config : planner={config.get('planner_model')}, executor={config.get('executor_model')}")

    # 2. Initialisation du gateway LLM (tous les providers, tiers, scoring)
    gateway = LLMGateway()

    # 2bis. [#T231 étape 2] Enregistrement des plugins provider internes au catalogue :
    # les providers dont le transport a pu être construit (identifiants présents
    # sur cet hôte) sont upsertés, les autres écartés en silence. Idempotent et
    # non bloquant — un échec laisse l'ancien chemin (seed + gateway) actif.
    if enregistrer_plugins:
        from core.llm.provider_registration import enregistrer_provider_plugins_once
        enregistrer_provider_plugins_once()

    # 3. Registry des outils disponibles pour l'ExecutorAgent
    # [#T213] Enregistrement factorisé (partagé avec pipeline_service et le
    # serveur MCP) : base + Git Safety + familles étendues Workspace/Cloud/Imagen.
    registry = ToolRegistry()
    register_base_tools(registry, git_safety=register_git_tools)
    register_extended_tools(registry)

    # 4. Gestionnaire de contexte, de mémoire et de RAG local
    context_manager = ContextManager(llm_gateway=gateway)
    rag_engine = RAGEngine()

    # Outil de RAG Pull (Phase 3) — seul site à posséder l'instance RAGEngine
    register_rag_tool(registry, rag_engine)

    # 5. Instanciation priorisée des agents cœur (Réduction latence Boot - T103)
    from agents.ha_agent import HACommandAgent

    # On initialise en priorité absolue les agents les plus souvent sollicités au boot
    executor = ExecutorAgent(
        llm_gateway=gateway,
        tool_registry=registry,
        provider_name=config.get("executor_model", "automatique")
    )
    planner = PlannerAgent(
        llm_gateway=gateway,
        provider_name=config.get("planner_model", "fort")
    )

    # 6. Assemblage initial : Engine + agents prioritaires
    engine = Engine(session_id=session_id, context_manager=context_manager)
    engine.register_agent(executor)
    engine.register_agent(planner)

    # [#T245] Outil de fan-out DAG : enregistré ici car il a besoin du moteur
    # (donc de son DAGRunner), qui n'existe qu'à partir de cette ligne. Même
    # schéma d'injection que register_rag_tool ci-dessus ; le registre étant
    # partagé, les agents déjà instanciés voient l'outil dès leur prochain appel.
    from tools.dag_map_reduce import register_map_reduce_tool
    register_map_reduce_tool(registry, engine)

    # 7. Chargement séquentiel différé pour les autres agents (optimisation V12)
    def _load_secondary_agents():
        try:
            antigravity_agent = AntigravityAgent(
                llm_gateway=gateway,
                tool_registry=registry,
                provider_name=config.get("antigravity_model", "fort")
            )
            ha_agent = HACommandAgent(
                llm_gateway=gateway,
                tool_registry=registry,
                provider_name=config.get("ha_model", "moyen")
            )
            reviewer = ReviewerAgent(
                llm_gateway=gateway,
                provider_name=config.get("reviewer_model", "moyen")
            )
            engine.register_agent(antigravity_agent)
            engine.register_agent(ha_agent)
            engine.register_agent(reviewer)

            # Registration dynamique des agents custom depuis le workflow HMI
            from core.custom_agents import build_custom_agent_prompt, register_config_custom_agents
            bridge = WorkflowBridge()
            custom_agents = bridge.get_custom_agents_config()
            for custom in custom_agents:
                custom_name = custom["name"]
                custom_tier = custom.get("tier", "automatique")
                custom_agent = ExecutorAgent(
                    llm_gateway=gateway,
                    tool_registry=registry,
                    provider_name=custom_tier,
                    # [#T233] Restrictions d'outils (absent/None = tous, [] = aucun)
                    allowed_tools=custom.get("allowed_tools"),
                )
                custom_agent.name = custom_name
                custom_agent.system_prompt = build_custom_agent_prompt(
                    custom_name, custom.get("label"), custom.get("allowed_tools")
                )
                engine.register_agent(custom_agent)
                logger.info(f"[FACTORY] Agent custom '{custom_name}' enregistré en différé (tier: {custom_tier}).")

            # [#T159] Agents custom déclarés dans config.json (CRUD /api/agents)
            register_config_custom_agents(engine, gateway, registry, config)
        except Exception as e:
            logger.error(f"[FACTORY] Erreur lors du chargement différé des agents secondaires : {e}")

    # On planifie le chargement secondaire dès que la boucle d'événements est libre
    loop = asyncio.get_event_loop()
    loop.call_soon(_load_secondary_agents)

    router = Router(default_agent="planner", rag_engine=rag_engine, llm_gateway=gateway, config=config)

    agent_names = list(engine.agents.keys())
    logger.info(f"[FACTORY] Moteur assemblé (session: {session_id}). Agents : {', '.join(agent_names)}")

    return engine, router, config
