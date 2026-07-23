"""
core/billing_sync.py — Pont vers api/services/billing_service.py.

`api/routes/billing.py` importe historiquement `core.billing_sync` pour les
routes /api/billing/sync, /sync/status et /launch-chrome. Le refactoring A8 a
extrait la logique réelle (scraping headless GCP + Claude via
tools/billing_scraper.js) vers api/services/billing_service.py sans recréer
ce module pont : les 3 routes levaient un ImportError (→ 500) depuis lors.
Ce module réexpose la même API pour rebrancher les routes sans dupliquer
la logique de scraping.
"""

import logging
import os

from api.services.billing_service import billing_sync_state, run_billing_sync_flow

logger = logging.getLogger(__name__)


async def run_billing_sync() -> dict:
    """Lance la synchronisation de facturation (GCP + Claude) et attend le résultat."""
    await run_billing_sync_flow()
    return dict(billing_sync_state)


def get_sync_status() -> dict:
    """Retourne l'état courant (idle/running/needs_login/success/error) de la sync."""
    return dict(billing_sync_state)


async def launch_chrome_billing() -> dict:
    """
    Force l'ouverture d'une fenêtre Chrome visible sur les pages de facturation
    (GCP + Claude), utile pour une ré-authentification manuelle.
    """
    import asyncio as _asyncio

    global billing_sync_state
    node_cmd = ["node", "tools/billing_scraper.js", "--headless=false"]
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    billing_sync_state["status"] = "running"
    billing_sync_state["message"] = "Ouverture de Chrome pour vérification manuelle…"

    proc = await _asyncio.create_subprocess_exec(
        *node_cmd,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        from api.services.billing_service import handle_scraper_success
        await handle_scraper_success(stdout.decode("utf-8", errors="ignore").strip())
    else:
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = stderr.decode("utf-8", errors="ignore").strip()[:300]
        logger.error(f"[BILLING] Échec lancement Chrome : {billing_sync_state['message']}")

    return dict(billing_sync_state)
