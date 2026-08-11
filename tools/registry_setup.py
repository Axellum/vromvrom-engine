"""
tools/registry_setup.py — Enregistrement factorisé des outils du moteur (#T213).

Avant ce module, l'enregistrement des outils était dupliqué et DIVERGENT entre
les 3 sites d'assemblage :
- core/factory.py (riche : base + git safety + Workspace + Cloud + Imagen + RAG)
- services/pipeline_service.py (chemin HTTP principal : base + git seulement)
- core/mcp_tools/orchestrator.py (serveur MCP : base seulement, sans git safety)

Chaque site appelle désormais les mêmes fonctions ; les différences restantes
sont des choix explicites (paramètres), plus des oublis silencieux. Le RAG
reste enregistré par l'appelant qui possède l'instance RAGEngine (factory).
"""

import logging

from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_base_tools(registry: ToolRegistry, git_safety: bool = True) -> None:
    """
    Enregistre les 6 outils de base de l'ExecutorAgent + (option) Git Safety.

    Args:
        registry: ToolRegistry cible.
        git_safety: Enregistre les checkpoints Git (désactivable pour les
                    sessions de test légères, cf. create_engine).
    """
    from tools.api import call_api
    from tools.system import read_file, validate_config_yaml, write_file
    from tools.terminal import run_terminal_command
    from tools.testing import run_tests

    registry.register("read_file", read_file, "Lit le contenu d'un fichier texte local.")
    registry.register("write_file", write_file, "Crée ou modifie un fichier texte local.")
    registry.register("run_terminal_command", run_terminal_command, "Exécute une commande système sur la machine hôte.")
    registry.register("call_api", call_api, "Effectue une requête HTTP (GET/POST) vers une API distante.")
    registry.register("validate_config_yaml", validate_config_yaml, "Valide la syntaxe et les dépendances d'un fichier YAML ESPHome.")
    registry.register("run_tests", run_tests, "Lance pytest sur un fichier ou dossier de tests et renvoie le résultat. Args: test_path (défaut 'tests/').")

    if git_safety:
        try:
            from tools.git_safety import git_apply_checkpoint, git_create_checkpoint, git_rollback_checkpoint
            registry.register("git_create_checkpoint", git_create_checkpoint, "Crée un checkpoint de sécurité Git.")
            registry.register("git_rollback_checkpoint", git_rollback_checkpoint, "Annule les modifications de l'agent via Git (rollback).")
            registry.register("git_apply_checkpoint", git_apply_checkpoint, "Valide le checkpoint Git en fusionnant les modifications.")
            logger.info("[REGISTRY_SETUP] Outils Git Safety enregistrés.")
        except ImportError as e:
            logger.warning(f"[REGISTRY_SETUP] Outils Git Safety non disponibles : {e}")


