"""
api/routes/chat_messages.py — Persistance des conversations Chat IHM (T83).

Endpoints :
  POST /api/chat/sessions/{chat_session_id}/messages  — sauvegarde un ou plusieurs messages
  GET  /api/chat/sessions/{chat_session_id}/messages  — récupère les messages d'une session
  GET  /api/chat/sessions                             — liste les sessions récentes
  DELETE /api/chat/sessions/{chat_session_id}         — supprime une session
"""
import time
import json
import sqlite3
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.runtime_db import get_connection

router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatMessageIn(BaseModel):
    role: str           # 'user' | 'assistant'
    content: str
    agents_used: Optional[List[str]] = None
    created_at: Optional[float] = None


class ChatMessageOut(BaseModel):
    id: int
    chat_session_id: str
    role: str
    content: str
    agents_used: Optional[List[str]]
    created_at: float


class SaveMessagesRequest(BaseModel):
    messages: List[ChatMessageIn]
    title: Optional[str] = None   # titre de la session (premier message tronqué)


class ChatSessionOut(BaseModel):
    chat_session_id: str
    title: Optional[str]
    created_at: float
    updated_at: float
    message_count: int


def _upsert_session(conn: sqlite3.Connection, chat_session_id: str,
                    title: Optional[str], message_count: int) -> None:
    now = time.time()
    # fetchone() peut être un tuple ou sqlite3.Row selon le contexte appelant
    existing = conn.execute(
        "SELECT 1 FROM chat_sessions WHERE chat_session_id = ?",
        (chat_session_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ?, message_count = ? "
            "WHERE chat_session_id = ?",
            (now, message_count, chat_session_id),
        )
        if title:
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE chat_session_id = ? AND title IS NULL",
                (title, chat_session_id),
            )
    else:
        conn.execute(
            "INSERT INTO chat_sessions (chat_session_id, title, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_session_id, title, now, now, message_count),
        )


@router.post("/sessions/{chat_session_id}/messages", status_code=201)
async def save_messages(chat_session_id: str, body: SaveMessagesRequest):
    """Sauvegarde un ou plusieurs messages dans la session chat donnée."""
    if not body.messages:
        raise HTTPException(status_code=400, detail="Aucun message fourni.")
    now = time.time()
    conn = get_connection()
    try:
        for msg in body.messages:
            agents_json = json.dumps(msg.agents_used) if msg.agents_used else None
            ts = msg.created_at or now
            conn.execute(
                "INSERT OR IGNORE INTO chat_messages "
                "(chat_session_id, role, content, agents_used, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_session_id, msg.role, msg.content, agents_json, ts),
            )

        count_row = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE chat_session_id = ?",
            (chat_session_id,),
        ).fetchone()
        _upsert_session(conn, chat_session_id, body.title, count_row[0])
        conn.commit()
    finally:
        conn.close()

    return {"saved": len(body.messages), "chat_session_id": chat_session_id}


@router.get("/sessions/{chat_session_id}/messages", response_model=List[ChatMessageOut])
async def get_messages(chat_session_id: str, limit: int = 200):
    """Récupère les messages d'une session chat (ordre chronologique)."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE chat_session_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (chat_session_id, limit),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        agents = json.loads(r["agents_used"]) if r["agents_used"] else None
        result.append(ChatMessageOut(
            id=r["id"],
            chat_session_id=r["chat_session_id"],
            role=r["role"],
            content=r["content"],
            agents_used=agents,
            created_at=r["created_at"],
        ))
    return result


@router.get("/sessions", response_model=List[ChatSessionOut])
async def list_sessions(limit: int = 50):
    """Liste les sessions chat récentes."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [
        ChatSessionOut(
            chat_session_id=r["chat_session_id"],
            title=r["title"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            message_count=r["message_count"],
        )
        for r in rows
    ]


@router.delete("/sessions/{chat_session_id}", status_code=200)
async def delete_session(chat_session_id: str):
    """Supprime une session chat et tous ses messages."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM chat_messages WHERE chat_session_id = ?", (chat_session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE chat_session_id = ?", (chat_session_id,))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": chat_session_id}
