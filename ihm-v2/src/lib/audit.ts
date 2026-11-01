/**
 * Lecture du journal d'audit vocal (`/api/vocal/audit`).
 *
 * Chaque échange écrit DEUX lignes (phase `request` puis `response`) : seule la
 * réponse porte le type de routage et la latence. Une latence à 0 veut dire
 * « jamais mesurée », pas « instantanée » (cf. api/routes/vocal.py).
 */
import type { VocalAuditEntry } from '../api/ha';
import { cheminDepuisRouting } from './carteMoteur';
import type { CheminId } from './carteMoteur';
import { mediane } from './format';

/** Réponses seulement, de la plus récente à la plus ancienne. */
export function echangesRecents(lignes: VocalAuditEntry[]): VocalAuditEntry[] {
  return lignes
    .filter((l) => l.phase === 'response' || (l.phase === null && l.routing_type !== null))
    .sort((a, b) => b.id - a.id);
}

export function latenceMesuree(l: VocalAuditEntry): number | null {
  return l.latency_ms !== null && l.latency_ms > 0 ? l.latency_ms : null;
}

export interface LatenceParChemin {
  chemin: CheminId;
  mediane: number | null;
  /** Échanges réussis dont la latence a été mesurée (la médiane porte sur eux seuls). */
  mesures: number;
  /** Tous les échanges de ce chemin dans l'échantillon. */
  total: number;
  /** Échanges en échec (délai dépassé, phrase d'excuse) : exclus de la médiane, qui sinon n'indiquerait que le délai maximal. */
  echecs: number;
}

export function latencesParChemin(echanges: VocalAuditEntry[]): Map<CheminId, LatenceParChemin> {
  const resultat = new Map<CheminId, { valeurs: number[]; total: number; echecs: number }>();
  for (const e of echanges) {
    const pris = cheminDepuisRouting(e.routing_type, e.source_mode);
    if (!pris.chemin) continue;
    const entree = resultat.get(pris.chemin) ?? { valeurs: [], total: 0, echecs: 0 };
    entree.total += 1;
    if (pris.echec) {
      entree.echecs += 1;
    } else {
      const l = latenceMesuree(e);
      if (l !== null) entree.valeurs.push(l);
    }
    resultat.set(pris.chemin, entree);
  }
  return new Map(
    [...resultat.entries()].map(([chemin, v]) => [
      chemin,
      { chemin, mediane: mediane(v.valeurs), mesures: v.valeurs.length, total: v.total, echecs: v.echecs },
    ]),
  );
}
