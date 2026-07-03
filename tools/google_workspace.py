"""
tools/google_workspace.py — Outils Google Workspace pour les agents.

[P5 + P9 + Phase 2] Expose les méthodes du GCPOAuthClient comme des outils
enregistrables dans le ToolRegistry pour utilisation par les agents
dans leurs boucles ReAct.

Services couverts :
  - Google Calendar (P5) : événements, liste des calendriers
  - Google Drive (P9) : fichiers récents, lecture de contenu
  - Gmail (Phase 2) : recherche d'emails
  - Google Sheets (Phase 2) : lecture de spreadsheets
  - Google Tasks (Phase 2) : tâches en cours
  - YouTube (Phase 2) : recherche de vidéos

Utilise le singleton GCPOAuthClient (core/gcp_oauth_client.py) qui gère
automatiquement le renouvellement des access_token OAuth2.
"""

import logging

logger = logging.getLogger("tools.google_workspace")


def get_calendar_events(calendar_id: str = "primary", max_results: str = "10") -> str:
    """Récupère les prochains événements d'un calendrier Google.
    
    Args:
        calendar_id: Identifiant du calendrier (défaut: 'primary' = calendrier principal)
        max_results: Nombre maximum d'événements à retourner (défaut: '10')
    
    Returns:
        Les événements formatés en texte lisible, ou un message d'erreur.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré. Exécutez setup_google_oauth.py"
        
        # Conversion en int (les agents passent parfois des strings)
        try:
            max_r = int(max_results)
        except (ValueError, TypeError):
            max_r = 10
        
        events = client.get_calendar_events(calendar_id, max_results=max_r)
        
        if not events:
            return "Aucun événement à venir trouvé dans le calendrier."
        
        # Formatage lisible pour l'agent
        lines = [f"📅 {len(events)} événement(s) à venir :"]
        for i, ev in enumerate(events, 1):
            start = ev.get("start", "?")
            end = ev.get("end", "?")
            summary = ev.get("summary", "(sans titre)")
            location = ev.get("location", "")
            loc_str = f" — 📍 {location}" if location else ""
            lines.append(f"  {i}. {summary} | {start} → {end}{loc_str}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[Calendar Tool] Erreur : {e}")
        return f"Erreur lors de la récupération des événements : {e}"


def list_calendars() -> str:
    """Liste tous les calendriers Google accessibles.
    
    Returns:
        La liste des calendriers avec leurs IDs et noms.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré. Exécutez setup_google_oauth.py"
        
        calendars = client.get_calendars()
        
        if not calendars:
            return "Aucun calendrier trouvé."
        
        lines = [f"📋 {len(calendars)} calendrier(s) trouvé(s) :"]
        for cal in calendars:
            primary = " ⭐" if cal.get("primary") else ""
            lines.append(
                f"  • {cal.get('summary', '?')}{primary} "
                f"(ID: {cal.get('id', '?')}, rôle: {cal.get('access_role', '?')})"
            )
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[Calendar Tool] Erreur list_calendars : {e}")
        return f"Erreur lors de la récupération des calendriers : {e}"


