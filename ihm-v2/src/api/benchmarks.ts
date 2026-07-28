import { apiFetch } from './client';

/**
 * Client de la vue « Comparatif modèles ».
 *
 * Deux natures de données, volontairement séparées à l'écran :
 *  - `observed` : ce que les modèles ont RÉELLEMENT fait en production, croisé
 *    depuis token_usage / model_elo_scores / routing_decisions. Un champ jamais
 *    mesuré vaut `null` — la vue affiche « — », jamais un zéro trompeur.
 *  - `runs` : comparatifs déclenchés explicitement, dont la latence est
 *    chronométrée côté serveur autour de l'appel provider.
 *
 * Aucun repli sur des données simulées : si un endpoint échoue, l'erreur remonte
 * à la vue, qui l'affiche. (Avant, ce module renvoyait des mocks en silence —
 * la page entière était une fiction crédible.)
 */

/** Performance réellement observée en production pour un modèle. */
export interface ObservedModelPerf {
  model: string;
  calls: number;
  total_tokens: number;
  cost_usd: number;
  avg_cost_per_call_usd: number | null;
  elo: number | null;
  matches: number | null;
  win_rate: number | null;
  avg_latency_ms: number | null;
  success_rate: number | null;
}

/** Résultat mesuré d'un modèle dans un comparatif déclenché manuellement. */
export interface BenchmarkResult {
  model: string;
  status: 'success' | 'error';
  latency_ms: number | null;
  response_text: string | null;
  response_chars: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cost_usd: number | null;
  error_message: string | null;
}

export interface BenchmarkRun {
  run_id: string;
  prompt: string;
  system_prompt: string | null;
  created_at: number;
  finished_at: number | null;
  status: string;
  results: BenchmarkResult[];
}

export interface BenchmarksPayload {
  period: string;
  generated_at: string;
  observed: ObservedModelPerf[];
  runs: BenchmarkRun[];
}

/** Comparatif complet : performance observée + historique des runs manuels. */
export async function fetchBenchmarks(period = '30d', limit = 10): Promise<BenchmarksPayload> {
  return apiFetch<BenchmarksPayload>(
    `/api/metrics/benchmarks?period=${encodeURIComponent(period)}&limit=${limit}`,
  );
}

/**
 * Lance un comparatif réel : le même prompt part vers chaque modèle en parallèle.
 * ⚠ Déclenche de vrais appels LLM facturés (le backend plafonne à 8 modèles).
 */
export async function runBenchmark(
  prompt: string,
  models: string[],
  options: { systemPrompt?: string; maxTokens?: number; temperature?: number } = {},
): Promise<BenchmarkRun> {
  return apiFetch<BenchmarkRun>('/api/metrics/benchmarks', {
    method: 'POST',
    body: JSON.stringify({
      prompt,
      models,
      system_prompt: options.systemPrompt ?? null,
      max_tokens: options.maxTokens ?? 512,
      temperature: options.temperature ?? 0.3,
    }),
  });
}

/** Classement Elo par domaine (GET /api/metrics/elo — scores réels, pas d'historique). */
export interface EloEntry {
  elo: number;
  matches: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_latency_ms: number | null;
  last_updated: number;
}

export interface EloPayload {
  /** modèle/agent → domaine → score */
  scores: Record<string, Record<string, EloEntry>>;
  domains: string[];
  leaderboards: Record<string, Array<Record<string, unknown>>>;
}

export async function fetchElo(): Promise<EloPayload> {
  return apiFetch<EloPayload>('/api/metrics/elo');
}
