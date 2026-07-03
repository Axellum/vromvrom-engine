"""
api/routes/google_workspace.py — Routes Google Workspace (Calendar, Drive, Gmail, Sheets, Tasks, YouTube, Contacts).

Extraites de gui_server.py dans le cadre de l'Audit V9 (P1.3).
Ces routes encapsulent les appels à l'OAuth Client GCP pour toutes les APIs Google.

@version 1.0.0 — Extraction depuis gui_server.py
"""

import logging
from fastapi import APIRouter

logger = logging.getLogger("api.google_workspace")

router = APIRouter(prefix="/api", tags=["Workspace"])


# ──────────────────────────────────────────────────────────────────
# Routes Calendar
# ──────────────────────────────────────────────────────────────────

@router.get("/calendar/events")
def api_calendar_events(calendar_id: str = "primary", max_results: int = 10):
    """[P5] Récupère les prochains événements d'un calendrier Google."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        if not client.available:
            return {"error": "OAuth2 non configuré", "events": []}
        events = client.get_calendar_events(calendar_id, max_results)
        return {"calendar_id": calendar_id, "count": len(events), "events": events}
    except Exception as e:
        logger.error(f"[API] Erreur calendar/events : {e}")
        return {"error": str(e), "events": []}


@router.get("/calendar/list")
def api_calendar_list():
    """[P5] Liste tous les calendriers Google accessibles."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        if not client.available:
            return {"error": "OAuth2 non configuré", "calendars": []}
        calendars = client.get_calendars()
        return {"count": len(calendars), "calendars": calendars}
    except Exception as e:
        logger.error(f"[API] Erreur calendar/list : {e}")
        return {"error": str(e), "calendars": []}


# ──────────────────────────────────────────────────────────────────
# Routes Google Drive
# ──────────────────────────────────────────────────────────────────

@router.get("/drive/files")
def api_drive_files(max_results: int = 20):
    """[P9] Liste les fichiers récents de Google Drive."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        if not client.available:
            return {"error": "OAuth2 non configuré", "files": []}
        files = client.get_drive_files(max_results)
        return {"count": len(files), "files": files}
    except Exception as e:
        logger.error(f"[API] Erreur drive/files : {e}")
        return {"error": str(e), "files": []}


@router.get("/drive/read/{file_id}")
def api_drive_read(file_id: str):
    """[P9] Lit le contenu textuel d'un fichier Google Drive."""
    try:
        from tools.google_workspace import read_drive_file
        result = read_drive_file(file_id)
        is_error = result.startswith("Erreur")
        return {"success": not is_error, "content": result}
    except Exception as e:
        logger.error(f"[API] Erreur drive/read : {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Routes Gmail
# ──────────────────────────────────────────────────────────────────

@router.get("/gmail/messages")
def api_gmail_messages(max_results: int = 10):
    """[Phase 2] Derniers emails Gmail."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        messages = client.get_gmail_messages(max_results=max_results)
        return {"success": True, "count": len(messages), "messages": messages}
    except Exception as e:
        logger.error(f"[API] Erreur Gmail : {e}")
        return {"success": False, "error": str(e)}


@router.get("/gmail/search")
def api_gmail_search(q: str, max_results: int = 10):
    """[Phase 2] Recherche dans Gmail (syntaxe Gmail)."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        messages = client.search_gmail(query=q, max_results=max_results)
        return {"success": True, "query": q, "count": len(messages), "messages": messages}
    except Exception as e:
        logger.error(f"[API] Erreur Gmail search : {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Routes Sheets
# ──────────────────────────────────────────────────────────────────

@router.get("/sheets/{spreadsheet_id}")
def api_sheets_read(spreadsheet_id: str, range: str = "Sheet1"):
    """[Phase 2] Lire un Google Spreadsheet."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        data = client.get_sheets_data(spreadsheet_id=spreadsheet_id, range_notation=range)
        return {"success": True, **data}
    except Exception as e:
        logger.error(f"[API] Erreur Sheets : {e}")
        return {"success": False, "error": str(e)}


@router.post("/sheets/{spreadsheet_id}")
def api_sheets_write(spreadsheet_id: str, body: dict):
    """[Phase 2] Écrire dans un Google Spreadsheet."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        result = client.write_sheets_data(
            spreadsheet_id=spreadsheet_id,
            range_notation=body.get("range", "Sheet1!A1"),
            values=body.get("values", [])
        )
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"[API] Erreur Sheets write : {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Routes Tasks, YouTube, Contacts
# ──────────────────────────────────────────────────────────────────

@router.get("/tasks")
def api_tasks():
    """[Phase 2] Listes et tâches Google Tasks."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        task_lists = client.get_task_lists()
        all_tasks = {}
        for tl in task_lists:
            tasks = client.get_tasks(task_list_id=tl["id"])
            all_tasks[tl["title"]] = tasks
        return {"success": True, "lists": task_lists, "tasks": all_tasks}
    except Exception as e:
        logger.error(f"[API] Erreur Tasks : {e}")
        return {"success": False, "error": str(e)}


@router.get("/youtube/search")
def api_youtube_search(q: str, max_results: int = 5):
    """[Phase 2] Recherche YouTube."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        videos = client.search_youtube(query=q, max_results=max_results)
        return {"success": True, "query": q, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"[API] Erreur YouTube : {e}")
        return {"success": False, "error": str(e)}


@router.get("/contacts")
def api_contacts(max_results: int = 20):
    """[Phase 2] Contacts Google."""
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        contacts = client.get_contacts(max_results=max_results)
        return {"success": True, "count": len(contacts), "contacts": contacts}
    except Exception as e:
        logger.error(f"[API] Erreur Contacts : {e}")
        return {"success": False, "error": str(e)}