def list_drive_files(max_results: str = "20") -> str:
    """Liste les fichiers récents de Google Drive.
    
    Args:
        max_results: Nombre maximum de fichiers à retourner (défaut: '20')
    
    Returns:
        La liste des fichiers avec leurs noms, types et dates de modification.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré. Exécutez setup_google_oauth.py"
        
        try:
            max_r = int(max_results)
        except (ValueError, TypeError):
            max_r = 20
        
        files = client.get_drive_files(max_results=max_r)
        
        if not files:
            return "Aucun fichier trouvé dans Google Drive."
        
        lines = [f"📁 {len(files)} fichier(s) Drive récent(s) :"]
        for f in files:
            name = f.get("name", "?")
            mime = f.get("mime_type", "?")
            modified = f.get("modified", "?")
            size = f.get("size", "0")
            # Formatage du type MIME court
            short_mime = mime.split("/")[-1] if "/" in mime else mime
            # Formatage de la taille
            try:
                size_kb = int(size) / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            except (ValueError, TypeError):
                size_str = "?"
            lines.append(f"  • {name} [{short_mime}] ({size_str}) — modifié: {modified[:10]}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[Drive Tool] Erreur list_drive_files : {e}")
        return f"Erreur lors de la récupération des fichiers Drive : {e}"


def read_drive_file(file_id: str) -> str:
    """Lit le contenu textuel d'un fichier Google Drive.
    
    Supporte les Google Docs (export en texte brut) et les fichiers texte standards.
    
    Args:
        file_id: L'identifiant unique du fichier Drive (obtenu via list_drive_files)
    
    Returns:
        Le contenu textuel du fichier, ou un message d'erreur.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré. Exécutez setup_google_oauth.py"
        
        import requests
        headers = client._get_headers()
        
        # D'abord, récupérer les métadonnées du fichier pour connaître le type
        meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=mimeType,name"
        meta_resp = requests.get(meta_url, headers=headers, timeout=10)
        
        if meta_resp.status_code != 200:
            return f"Erreur: Impossible de récupérer les métadonnées du fichier (HTTP {meta_resp.status_code})"
        
        meta = meta_resp.json()
        mime_type = meta.get("mimeType", "")
        file_name = meta.get("name", "?")
        
        # Google Docs/Sheets/Slides → export en texte brut
        if "google-apps" in mime_type:
            export_mime = "text/plain"
            if "spreadsheet" in mime_type:
                export_mime = "text/csv"
            elif "presentation" in mime_type:
                export_mime = "text/plain"
            
            export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType={export_mime}"
            resp = requests.get(export_url, headers=headers, timeout=30)
        else:
            # Fichiers standards → téléchargement direct
            download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            resp = requests.get(download_url, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            return f"Erreur: Impossible de lire le fichier '{file_name}' (HTTP {resp.status_code})"
        
        # Limiter la taille de la sortie pour éviter l'explosion de contexte
        content = resp.text
        if len(content) > 30000:
            content = content[:30000] + f"\n\n[... tronqué à 30000 caractères, fichier total: {len(resp.text)} chars]"
        
        return f"📄 Contenu de '{file_name}' ({mime_type}) :\n\n{content}"
        
    except Exception as e:
        logger.error(f"[Drive Tool] Erreur read_drive_file : {e}")
        return f"Erreur lors de la lecture du fichier Drive : {e}"


# ─── Phase 2 : Nouveaux outils Workspace ─────────────────────────────

def search_gmail(query: str, max_results: str = "5") -> str:
    """Recherche dans les emails Gmail de l'utilisateur.
    
    Utilise la même syntaxe que la barre de recherche Gmail.
    Exemples : "from:google", "is:unread", "subject:alerte", "after:2026/05/01"
    
    Args:
        query: Requête de recherche Gmail
        max_results: Nombre max de résultats (défaut: '5')
    
    Returns:
        Les emails trouvés formatés en texte lisible.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré."
        
        try:
            max_r = int(max_results)
        except (ValueError, TypeError):
            max_r = 5
        
        messages = client.search_gmail(query=query, max_results=max_r)
        
        if not messages:
            return f"Aucun email trouvé pour la recherche: '{query}'"
        
        lines = [f"📧 {len(messages)} email(s) trouvé(s) pour '{query}' :"]
        for i, msg in enumerate(messages, 1):
            lines.append(f"  {i}. [{msg['date'][:16]}] {msg['subject']}")
            lines.append(f"     De: {msg['from']}")
            if msg.get("snippet"):
                lines.append(f"     → {msg['snippet'][:100]}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Gmail Tool] Erreur search_gmail : {e}")
        return f"Erreur lors de la recherche Gmail : {e}"


def read_spreadsheet(spreadsheet_id: str, range_notation: str = "Sheet1") -> str:
    """Lit les données d'un Google Spreadsheet.
    
    Args:
        spreadsheet_id: L'ID du spreadsheet (visible dans l'URL Google Sheets)
        range_notation: Plage de cellules à lire (ex: 'Sheet1!A1:D10', défaut: 'Sheet1')
    
    Returns:
        Les données du spreadsheet formatées en tableau texte.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré."
        
        data = client.get_sheets_data(spreadsheet_id, range_notation)
        values = data.get("values", [])
        
        if not values:
            return f"Aucune donnée trouvée dans {range_notation}"
        
        # Formatage en tableau texte
        lines = [f"📊 {data.get('rows', 0)} ligne(s) depuis {data.get('range', range_notation)} :"]
        for row in values[:50]:  # Limiter à 50 lignes
            lines.append("  | " + " | ".join(str(cell) for cell in row) + " |")
        
        if len(values) > 50:
            lines.append(f"  ... ({len(values) - 50} lignes supplémentaires)")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Sheets Tool] Erreur read_spreadsheet : {e}")
        return f"Erreur lors de la lecture du spreadsheet : {e}"


