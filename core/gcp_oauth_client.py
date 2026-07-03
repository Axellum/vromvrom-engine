"""
gcp_oauth_client.py — Client OAuth2 GCP pour les APIs d'infrastructure.

Utilise le refresh_token permanent (obtenu via setup_google_oauth.py)
pour accéder aux APIs Cloud Billing, Service Usage et Resource Manager
SANS intervention utilisateur.

Routes intégrées dans gui_server.py via get_gcp_billing_info().
"""

import os
import json
import time
import logging
import requests
from typing import Optional, Dict, Any
from core.validation import is_valid_gsheet_id  # [P0-1.6] validation spreadsheet_id

logger = logging.getLogger("core.gcp_oauth")

# Chemin du fichier de token sauvegardé par setup_google_oauth.py
TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "google_token.json")


class GCPOAuthClient:
    """Client OAuth2 pour les APIs d'infrastructure Google Cloud.
    
    Gère automatiquement le renouvellement de l'access_token via le
    refresh_token permanent. Aucune interaction utilisateur nécessaire.
    """
    
    def __init__(self, token_file: str = TOKEN_FILE):
        self._token_file = token_file
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0  # timestamp d'expiration
        self._client_id: Optional[str] = None
        self._client_secret: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._available = False
        
        self._load_credentials()
    
    def _load_credentials(self):
        """Charge les credentials depuis google_token.json."""
        if not os.path.exists(self._token_file):
            logger.warning(f"[GCP OAuth] Fichier token introuvable : {self._token_file}")
            return
        
        try:
            with open(self._token_file, "r") as f:
                data = json.load(f)
            
            self._refresh_token = data.get("refresh_token")
            self._client_id = data.get("client_id")
            self._client_secret = data.get("client_secret")
            self._access_token = data.get("token")
            
            # Estimer l'expiration (les access tokens durent ~1h)
            expiry = data.get("expiry")
            if expiry:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    self._token_expiry = dt.timestamp()
                except Exception:
                    self._token_expiry = 0
            
            if self._refresh_token and self._client_id:
                self._available = True
                logger.info("[GCP OAuth] ✅ Credentials chargées avec succès")
            else:
                logger.warning("[GCP OAuth] âŒ Credentials incomplètes (refresh_token ou client_id manquant)")
        except Exception as e:
            logger.error(f"[GCP OAuth] Erreur de chargement : {e}")
    
    @property
    def available(self) -> bool:
        """Indique si le client OAuth est configuré et prêt."""
        return self._available
    
    def _refresh_access_token(self) -> bool:
        """Renouvelle l'access_token via le refresh_token (appel Google)."""
        if not self._refresh_token:
            return False
        
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data["access_token"]
                # Les access tokens expirent en ~3600s, on prend 3500s de marge
                self._token_expiry = time.time() + data.get("expires_in", 3600) - 100
                logger.info("[GCP OAuth] 🔄 Access token renouvelé avec succès")
                return True
            else:
                logger.error(f"[GCP OAuth] Échec du refresh : {resp.status_code} — {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"[GCP OAuth] Erreur réseau lors du refresh : {e}")
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """Retourne les headers HTTP avec un access_token valide."""
        # Renouveler si expiré ou proche de l'expiration
        if time.time() >= self._token_expiry:
            self._refresh_access_token()
        
        return {"Authorization": f"Bearer {self._access_token}"}
    
    def _get(self, url: str) -> Optional[Dict]:
        """Requête GET authentifiée vers une API GCP."""
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"[GCP OAuth] GET {url} → {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"[GCP OAuth] Erreur GET {url} : {e}")
            return None
    
    # ─── APIs de haut niveau ────────────────────────────────────
    
    def get_billing_accounts(self) -> list:
        """Récupère la liste des comptes de facturation accessibles."""
        data = self._get("https://cloudbilling.googleapis.com/v1/billingAccounts")
        return data.get("billingAccounts", []) if data else []
    
    def get_billing_projects(self, billing_account_name: str) -> list:
        """Récupère les projets liés à un compte de facturation."""
        data = self._get(f"https://cloudbilling.googleapis.com/v1/{billing_account_name}/projects")
        return data.get("projectBillingInfo", []) if data else []
    
    def get_enabled_services(self, project_id: str) -> list:
        """Liste les APIs activées sur un projet GCP."""
        data = self._get(
            f"https://serviceusage.googleapis.com/v1/projects/{project_id}/services?filter=state:ENABLED"
        )
        if data:
            return [s["config"]["name"] for s in data.get("services", [])]
        return []
    
    def get_projects(self) -> list:
        """Liste tous les projets GCP accessibles."""
        data = self._get("https://cloudresourcemanager.googleapis.com/v1/projects")
        return data.get("projects", []) if data else []
    
    # ─── APIs Workspace ───────────────────────────────────────â”€â”€
    
    def get_calendars(self) -> list:
        """Liste les calendriers Google accessibles."""
        data = self._get("https://www.googleapis.com/calendar/v3/users/me/calendarList")
        if not data:
            return []
        return [
            {
                "id": c.get("id", ""),
                "summary": c.get("summary", ""),
                "access_role": c.get("accessRole", ""),
                "primary": c.get("primary", False),
                "background_color": c.get("backgroundColor", ""),
            }
            for c in data.get("items", [])
        ]
    
    def get_calendar_events(self, calendar_id: str = "primary", max_results: int = 10) -> list:
        """Récupère les prochains événements d'un calendrier.
        
        Args:
            calendar_id: ID du calendrier (défaut: "primary" = calendrier principal)
            max_results: Nombre max d'événements à retourner
        """
        from datetime import datetime, timezone
        # L'API Calendar v3 attend le format RFC 3339 avec 'Z' (pas '+00:00')
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # URL-encode l'URL pour éviter les problèmes avec les caractères spéciaux
        from urllib.parse import quote
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
            f"?maxResults={max_results}&timeMin={now}&singleEvents=true&orderBy=startTime"
        )
        data = self._get(url)
        if not data:
            return []
        return [
            {
                "summary": e.get("summary", "(sans titre)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                "location": e.get("location", ""),
                "status": e.get("status", ""),
            }
            for e in data.get("items", [])
        ]
    
    def get_drive_files(self, max_results: int = 20) -> list:
        """Liste les fichiers Google Drive récents."""
        data = self._get(
            f"https://www.googleapis.com/drive/v3/files"
            f"?pageSize={max_results}&orderBy=modifiedTime desc"
            f"&fields=files(id,name,mimeType,modifiedTime,size)"
        )
        if not data:
            return []
        return [
            {
                "id": f.get("id", ""),
                "name": f.get("name", ""),
                "mime_type": f.get("mimeType", ""),
                "modified": f.get("modifiedTime", ""),
                "size": f.get("size", "0"),
            }
            for f in data.get("files", [])
        ]
    
    # ─── APIs Gmail ──────────────────────────────────────────────────
    
    def get_gmail_messages(self, max_results: int = 10, label: str = "INBOX") -> list:
        """Récupère les derniers emails (sujet, expéditeur, date).
        
        Requiert le scope gmail.readonly.
        """
        data = self._get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages"
            f"?maxResults={max_results}&labelIds={label}"
        )
        if not data:
            return []
        
        messages = []
        for msg_ref in data.get("messages", [])[:max_results]:
            msg_data = self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}"
                f"?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
            )
            if msg_data:
                headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                messages.append({
                    "id": msg_ref["id"],
                    "subject": headers.get("Subject", "(sans sujet)"),
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": msg_data.get("snippet", ""),
                })
        return messages
    
    def search_gmail(self, query: str, max_results: int = 10) -> list:
        """Recherche dans Gmail (même syntaxe que la barre de recherche Gmail).
        
        Exemples de requêtes :
          - "from:google subject:sécurité"
          - "is:unread after:2026/05/01"
          - "has:attachment filename:pdf"
        """
        from urllib.parse import quote
        data = self._get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages"
            f"?q={quote(query)}&maxResults={max_results}"
        )
        if not data:
            return []
        
        messages = []
        for msg_ref in data.get("messages", [])[:max_results]:
            msg_data = self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}"
                f"?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
            )
            if msg_data:
                headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                messages.append({
                    "id": msg_ref["id"],
                    "subject": headers.get("Subject", "(sans sujet)"),
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": msg_data.get("snippet", ""),
                })
        return messages
    
    # ─── APIs Google Sheets ──────────────────────────────────────────
    
    def get_sheets_data(self, spreadsheet_id: str, range_notation: str = "Sheet1") -> dict:
        """Lit les données d'un Google Spreadsheet.
        
        Args:
            spreadsheet_id: ID du spreadsheet (dans l'URL Google Sheets)
            range_notation: Notation A1 (ex: "Sheet1!A1:D10" ou juste "Sheet1")
        """
        if not is_valid_gsheet_id(spreadsheet_id):
            raise ValueError(f"spreadsheet_id invalide : {spreadsheet_id!r}")
        from urllib.parse import quote
        data = self._get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            f"/values/{quote(range_notation)}"
        )
        if not data:
            return {"values": []}
        return {
            "range": data.get("range", ""),
            "values": data.get("values", []),
            "rows": len(data.get("values", [])),
        }
    
    def write_sheets_data(self, spreadsheet_id: str, range_notation: str,
                          values: list) -> dict:
        """Écrit des données dans un Google Spreadsheet.
        
        Args:
            spreadsheet_id: ID du spreadsheet
            range_notation: Notation A1 (ex: "Sheet1!A1")
            values: Liste de listes (lignes × colonnes)
        """
        if not is_valid_gsheet_id(spreadsheet_id):
            raise ValueError(f"spreadsheet_id invalide : {spreadsheet_id!r}")
        from urllib.parse import quote
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            f"/values/{quote(range_notation)}?valueInputOption=USER_ENTERED"
        )
        try:
            resp = requests.put(
                url,
                headers=self._get_headers(),
                json={"values": values},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "updated_range": data.get("updatedRange", ""),
                    "updated_rows": data.get("updatedRows", 0),
                    "updated_cells": data.get("updatedCells", 0),
                }
            else:
                logger.warning(f"[GCP OAuth] Sheets write → {resp.status_code}: {resp.text[:200]}")
                return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    # ─── APIs Google Tasks ───────────────────────────────────────────
    
    def get_task_lists(self) -> list:
        """Liste les listes de tâches Google Tasks."""
        data = self._get("https://tasks.googleapis.com/tasks/v1/users/@me/lists")
        if not data:
            return []
        return [
            {"id": tl.get("id", ""), "title": tl.get("title", ""), "updated": tl.get("updated", "")}
            for tl in data.get("items", [])
        ]
    
    def get_tasks(self, task_list_id: str = "@default", max_results: int = 20) -> list:
        """Récupère les tâches d'une liste."""
        data = self._get(
            f"https://tasks.googleapis.com/tasks/v1/lists/{task_list_id}/tasks"
            f"?maxResults={max_results}&showCompleted=false"
        )
        if not data:
            return []
        return [
            {
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "status": t.get("status", ""),
                "due": t.get("due", ""),
                "notes": t.get("notes", ""),
                "updated": t.get("updated", ""),
            }
            for t in data.get("items", [])
        ]
    
    # ─── APIs YouTube ────────────────────────────────────────────────
    
    def search_youtube(self, query: str, max_results: int = 5) -> list:
        """Recherche des vidéos YouTube."""
        from urllib.parse import quote
        data = self._get(
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&type=video&q={quote(query)}&maxResults={max_results}"
        )
        if not data:
            return []
        return [
            {
                "video_id": item.get("id", {}).get("videoId", ""),
                "title": item.get("snippet", {}).get("title", ""),
                "channel": item.get("snippet", {}).get("channelTitle", ""),
                "published": item.get("snippet", {}).get("publishedAt", ""),
                "description": item.get("snippet", {}).get("description", "")[:200],
                "url": f"https://www.youtube.com/watch?v={item.get('id', {}).get('videoId', '')}",
            }
            for item in data.get("items", [])
        ]
    
    # ─── APIs Contacts ───────────────────────────────────────────────
    
    def get_contacts(self, max_results: int = 20) -> list:
        """Liste les contacts Google (People API)."""
        data = self._get(
            f"https://people.googleapis.com/v1/people/me/connections"
            f"?personFields=names,emailAddresses,phoneNumbers&pageSize={max_results}"
        )
        if not data:
            return []
        contacts = []
        for person in data.get("connections", []):
            names = person.get("names", [{}])
            emails = person.get("emailAddresses", [])
            phones = person.get("phoneNumbers", [])
            contacts.append({
                "name": names[0].get("displayName", "") if names else "",
                "email": emails[0].get("value", "") if emails else "",
                "phone": phones[0].get("value", "") if phones else "",
            })
        return contacts
    
    # ─── Agrégation ──────────────────────────────────────────────────
    
    def get_full_billing_info(self) -> Dict[str, Any]:
        """Récupère un résumé complet de l'état GCP + Workspace.
        
        Retourne un dict prêt pour l'API /api/gcp-billing avec :
        - Comptes de facturation (nom, statut, projets liés)
        - Projets GCP accessibles (id, nom, numéro)
        - APIs IA activées
        - Calendriers et prochains événements
        - Fichiers Drive récents
        """
        result = {
            "available": self._available,
            "billing_accounts": [],
            "projects": [],
            "ai_services": {},
            "calendar": {"calendars": [], "upcoming_events": []},
            "drive": {"files": []},
            "timestamp": time.time(),
        }
        
        if not self._available:
            result["error"] = "OAuth2 non configuré. Exécutez setup_google_oauth.py"
            return result
        
        # 1. Comptes de facturation
        accounts = self.get_billing_accounts()
        for acc in accounts:
            account_info = {
                "name": acc.get("name", ""),
                "display_name": acc.get("displayName", ""),
                "open": acc.get("open", False),
                "projects": [],
            }
            # Projets liés à ce compte
            projects = self.get_billing_projects(acc["name"])
            for p in projects:
                account_info["projects"].append({
                    "project_id": p.get("projectId", ""),
                    "billing_enabled": p.get("billingEnabled", False),
                })
            result["billing_accounts"].append(account_info)
        
        # 2. Projets accessibles
        for p in self.get_projects():
            result["projects"].append({
                "project_id": p.get("projectId", ""),
                "name": p.get("name", ""),
                "number": p.get("projectNumber", ""),
                "state": p.get("lifecycleState", ""),
            })
        
        # 3. APIs IA sur les projets principaux
        keywords_ia = ["ai", "generat", "vertex", "language", "vision", "speech"]
        for proj in result["projects"]:
            pid = proj["project_id"]
            if pid in ("moteur-ia-free", "gen-lang-client-0619520185"):
                all_services = self.get_enabled_services(proj["number"])
                ia_services = [s for s in all_services if any(k in s.lower() for k in keywords_ia)]
                result["ai_services"][pid] = {
                    "total": len(all_services),
                    "ia_services": ia_services,
                }
        
        # 4. Calendriers et événements (scope calendar.readonly)
        try:
            result["calendar"]["calendars"] = self.get_calendars()
            result["calendar"]["upcoming_events"] = self.get_calendar_events("primary", 5)
        except Exception as e:
            logger.warning(f"[GCP OAuth] Calendar non accessible : {e}")
        
        # 5. Fichiers Drive récents (scope drive.readonly)
        try:
            result["drive"]["files"] = self.get_drive_files(10)
        except Exception as e:
            logger.warning(f"[GCP OAuth] Drive non accessible : {e}")
        
        return result


# Singleton global — instancié au premier import
_client: Optional[GCPOAuthClient] = None

def get_gcp_client() -> GCPOAuthClient:
    """Retourne le singleton du client GCP OAuth2."""
    global _client
    if _client is None:
        _client = GCPOAuthClient()
    return _client
