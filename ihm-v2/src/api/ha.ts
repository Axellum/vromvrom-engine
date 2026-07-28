import { apiFetch } from "./client";

/**
 * Client Home Assistant + assistant vocal.
 *
 * Point important : `fetchHaHealth` ne renvoie jamais un « connecté » supposé.
 * Le backend effectue un vrai appel à Home Assistant et rapporte la cause exacte
 * en cas d'échec (TLS, token refusé, hôte injoignable). L'ancienne vue affichait
 * un pastille verte « ● Connecté » codée en dur, jamais vérifiée.
 */

export interface HaHealth {
  url: string;
  token_configured: boolean;
  verify_tls: boolean;
  ca_bundle: string | null;
  reachable: boolean;
  latency_ms: number | null;
  version: string | null;
  location_name?: string | null;
  entity_count: number | null;
  domains: Record<string, number>;
  error: string | null;
}

export async function fetchHaHealth(): Promise<HaHealth> {
  return apiFetch<HaHealth>("/api/ha/health");
}

export interface HaEntity {
  entity_id: string;
  friendly_name: string;
  state: string | null;
  unit: string | null;
  device_class: string | null;
  last_changed: string | null;
}

export async function fetchHaEntities(params: {
  search?: string;
  domain?: string;
  limit?: number;
}): Promise<{ total_matched: number; returned: number; entities: HaEntity[] }> {
  const qs = new URLSearchParams();
  if (params.search) qs.set("search", params.search);
  if (params.domain) qs.set("domain", params.domain);
  qs.set("limit", String(params.limit ?? 200));
  return apiFetch(`/api/ha/entities?${qs.toString()}`);
}

/** Appelle un service HA sur une entité. Action réelle sur le domicile. */
export async function controlHaEntity(input: {
  entity_id: string;
  service: string;
  domain?: string;
  service_data?: Record<string, unknown>;
}): Promise<unknown> {
  return apiFetch("/api/ha/control", { method: "POST", body: JSON.stringify(input) });
}

/* ── Assistant vocal ──────────────────────────────────────────────────────── */

export interface VocalStats {
  period: string;
  total_events: number;
  total_exchanges: number;
  tts_enabled_events: number;
  latency_ms: { avg: number | null; min: number | null; max: number | null };
  by_mode: Array<{ source_mode: string | null; n: number; avg_ms: number | null }>;
  by_device: Array<{ device_id: string; n: number }>;
  by_routing: Array<{ routing_type: string; n: number }>;
}

export async function fetchVocalStats(period = "7d"): Promise<VocalStats> {
  return apiFetch<VocalStats>(`/api/vocal/stats?period=${encodeURIComponent(period)}`);
}

export interface VocalAuditEntry {
  id: number;
  created_at: string;
  session_id: string | null;
  user_prompt: string | null;
  source_type: string | null;
  source_mode: string | null;
  tts_enabled: number | null;
  device_id: string | null;
  routing_type: string | null;
  agents_used: string | null;
  response_text: string | null;
  latency_ms: number | null;
  phase: string | null;
}

export async function fetchVocalAudit(limit = 50): Promise<{ count: number; logs: VocalAuditEntry[] }> {
  return apiFetch(`/api/vocal/audit?limit=${limit}`);
}

export interface VocalConfig {
  ha_model: string | null;
  executor_model: string | null;
  semantic_cache: { enabled?: boolean; similarity_threshold?: number };
  secrets_present: Record<string, boolean>;
  tts_cloud_available: boolean;
}

export async function fetchVocalConfig(): Promise<VocalConfig> {
  return apiFetch<VocalConfig>("/api/vocal/config");
}

/** Interrompt les flux vocaux en cours (barge-in Tab5). */
export async function abortVocal(): Promise<{ aborted: number; active_before: unknown }> {
  return apiFetch("/api/vocal/abort", { method: "POST", body: JSON.stringify({}) });
}

/** Voix TTS cloud disponibles. */
export async function fetchTtsVoices(): Promise<unknown> {
  return apiFetch("/api/tts-cloud/voices");
}
