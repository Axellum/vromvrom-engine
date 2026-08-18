/**
 * Types de la télémétrie moteur (GET /api/metrics/telemetry).
 * Reflète la structure agrégée par api/routes/metrics.py (conçue pour Chart.js).
 */

export type TelemetryPeriod = "1h" | "6h" | "24h" | "7d" | "30d";

export interface TimeSeries {
  labels: string[];
  tokens: number[];
  costs: number[];
}

export interface AgentStat {
  agent: string;
  total_calls: number;
  successes?: number;
  total_input?: number;
  total_output?: number;
  avg_tokens?: number;
  success_rate?: number;
}

export interface ModelStat {
  total_tokens: number;
  total_calls: number;
  cost_usd: number;
  avg_elo?: number;
  elo_domains?: Record<string, unknown>;
}

export interface Kpis {
  total_cost_usd: number;
  total_sessions: number;
  total_tokens: number;
}

export interface BudgetForecast {
  avg_daily_cost: number;
  projected_7d: number;
  projected_30d: number;
  daily_breakdown?: { day: string; daily_cost: number }[];
}

export interface Telemetry {
  period: string;
  generated_at: string;
  time_series: TimeSeries;
  agent_stats: AgentStat[];
  model_stats: Record<string, ModelStat>;
  routing_stats: Record<string, unknown>;
  kpis: Kpis;
  budget_forecast: BudgetForecast;
}
