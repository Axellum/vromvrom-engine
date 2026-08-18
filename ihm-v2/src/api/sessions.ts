/** Endpoints historique des sessions (/api/sessions). */
import { apiFetch } from "./client";

export interface Session {
  session_id: string;
  objective: string;
  status: "success" | "error" | "running" | string;
  started_at: number;   // epoch float (seconds)
  ended_at: number | null;
  duration_ms: number | null;
  starting_agent: string | null;
  agents_invoked: string | null;  // JSON array string
  result_summary: string | null;
}

export function fetchSessions(limit = 40): Promise<{ sessions: Session[] }> {
  return apiFetch<{ sessions: Session[] }>(`/api/sessions?limit=${limit}`);
}

// ─── Chat message persistence (T83) ───────────────────────────────────────────

export interface ChatMessagePersisted {
  id: number;
  chat_session_id: string;
  role: "user" | "assistant";
  content: string;
  agents_used: string[] | null;
  created_at: number;
}

export interface ChatSessionPersisted {
  chat_session_id: string;
  title: string | null;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface SaveMessagesPayload {
  messages: { role: string; content: string; agents_used?: string[]; created_at?: number }[];
  title?: string;
}

export function fetchChatMessages(chatSessionId: string, limit = 200): Promise<ChatMessagePersisted[]> {
  return apiFetch<ChatMessagePersisted[]>(`/api/chat/sessions/${chatSessionId}/messages?limit=${limit}`);
}

export function saveChatMessages(chatSessionId: string, payload: SaveMessagesPayload): Promise<{ saved: number }> {
  return apiFetch<{ saved: number }>(`/api/chat/sessions/${chatSessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchChatSessions(limit = 50): Promise<ChatSessionPersisted[]> {
  return apiFetch<ChatSessionPersisted[]>(`/api/chat/sessions?limit=${limit}`);
}

export function deleteChatSession(chatSessionId: string): Promise<{ deleted: string }> {
  return apiFetch<{ deleted: string }>(`/api/chat/sessions/${chatSessionId}`, { method: "DELETE" });
}
