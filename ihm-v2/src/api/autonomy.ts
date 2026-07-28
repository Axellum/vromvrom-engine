import { apiFetch } from "./client";

/**
 * Client des agents autonomes (Daemon Sentinelle, Dreamer/DreamCoder, Auditeur).
 *
 * Contrats relevés en direct sur le moteur — les champs optionnels le sont parce
 * que le backend ne les renseigne pas toujours (ex: `last_error` reste null tant
 * qu'aucun cycle n'a échoué), pas par prudence défensive.
 */

export interface DaemonAnomaly {
  check: string;
  status: "ok" | "info" | "warning" | "error" | string;
  alert: string;
}

export interface DaemonStatus {
  running: boolean;
  enabled: boolean;
  last_cycle_at: string | null;
  last_cycle_duration_ms: number | null;
  total_cycles: number;
  anomalies: DaemonAnomaly[];
  errors_count: number;
  last_error: string | null;
  interval_minutes: number;
  started_at: string | null;
}

export async function fetchDaemonStatus(): Promise<DaemonStatus> {
  return apiFetch<DaemonStatus>("/api/daemon/status");
}

export interface DaemonLogEntry {
  cycle_id: string;
  timestamp: string;
  duration_ms: number;
  anomalies_count: number;
  checks_summary: Record<string, string>;
}

export async function fetchDaemonLogs(limit = 30): Promise<{ logs: DaemonLogEntry[] }> {
  return apiFetch(`/api/daemon/logs?limit=${limit}`);
}

export interface DreamerStatus {
  running: boolean;
  enabled: boolean;
  last_run_at: string | null;
  last_run_duration_ms: number | null;
  total_runs: number;
  last_report: {
    path?: string;
    summary?: string;
    actions?: Record<string, Record<string, unknown>>;
  } | null;
  idle_trigger_hours?: number;
}

export async function fetchDreamerStatus(): Promise<DreamerStatus> {
  return apiFetch<DreamerStatus>("/api/dreamer/status");
}

/** Déclenche un cycle de consolidation immédiat. Consomme des appels LLM. */
export async function triggerDreamer(): Promise<{ status: string; report: unknown }> {
  return apiFetch("/api/dreamer/trigger", { method: "POST" });
}

/** Configuration des agents persistants (sous-arbre persistent_agents de config.json). */
export async function fetchPersistentConfig(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/api/persistent-agents/config");
}

export async function updatePersistentConfig(
  patch: Record<string, unknown>,
): Promise<{ message: string; config: Record<string, unknown> }> {
  return apiFetch("/api/persistent-agents/config", {
    method: "POST",
    body: JSON.stringify(patch),
  });
}
