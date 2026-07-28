import { apiFetch } from "./client";

/**
 * Diagnostic d'installation. Chaque contrôle est effectué réellement côté
 * serveur (fichier lu, table comptée, connexion tentée) et porte une
 * remédiation quand il échoue.
 */

export type CheckStatus = "ok" | "warn" | "error" | "info";

export interface SetupCheck {
  name: string;
  label: string;
  status: CheckStatus;
  detail: string;
  fix: string | null;
}

export interface SetupGroup {
  id: string;
  label: string;
  checks: SetupCheck[];
}

export interface SetupDiagnostics {
  groups: SetupGroup[];
  summary: {
    total: number;
    ok: number;
    warnings: number;
    errors: number;
    /** Vrai si aucun contrôle bloquant n'est en erreur. */
    operational: boolean;
  };
}

export async function fetchSetupDiagnostics(): Promise<SetupDiagnostics> {
  return apiFetch<SetupDiagnostics>("/api/setup/diagnostics");
}
