/**
 * Routes « guide » : synthèse de l'état du moteur (Accueil) et ordre réel de
 * la cascade de modèles (Comprendre).
 *
 * Contrat (écrit côté Python en parallèle) : tout nombre peut être `null`, ce qui
 * veut dire « inconnu » — jamais zéro. Tant qu'une route répond 404, la vue
 * affiche « donnée non disponible » (voir `DataGate`).
 */
import { fetchObjet } from "./helpers";

export type NiveauAlerte = "info" | "attention" | "critique";

export interface GuideAlerte {
  niveau?: NiveauAlerte | string | null;
  texte: string;
}

export interface GuideBoucle {
  nom: string;
  active?: boolean | null;
  description?: string | null;
  /** Réglage (clé de configuration) qui commande cette boucle. */
  reglage?: string | null;
}

export interface GuideOverview {
  genere_le?: string | number | null;
  moteur?: {
    commit?: string | null;
    demarre_le?: string | number | null;
    uptime_s?: number | null;
  } | null;
  fournisseurs?: {
    total?: number | null;
    utilisables?: number | null;
    en_panne?: Array<{ nom: string; raison?: string | null }> | null;
    /** Fournisseurs sans test récent : ni utilisables, ni en panne, simplement inconnus. */
    non_testes?: number | null;
    /** Fournisseurs sans aucun modèle actif au catalogue. */
    sans_modele_actif?: number | null;
    /** Précision du moteur sur la fraîcheur des tests (sonde désactivée, périmée…). */
    note?: string | null;
  } | null;
  modeles?: { total?: number | null; actifs?: number | null } | null;
  usage?: {
    appels_24h?: number | null;
    appels_reels_7j?: number | null;
    appels_sondes_7j?: number | null;
    cout_30j_usd?: number | null;
    /** Précision du moteur : comment les sondes sont comptées et ce que ça change. */
    note?: string | null;
  } | null;
  vocal?: {
    echanges_7j?: number | null;
    latence_p50_ms?: number | null;
    /** Date de la dernière demande vocale (texte ISO, époque ou objet portant une date). */
    dernier_echange?: string | number | Record<string, unknown> | null;
  } | null;
  home_assistant?: { configure?: boolean | null } | null;
  sauvegardes?: {
    /** Date de la dernière sauvegarde (texte ISO, époque ou objet portant une date). */
    derniere?: string | number | Record<string, unknown> | null;
    nombre?: number | null;
    dossier?: string | null;
  } | null;
  boucles?: GuideBoucle[] | null;
  alertes?: GuideAlerte[] | null;
}

export function fetchGuideOverview(): Promise<GuideOverview> {
  return fetchObjet<GuideOverview>("/api/guide/overview");
}

export interface GuideCascade {
  source?: string | null;
  /** Ordre des modèles essayés pour la discussion et le vocal. */
  fast_path_providers?: string[] | null;
  /** Ordre des modèles par niveau (léger, moyen, fort…). */
  tiers?: Record<string, string[]> | null;
  budget?: Record<string, unknown> | null;
  explication?: {
    fast_path_providers?: string | null;
    tiers?: string | null;
    budget?: string | null;
  } | null;
  /** Même ordre, depuis la tablette Tab5 (ses modèles passent d'abord). */
  fast_path_providers_tab5?: string[] | null;
  /**
   * Niveaux tels que le moteur les lit réellement : modèles actifs du catalogue.
   * `tiers` (config.json) n'est qu'un filet de sécurité si le catalogue ne renvoie rien.
   */
  tiers_catalogue?: Record<string, string[]> | null;
  tiers_source?: Record<string, string> | null;
  exclus_de_la_cascade?: string[] | null;
  note?: string | null;
}

export function fetchGuideCascade(): Promise<GuideCascade> {
  return fetchObjet<GuideCascade>("/api/guide/cascade");
}
