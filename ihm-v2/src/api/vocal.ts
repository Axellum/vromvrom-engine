/**
 * Démo vocale : santé publique du moteur, et simulation d'une phrase sans exécution.
 *
 * Les routes d'audit et de statistiques vocales vivent déjà dans `api/ha.ts`
 * (`fetchVocalAudit`, `fetchVocalStats`) ; elles sont réutilisées telles quelles.
 */
import { apiFetch } from "./client";
import { fetchObjet } from "./helpers";

/** Réponse de `GET /healthz` (route publique). */
export interface Healthz {
  ok?: boolean;
  service?: string;
  status?: string;
  commit?: string | null;
}

export function fetchHealthz(): Promise<Healthz> {
  return fetchObjet<Healthz>("/healthz");
}

export const ROUTE_SIMULATION = "/api/vocal/simulate";

/**
 * Indique si ce moteur expose la route de simulation. On lit le schéma OpenAPI
 * plutôt que d'essayer la route : un POST d'essai pourrait, sur un moteur ancien,
 * ne pas être inoffensif. `null` = on n'a pas pu le savoir.
 */
export async function simulationDisponible(): Promise<boolean | null> {
  try {
    const schema = await apiFetch<{ paths?: Record<string, unknown> }>("/openapi.json");
    if (typeof schema !== "object" || schema === null || typeof schema.paths !== "object" || schema.paths === null) {
      return null;
    }
    return ROUTE_SIMULATION in schema.paths;
  } catch {
    return null;
  }
}

export type ModeVocal = "ha" | "chat";

export interface SimulationVocale {
  /** Nom du chemin choisi par le moteur. */
  chemin?: string | null;
  /** Explication en français du choix. */
  explication?: string | null;
  action_prevue?: { service?: string | null; entite?: string | null } | null;
  llm_requis?: boolean | null;
  executerait_une_action?: boolean | null;
}

/** Simule le routage d'une phrase : le moteur ne doit RIEN exécuter. */
export function simulerPhrase(texte: string, mode: ModeVocal): Promise<SimulationVocale> {
  return apiFetch<SimulationVocale>(ROUTE_SIMULATION, {
    method: "POST",
    body: JSON.stringify({ texte, mode }),
  });
}
