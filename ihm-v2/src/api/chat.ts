/** Appel SSE vers /api/execute/stream. */
import { getApiKey, notifyAuthError } from "./client";
import type { AgentStep, ToolCallData, ToolCallResult } from "../state/chatStore";

export interface StreamCallbacks {
  onStep: (step: AgentStep) => void;
  onDone: (response: string, agentsUsed: string[]) => void;
  onError: (message: string) => void;
  /** [#T291] Fragment de raisonnement (`thinking`) — jamais mêlé au contenu. */
  onThinking?: (text: string) => void;
  /** [#T291] Fin du raisonnement (`thinking_done`), durée annoncée par le backend. */
  onThinkingDone?: (durationMs?: number) => void;
  /** [#T291] Annonce d'un appel d'outil (`tool_call`), statut « running ». */
  onToolCall?: (call: ToolCallData) => void;
  /** [#T291] Résultat d'un appel d'outil (`tool_result`). */
  onToolResult?: (result: ToolCallResult) => void;
}

export interface WorkloadOptions {
  /** [#T194] Force du workload : leger/moyen/fort/automatique (backend get_provider_for_tier). */
  tier?: "leger" | "moyen" | "fort" | "automatique";
  /** Override de modèle explicite (id littéral du catalogue), prioritaire sur tier. */
  model?: string;
}

export async function executeStream(
  userPrompt: string,
  source: Record<string, unknown>,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  workload?: WorkloadOptions,
): Promise<void> {
  const key = getApiKey();
  const res = await fetch("/api/execute/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
    },
    body: JSON.stringify({
      user_prompt: userPrompt,
      source,
      ...(workload?.tier ? { tier: workload.tier } : {}),
      ...(workload?.model ? { model: workload.model } : {}),
    }),
    signal,
  });

  if (!res.ok) {
    notifyAuthError(res.status);
    throw new Error(`HTTP ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("Pas de body dans la réponse SSE");

  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const lines = buf.split("\n");
    buf = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        const evt = JSON.parse(raw) as {
          type: string;
          data?: Record<string, unknown>;
          response?: string;
          agents_used?: string[];
          message?: string;
          text?: string;
          durationMs?: number;
          id?: string;
          toolName?: string;
          args?: Record<string, unknown>;
          status?: string;
          resultData?: unknown;
          errorMessage?: string;
          executionTimeMs?: number;
        };

        switch (evt.type) {
          // ── Étapes du pipeline complet (source.mode = "default") ──
          // Le backend émet `agent_started` / `agent_completed` / `phase_changed`
          // (`core/engine.py:370,394,1127`), jamais `orchestration_step` : ce nom
          // n'existe nulle part côté Python (vérifié par grep sur tout le dépôt).
          // L'ancien code n'écoutait que ce nom fantôme — les badges d'étapes de
          // la vue Chat et du Prompt Studio ne se sont donc jamais affichés.
          case "agent_started":
          case "agent_completed":
            callbacks.onStep({
              agent: (evt.data?.agent_name as string) ?? (evt.data?.agent as string) ?? "agent",
              status:
                (evt.data?.status as string) ??
                (evt.type === "agent_started" ? "en cours" : "terminé"),
            });
            break;

          case "phase_changed":
            callbacks.onStep({
              agent: "pipeline",
              status: (evt.data?.phase as string) ?? "",
            });
            break;

          // ── Raisonnement et outils (source.mode = "chat", #T346) ──
          case "thinking":
            callbacks.onThinking?.(evt.text ?? "");
            break;

          case "thinking_done":
            callbacks.onThinkingDone?.(evt.durationMs);
            break;

          case "tool_call":
            callbacks.onToolCall?.({
              id: evt.id ?? "",
              toolName: evt.toolName ?? "outil",
              args: evt.args ?? {},
              status: "running",
            });
            break;

          case "tool_result":
            callbacks.onToolResult?.({
              id: evt.id ?? "",
              toolName: evt.toolName ?? "outil",
              status: evt.status === "error" ? "error" : "success",
              resultData: evt.resultData,
              errorMessage: evt.errorMessage ?? undefined,
              executionTimeMs: evt.executionTimeMs,
            });
            break;

          case "done":
            callbacks.onDone(evt.response ?? "", evt.agents_used ?? []);
            return;

          case "error":
            callbacks.onError(evt.message ?? "Erreur inconnue");
            return;

          // heartbeat / token / sentence / aborted → silencieux (la vue Chat
          // n'affiche pas le contenu mot-à-mot : elle rend la réponse sur `done`).
        }
      } catch {
        /* ligne SSE non-JSON (ping, commentaire) */
      }
    }
  }
}
