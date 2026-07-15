"""
core/vocal_abort.py — Annulation des streams vocaux Discussion (Sprint A4 barge-in).

Permet d'interrompre un stream LLM en cours quand l'utilisateur reprend la parole
ou quand HA déclenche un nouvel utterance sur la même conversation_id.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# session_id ou conversation_id → asyncio.Event (set = abort demandé)
_abort_events: dict[str, asyncio.Event] = {}
_session_meta: dict[str, dict[str, Any]] = {}


def _ensure_event(key: str) -> asyncio.Event:
    with _lock:
        ev = _abort_events.get(key)
        if ev is None:
            ev = asyncio.Event()
            _abort_events[key] = ev
        return ev


def register_vocal_stream(
    session_id: str,
    *,
    conversation_id: str | None = None,
    device_id: str | None = None,
) -> None:
    """Enregistre un stream actif et réinitialise son flag d'abort."""
    keys = [session_id]
    if conversation_id:
        keys.append(conversation_id)
    with _lock:
        for key in keys:
            ev = _abort_events.get(key)
            if ev is None:
                ev = asyncio.Event()
                _abort_events[key] = ev
            ev.clear()
            _session_meta[key] = {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "device_id": device_id,
            }


def unregister_vocal_stream(
    session_id: str,
    *,
    conversation_id: str | None = None,
) -> None:
    """Nettoie les entrées d'abort après fin normale du stream."""
    keys = [session_id]
    if conversation_id:
        keys.append(conversation_id)
    with _lock:
        for key in keys:
            _abort_events.pop(key, None)
            _session_meta.pop(key, None)


def is_vocal_aborted(
    session_id: str,
    *,
    conversation_id: str | None = None,
) -> bool:
    """True si un abort a été demandé pour ce stream."""
    with _lock:
        for key in (session_id, conversation_id):
            if not key:
                continue
            ev = _abort_events.get(key)
            if ev and ev.is_set():
                return True
    return False


def abort_vocal_streams(
    *,
    session_id: str | None = None,
    conversation_id: str | None = None,
    device_id: str | None = None,
) -> int:
    """
    Signale l'abort des streams correspondants.
    Retourne le nombre de clés affectées.
    """
    targets: set[str] = set()
    with _lock:
        if session_id:
            targets.add(session_id)
        if conversation_id:
            targets.add(conversation_id)
        if device_id:
            for key, meta in _session_meta.items():
                if meta.get("device_id") == device_id:
                    targets.add(key)
        if not targets and not session_id and not conversation_id and not device_id:
            targets = set(_abort_events.keys())

        count = 0
        for key in targets:
            ev = _abort_events.get(key)
            if ev and not ev.is_set():
                ev.set()
                count += 1
                logger.info("[VOCAL_ABORT] Abort signalé pour clé=%s", key)
        return count


def list_active_vocal_streams() -> list[dict[str, Any]]:
    """Liste les streams vocaux actifs (diagnostic)."""
    with _lock:
        return [
            {"key": key, **meta, "aborted": _abort_events[key].is_set()}
            for key, meta in _session_meta.items()
        ]
