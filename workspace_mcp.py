"""
workspace_mcp.py — Serveur MCP Google Workspace pour Antigravity IDE.

Expose les APIs Google Workspace (Calendar, Gmail, Drive, Sheets, Tasks,
YouTube, Contacts) directement dans l'IDE via le protocole MCP.

Réutilise le GCPOAuthClient existant (core/gcp_oauth_client.py) qui gère
automatiquement le renouvellement du token OAuth2 via le refresh_token.

@version 1.0.0
"""

import sys
import os
import asyncio
import functools
import logging

# Ajouter le répertoire moteur_agents au PYTHONPATH pour les imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Désactiver les logs bruyants
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("workspace_mcp")

# Initialisation du serveur FastMCP
mcp = FastMCP("Google Workspace")

# Singleton du client OAuth (lazy init pour gérer les erreurs proprement)
_oauth_client = None

def get_client():
    """Retourne le client OAuth GCP, initialisé au premier appel."""
    global _oauth_client
    if _oauth_client is None:
        from core.gcp_oauth_client import GCPOAuthClient
        _oauth_client = GCPOAuthClient()
        if not _oauth_client.available:
            logger.warning("[Workspace MCP] ⚠️ OAuth non configuré — les outils retourneront des erreurs.")
    return _oauth_client


# ═══════════════════════════════════════════════════════
# [T125] HITL — consentement humain pour les outils Gmail/Drive sensibles
# ═══════════════════════════════════════════════════════
# Protège contre le scénario "Confused Deputy" (agent compromis par prompt
# injection appelant silencieusement ces outils) : une popup Windows bloquante
# doit être validée par Axel avant tout accès. Fail-closed si tkinter est
# indisponible (headless) ou si personne ne répond sous _CONSENT_TIMEOUT_S.

_CONSENT_TIMEOUT_S = 45


def _ask_consent_blocking(tool_name: str, summary: str) -> bool:
    """Popup Yes/No bloquante. À exécuter dans un thread dédié (asyncio.to_thread)."""
    try:
        import tkinter
        from tkinter import messagebox
    except ImportError:
        logger.error(
            f"[Workspace MCP] ⚠️ tkinter indisponible — consentement refusé par défaut "
            f"pour '{tool_name}' (fail-closed, environnement headless ?)."
        )
        return False

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.after(_CONSENT_TIMEOUT_S * 1000, root.quit)
    try:
        approved = messagebox.askyesno(
            "Autorisation requise — Moteur Agents",
            f"L'outil MCP « {tool_name} » demande à accéder à vos données Google :\n\n"
            f"{summary}\n\nAutoriser cet appel ?",
            parent=root,
        )
    except Exception as e:
        logger.error(f"[Workspace MCP] ⚠️ Erreur popup consentement pour '{tool_name}' : {e}")
        approved = False
    finally:
        root.destroy()
    return bool(approved)


def require_human_consent(func):
    """
    [T125] Décorateur HITL : bloque l'exécution derrière une confirmation humaine
    explicite avant tout accès aux données Gmail/Drive. À placer sous @mcp.tool().
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        summary = ", ".join(f"{k}={v!r}" for k, v in kwargs.items()) or "(sans paramètre)"
        approved = await asyncio.to_thread(_ask_consent_blocking, func.__name__, summary)
        if not approved:
            logger.warning(f"[Workspace MCP] 🚫 Consentement refusé/expiré pour '{func.__name__}'.")
            return (
                f"🚫 Accès refusé : l'appel à « {func.__name__} » nécessite une validation "
                f"humaine (popup non confirmé ou expiré après {_CONSENT_TIMEOUT_S}s)."
            )
        logger.info(f"[Workspace MCP] ✅ Consentement accordé pour '{func.__name__}'.")
        return await func(*args, **kwargs)
    return wrapper


def _safe_error(context: str, exc: Exception) -> str:
    """[T128] Logue l'exception complète en local (avec traceback) et renvoie un
    message générique au LLM — évite de fuiter des détails d'implémentation ou
    d'exception de l'API Google (chemins, tokens, structure interne) au modèle.
    """
    logger.error(f"[Workspace MCP] Erreur {context} : {exc}", exc_info=True)
    return f"❌ Erreur lors de l'appel à {context}. Voir les logs serveur pour le détail."


# ═══════════════════════════════════════════════════════
# Outils Calendar
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_calendar_events(
    calendar_id: str = "primary",
    max_results: int = 10
) -> str:
    """Récupère les prochains événements du calendrier Google.
    
    Retourne les événements avec titre, date/heure de début et fin,
    lieu et statut. Scope OAuth requis : calendar.readonly.
    
    Args:
        calendar_id: ID du calendrier (défaut: "primary" = calendrier principal).
        max_results: Nombre max d'événements à retourner (défaut: 10).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré. Exécutez setup_google_oauth.py pour autoriser l'accès Calendar."
    
    try:
        events = client.get_calendar_events(calendar_id, max_results)
        if not events:
            return "📅 Aucun événement à venir trouvé."
        
        lines = [f"📅 **{len(events)} événement(s) à venir** (calendrier: {calendar_id})\n"]
        for i, e in enumerate(events, 1):
            start = e.get("start", "?")
            end = e.get("end", "?")
            location = e.get("location", "")
            loc_str = f" | 📍 {location}" if location else ""
            lines.append(f"{i}. **{e['summary']}** — {start} → {end}{loc_str}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Calendar", e)


