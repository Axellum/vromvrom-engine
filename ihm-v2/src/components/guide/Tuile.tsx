import type { ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import { hashForView } from '../../lib/navigation';
import type { ViewKey } from '../../lib/navigation';

export type TonTuile = 'neutral' | 'good' | 'warn' | 'bad' | 'info';

const COULEUR_VALEUR: Record<TonTuile, string> = {
  neutral: 'text-slate-100',
  good: 'text-emerald-400',
  warn: 'text-amber-400',
  bad: 'text-red-400',
  info: 'text-sky-400',
};

const BORDURE: Record<TonTuile, string> = {
  neutral: 'border-slate-800',
  good: 'border-slate-800',
  warn: 'border-amber-900/60',
  bad: 'border-red-900/70',
  info: 'border-slate-800',
};

/**
 * Tuile d'indicateur : un chiffre réel, ce qu'il veut dire en une phrase, et
 * d'où il vient. `valeur` à `null` s'affiche « — » (inconnu), jamais 0.
 */
export function Tuile({
  titre,
  valeur,
  detail,
  ton = 'neutral',
  signification,
  source,
  lien,
}: {
  titre: ReactNode;
  valeur: string | null;
  detail?: ReactNode;
  ton?: TonTuile;
  /** Ce que le chiffre veut dire, en langage courant. */
  signification: ReactNode;
  /** Route et champ d'où vient le chiffre. */
  source: string;
  lien?: { vue: ViewKey; texte: string };
}) {
  return (
    <article className={`flex min-w-0 flex-col rounded-xl border bg-slate-900/50 p-4 ${BORDURE[ton]}`}>
      <h3 className="text-sm font-medium text-slate-400">{titre}</h3>
      <p className={`mt-1.5 break-words text-3xl font-semibold tracking-tight ${valeur === null ? 'text-slate-600' : COULEUR_VALEUR[ton]}`}>
        {valeur ?? '—'}
      </p>
      {valeur === null && <p className="mt-0.5 text-xs text-slate-500">Inconnu : le moteur ne le mesure pas.</p>}
      {detail && <div className="mt-1.5 text-sm leading-snug text-slate-300">{detail}</div>}
      <div className="mt-auto pt-3">
      <div className="space-y-1.5 border-t border-slate-800/80 pt-3 text-xs leading-relaxed">
        <p className="text-slate-400">
          <span className="font-medium text-slate-300">Ce que ça veut dire. </span>
          {signification}
        </p>
        <p className="break-words text-slate-500">
          <span className="font-medium text-slate-400">D’où ça vient. </span>
          <code className="font-mono text-[11px]">{source}</code>
        </p>
        {lien && (
          <a
            href={hashForView(lien.vue)}
            className="inline-flex items-center gap-1 pt-0.5 font-medium text-sky-400 transition-colors hover:text-sky-300"
          >
            {lien.texte}
            <ArrowRight className="h-3 w-3" aria-hidden />
          </a>
        )}
      </div>
      </div>
    </article>
  );
}
