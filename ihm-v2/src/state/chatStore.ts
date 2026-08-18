/** Store Zustand pour la vue Chat avec persistance backend (T83). */
import { create } from "zustand";
import { fetchChatMessages, saveChatMessages } from "../api/sessions";

export type MessageRole = "user" | "assistant";
export type MessageStatus = "streaming" | "done" | "error";

export interface AgentStep {
  agent: string;
  status: string;
}

/**
 * [#T291] Appel d'outil tel que le backend le décrit sur le flux SSE
 * (`core/vocal_tools.py:569,579`, contrat documenté dans
 * `services/pipeline_service.py:1114-1118`). Défini ici, dans la couche état :
 * `ToolCallDisplay` l'importe au lieu d'en tenir une copie.
 */
export interface ToolCallData {
  id: string;
  toolName: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "error";
  resultData?: unknown;
  errorMessage?: string;
  executionTimeMs?: number;
}

/**
 * [#T291] Ce que porte `tool_result` — les arguments n'y sont PAS repris. Type
 * distinct pour que la fusion ne puisse pas écraser les `args` de l'annonce.
 */
export interface ToolCallResult {
  id: string;
  toolName: string;
  status: "success" | "error";
  resultData?: unknown;
  errorMessage?: string;
  executionTimeMs?: number;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  agentsUsed?: string[];
  steps?: AgentStep[];
  error?: string;
  createdAt: number;
  /** [#T291] Raisonnement du modèle (événements `thinking` / `thinking_done`). */
  thinkingText?: string;
  thinkingStreaming?: boolean;
  thinkingDurationMs?: number;
  /** [#T291] Appels d'outils annoncés (`tool_call`) puis résolus (`tool_result`). */
  toolCalls?: ToolCallData[];
}

/** [#T194] Tiers réels du backend (get_provider_for_tier) + "auto" (cascade). */
export type Tier = "leger" | "moyen" | "fort" | "auto";

// UUID v4 persisté en localStorage — identifie la session chat courante.
function getOrCreateChatSessionId(): string {
  const key = "chat_session_id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const id = crypto.randomUUID();
  localStorage.setItem(key, id);
  return id;
}

interface ChatStore {
  messages: ChatMessage[];
  streaming: boolean;
  tier: Tier;
  chatSessionId: string;
  hydrated: boolean;            // true après chargement depuis le backend
  setTier: (t: Tier) => void;
  addUserMessage: (content: string) => string;
  addAssistantMessage: () => string;
  appendStep: (id: string, step: AgentStep) => void;
  appendThinking: (id: string, chunk: string) => void;
  finishThinking: (id: string, durationMs?: number) => void;
  startToolCall: (id: string, call: ToolCallData) => void;
  resolveToolCall: (id: string, result: ToolCallResult) => void;
  setContent: (id: string, content: string) => void;
  finalizeMessage: (id: string, content: string, agentsUsed: string[]) => void;
  failMessage: (id: string, error: string) => void;
  setStreaming: (v: boolean) => void;
  clearHistory: () => void;
  newSession: () => void;
  loadFromBackend: () => Promise<void>;
  persistMessage: (msg: ChatMessage) => void;
}

