import { apiFetch } from "./client";

/**
 * Contrat réel du CRUD /api/agents (#T159, api/routes/agents_crud.py).
 * - core       : agents du pipeline ; "model" = clé `<agent>_model` de config.json
 *                (tier leger/moyen/fort/automatique OU id de modèle littéral).
 * - persistent : daemon/dreamer ; enabled/interval = persistent_agents.* de config.json.
 * - custom     : section custom_agents de config.json (réellement chargée par le
 *                moteur) ou nœuds de l'éditeur de workflow (source "workflow",
 *                gérés là-bas).
 * Pas de temperature/max_tokens ici : aucun réglage réel de ce type n'existe
 * côté moteur — on n'expose pas de faux boutons.
 */
export interface AgentConfig {
  name: string;
  label: string;
  kind: "core" | "persistent" | "custom";
  enabled: boolean;
  can_disable: boolean;
  model: string | null;
  tier: string;
  system_prompt: string | null;
  prompt_editable: boolean;
  prompt_source: "markdown" | "defaut_code" | "config" | null;
  is_custom: boolean;
  interval_minutes?: number | null;
  source: "config" | "workflow";
  // [#T233/#T290] Permissions d'outils. Trois états distincts :
  // null = tous les outils, [] = aucun, liste non vide = sélection partielle.
  // Rendu par le GET pour les agents custom (agents_crud.py:213).
  allowed_tools?: string[] | null;
}

export async function fetchAgents(): Promise<AgentConfig[]> {
  const res = await apiFetch<{ agents: AgentConfig[] }>("/api/agents");
  return res.agents;
}

export async function toggleAgent(name: string): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>(`/api/agents/${encodeURIComponent(name)}/toggle`, {
    method: "POST",
  });
}

export interface AgentPatch {
  model?: string;
  tier?: string;
  label?: string;
  enabled?: boolean;
  interval_minutes?: number;
  system_prompt?: string;
  // [#T233/#T290/#T345] Permissions d'outils (null = tous, [] = aucun, liste = partiel).
  // Le PUT backend distingue « champ absent » (inchangé) de « fourni à null »
  // (= tous les outils) via model_fields_set : envoyer explicitement null
  // lève bien une restriction existante (#T345).
  allowed_tools?: string[] | null;
}

export async function updateAgent(name: string, patch: AgentPatch): Promise<AgentConfig> {
  return apiFetch<AgentConfig>(`/api/agents/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export async function createAgent(config: {
  name: string;
  label?: string;
  tier: string;
  system_prompt?: string;
  // [#T233/#T290] Permissions d'outils (null = tous, [] = aucun, liste = partiel).
  allowed_tools?: string[] | null;
}): Promise<AgentConfig> {
  return apiFetch<AgentConfig>("/api/agents", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function deleteAgent(name: string): Promise<void> {
  return apiFetch<void>(`/api/agents/${encodeURIComponent(name)}`, { method: "DELETE" });
}
