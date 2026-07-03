"""
api/services/billing_service.py — Service de synchronisation de facturation GCP/Claude.

Extrait de gui_server.py (A8 Audit V5.5).
"""

import os
import json
import asyncio
import logging

from core import token_tracker

logger = logging.getLogger(__name__)

# Variables pour le suivi de la synchronisation de facturation
billing_sync_state = {
    "status": "idle",
    "message": "",
    "cost": 0.0,
    "currency": "USD",
    "last_sync": None
}


async def handle_scraper_success(stdout_str: str):
    """Parse et enregistre les résultats du scraper de facturation."""
    global billing_sync_state
    try:
        result_json = None
        for line in stdout_str.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    result_json = json.loads(line)
                    break
                except Exception:
                    pass

        if result_json and result_json.get("status") == "success":
            gcp = result_json.get("gcp")
            claude = result_json.get("claude")

            cost_raw = 0.0
            currency = "USD"

            if gcp:
                gcp_cost_usd = gcp.get("cost_usd", 0.0)
                cost_raw = gcp.get("cost_raw", 0.0)
                currency = gcp.get("currency", "USD")
                token_tracker.update_real_billing(gcp_cost_usd=gcp_cost_usd)

            if claude:
                claude_usage = claude.get("message_usage_pct")
                claude_text = claude.get("summary_text")
                token_tracker.update_real_billing(
                    claude_message_usage_pct=claude_usage,
                    claude_summary_text=claude_text
                )

            billing_sync_state["status"] = "success"
            billing_sync_state["message"] = "Synchronisation réussie !"
            billing_sync_state["cost"] = cost_raw
            billing_sync_state["currency"] = currency
            from datetime import datetime
            billing_sync_state["last_sync"] = datetime.now().isoformat()
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = (
                result_json.get("message") if result_json
                else "Format de sortie du scraper invalide."
            )
    except Exception as e:
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = f"Erreur de décodage des résultats : {str(e)}"


async def run_billing_sync_flow():
    """Exécute le processus de synchronisation de facturation en arrière-plan."""
    global billing_sync_state

    node_cmd = ["node", "tools/billing_scraper.js", "--headless=true"]
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    try:
        billing_sync_state["status"] = "running"
        billing_sync_state["message"] = "Vérification de la session en arrière-plan (mode headless)..."

        proc = await asyncio.create_subprocess_exec(
            *node_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )

        stdout, stderr = await proc.communicate()
        exit_code = proc.returncode

        stdout_str = stdout.decode("utf-8", errors="ignore").strip()
        stderr_str = stderr.decode("utf-8", errors="ignore").strip()

        logger.info(f"Headless scraper exit code: {exit_code}")

        # Détecter si login requis
        needs_login = (exit_code == 2) or ("Authentification requise" in stderr_str) or ("signin" in stdout_str)

        if needs_login:
            billing_sync_state["status"] = "needs_login"
            billing_sync_state["message"] = "Connexion requise. Veuillez vous connecter dans Chrome."

            node_cmd_headed = ["node", "tools/billing_scraper.js", "--headless=false"]
            proc_headed = await asyncio.create_subprocess_exec(
                *node_cmd_headed,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
            stdout_headed, stderr_headed = await proc_headed.communicate()
            exit_code_headed = proc_headed.returncode
            stdout_str_headed = stdout_headed.decode("utf-8", errors="ignore").strip()
            stderr_str_headed = stderr_headed.decode("utf-8", errors="ignore").strip()

            if exit_code_headed == 0:
                await handle_scraper_success(stdout_str_headed)
            else:
                billing_sync_state["status"] = "error"
                billing_sync_state["message"] = f"Échec de l'authentification : {stderr_str_headed}"
        elif exit_code == 0:
            await handle_scraper_success(stdout_str)
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = f"Erreur de facturation : {stderr_str}"

    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation de facturation: {e}")
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = str(e)
