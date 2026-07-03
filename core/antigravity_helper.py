"""
core/antigravity_helper.py — Fonctions d'analyse et de récupération passive du statut d'Antigravity IDE.

Extrait de gui_server.py pour éviter les duplications entre l'IHM et les endpoints API.
Auteur : Antigravity IDE
Date : 2026-06-16
"""

import os
import glob
import json
import logging
import shutil
import tempfile
import sqlite3
import base64
import re
from datetime import datetime, timedelta
from typing import Tuple, Dict, Any

logger = logging.getLogger(__name__)


def decode_protobuf_varint(data: bytes, index: int = 0) -> Tuple[int, int]:
    """Décode un entier encodé en varint protobuf à partir d'un index donné."""
    result = 0
    shift = 0
    while True:
        if index >= len(data):
            break
        b = data[index]
        index += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, index


def get_antigravity_ide_usage() -> Dict[str, Any]:
    """Scanne le répertoire brain d'Antigravity IDE pour compter les messages des 5 dernières heures et en déduire le reset glissant."""
    user_home = os.path.expanduser("~")
    brain_dir = os.path.join(user_home, ".gemini", "antigravity-ide", "brain")
    now = datetime.now()
    t_5h = now - timedelta(hours=5)
    
    gemini_requests = []
    others_requests = []
    
    if os.path.exists(brain_dir):
        pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
        for filepath in glob.glob(pattern):
            try:
                # Vérification de la date de modification
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if mtime < t_5h:
                    continue
                    
                with open(filepath, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    
                # Détecter le modèle de la session (Claude vs Gemini)
                is_claude = False
                for line in lines:
                    if "claude" in line.lower() or "opus" in line.lower() or "sonnet" in line.lower():
                        is_claude = True
                        break
                        
                for line in lines:
                    try:
                        step = json.loads(line)
                        if step.get("type") == "USER_INPUT" and step.get("source") == "USER_EXPLICIT":
                            ts_str = step.get("timestamp") or step.get("created_at")
                            if ts_str:
                                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                dt_naive = dt.replace(tzinfo=None)
                                if dt.tzinfo:
                                    dt_naive = dt.astimezone().replace(tzinfo=None)
                                    
                                if dt_naive > t_5h:
                                    if is_claude:
                                        others_requests.append(dt_naive)
                                    else:
                                        gemini_requests.append(dt_naive)
                    except Exception:
                        pass
            except Exception:
                pass
                
    # Tri
    gemini_requests.sort()
    others_requests.sort()
    
    # Calcul des temps de recharge restants
    gemini_reset = 0
    if gemini_requests:
        oldest = gemini_requests[0]
        reset_time = oldest + timedelta(hours=5)
        gemini_reset = max(0, int((reset_time - now).total_seconds()))
        
    others_reset = 0
    if others_requests:
        oldest = others_requests[0]
        reset_time = oldest + timedelta(hours=5)
        others_reset = max(0, int((reset_time - now).total_seconds()))
        
    return {
        "gemini": {
            "used": len(gemini_requests),
            "max": 50,
            "reset_seconds": gemini_reset
        },
        "others": {
            "used": len(others_requests),
            "max": 45,
            "reset_seconds": others_reset
        }
    }


def get_antigravity_status() -> Dict[str, Any]:
    """Récupère passivement le profil utilisateur et les crédits d'IA d'Antigravity IDE depuis sa base SQLite locale."""
    if os.name == "nt":  # Windows
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        db_path = os.path.join(appdata, "Antigravity IDE", "User", "globalStorage", "state.vscdb")
    else:  # Linux/Mac (Steam Deck ou serveur)
        user_home = os.path.expanduser("~")
        db_path = os.path.join(user_home, ".config", "Antigravity IDE", "User", "globalStorage", "state.vscdb")
    
    result = {
        "connected": False,
        "user": "Non connecté",
        "email": "Inconnu",
        "plan": "Aucun plan",
        "credits": 0.0,
        "ide_usage": {
            "gemini": {"used": 0, "max": 50, "reset_seconds": 0},
            "others": {"used": 0, "max": 45, "reset_seconds": 0}
        },
        "error": None
    }
    
    if not os.path.exists(db_path):
        result["error"] = "Base de données introuvable"
        return result
        
    temp_dir = tempfile.gettempdir()
    temp_db_path = os.path.join(temp_dir, f"antigravity_state_temp_{os.getpid()}.vscdb")
    
    try:
        # Copie temporaire sécurisée pour éviter les verrous
        shutil.copy2(db_path, temp_db_path)
        
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        
        # Sélectionner les clés d'état requises
        cursor.execute("SELECT key, value FROM ItemTable WHERE key IN ('antigravityUnifiedStateSync.modelCredits', 'antigravityUnifiedStateSync.userStatus', 'antigravityUnifiedStateSync.oauthToken');")
        rows = cursor.fetchall()
        data = {row[0]: row[1] for row in rows}
        
        conn.close()
        
        # Suppression immédiate de la copie
        try:
            os.remove(temp_db_path)
        except Exception:
            pass
            
        # 1. Extraction du statut OAuth
        if 'antigravityUnifiedStateSync.oauthToken' in data:
            oauth_b64 = data['antigravityUnifiedStateSync.oauthToken']
            try:
                raw_bytes = base64.b64decode(oauth_b64)
                decoded_text = raw_bytes.decode('ascii', errors='ignore')
                json_match = re.search(r"\{[^{}]*\"state\"\s*:\s*\"[^\"]+\"[^{}]*\}", decoded_text)
                if json_match:
                     json_str = json_match.group(0)
                     oauth_data = json.loads(json_str)
                     result["connected"] = oauth_data.get("state") == "signedIn"
                else:
                     result["connected"] = "signedIn" in decoded_text
            except Exception:
                pass

        # 2. Extraction du profil utilisateur
        if 'antigravityUnifiedStateSync.userStatus' in data:
            user_status_b64 = data['antigravityUnifiedStateSync.userStatus']
            try:
                raw_bytes = base64.b64decode(user_status_b64)
                decoded_text = raw_bytes.decode('ascii', errors='ignore')
                match = re.search(r"userStatusSentinelKey[^\w]+([a-zA-Z0-9+/=]{20,})", decoded_text)
                if match:
                    val_b64 = match.group(1)
                    val_bytes = base64.b64decode(val_b64)
                    status_text = val_bytes.decode('utf-8', errors='ignore')
                    
                    # Extraction Email
                    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", status_text)
                    if email_match:
                        result["email"] = email_match.group(0)
                        
                    # Extraction Plan
                    plan_match = re.search(r"(Google AI [a-zA-Z0-9\s]+|[a-z0-9\-]+tier)", status_text)
                    if plan_match:
                        plan_val = plan_match.group(0)
                        result["plan"] = "Google AI Ultra" if plan_val == "g1-ultra-tier" else plan_val
                        
                    # Extraction Nom d'utilisateur
                    clean_text = "".join([c if (c.isalnum() or c in " @.-_") else " " for c in status_text])
                    name_match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", clean_text)
                    if name_match:
                        result["user"] = name_match.group(0).strip()
            except Exception as ue:
                logger.warning(f"Erreur d'extraction userStatus: {ue}")

        # 3. Extraction des crédits restants
        if 'antigravityUnifiedStateSync.modelCredits' in data:
            model_credits_b64 = data['antigravityUnifiedStateSync.modelCredits']
            try:
                raw_bytes = base64.b64decode(model_credits_b64)
                decoded_text = raw_bytes.decode('ascii', errors='ignore')
                match = re.search(r"availableCreditsSentinelKey[^\w]+([a-zA-Z0-9+/=]{4,16})", decoded_text)
                if match:
                    val_b64 = match.group(1)
                    val_bytes = base64.b64decode(val_b64)
                    if len(val_bytes) > 1:
                        varint_bytes = val_bytes[1:]
                        credits_val, _ = decode_protobuf_varint(varint_bytes, 0)
                        result["credits"] = credits_val / 100.0
                    else:
                        credits_val, _ = decode_protobuf_varint(val_bytes, 0)
                        result["credits"] = credits_val / 100.0
            except Exception as ce:
                logger.warning(f"Erreur d'extraction credits: {ce}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Erreur d'extraction Antigravity DB: {e}")
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass
                
    return result
