import { apiFetch } from "./client";

/**
 * Clients des endpoints d'exploitation réels du moteur (vue Opérations) :
 * - GET /api/status                          : état d'exécution (AppState)
 * - GET /api/observability/circuit-breakers  : santé des CB (registre mémoire)
 * - GET /api/backlog/quota                   : quotas/budget (BudgetGuard)
 * - GET /api/daemon/status, /api/dreamer/status : agents persistants
 * - GET /api/apis-status                     : vivacité DeepSeek/Gemini/Claude/Antigravity
 * - GET /version                             : version du moteur (endpoint public)
 */

export interface ExecutionStatus {
  status?: string;
  objective?: string;
  session_id?: string;
  error_message?: string | null;
}

export async function fetchExecutionStatus(): Promise<ExecutionStatus> {
  return apiFetch<ExecutionStatus>("/api/status");
}

export interface CircuitBreakerInfo {
  name: string;
  state: "CLOSED" | "OPEN" | "HALF_OPEN";
  failure_count: number;
  total_calls: number;
  total_failures: number;
  total_trips: number;
  rate_limits: number;
  avg_latency_ms: number | null;
}

export interface CircuitBreakersStatus {
  uptime_seconds: number;
  total: number;
  open: number;
  half_open: number;
  healthy: number;
  circuit_breakers: CircuitBreakerInfo[];
}

export async function fetchCircuitBreakers(): Promise<CircuitBreakersStatus> {
  return apiFetch<CircuitBreakersStatus>("/api/observability/circuit-breakers");
}

export interface QuotaProvider {
  available: boolean;
  metric: string;
  used: number;
  limit: number;
  unit: string;
}

export interface QuotaSummary {
  timestamp: number;
  providers: Record<string, QuotaProvider>;
}

export async function fetchQuotas(): Promise<QuotaSummary> {
  return apiFetch<QuotaSummary>("/api/backlog/quota");
}

/** Statuts daemon/dreamer : dicts libres côté backend, affichés champ par champ. */
export type PersistentStatus = Record<string, unknown>;

export async function fetchDaemonStatus(): Promise<PersistentStatus> {
  return apiFetch<PersistentStatus>("/api/daemon/status");
}

export async function fetchDreamerStatus(): Promise<PersistentStatus> {
  return apiFetch<PersistentStatus>("/api/dreamer/status");
}

export interface ApisStatus {
  deepseek?: { configured: boolean; active: boolean; real_balance_usd?: number; error?: string | null };
  gemini?: { configured: boolean; active: boolean; error?: string | null };
  claude?: { real_usage_pct?: number | null; summary_text?: string | null };
  antigravity?: { connected?: boolean; plan?: string; credits?: number };
}

export async function fetchApisStatus(): Promise<ApisStatus> {
  return apiFetch<ApisStatus>("/api/apis-status");
}

export interface VersionInfo {
  version: string;
  git_hash?: string;
  build_date?: string;
}

export async function fetchVersion(): Promise<VersionInfo> {
  // Endpoint public GET /version (gui_server.py) — PAS /api/version (n'existe pas).
  return apiFetch<VersionInfo>("/version");
}
