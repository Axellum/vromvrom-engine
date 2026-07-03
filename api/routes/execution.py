"""
api/routes/execution.py — Routes API d'exécution et contrôle du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/status, /api/stop, /api/sandbox/*, /ws (WebSocket).

Utilise AppState (core/app_state.py) pour accéder à execution_state, 
sse_clients et broadcast_event de manière thread-safe.

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import logging
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Exécution"])

_stop_requested: bool = False
_ws_clients: list = []


@router.get("/api/status")
def get_status():
    """Récupère l'état actuel de l'exécution du moteur."""
    from core.app_state import get_app_state
    return get_app_state().execution_state


@router.post("/api/stop")
async def stop_execution():
    """Demande l'arrêt de l'exécution en cours."""
    global _stop_requested
    from core.app_state import get_app_state, broadcast_event
    state = get_app_state()

    async with state.execution_lock:
        if state.execution_state.get("status") != "running":
            raise HTTPException(status_code=400, detail="Aucune exécution en cours à arrêter.")
        _stop_requested = True
        state.execution_state["status"] = "error"
        state.execution_state["error_message"] = "Arrêt demandé par l'utilisateur"

    await broadcast_event("orchestration_completed", {
        "status": "error",
        "error_message": "Arrêt demandé par l'utilisateur",
    })
    return {"message": "Arrêt demandé avec succès.", "status": "stopped"}


@router.get("/api/sandbox/pending")
def get_sandbox_pending():
    """Retourne l'état du sandbox : écritures en attente, commandes bloquées, diffs."""
    from core.app_state import get_app_state
    state = get_app_state()
    sandbox = getattr(state.engine, "sandbox", None)
    if sandbox is None:
        return {"dry_run": False, "pending_writes": 0, "blocked_commands": 0, "total_diffs_generated": 0, "files_pending": []}
    return sandbox.get_pending_summary()


@router.post("/api/sandbox/approve")
async def approve_sandbox():
    """Valide et exécute toutes les écritures en attente du sandbox."""
    from core.app_state import get_app_state, broadcast_event
    state = get_app_state()
    sandbox = getattr(state.engine, "sandbox", None)
    if sandbox is None:
        raise HTTPException(status_code=400, detail="Pas de sandbox actif.")
    writes_report = await sandbox.flush_pending_writes()
    cmds_report = await sandbox.flush_pending_commands()
    await broadcast_event("sandbox_flushed", {"writes": writes_report, "commands": cmds_report})
    return {"message": "Sandbox vidé avec succès.", "writes": writes_report, "commands": cmds_report}


@router.post("/api/sandbox/reject")
def reject_sandbox():
    """Rejette et vide toutes les écritures en attente du sandbox."""
    from core.app_state import get_app_state
    state = get_app_state()
    sandbox = getattr(state.engine, "sandbox", None)
    if sandbox is None:
        raise HTTPException(status_code=400, detail="Pas de sandbox actif.")
    count = len(getattr(sandbox, "_pending_writes", [])) + len(getattr(sandbox, "_blocked_commands", []))
    if hasattr(sandbox, "_pending_writes"):
        sandbox._pending_writes.clear()
    if hasattr(sandbox, "_blocked_commands"):
        sandbox._blocked_commands.clear()
    return {"message": f"Sandbox rejeté : {count} opération(s) supprimée(s).", "rejected_count": count}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Canal WebSocket bidirectionnel pour les actions utilisateur en temps réel."""
    # [P0-1.1] Auth WS : le navigateur ne peut pas porter de header Authorization →
    # le token transite en query param (?token=). Fail-closed comme require_auth.
    import hmac
    from core.auth import _get_api_key
    _required_key = _get_api_key()
    _token = websocket.query_params.get("token", "")
    if not _required_key or not _token or not hmac.compare_digest(_token, _required_key):
        await websocket.close(code=1008)  # Policy Violation
        logger.warning("[WS] Connexion refusée : token absent ou invalide.")
        return

    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info(f"[WS] Client connecté. Total : {len(_ws_clients)}")

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")
            if action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action == "stop":
                from core.app_state import get_app_state
                state = get_app_state()
                async with state.execution_lock:
                    if state.execution_state.get("status") == "running":
                        state.execution_state["status"] = "error"
                        state.execution_state["error_message"] = "Arrêt WebSocket"
                await websocket.send_json({"type": "ack", "action": "stop"})
            elif action == "get_status":
                from core.app_state import get_app_state
                await websocket.send_json({"type": "status", "data": get_app_state().execution_state})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        logger.info(f"[WS] Client déconnecté. Total : {len(_ws_clients)}")
