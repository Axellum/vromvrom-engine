"""
core/factory.py — Module de fabrique du tab5-engine.

Ce module centralise l'assemblage des composants du moteur (gateway, registry,
agents, engine) en une seule fonction create_engine() réutilisable.

Créé le 2026-05-24 suite au bug "No module named 'core.factory'" détecté lors
du démarrage du serveur MCP Tab5-Engine.
"""

import logging
import asyncio

from core.engine import Engine
from core.router import Router
from core.llm_gateway import LLMGateway, load_config
from core.workflow_bridge import WorkflowBridge
from tools.tool_registry import ToolRegistry
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.antigravity_agent import AntigravityAgent
from agents.reviewer import ReviewerAgent
from tools.system import read_file, write_file, validate_config_yaml
from tools.terminal import run_terminal_command
from tools.api import call_api
from memory.context_manager import ContextManager
from memory.rag import RAGEngine

logger = logging.getLogger(__name__)


def create_engine(session_id: str = "factory_session", register_git_tools: bool = True) -> tuple:
    """
    Crée et assemble tous les composants du tab5-engine.
    
    Args:
        session_id:          Identifiant unique de la session d'exécution.
        register_git_tools:  Si True, enregistre les outils Git Safety dans le registry.
    
    Returns:
        Tuple (engine: Engine, router: Router, config: dict)
    """
    # 1. Chargement de la configuration depuis config.json
    config = load_config()
    logger.info(f"[FACTORY] Chargement config : planner={config.get('planner_model')}, executor={config.get('executor_model')}")
    
    # 2. Initialisation du gateway LLM (tous les providers, tiers, scoring)
    gateway = LLMGateway()
    
    # 3. Registry des outils disponibles pour l'ExecutorAgent
    registry = ToolRegistry()
    registry.register("read_file", read_file, "Lit le contenu d'un fichier texte local.")
    registry.register("write_file", write_file, "Crée ou modifie un fichier texte local.")
    registry.register("run_terminal_command", run_terminal_command, "Exécute une commande système sur la machine hôte.")
    registry.register("call_api", call_api, "Effectue une requête HTTP (GET/POST) vers une API distante.")
    registry.register("validate_config_yaml", validate_config_yaml, "Valide la syntaxe et les dépendances d'un fichier YAML ESPHome.")
    
    # Outils Git Safety optionnels (désactivés pour les sessions MCP légères)
    if register_git_tools:
        try:
            from tools.git_safety import git_create_checkpoint, git_rollback_checkpoint, git_apply_checkpoint
            registry.register("git_create_checkpoint", git_create_checkpoint, "Crée un checkpoint de sécurité Git.")
            registry.register("git_rollback_checkpoint", git_rollback_checkpoint, "Annule les modifications de l'agent via Git (rollback).")
            registry.register("git_apply_checkpoint", git_apply_checkpoint, "Valide le checkpoint Git en fusionnant les modifications.")
            logger.info("[FACTORY] Outils Git Safety enregistrés.")
        except ImportError as e:
            logger.warning(f"[FACTORY] Outils Git Safety non disponibles : {e}")
    
    # [P5+P9] Outils Google Workspace (Calendar + Drive)
    try:
        from tools.google_workspace import get_calendar_events, list_calendars, list_drive_files, read_drive_file
        registry.register("get_calendar_events", get_calendar_events, "Récupère les prochains événements d'un calendrier Google. Args: calendar_id (défaut 'primary'), max_results (défaut '10').")
        registry.register("list_calendars", list_calendars, "Liste tous les calendriers Google accessibles avec leurs IDs.")
        registry.register("list_drive_files", list_drive_files, "Liste les fichiers récents de Google Drive avec leurs noms, types et tailles. Args: max_results (défaut '20').")
        registry.register("read_drive_file", read_drive_file, "Lit le contenu textuel d'un fichier Google Drive. Args: file_id (identifiant du fichier obtenu via list_drive_files).")
        logger.info("[FACTORY] Outils Google Calendar + Drive enregistrés.")
    except ImportError as e:
        logger.warning(f"[FACTORY] Outils Workspace Calendar/Drive non disponibles : {e}")
    
    # [Phase 2] Outils Gmail, Sheets, Tasks, YouTube, Contacts
    try:
        from tools.google_workspace import search_gmail, read_spreadsheet, get_tasks, search_youtube, get_contacts
        registry.register("search_gmail", search_gmail, "Recherche dans les emails Gmail. Syntaxe Gmail : 'from:X', 'subject:Y', 'is:unread'. Args: query, max_results (défaut '5').")
        registry.register("read_spreadsheet", read_spreadsheet, "Lit les données d'un Google Spreadsheet. Args: spreadsheet_id, range_notation (défaut 'Sheet1').")
        registry.register("get_tasks", get_tasks, "Récupère les tâches Google Tasks en cours. Args: task_list_name (défaut '@default').")
        registry.register("search_youtube", search_youtube, "Recherche des vidéos YouTube. Args: query, max_results (défaut '5').")
        registry.register("get_contacts", get_contacts, "Récupère les contacts Google (nom, email, téléphone). Args: max_results (défaut '10').")
        logger.info("[FACTORY] Outils Workspace Phase 2 enregistrés (Gmail, Sheets, Tasks, YouTube, Contacts).")
    except ImportError as e:
        logger.warning(f"[FACTORY] Outils Workspace Phase 2 non disponibles : {e}")
    
    # [Phase 3] Outils Cloud TTS, Translation, Vision, STT
    try:
        from tools.cloud_tts import cloud_tts_synthesize
        from tools.cloud_translate import translate_text
        from tools.cloud_vision import analyze_image
        from tools.cloud_stt import transcribe_audio
        registry.register("cloud_tts", cloud_tts_synthesize, "Synthèse vocale premium (Neural2/WaveNet). Args: text, voice, language (défaut 'fr-FR').")
        registry.register("translate_text", translate_text, "Traduit du texte via Cloud Translation. Args: text, target_lang (défaut 'fr'), source_lang (auto-détection).")
        registry.register("analyze_image", analyze_image, "Analyse une image (labels, texte OCR, objets). Args: image_path (chemin local).")
        registry.register("transcribe_audio", transcribe_audio, "Transcrit un fichier audio en texte. Args: audio_path, language (défaut 'fr-FR').")
        logger.info("[FACTORY] Outils Cloud APIs Phase 3 enregistrés (TTS, Translate, Vision, STT).")
    except ImportError as e:
        logger.warning(f"[FACTORY] Outils Cloud APIs Phase 3 non disponibles : {e}")
    
    # [P6] Outil de génération d'images Imagen 4
    try:
        from tools.imagen import generate_image
        registry.register("generate_image", generate_image, "Génère une image via Google Imagen 4. Args: prompt, output_path (optionnel), model_variant ('fast'/'standard'/'ultra'), aspect_ratio ('1:1'/'16:9'/etc).")
        logger.info("[FACTORY] Outil Imagen 4 enregistré.")
    except ImportError as e:
        logger.warning(f"[FACTORY] Outil Imagen non disponible : {e}")
    
    # 4. Gestionnaire de contexte, de mémoire et de RAG local
    context_manager = ContextManager(llm_gateway=gateway)
    rag_engine = RAGEngine()
    
    # Outil de RAG Pull (Phase 3)
    registry.register(
        "query_technical_knowledge",
        lambda query, top_n=3: rag_engine.query(query, top_n=top_n),
        "Interroge la base de connaissances sémantique locale (RAG) sur la domotique, le matériel (Tab5, ESPHome, LVGL) et le tab5-engine. Args: query (requête de recherche), top_n (nombre de résultats souhaités, défaut 3)."
    )
    
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
            bridge = WorkflowBridge()
            custom_agents = bridge.get_custom_agents_config()
            for custom in custom_agents:
                custom_name = custom["name"]
                custom_tier = custom.get("tier", "automatique")
                custom_agent = ExecutorAgent(
                    llm_gateway=gateway,
                    tool_registry=registry,
                    provider_name=custom_tier
                )
                custom_agent.name = custom_name
                custom_agent.system_prompt = (
                    f"Tu es l'agent custom '{custom_name}' ({custom.get('label', custom_name)}).\n"
                    f"Tu hérites de la boucle ReAct d'ExecutorAgent avec accès à tous les outils.\n"
                    f"Exécute la tâche qui t'est assignée de manière rigoureuse et pédagogue."
                )
                engine.register_agent(custom_agent)
                logger.info(f"[FACTORY] Agent custom '{custom_name}' enregistré en différé (tier: {custom_tier}).")
        except Exception as e:
            logger.error(f"[FACTORY] Erreur lors du chargement différé des agents secondaires : {e}")

    # On planifie le chargement secondaire dès que la boucle d'événements est libre
    loop = asyncio.get_event_loop()
    loop.call_soon(_load_secondary_agents)
    
    router = Router(default_agent="planner", rag_engine=rag_engine, llm_gateway=gateway, config=config)
    
    agent_names = list(engine.agents.keys())
    logger.info(f"[FACTORY] Moteur assemblé (session: {session_id}). Agents : {', '.join(agent_names)}")
    
    return engine, router, config