@mcp.tool()
async def list_calendars() -> str:
    """Liste tous les calendriers Google accessibles.
    
    Retourne le nom, l'ID et le rôle d'accès de chaque calendrier.
    Scope OAuth requis : calendar.readonly.
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        calendars = client.get_calendars()
        if not calendars:
            return "📅 Aucun calendrier trouvé."
        
        lines = [f"📅 **{len(calendars)} calendrier(s)** :\n"]
        for c in calendars:
            primary = " ⭐" if c.get("primary") else ""
            lines.append(f"- **{c['summary']}**{primary} | ID: `{c['id']}` | Rôle: {c['access_role']}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Calendar (liste)", e)


# ═══════════════════════════════════════════════════════
# Outils Gmail
# ═══════════════════════════════════════════════════════

@mcp.tool()
@require_human_consent
async def search_gmail(
    query: str,
    max_results: int = 10
) -> str:
    """Recherche dans Gmail avec la même syntaxe que la barre de recherche.
    
    Exemples de requêtes :
      - "from:google subject:sécurité"
      - "is:unread after:2026/05/01"
      - "has:attachment filename:pdf"
      - "domotique OR home assistant"
    
    Scope OAuth requis : gmail.readonly.
    
    Args:
        query: Requête de recherche Gmail.
        max_results: Nombre max de résultats (défaut: 10).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        messages = client.search_gmail(query, max_results)
        if not messages:
            return f"📧 Aucun email trouvé pour : {query}"
        
        lines = [f"📧 **{len(messages)} email(s)** trouvé(s) pour : \"{query}\"\n"]
        for i, m in enumerate(messages, 1):
            lines.append(f"{i}. **{m['subject']}**\n   De: {m['from']} | {m['date']}\n   {m['snippet'][:120]}...")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Gmail", e)


@mcp.tool()
@require_human_consent
async def get_recent_emails(
    max_results: int = 10,
    label: str = "INBOX"
) -> str:
    """Récupère les derniers emails de la boîte de réception.
    
    Scope OAuth requis : gmail.readonly.
    
    Args:
        max_results: Nombre max d'emails (défaut: 10).
        label: Label Gmail (défaut: "INBOX"). Options: INBOX, SENT, DRAFT, SPAM, TRASH.
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        messages = client.get_gmail_messages(max_results, label)
        if not messages:
            return f"📧 Aucun email dans {label}."
        
        lines = [f"📧 **{len(messages)} email(s)** dans {label} :\n"]
        for i, m in enumerate(messages, 1):
            lines.append(f"{i}. **{m['subject']}**\n   De: {m['from']} | {m['date']}\n   {m['snippet'][:120]}...")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Gmail", e)


# ═══════════════════════════════════════════════════════
# Outils Drive
# ═══════════════════════════════════════════════════════

@mcp.tool()
@require_human_consent
async def list_drive_files(
    max_results: int = 20
) -> str:
    """Liste les fichiers Google Drive récents.
    
    Retourne le nom, le type, la taille et la date de modification.
    Scope OAuth requis : drive.readonly.
    
    Args:
        max_results: Nombre max de fichiers (défaut: 20).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        files = client.get_drive_files(max_results)
        if not files:
            return "📁 Aucun fichier trouvé dans Drive."
        
        lines = [f"📁 **{len(files)} fichier(s)** Drive récents :\n"]
        for f in files:
            size = f.get("size", "0")
            size_str = f"{int(size) / 1024:.1f} KB" if size != "0" else "—"
            mime = f.get("mime_type", "").split(".")[-1] if "." in f.get("mime_type", "") else f.get("mime_type", "")
            lines.append(f"- **{f['name']}** | {mime} | {size_str} | Modifié: {f['modified'][:10]}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Drive", e)


# ═══════════════════════════════════════════════════════
# Outils Sheets
# ═══════════════════════════════════════════════════════

