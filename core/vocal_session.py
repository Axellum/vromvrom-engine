"""
core/vocal_session.py — Historique multi-tour vocal Discussion (Sprint A3).

Persiste les tours user/assistant par conversation_id HA dans moteur_runtime.db.
Utilisé pour injecter 3–5 tours récents dans le prompt discussion (jamais contexte_ia).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_MAX_CONTENT_LEN = 600
_DEFAULT_MAX_TURNS = 5


def record_vocal_turn(
    conversation_id: str,
    role: str,
    content: str,
    *,
    source_mode: str = "chat",
    device_id: str | None = None,
) -> None:
    """Enregistre un tour user ou assistant pour une conversation vocale."""
    if not conversation_id or not content or role not in ("user", "assistant"):
        return
    text = str(content).strip()[:_MAX_CONTENT_LEN]
    if not text:
        return
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) FROM vocal_conversation_turns WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            next_index = int(row[0]) + 1 if row else 0
            conn.execute(
                """
                INSERT INTO vocal_conversation_turns
                (conversation_id, turn_index, role, content, source_mode, device_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, next_index, role, text, source_mode, device_id, time.time()),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("[VOCAL_SESSION] Enregistrement ignoré : %s", exc)


def get_recent_vocal_turns(
    conversation_id: str,
    max_turns: int = _DEFAULT_MAX_TURNS,
) -> list[dict[str, Any]]:
    """Retourne les N derniers tours (ordre chronologique)."""
    if not conversation_id or max_turns <= 0:
        return []
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT role, content, turn_index, created_at
                FROM vocal_conversation_turns
                WHERE conversation_id = ?
                ORDER BY turn_index DESC
                LIMIT ?
                """,
                (conversation_id, max_turns),
            ).fetchall()
        turns = [
            {"role": r[0], "content": r[1], "turn_index": r[2], "created_at": r[3]}
            for r in reversed(rows)
        ]
        return turns
    except Exception as exc:
        logger.debug("[VOCAL_SESSION] Lecture ignorée : %s", exc)
        return []


def build_vocal_session_context(
    conversation_id: str | None,
    max_turns: int = _DEFAULT_MAX_TURNS,
) -> str:
    """
    Formate l'historique récent pour le system prompt discussion.
    Retourne une chaîne vide si pas d'historique.
    """
    if not conversation_id:
        return ""
    turns = get_recent_vocal_turns(conversation_id, max_turns=max_turns)
    if not turns:
        return ""
    lines = ["\n\n[HISTORIQUE VOCAL RÉCENT — ne pas répéter mot pour mot]"]
    for turn in turns:
        label = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{label}: {turn['content']}")
    lines.append("Réponds en tenant compte de cet historique si pertinent.")
    return "\n".join(lines)
