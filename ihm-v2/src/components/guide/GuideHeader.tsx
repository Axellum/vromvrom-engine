/**
 * En-tête commun des vues « comprendre et régler » :
 *  - le titre ;
 *  - UNE phrase qui dit à quoi sert la page ;
 *  - un bloc repliable « Comprendre cette page » (ouvert à la première visite,
 *    puis la préférence est retenue dans le navigateur) ;
 *  - la liste des routes d'où viennent les données.
 *
 * `ExplicationPage` est la même chose sans titre : on l'ajoute sous l'en-tête
 * d'une vue existante sans toucher à sa logique.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { ChevronDown, ChevronRight, Database, HelpCircle } from 'lucide-react';

export interface SourceDonnees {
  route: string;
  /** Ce que cette route apporte à la page. */
  role?: string;
}

function lirePliage(cle: string): boolean {
  try {
    return localStorage.getItem(`moteur_ui_comprendre_${cle}`) !== 'ferme';
  } catch {
    return true;
  }
}

function ecrirePliage(cle: string, ouvert: boolean) {
  try {
    localStorage.setItem(`moteur_ui_comprendre_${cle}`, ouvert ? 'ouvert' : 'ferme');
  } catch {
    /* préférence non retenue : sans conséquence */
  }
}

function BlocComprendre({ cle, children }: { cle: string; children: ReactNode }) {
  const [ouvert, setOuvert] = useState(() => lirePliage(cle));
  const idRegion = `comprendre-${cle}`;
  const Chevron = ouvert ? ChevronDown : ChevronRight;
  return (
    <div className="rounded-lg border border-sky-900/50 bg-sky-950/20">
      <button
        type="button"
        aria-expanded={ouvert}
        aria-controls={idRegion}
        onClick={() => {
          setOuvert(!ouvert);
          ecrirePliage(cle, !ouvert);
        }}
        className="flex w-full items-center gap-2 rounded-lg px-4 py-2.5 text-left text-sm font-medium text-sky-300 transition-colors hover:bg-sky-950/40"
      >
        <HelpCircle className="h-4 w-4 shrink-0" aria-hidden />
        <span className="flex-1">Comprendre cette page</span>
        <Chevron className="h-4 w-4 shrink-0" aria-hidden />
      </button>
      {ouvert && (
        <div id={idRegion} className="space-y-2 px-4 pb-4 text-sm leading-relaxed text-slate-400">
          {children}
        </div>
      )}
    </div>
  );
}

export function SourcesDonnees({ sources }: { sources: SourceDonnees[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5 text-xs text-slate-500">
      <span className="inline-flex items-center gap-1.5 font-medium text-slate-400">
        <Database className="h-3.5 w-3.5" aria-hidden />
        Sources des données
      </span>
      <ul className="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
        {sources.map((s) => (
          <li key={s.route} className="min-w-0 break-words">
            <code className="rounded bg-slate-800/70 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">{s.route}</code>
            {s.role && <span className="ml-1.5 text-slate-500">{s.role}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface ExplicationProps {
  /** Identifiant stable, sert à retenir si le bloc est replié. */
  cle: string;
  /** Une phrase : à quoi sert cette page. */
  objectif: ReactNode;
  comprendre?: ReactNode;
  sources?: SourceDonnees[];
  /** Marge externe : `mb-6` par défaut ; vide quand le parent espace déjà ses enfants (`space-y-*`). */
  className?: string;
}

export function ExplicationPage({ cle, objectif, comprendre, sources, className = 'mb-6' }: ExplicationProps) {
  return (
    <div className={`space-y-3 ${className}`}>
      <p className="max-w-3xl text-base leading-relaxed text-slate-300">{objectif}</p>
      {comprendre && <BlocComprendre cle={cle}>{comprendre}</BlocComprendre>}
      {sources && <SourcesDonnees sources={sources} />}
    </div>
  );
}

export function GuideHeader({
  cle,
  icone: Icone,
  titre,
  objectif,
  comprendre,
  sources,
  actions,
}: ExplicationProps & { icone: LucideIcon; titre: string; actions?: ReactNode }) {
  return (
    <header className="mb-6 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-500/15 text-sky-300">
            <Icone className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-slate-100">{titre}</h1>
            <p className="mt-1 max-w-3xl text-base leading-relaxed text-slate-300">{objectif}</p>
          </div>
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {comprendre && <BlocComprendre cle={cle}>{comprendre}</BlocComprendre>}
      {sources && <SourcesDonnees sources={sources} />}
    </header>
  );
}