let _seq = 0;
const uid = () => `msg_${Date.now()}_${++_seq}`;

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  streaming: false,
  tier: "auto",
  chatSessionId: getOrCreateChatSessionId(),
  hydrated: false,

  setTier: (tier) => set({ tier }),

  addUserMessage: (content) => {
    const id = uid();
    const msg: ChatMessage = { id, role: "user", content, status: "done", createdAt: Date.now() };
    set((s) => ({ messages: [...s.messages, msg] }));
    get().persistMessage(msg);
    return id;
  },

  addAssistantMessage: () => {
    const id = uid();
    set((s) => ({
      messages: [
        ...s.messages,
        { id, role: "assistant", content: "", status: "streaming", steps: [], createdAt: Date.now() },
      ],
    }));
    return id;
  },

  appendStep: (id, step) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, steps: [...(m.steps ?? []), step] } : m
      ),
    })),

  /**
   * [#T291] Concatène un fragment de raisonnement. Le texte est tenu à part du
   * `content` : il ne doit jamais se retrouver dans la réponse rendue.
   */
  appendThinking: (id, chunk) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id
          ? { ...m, thinkingText: (m.thinkingText ?? "") + chunk, thinkingStreaming: true }
          : m
      ),
    })),

  /** [#T291] Fin de la phase de raisonnement — fige la durée annoncée. */
  finishThinking: (id, durationMs) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id
          ? { ...m, thinkingStreaming: false, thinkingDurationMs: durationMs ?? m.thinkingDurationMs }
          : m
      ),
    })),

  /** [#T291] Annonce d'un appel d'outil (statut « running »). */
  startToolCall: (id, call) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, toolCalls: [...(m.toolCalls ?? []), call] } : m
      ),
    })),

  /**
   * [#T291] Résultat d'un outil : remplace l'appel de même `id`. Si l'annonce
   * n'a pas été vue (événement perdu), le résultat est ajouté tel quel plutôt
   * que jeté silencieusement.
   */
  resolveToolCall: (id, result) =>
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.id !== id) return m;
        const calls = m.toolCalls ?? [];
        const index = calls.findIndex((c) => c.id === result.id);
        if (index === -1) return { ...m, toolCalls: [...calls, { ...result, args: {} }] };
        // `args` vient de l'annonce et n'est pas dans le résultat : la fusion le
        // conserve au lieu de le vider.
        const fusionne = { ...calls[index], ...result };
        return { ...m, toolCalls: calls.map((c, i) => (i === index ? fusionne : c)) };
      }),
    })),

  setContent: (id, content) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, content } : m)),
    })),

  finalizeMessage: (id, content, agentsUsed) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        // [#T291] `thinkingStreaming: false` : un flux clos ne laisse jamais le
        // bloc de raisonnement en « Pensée en cours… ».
        m.id === id ? { ...m, content, agentsUsed, status: "done", thinkingStreaming: false } : m
      ),
    }));
    // Persister le message finalisé
    const finalized = get().messages.find((m) => m.id === id);
    if (finalized) get().persistMessage({ ...finalized, content, agentsUsed, status: "done" });
  },

  failMessage: (id, error) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, error, status: "error", thinkingStreaming: false } : m
      ),
    })),

  setStreaming: (streaming) => set({ streaming }),

  clearHistory: () => set({ messages: [] }),

  newSession: () => {
    const id = crypto.randomUUID();
    localStorage.setItem("chat_session_id", id);
    set({ messages: [], chatSessionId: id, hydrated: true });
  },

  loadFromBackend: async () => {
    const { chatSessionId } = get();
    try {
      const msgs = await fetchChatMessages(chatSessionId);
      if (msgs.length === 0) {
        set({ hydrated: true });
        return;
      }
      const restored: ChatMessage[] = msgs.map((m) => ({
        id: `persisted_${m.id}`,
        role: m.role as MessageRole,
        content: m.content,
        status: "done" as MessageStatus,
        agentsUsed: m.agents_used ?? undefined,
        createdAt: m.created_at * 1000,
      }));
      set({ messages: restored, hydrated: true });
    } catch {
      // backend indisponible — on continue sans persistance
      set({ hydrated: true });
    }
  },

  persistMessage: (msg) => {
    const { chatSessionId, messages } = get();
    const firstUserMsg = messages.find((m) => m.role === "user");
    const title = firstUserMsg?.content?.slice(0, 60) ?? undefined;
    saveChatMessages(chatSessionId, {
      messages: [{
        role: msg.role,
        content: msg.content,
        agents_used: msg.agentsUsed,
        created_at: msg.createdAt / 1000,
      }],
      title,
    }).catch(() => { /* silencieux si backend down */ });
  },
}));
