import { apiFetch } from "./client";

export interface ProviderAccount {
  name: string;
  balance_usd: number | null;
  balance_eur: number | null;
  quota_rpm: number | null;
  quota_rpd: number | null;
  used_rpm: number | null;
  used_rpd: number | null;
  used_pct: number;
  last_sync: string | null;
  dashboard_url: string;
  status: string;
  plan: string;
  /** Identifiant technique stable pour POST /api/billing/manual (absent sur les providers suivis par quota). */
  provider_key?: string;
}

export interface BillingAccountsResponse {
  providers: ProviderAccount[];
  /** Dépense réelle depuis le 1er du mois calendaire courant (pas un solde de compte). */
  total_cost_usd_month: number;
}

/**
 * `GET /api/billing` existe depuis #T178 (agrégateur de soldes réels). Le
 * try/catch qui renvoyait une réponse vide datait d'avant : il masquait
 * désormais les vraies pannes derrière une grille silencieusement vide.
 */
export async function fetchAccounts(): Promise<BillingAccountsResponse> {
  return apiFetch<BillingAccountsResponse>("/api/billing");
}

/**
 * `GET /api/metrics/cost-per-success` (core/elo_scorer.py::get_cost_per_successful_task)
 * renvoie un dict `{providers: {<nom>: {total_cost_usd, successful_tasks, cost_per_success_usd}}}`,
 * pas un tableau `by_provider` — normalisé ici pour le graphique.
 */
export async function fetchCostPerSuccess(): Promise<{ by_provider: Array<{ provider: string; cost_usd: number; tasks: number }> }> {
  const res = await apiFetch<{
    providers: Record<string, { total_cost_usd: number; successful_tasks: number; cost_per_success_usd: number }>;
  }>("/api/metrics/cost-per-success");
  const by_provider = Object.entries(res.providers ?? {}).map(([provider, stats]) => ({
    provider,
    cost_usd: stats.total_cost_usd,
    tasks: stats.successful_tasks,
  }));
  return { by_provider };
}

// `reauthorizeGoogle` a été retirée : elle appelait POST /api/auth/google/reauthorize,
// route qui n'existe nulle part côté backend (vérifié par grep sur api/, core/ et
// gui_server.py). Le bouton correspondant échouait donc toujours en 404, et son
// `onSuccess` — qui affichait « Redirection vers le flow OAuth » — ne s'exécutait
// jamais. La réautorisation passe par `python setup_google_oauth.py`.

/** Enregistre un solde saisi à la main pour un provider sans API de solde (cf. status "non_suivi"). */
export async function setManualBalance(provider: string, balance_usd: number, note = ""): Promise<void> {
  return apiFetch<void>("/api/billing/manual", {
    method: "POST",
    body: JSON.stringify({ provider, balance_usd, note }),
  });
}