@mcp.tool()
@require_human_consent
async def read_sheet(
    spreadsheet_id: str,
    range_notation: str = "Sheet1"
) -> str:
    """Lit les données d'un Google Spreadsheet.
    
    Retourne les données sous forme de tableau formaté.
    Scope OAuth requis : spreadsheets.
    
    Args:
        spreadsheet_id: ID du spreadsheet (visible dans l'URL Google Sheets).
        range_notation: Plage en notation A1 (défaut: "Sheet1"). Ex: "Sheet1!A1:D10".
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        data = client.get_sheets_data(spreadsheet_id, range_notation)
        values = data.get("values", [])
        if not values:
            return f"📊 Aucune donnée dans {range_notation}."
        
        # Formatage en tableau Markdown
        lines = [f"📊 **{data.get('rows', 0)} lignes** (plage: {data.get('range', range_notation)})\n"]
        
        # Header
        if len(values) > 0:
            header = " | ".join(str(c) for c in values[0])
            lines.append(f"| {header} |")
            lines.append("|" + "|".join(["---"] * len(values[0])) + "|")
            
            # Lignes de données
            for row in values[1:]:
                # Compléter les lignes courtes
                padded = row + [""] * (len(values[0]) - len(row))
                lines.append("| " + " | ".join(str(c) for c in padded) + " |")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Sheets", e)


# ═══════════════════════════════════════════════════════
# Outils Tasks
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_tasks(
    task_list_id: str = "@default",
    max_results: int = 20
) -> str:
    """Récupère les tâches Google Tasks (non complétées).
    
    Scope OAuth requis : tasks.readonly.
    
    Args:
        task_list_id: ID de la liste de tâches (défaut: "@default" = liste principale).
        max_results: Nombre max de tâches (défaut: 20).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        tasks = client.get_tasks(task_list_id, max_results)
        if not tasks:
            return "✅ Aucune tâche en attente."
        
        lines = [f"📋 **{len(tasks)} tâche(s)** en attente :\n"]
        for i, t in enumerate(tasks, 1):
            due = f" | 📅 Échéance: {t['due'][:10]}" if t.get("due") else ""
            notes = f"\n   💬 {t['notes'][:100]}" if t.get("notes") else ""
            status_icon = "✅" if t.get("status") == "completed" else "⬜"
            lines.append(f"{i}. {status_icon} **{t['title']}**{due}{notes}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Tasks", e)


@mcp.tool()
async def list_task_lists() -> str:
    """Liste toutes les listes de tâches Google Tasks.
    
    Scope OAuth requis : tasks.readonly.
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        lists = client.get_task_lists()
        if not lists:
            return "📋 Aucune liste de tâches trouvée."
        
        lines = [f"📋 **{len(lists)} liste(s)** de tâches :\n"]
        for tl in lists:
            lines.append(f"- **{tl['title']}** | ID: `{tl['id']}` | Modifié: {tl.get('updated', '?')[:10]}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Tasks (listes)", e)


# ═══════════════════════════════════════════════════════
# Outils Contacts
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_contacts(
    max_results: int = 20
) -> str:
    """Liste les contacts Google (People API).
    
    Retourne le nom, l'email et le téléphone de chaque contact.
    Scope OAuth requis : contacts.readonly.
    
    Args:
        max_results: Nombre max de contacts (défaut: 20).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        contacts = client.get_contacts(max_results)
        if not contacts:
            return "👥 Aucun contact trouvé."
        
        lines = [f"👥 **{len(contacts)} contact(s)** :\n"]
        for c in contacts:
            email = f" | 📧 {c['email']}" if c.get("email") else ""
            phone = f" | 📞 {c['phone']}" if c.get("phone") else ""
            lines.append(f"- **{c['name']}**{email}{phone}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("Google Contacts", e)


# ═══════════════════════════════════════════════════════
# Outils YouTube
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def search_youtube(
    query: str,
    max_results: int = 5
) -> str:
    """Recherche des vidéos YouTube.
    
    Scope OAuth requis : youtube.readonly.
    
    Args:
        query: Terme de recherche YouTube.
        max_results: Nombre max de résultats (défaut: 5).
    """
    client = get_client()
    if not client.available:
        return "❌ OAuth non configuré."
    
    try:
        videos = client.search_youtube(query, max_results)
        if not videos:
            return f"🎬 Aucune vidéo trouvée pour : {query}"
        
        lines = [f"🎬 **{len(videos)} vidéo(s)** pour : \"{query}\"\n"]
        for i, v in enumerate(videos, 1):
            lines.append(f"{i}. **{v['title']}**\n   🎥 {v['channel']} | {v['published'][:10]}\n   🔗 {v['url']}")
        
        return "\n".join(lines)
    except Exception as e:
        return _safe_error("YouTube", e)


# ═══════════════════════════════════════════════════════
# Lancement
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
