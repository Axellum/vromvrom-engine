/** Mises en forme en français : durées, dates relatives, montants, octets. */

const NF = new Intl.NumberFormat('fr-FR');

export function formatNombre(n: number | null | undefined): string {
  return n === null || n === undefined || Number.isNaN(n) ? '—' : NF.format(n);
}

/** 145 → « 145 ms » ; 3038 → « 3,0 s ». */
export function formatDureeMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} s`;
}

/** 273600 → « 3 j 4 h » ; 8100 → « 2 h 15 min ». */
export function formatDureeSec(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s) || s < 0) return '—';
  const j = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (j > 0) return h > 0 ? `${j} j ${h} h` : `${j} j`;
  if (h > 0) return m > 0 ? `${h} h ${m} min` : `${h} h`;
  if (m > 0) return `${m} min`;
  return `${Math.floor(s)} s`;
}

export function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  if (n > 0 && n < 0.01) return '< 0,01 $';
  return `${n.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`;
}

export function formatOctets(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const unites = ['o', 'Ko', 'Mo', 'Go', 'To'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < unites.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toLocaleString('fr-FR', { maximumFractionDigits: i === 0 ? 0 : 1 })} ${unites[i]}`;
}

/**
 * Convertit une valeur de date quelconque en `Date` : texte ISO, secondes ou
 * millisecondes depuis 1970, ou objet portant une date. Renvoie `null` si rien
 * d'exploitable (on n'invente jamais une date).
 */
export function parseDate(v: unknown): Date | null {
  if (v === null || v === undefined || v === '') return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v;
  if (typeof v === 'number') {
    const d = new Date(v < 1e11 ? v * 1000 : v);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (typeof v === 'string') {
    // « 2026-11-01 13:13:00 » (heure locale du moteur) : on remplace l'espace pour Date.
    const d = new Date(/^\d{4}-\d{2}-\d{2} \d/.test(v) ? v.replace(' ', 'T') : v);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    for (const cle of ['cree_le', 'created_at', 'date', 'horodatage', 'timestamp']) {
      if (cle in o) {
        const d = parseDate(o[cle]);
        if (d) return d;
      }
    }
  }
  return null;
}

/** « il y a 2 h », « il y a 3 min », « à l’instant ». */
export function ilYA(date: Date | null, maintenant: number = Date.now()): string {
  if (!date) return '—';
  const s = Math.round((maintenant - date.getTime()) / 1000);
  if (s < -60) return 'dans le futur (horloge à vérifier)';
  if (s < 45) return 'à l’instant';
  if (s < 3600) return `il y a ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  const j = Math.round(s / 86400);
  return `il y a ${j} j`;
}

export function formatDateHeure(date: Date | null): string {
  if (!date) return '—';
  return date.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function pluriel(n: number, singulier: string, plur?: string): string {
  return n > 1 ? plur ?? `${singulier}s` : singulier;
}

/** Médiane d'une liste de nombres (liste vide → `null`). */
export function mediane(valeurs: number[]): number | null {
  if (valeurs.length === 0) return null;
  const t = [...valeurs].sort((a, b) => a - b);
  const m = Math.floor(t.length / 2);
  return t.length % 2 ? t[m]! : (t[m - 1]! + t[m]!) / 2;
}
