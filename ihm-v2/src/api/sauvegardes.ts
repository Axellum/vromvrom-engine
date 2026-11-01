/**
 * Sauvegardes du moteur.
 *  - GET  /api/sauvegardes : inventaire.
 *  - POST /api/sauvegardes : crée une sauvegarde maintenant (201 + l'entrée créée,
 *    409 si une sauvegarde est déjà en cours, 503 si le dossier est inaccessible).
 */
import { apiFetch } from "./client";
import { fetchObjet } from "./helpers";

export interface FichierSauvegarde {
  nom: string;
  taille_octets?: number | null;
  /** « ok » : la copie passe le contrôle d'intégrité ; « ko » : anomalie ; `null` : non contrôlée. */
  integrite?: "ok" | "ko" | string | null;
}

export interface Sauvegarde {
  id: string;
  cree_le?: string | null;
  taille_octets?: number | null;
  fichiers?: FichierSauvegarde[] | null;
  commit?: string | null;
  /** `false` quand le manifeste de la sauvegarde est illisible. */
  valide?: boolean | null;
}

export interface InventaireSauvegardes {
  dossier?: string | null;
  /** Nombre de sauvegardes conservées. */
  retention?: number | null;
  sauvegardes?: Sauvegarde[] | null;
  derniere?: string | null;
}

export function fetchSauvegardes(): Promise<InventaireSauvegardes> {
  return fetchObjet<InventaireSauvegardes>("/api/sauvegardes");
}

export function creerSauvegarde(): Promise<Sauvegarde> {
  return apiFetch<Sauvegarde>("/api/sauvegardes", { method: "POST" });
}
