/**
 * Outils MCP : ce que le moteur offre à Claude ou Cursor, et ce qu'il utilise lui-même.
 * Route : GET /api/mcp/overview (écrite en parallèle, contrat ci-dessous).
 */
import { fetchObjet } from "./helpers";

export interface McpOutilExpose {
  nom: string;
  description?: string | null;
}

export interface McpServeurConsomme {
  nom: string;
  type?: string | null;
  actif?: boolean | null;
  cible?: string | null;
  /** Règles d'activation : quand cet outil est proposé aux agents. */
  regles?: string[] | null;
}

export interface McpOverview {
  expose?: {
    disponible?: boolean | null;
    point_d_entree?: string | null;
    outils?: McpOutilExpose[] | null;
  } | null;
  consomme?: McpServeurConsomme[] | null;
  notes?: string[] | null;
}

export function fetchMcpOverview(): Promise<McpOverview> {
  return fetchObjet<McpOverview>("/api/mcp/overview");
}
