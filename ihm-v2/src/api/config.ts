/** Endpoints de configuration (config.json éditable + présence des clés .env). */
import { apiFetch } from "./client";

export type EngineConfig = Record<string, unknown>;

/** GET /api/config → contenu de config.json. */
export function fetchConfig(): Promise<EngineConfig> {
  return apiFetch<EngineConfig>("/api/config");
}

/**
 * POST /api/config → merge partiel. On n'envoie que les clés de premier niveau
 * modifiées (le backend fait `config.update(body)`).
 */
export function updateConfig(patch: EngineConfig): Promise<{
  message: string;
  config: EngineConfig;
}> {
  return apiFetch("/api/config", {
    method: "POST",
    body: JSON.stringify(patch),
  });
}

/** GET /api/keys → présence (booléenne) des secrets .env, sans les valeurs. */
export function fetchKeys(): Promise<{
  keys: Record<string, boolean>;
  configured_count: number;
}> {
  return apiFetch("/api/keys");
}
