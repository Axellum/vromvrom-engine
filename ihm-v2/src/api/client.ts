/**
 * Client API du moteur — authentification propre (HMI v2).
 *
 * Modèle d'auth vérifié contre core/auth.py + gui_server.py :
 *  - REST /api/* et /v1/* : header `Authorization: Bearer <MOTEUR_API_KEY>`.
 *  - SSE/WS              : ticket éphémère à USAGE UNIQUE via POST /api/auth/ticket,
 *                          puis `?ticket=<ticket>` (la vraie clé ne transite jamais
 *                          dans l'URL). C'est ce qui permettra de retirer le fallback
 *                          déprécié `?token=` (#T65, core/auth.py:204).
 *
 * La clé est stockée en localStorage (outil personnel sur LAN — cf. spec §7).
 */

const KEY_STORAGE = "moteur_api_key";

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? "";
}

export function setApiKey(key: string): void {
  if (key) localStorage.setItem(KEY_STORAGE, key.trim());
}

export function clearApiKey(): void {
  localStorage.removeItem(KEY_STORAGE);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Indique une erreur d'authentification (clé manquante/invalide ou non configurée serveur). */
export function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && [401, 403].includes(err.status);
}

/**
 * Callback global notifié sur tout 401/403 renvoyé par le moteur, quelle que soit
 * la vue à l'origine de l'appel. Enregistré une fois par App.tsx (réaffiche
 * ApiKeyGate) — avant ce correctif, seul le flux SSE (useEngineStream) le faisait,
 * laissant les ~15 autres vues REST afficher une erreur muette sans recours.
 */
let authErrorHandler: (() => void) | null = null;

export function registerAuthErrorHandler(handler: (() => void) | null): void {
  authErrorHandler = handler;
}

/** À appeler par tout code qui fait sa propre requête HTTP (hors apiFetch) sur un statut non-2xx. */
export function notifyAuthError(status: number): void {
  if (status === 401 || status === 403) authErrorHandler?.();
}

/** Appel REST authentifié (Bearer). Lève ApiError sur statut non-2xx. */
export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const key = getApiKey();
  const headers = new Headers(init.headers);
  if (key && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${key}`);
  }
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body as { detail?: string })?.detail ?? detail;
    } catch {
      /* corps non-JSON : on garde statusText */
    }
    notifyAuthError(res.status);
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("Content-Type") ?? "";
  return (ct.includes("application/json") ? await res.json() : await res.text()) as T;
}

/**
 * Demande un ticket éphémère (usage unique, ~60s) pour ouvrir un flux SSE/WS.
 * Retourne l'URL du flux avec `?ticket=` prêt à l'emploi pour EventSource.
 */
export async function buildStreamUrl(streamPath: string): Promise<string> {
  const { ticket } = await apiFetch<{ ticket: string; expires_in: number }>(
    "/api/auth/ticket",
    { method: "POST" },
  );
  const url = new URL(streamPath, window.location.origin);
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