def register_extended_tools(registry: ToolRegistry) -> None:
    """
    Enregistre les familles d'outils étendues : Google Workspace (Calendar,
    Drive, Gmail, Sheets, Tasks, YouTube, Contacts), Cloud APIs (TTS,
    Translation, Vision, STT) et Imagen 4.

    Chaque famille est optionnelle (try/except ImportError) : un environnement
    sans les dépendances Google fonctionne sans ces outils, avec un warning.
    """
    # [P5+P9] Outils Google Workspace (Calendar + Drive)
    try:
        from tools.google_workspace import get_calendar_events, list_calendars, list_drive_files, read_drive_file
        registry.register("get_calendar_events", get_calendar_events, "Récupère les prochains événements d'un calendrier Google. Args: calendar_id (défaut 'primary'), max_results (défaut '10').")
        registry.register("list_calendars", list_calendars, "Liste tous les calendriers Google accessibles avec leurs IDs.")
        registry.register("list_drive_files", list_drive_files, "Liste les fichiers récents de Google Drive avec leurs noms, types et tailles. Args: max_results (défaut '20').")
        registry.register("read_drive_file", read_drive_file, "Lit le contenu textuel d'un fichier Google Drive. Args: file_id (identifiant du fichier obtenu via list_drive_files).")
        logger.info("[REGISTRY_SETUP] Outils Google Calendar + Drive enregistrés.")
    except ImportError as e:
        logger.warning(f"[REGISTRY_SETUP] Outils Workspace Calendar/Drive non disponibles : {e}")

    # [Phase 2] Outils Gmail, Sheets, Tasks, YouTube, Contacts
    try:
        from tools.google_workspace import get_contacts, get_tasks, read_spreadsheet, search_gmail, search_youtube
        registry.register("search_gmail", search_gmail, "Recherche dans les emails Gmail. Syntaxe Gmail : 'from:X', 'subject:Y', 'is:unread'. Args: query, max_results (défaut '5').")
        registry.register("read_spreadsheet", read_spreadsheet, "Lit les données d'un Google Spreadsheet. Args: spreadsheet_id, range_notation (défaut 'Sheet1').")
        registry.register("get_tasks", get_tasks, "Récupère les tâches Google Tasks en cours. Args: task_list_name (défaut '@default').")
        registry.register("search_youtube", search_youtube, "Recherche des vidéos YouTube. Args: query, max_results (défaut '5').")
        registry.register("get_contacts", get_contacts, "Récupère les contacts Google (nom, email, téléphone). Args: max_results (défaut '10').")
        logger.info("[REGISTRY_SETUP] Outils Workspace Phase 2 enregistrés (Gmail, Sheets, Tasks, YouTube, Contacts).")
    except ImportError as e:
        logger.warning(f"[REGISTRY_SETUP] Outils Workspace Phase 2 non disponibles : {e}")

    # [Phase 3] Outils Cloud TTS, Translation, Vision, STT
    try:
        from tools.cloud_stt import transcribe_audio
        from tools.cloud_translate import translate_text
        from tools.cloud_tts import cloud_tts_synthesize
        from tools.cloud_vision import analyze_image
        registry.register("cloud_tts", cloud_tts_synthesize, "Synthèse vocale premium (Neural2/WaveNet). Args: text, voice, language (défaut 'fr-FR').")
        registry.register("translate_text", translate_text, "Traduit du texte via Cloud Translation. Args: text, target_lang (défaut 'fr'), source_lang (auto-détection).")
        registry.register("analyze_image", analyze_image, "Analyse une image (labels, texte OCR, objets). Args: image_path (chemin local).")
        registry.register("transcribe_audio", transcribe_audio, "Transcrit un fichier audio en texte. Args: audio_path, language (défaut 'fr-FR').")
        logger.info("[REGISTRY_SETUP] Outils Cloud APIs Phase 3 enregistrés (TTS, Translate, Vision, STT).")
    except ImportError as e:
        logger.warning(f"[REGISTRY_SETUP] Outils Cloud APIs Phase 3 non disponibles : {e}")

    # [P6] Outil de génération d'images Imagen 4
    try:
        from tools.imagen import generate_image
        registry.register("generate_image", generate_image, "Génère une image via Google Imagen 4. Args: prompt, output_path (optionnel), model_variant ('fast'/'standard'/'ultra'), aspect_ratio ('1:1'/'16:9'/etc).")
        logger.info("[REGISTRY_SETUP] Outil Imagen 4 enregistré.")
    except ImportError as e:
        logger.warning(f"[REGISTRY_SETUP] Outil Imagen non disponible : {e}")


def register_rag_tool(registry: ToolRegistry, rag_engine) -> None:
    """Enregistre l'outil RAG Pull sur une instance RAGEngine fournie par l'appelant."""
    registry.register(
        "query_technical_knowledge",
        lambda query, top_n=3: rag_engine.query(query, top_n=top_n),
        "Interroge la base de connaissances sémantique locale (RAG) sur la domotique, le matériel (Tab5, ESPHome, LVGL) et le tab5-engine. Args: query (requête de recherche), top_n (nombre de résultats souhaités, défaut 3)."
    )
