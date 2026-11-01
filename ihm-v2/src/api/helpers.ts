/**
 * Aides communes aux appels des nouvelles vues « comprendre et régler ».
 *
 * Principe : une route que ce moteur n'expose pas (404) est un état à part
 * entière — « donnée non disponible » — et non une erreur ni un résultat vide.
 */
import { ApiError, apiFetch, isAuthError } from "./client";

/** Vrai quand le moteur répond que la route n'existe pas (moteur plus ancien que l'IHM). */
export function isRouteAbsente(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 501);
}

/**
 * Lit une route qui doit renvoyer un objet JSON. Toute autre forme (page HTML,
 * tableau, texte) est refusée : on n'affiche rien plutôt qu'un chiffre inventé.
 */
export async function fetchObjet<T extends object>(path: string): Promise<T> {
  const data = await apiFetch<unknown>(path);
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new ApiError(502, `Réponse inattendue de ${path} (objet JSON attendu)`);
  }
  return data as T;
}

/** Relance au plus deux fois, sauf pour une route absente ou un refus d'accès (inutile de réessayer). */
export function retryRaisonnable(echecs: number, err: unknown): boolean {
  if (isRouteAbsente(err) || isAuthError(err)) return false;
  return echecs < 2;
}
