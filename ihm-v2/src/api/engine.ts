/** Endpoints moteur utilisés par le Dashboard. */
import { apiFetch } from "./client";
import type { ExecutionState } from "../types/engine";

/** État d'exécution courant (GET /api/status). */
export function fetchStatus(): Promise<ExecutionState> {
  return apiFetch<ExecutionState>("/api/status");
}

/** Lance une tâche en arrière-plan (POST /api/run). Les événements arrivent via /api/stream. */
export function runTask(objective: string): Promise<{
  message: string;
  status: string;
  session_id: string;
}> {
  return apiFetch("/api/run", {
    method: "POST",
    body: JSON.stringify({ objective }),
  });
}

/** Demande l'arrêt de l'exécution en cours (POST /api/stop). */
export function stopExecution(): Promise<{ message: string; status: string }> {
  return apiFetch("/api/stop", { method: "POST" });
}
