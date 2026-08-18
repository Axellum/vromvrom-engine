import { apiFetch } from "./client";

/**
 * Contrat réel de GET /api/models/registry (#T158, api/routes/context.py) :
 * TOUS les modèles (actifs + inactifs), joints à leurs métriques réelles.
 * Les champs sans donnée sont null — jamais de valeur inventée côté vue.
 */
export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  /** Canal d'accès (free/paid/subscription/local…), colonne `tier` de la DB. */
  tier: string;
  /** Capacité de routage (leger/moyen/fort) ou null si non câblé (#T186). */
  routing_tier: "leger" | "moyen" | "fort" | null;
  enabled: boolean;
  /** Elo moyen inter-domaines (model_elo_scores) — null si jamais évalué. */
  elo_score: number | null;
  /** Latence moyenne réelle (Circuit Breaker, repli Elo puis ttft_ms). */
  avg_latency_ms: number | null;
  cost_usd_30d: number | null;
  calls_30d: number | null;
  /** Appels depuis TOUJOURS (#T243) — 30 jours ne suffit pas à décider d'une désactivation. */
  calls_total: number;
  cost_per_success: number | null;
  circuit_breaker_status: "CLOSED" | "OPEN" | "HALF_OPEN" | null;
  /** false = au catalogue mais pas câblé dans le gateway (#T186). */
  is_wired: boolean;
  /**
   * Le modèle figure-t-il encore dans le listing de son API ? (#T243)
   *
   * `null` = on ne sait pas (aucun inventaire généré, ou provider muet au dernier
   * passage) — à ne JAMAIS confondre avec `false`. Et `false` ne veut pas dire
   * « mort » : un alias encore servi peut être absent du listing (`deepseek-chat`
   * répond alors qu'il n'y figure pas). C'est un indice, pas une preuve.
   */
  offered_by_api: boolean | null;
  cost_input_per_m: number | null;
  cost_output_per_m: number | null;
}

/** Bilan du dernier inventaire live des API (#T243). */
export interface InventaireResume {
  genere_le: string | null;
  providers_ok: number;
  providers_total: number;
  modeles_recenses: number;
  par_provider?: Record<string, { statut: string; offerts: number }>;
}

/**
 * Candidat à la désactivation (#T243) : jamais appelé de toute l'histoire du moteur
 * ET injoignable faute d'être câblé dans le gateway.
 *
 * Les deux conditions sont exigées à dessein. « Jamais appelé » seul ne suffit pas :
 * un modèle câblé mais inutilisé peut être un repli de cascade légitime, qui ne sert
 * précisément que le jour où les autres tombent.
 */
export function estCandidatDesactivation(m: ModelInfo): boolean {
  return m.enabled && m.calls_total === 0 && !m.is_wired;
}

interface RegistryModel {
  id: string;
  provider_id?: string;
  display_name?: string;
  status?: string;
  tier?: string;
  routing_tier?: "leger" | "moyen" | "fort" | null;
  elo_score?: number | null;
  avg_latency_ms?: number | null;
  cost_usd_30d?: number | null;
  calls_30d?: number | null;
  calls_total?: number | null;
  cost_per_success?: number | null;
  circuit_breaker_status?: "CLOSED" | "OPEN" | "HALF_OPEN" | null;
  is_wired?: boolean;
  offered_by_api?: boolean | null;
  cost_input_per_m?: number | null;
  cost_output_per_m?: number | null;
}

export async function fetchModels(): Promise<ModelInfo[]> {
  const res = await apiFetch<{ models: RegistryModel[] }>("/api/models/registry");
  return (res.models ?? []).map((m) => ({
    id: m.id,
    name: m.display_name ?? m.id,
    provider: m.provider_id ?? "inconnu",
    tier: m.tier ?? "?",
    routing_tier: m.routing_tier ?? null,
    enabled: m.status === "active",
    elo_score: m.elo_score ?? null,
    avg_latency_ms: m.avg_latency_ms ?? null,
    cost_usd_30d: m.cost_usd_30d ?? null,
    calls_30d: m.calls_30d ?? null,
    calls_total: m.calls_total ?? 0,
    cost_per_success: m.cost_per_success ?? null,
    circuit_breaker_status: m.circuit_breaker_status ?? null,
    is_wired: m.is_wired ?? false,
    // `?? null` et non `?? false` : l'absence d'information n'est pas une absence
    // du listing. Un backend antérieur à #T243 ne renvoie pas ce champ.
    offered_by_api: m.offered_by_api ?? null,
    cost_input_per_m: m.cost_input_per_m ?? null,
    cost_output_per_m: m.cost_output_per_m ?? null,
  }));
}

/** Bilan du dernier inventaire (lecture seule, aucun appel réseau vers les API). */
export async function fetchInventaire(): Promise<InventaireResume> {
  const res = await apiFetch<{ inventaire: InventaireResume }>("/api/models/inventory");
  return res.inventaire;
}

/** Relance l'interrogation de toutes les API de listing. Plusieurs secondes. */
export async function refreshInventaire(): Promise<InventaireResume> {
  const res = await apiFetch<{ inventaire: InventaireResume }>(
    "/api/models/inventory/refresh",
    { method: "POST" },
  );
  return res.inventaire;
}

export async function toggleModel(id: string): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>(`/api/models/${encodeURIComponent(id)}/toggle`, {
    method: "POST",
  });
}

export async function pingModel(
  id: string,
): Promise<{ ok: boolean; latency_ms: number; error?: string }> {
  return apiFetch<{ ok: boolean; latency_ms: number; error?: string }>(
    `/api/models/${encodeURIComponent(id)}/ping`,
    { method: "POST" },
  );
}

/** Résultat de la bascule groupée (#T243) — les ids inconnus n'échouent pas le lot. */
export interface BulkStatusResult {
  status: "ok" | "partiel";
  applique: "active" | "inactive";
  modifies: string[];
  inchanges: string[];
  introuvables: string[];
  echecs: string[];
}

/** Active ou désactive plusieurs modèles en un appel (#T243). */
export async function setModelsStatus(
  ids: string[],
  status: "active" | "inactive",
): Promise<BulkStatusResult> {
  return apiFetch<BulkStatusResult>("/api/models/bulk-status", {
    method: "POST",
    body: JSON.stringify({ ids, status }),
  });
}

/** Change le routing_tier (capacité leger/moyen/fort, null = hors routage). */
export async function setRoutingTier(
  id: string,
  routing_tier: "leger" | "moyen" | "fort" | null,
): Promise<void> {
  await apiFetch(`/api/models/${encodeURIComponent(id)}/routing-tier`, {
    method: "POST",
    body: JSON.stringify({ routing_tier }),
  });
}
