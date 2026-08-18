/**
 * Store global de l'état moteur (Zustand).
 *
 * Centralise l'état d'exécution + le flux d'événements SSE, alimenté à la fois
 * par le snapshot REST (GET /api/status) et par les événements temps réel
 * (GET /api/stream). Les vues s'abonnent via des sélecteurs ciblés.
 */
import { create } from "zustand";
import type { EngineEvent, EngineStatus, ExecutionState, GlobalState } from "../types/engine";

export type ConnectionState = "connecting" | "open" | "closed";

const MAX_EVENTS = 200;

interface EngineStore {
  status: EngineStatus;
  objective: string | null;
  sessionId: string | null;
  engineState: GlobalState | null;
  errorMessage: string | null;
  events: EngineEvent[];
  connection: ConnectionState;

  /** Remplace l'état depuis un snapshot REST (/api/status). */
  setSnapshot: (s: ExecutionState) => void;
  /** Applique un événement SSE temps réel et l'ajoute au journal. */
  applyEvent: (e: EngineEvent) => void;
  setConnection: (c: ConnectionState) => void;
  clearEvents: () => void;
}

/** Statuts terminaux d'une orchestration. */
function statusFromEvent(e: EngineEvent, current: EngineStatus): EngineStatus {
  if (typeof e.status === "string") return e.status;
  if (e.event === "orchestration_completed") {
    const s = (e.data?.status as EngineStatus) ?? "success";
    return s;
  }
  if (e.event === "agent_started" || e.event === "task_started") return "running";
  return current;
}

export const useEngineStore = create<EngineStore>((set) => ({
  status: "idle",
  objective: null,
  sessionId: null,
  engineState: null,
  errorMessage: null,
  events: [],
  connection: "connecting",

  setSnapshot: (s) =>
    set({
      status: s.status ?? "idle",
      objective: s.objective ?? null,
      sessionId: s.session_id ?? null,
      engineState: s.engine_state ?? null,
      errorMessage: s.error_message ?? null,
    }),

  applyEvent: (e) =>
    set((state) => ({
      status: statusFromEvent(e, state.status),
      engineState: e.engine_state ?? state.engineState,
      errorMessage:
        e.event === "orchestration_completed" && e.data?.error_message
          ? String(e.data.error_message)
          : state.errorMessage,
      events: [e, ...state.events].slice(0, MAX_EVENTS),
    })),

  setConnection: (connection) => set({ connection }),
  clearEvents: () => set({ events: [] }),
}));