def get_tasks(task_list_name: str = "@default") -> str:
    """Récupère les tâches en cours depuis Google Tasks.
    
    Args:
        task_list_name: Nom ou ID de la liste de tâches (défaut: liste principale)
    
    Returns:
        Les tâches formatées en liste texte.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré."
        
        # Si c'est un nom, essayer de trouver l'ID correspondant
        list_id = task_list_name
        if not task_list_name.startswith("@") and not task_list_name.startswith("M"):
            task_lists = client.get_task_lists()
            for tl in task_lists:
                if tl["title"].lower() == task_list_name.lower():
                    list_id = tl["id"]
                    break
        
        tasks = client.get_tasks(task_list_id=list_id)
        
        if not tasks:
            return "Aucune tâche en cours trouvée."
        
        lines = [f"✅ {len(tasks)} tâche(s) en cours :"]
        for i, t in enumerate(tasks, 1):
            due = f" (échéance: {t['due'][:10]})" if t.get("due") else ""
            notes = f" — {t['notes'][:60]}" if t.get("notes") else ""
            lines.append(f"  {i}. {t['title']}{due}{notes}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Tasks Tool] Erreur get_tasks : {e}")
        return f"Erreur lors de la récupération des tâches : {e}"


def search_youtube(query: str, max_results: str = "5") -> str:
    """Recherche des vidéos sur YouTube.
    
    Args:
        query: Termes de recherche
        max_results: Nombre max de résultats (défaut: '5')
    
    Returns:
        Les vidéos trouvées avec titre, chaîne et URL.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré."
        
        try:
            max_r = int(max_results)
        except (ValueError, TypeError):
            max_r = 5
        
        videos = client.search_youtube(query=query, max_results=max_r)
        
        if not videos:
            return f"Aucune vidéo trouvée pour: '{query}'"
        
        lines = [f"🎬 {len(videos)} vidéo(s) trouvée(s) pour '{query}' :"]
        for i, v in enumerate(videos, 1):
            lines.append(f"  {i}. {v['title']}")
            lines.append(f"     📺 {v['channel']} — {v['published'][:10]}")
            lines.append(f"     🔗 {v['url']}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[YouTube Tool] Erreur search_youtube : {e}")
        return f"Erreur lors de la recherche YouTube : {e}"


def get_contacts(max_results: str = "10") -> str:
    """Récupère les contacts Google de l'utilisateur.
    
    Args:
        max_results: Nombre max de contacts (défaut: '10')
    
    Returns:
        Les contacts avec nom, email et téléphone.
    """
    try:
        from core.gcp_oauth_client import get_gcp_client
        client = get_gcp_client()
        
        if not client.available:
            return "Erreur: OAuth2 Google non configuré."
        
        try:
            max_r = int(max_results)
        except (ValueError, TypeError):
            max_r = 10
        
        contacts = client.get_contacts(max_results=max_r)
        
        if not contacts:
            return "Aucun contact trouvé."
        
        lines = [f"👥 {len(contacts)} contact(s) :"]
        for c in contacts:
            parts = [c.get("name", "?")]
            if c.get("email"):
                parts.append(f"📧 {c['email']}")
            if c.get("phone"):
                parts.append(f"📱 {c['phone']}")
            lines.append(f"  • {' — '.join(parts)}")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Contacts Tool] Erreur get_contacts : {e}")
        return f"Erreur lors de la récupération des contacts : {e}"
