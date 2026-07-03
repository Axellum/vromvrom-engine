"""
api/routes/daemon.py — Routes API des Agents Persistants (Daemon + Dreamer).

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient les routes /api/daemon/* et /api/dreamer/* et /api/persistent-agents/*.

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import json
import logging
from fastapi import APIRouter, HTTPException

from core.safe_io import safe_json_write, file_lock  # [P1-2.3]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agents Persistants"])


# ──────────────────────────────────────────────────────────────────
# Daemon Sentinelle 24/7
# ──────────────────────────────────────────────────────────────────

@router.get("/api/daemon/status")
def api_daemon_status():
    """Retourne l'état complet du Démon Sentinelle 24/7."""
    from core.daemon_loop import get_daemon_status
    return get_daemon_status()


@router.get("/api/daemon/logs")
def api_daemon_logs(limit: int = 50):
    """Retourne les N derniers logs du démon sentinelle."""
    from core.daemon_loop import get_daemon_logs
    return {"logs": get_daemon_logs(limit)}


# ──────────────────────────────────────────────────────────────────
# DreamerAgent (consolidation mémoire nocturne)
# ──────────────────────────────────────────────────────────────────

@router.get("/api/dreamer/status")
def api_dreamer_status():
    """Retourne l'état complet de l'agent autoDream."""
    from agents.dreamer_agent import get_dreamer_status
    return get_dreamer_status()


@router.post("/api/dreamer/trigger")
async def api_dreamer_trigger():
    """Déclenche manuellement un cycle de consolidation mémoire."""
    from agents.dreamer_agent import trigger_dreamer_manual
    report = await trigger_dreamer_manual()
    return {"status": "ok", "report": report}


# ──────────────────────────────────────────────────────────────────
# Configuration des agents persistants
# ──────────────────────────────────────────────────────────────────

@router.get("/api/persistent-agents/config")
def api_persistent_config():
    """Retourne la configuration des agents persistants."""
    import os
    config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
    try:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        return config.get("persistent_agents", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lecture config: {e}")


@router.post("/api/persistent-agents/config")
def api_update_persistent_config(body: dict):
    """Met à jour la configuration des agents persistants dans config.json."""
    import os
    config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
    try:
        # [P1-2.3] Read-modify-write atomique sous FileLock (évite la perte de
        # mise à jour et la corruption en cas d'écriture concurrente sur config.json).
        with file_lock(config_file):
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)
            config["persistent_agents"] = {
                **config.get("persistent_agents", {}),
                **body,
            }
            safe_json_write(config_file, config, lock=False)
        return {
            "message": "Configuration agents persistants sauvegardée.",
            "config": config["persistent_agents"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")
