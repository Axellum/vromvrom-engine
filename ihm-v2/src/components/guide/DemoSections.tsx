/**
 * Sections de la page « Démo vocale & maison » qui reposent sur le contenu vérifié
 * de `lib/demoVocale.ts` : trajet d'une phrase, durées mesurées, trois chemins,
 * tableau « Je veux… », checklist de tournage, dépannage, limites.
 *
 * Mise en page : aucune table large. Sur petit écran chaque ligne devient une carte,
 * pour qu'aucun défilement horizontal ne soit nécessaire.
 */
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { CheckCircle2, ChevronRight, Circle, Search } from 'lucide-react';
import {
  CHECKLIST_AVANT, CHEMINS_DEMO, DATE_MESURE, DEPANNAGE, ETAPES_TRAJET, LIMITES, MESURES_DUREE,
  REGLAGES, RETOUR_NORMAL,
} from '../../lib/demoVocale';
import type { EtapeTrajet, Visibilite } from '../../lib/demoVocale';
import { formatDureeMs, formatNombre, pluriel } from '../../lib/format';
import type { LatenceParChemin } from '../../lib/audit';
import type { CheminId } from '../../lib/carteMoteur';
import { Card, Pill } from '../ui/primitives';
import { Glose } from './Glose';

/* ── Trajet d'une phrase ─────────────────────────────────────────────────── */

const VISIBILITE: Record<Visibilite, { libelle: string; tone: 'good' | 'info' | 'neutral' }> = {
  'direct-moteur': { libelle: 'Visible par le moteur', tone: 'good' },
  'direct-ha': { libelle: 'Visible via Home Assistant', tone: 'info' },
  invisible: { libelle: 'Invisible depuis le moteur', tone: 'neutral' },
};

export function TrajetPhrase({ etatEnDirect }: { etatEnDirect: Partial<Record<number, ReactNode>> }) {
  return (
    <ol className="relative space-y-0">
      {ETAPES_TRAJET.map((e: EtapeTrajet, i) => {
        const Icone = e.icone;
        const v = VISIBILITE[e.visibilite];
        const dernier = i === ETAPES_TRAJET.length - 1;
        return (
          <li key={e.numero} className="relative flex gap-4 pb-5 last:pb-0">
            {!dernier && <span className="absolute bottom-0 left-[17px] top-9 w-px bg-slate-800" aria-hidden />}
            <span className="relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-sky-300">
              <Icone className="h-4 w-4" aria-hidden />
              <span className="sr-only">Étape {e.numero}</span>
            </span>
            <div className="min-w-0 flex-1 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                <h3 className="min-w-0 break-words text-base font-semibold text-slate-100">
                  <span className="mr-1.5 text-slate-500">{e.numero}.</span>
                  {e.titre}
                </h3>
                <Pill tone={v.tone}>{v.libelle}</Pill>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">{e.lieu}</p>
              <p className="mt-2 text-sm leading-relaxed text-slate-400"><Glose texte={e.resume} /></p>
              <p className="mt-2 text-sm text-slate-300">
                <span className="font-medium text-slate-200">Durée mesurée le {DATE_MESURE} : </span>
                {e.duree}
              </p>
              {etatEnDirect[e.numero] && (
                <div className="mt-2.5 border-t border-slate-800 pt-2.5 text-sm">{etatEnDirect[e.numero]}</div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/* ── Durées mesurées ─────────────────────────────────────────────────────── */

export function DureesMesurees() {
  const max = 10; // échelle de la barre, en secondes
  return (
    <div>
      <ul className="space-y-3.5">
        {MESURES_DUREE.map((m) => (
          <li key={m.libelle}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
              <span className="min-w-0 break-words text-sm font-medium text-slate-200">{m.libelle}</span>
              <span className="shrink-0 font-mono text-sm text-sky-300">{m.valeur}</span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-800" aria-hidden>
              <div
                className="h-full rounded-full bg-sky-500"
                style={{ width: `${Math.max(1.5, Math.min(100, (m.barre / max) * 100))}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-slate-500">{m.detail}</p>
          </li>
        ))}
      </ul>
      <p className="mt-4 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2.5 text-xs leading-relaxed text-slate-400">
        Mesures du {DATE_MESURE} (échantillons de 4 à 10 passages : ordres de grandeur, pas des garanties). Ce sont des{' '}
        <strong className="text-slate-300">médianes indépendantes</strong> : elles ne s’additionnent pas. Les barres sont à la
        même échelle, de 0 à 10 secondes.
      </p>
    </div>
  );
}

/* ── Trois chemins ───────────────────────────────────────────────────────── */

export function TroisChemins({ latences }: { latences: Map<CheminId, LatenceParChemin> }) {
  const cleVersChemin = { commande: 'p-commande', question: 'p-question', discussion: 'p-discussion' } as const;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {CHEMINS_DEMO.map((c) => {
        const live = latences.get(cleVersChemin[c.cle]);
        return (
          <article key={c.cle} className="flex min-w-0 flex-col rounded-xl border border-slate-800 bg-slate-900/50 p-5">
            <div className="flex items-center gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky-600 text-sm font-semibold text-white">
                {c.lettre}
              </span>
              <h3 className="min-w-0 break-words text-base font-semibold text-slate-100">{c.titre}</h3>
            </div>
            <p className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">Exemples</p>
            <ul className="mt-1 space-y-1">
              {c.exemples.map((x) => (
                <li key={x} className="text-sm italic text-slate-300">« {x} »</li>
              ))}
            </ul>
            <p className="mt-3 text-sm leading-relaxed text-slate-400"><Glose texte={c.fonctionnement} /></p>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              <span className="font-medium text-slate-300">Garde-fou. </span>{c.garde}
            </p>
            <div className="mt-auto space-y-1 pt-4 text-sm">
              <p className="text-slate-300">
                <span className="font-medium text-slate-200">Mesuré le {DATE_MESURE} : </span>{c.duree}
              </p>
              <p className="text-slate-300">
                <span className="font-medium text-slate-200">En direct : </span>
                {live && live.mediane !== null ? (
                  <>
                    médiane {formatDureeMs(live.mediane)} sur {formatNombre(live.mesures)} {pluriel(live.mesures, 'échange mesuré', 'échanges mesurés')}
                    {live.echecs > 0 && ` (${formatNombre(live.echecs)} en échec, exclus)`}
                  </>
                ) : (
                  <span className="text-slate-500">pas assez d’échanges récents mesurés</span>
                )}
              </p>
            </div>
          </article>
        );
      })}
    </div>
  );
}

/* ── Je veux… → où → comment ─────────────────────────────────────────────── */

export function TableauReglages() {
  const [filtre, setFiltre] = useState('');
  const lignes = useMemo(() => {
    const f = filtre.trim().toLowerCase();
    if (!f) return REGLAGES;
    return REGLAGES.filter((r) => `${r.veux} ${r.ou} ${r.comment} ${r.effet}`.toLowerCase().includes(f));
  }, [filtre]);
  return (
    <div className="space-y-3">
      <label className="relative block max-w-md">
        <span className="sr-only">Filtrer les réglages</span>
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" aria-hidden />
        <input
          type="search"
          value={filtre}
          onChange={(e) => setFiltre(e.target.value)}
          placeholder="Filtrer : volume, langue, voix, modèle…"
          className="w-full rounded-lg border border-slate-700 bg-slate-900 py-2 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-500"
        />
      </label>
      {lignes.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-500">Aucun réglage ne correspond à « {filtre} ».</p>
      ) : (
        <ul className="space-y-2">
          {lignes.map((r) => (
            <li key={r.veux} className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <p className="text-sm font-semibold text-slate-100">
                <span className="text-sky-400">Je veux </span>{r.veux.charAt(0).toLowerCase() + r.veux.slice(1)}
              </p>
              <dl className="mt-2 grid gap-x-6 gap-y-2 text-sm md:grid-cols-3">
                <div className="min-w-0">
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Où</dt>
                  <dd className="mt-0.5 break-words leading-relaxed text-slate-300">{r.ou}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Comment</dt>
                  <dd className="mt-0.5 break-words leading-relaxed text-slate-300">{r.comment}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Effet</dt>
                  <dd className="mt-0.5 break-words leading-relaxed text-slate-400">{r.effet}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ── Avant et après un tournage ──────────────────────────────────────────── */

export type ControleEnDirect = 'ok' | 'ko' | 'attente' | 'inconnu';

export function ChecklistTournage({ controles }: { controles: Record<'moteur' | 'ha', ControleEnDirect> }) {
  const [coches, setCoches] = useState<Set<string>>(() => new Set());
  const bascule = (id: string) =>
    setCoches((c) => {
      const n = new Set(c);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-400" aria-live="polite">
        {coches.size} point{coches.size > 1 ? 's' : ''} coché{coches.size > 1 ? 's' : ''} sur {CHECKLIST_AVANT.length}. Les cases
        ne sont pas enregistrées : elles servent à suivre votre préparation.
      </p>
      <ul className="space-y-2">
        {CHECKLIST_AVANT.map((p, i) => {
          const fait = coches.has(p.id);
          const ctl = p.controle ? controles[p.controle] : null;
          return (
            <li key={p.id}>
              <label
                className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors ${
                  fait ? 'border-emerald-800/60 bg-emerald-950/15' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'
                }`}
              >
                <input type="checkbox" checked={fait} onChange={() => bascule(p.id)} className="peer sr-only" />
                {fait ? (
                  <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" aria-hidden />
                ) : (
                  <Circle className="mt-0.5 h-5 w-5 shrink-0 text-slate-600 peer-focus-visible:text-sky-400" aria-hidden />
                )}
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-sm font-semibold text-slate-100">{i + 1}. {p.titre}</span>
                    {ctl && (
                      <Pill tone={ctl === 'ok' ? 'good' : ctl === 'ko' ? 'bad' : 'neutral'}>
                        {ctl === 'ok' ? 'vérifié à l’instant : ok' : ctl === 'ko' ? 'vérifié à l’instant : échec' : ctl === 'attente' ? 'vérification en cours…' : 'non vérifiable'}
                      </Pill>
                    )}
                  </span>
                  <span className="mt-1 block text-sm leading-relaxed text-slate-400">{p.detail}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function RetourALaNormale() {
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-slate-400">
        Relevé fait le {DATE_MESURE}, en fin d’après-midi, dans Home Assistant (lecture seule). Cette page ne voit pas Home
        Assistant à ce niveau de détail : <strong className="text-slate-300">revérifiez chaque point avant d’agir</strong>.
        Avant toute suppression, notez l’état (alias, réglages du pipeline) : rien ne doit se perdre.
      </p>
      <ul className="space-y-2">
        {RETOUR_NORMAL.map((r) => (
          <li key={r.quoi} className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <p className="text-sm font-semibold text-slate-100">{r.quoi}</p>
            <p className="mt-0.5 text-xs text-amber-300/90">État relevé le {DATE_MESURE} : {r.etat}</p>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{r.comment}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── Dépannage et limites ────────────────────────────────────────────────── */

export function ListeDepannage() {
  return (
    <ul className="space-y-2">
      {DEPANNAGE.map((d) => (
        <li key={d.symptome}>
          <details className="group rounded-xl border border-slate-800 bg-slate-900/50 open:border-slate-700">
            <summary className="flex cursor-pointer select-none items-start gap-2 rounded-xl px-4 py-3 text-sm font-medium text-slate-100 marker:content-none">
              <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-sky-400 transition-transform group-open:rotate-90" aria-hidden />
              <span className="min-w-0 break-words">{d.symptome}</span>
            </summary>
            <dl className="space-y-2 border-t border-slate-800 px-4 py-3 pl-10 text-sm">
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Cause probable</dt>
                <dd className="mt-0.5 leading-relaxed text-slate-300">{d.cause}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Où regarder</dt>
                <dd className="mt-0.5 leading-relaxed text-slate-400">{d.regarder}</dd>
              </div>
            </dl>
          </details>
        </li>
      ))}
    </ul>
  );
}

export function ListeLimites() {
  return (
    <Card title="Limites connues et précautions" subtitle={`Constats du ${DATE_MESURE}. Rien de ceci n’est caché : mieux vaut le savoir avant de filmer.`}>
      <ul className="space-y-2.5">
        {LIMITES.map((l) => (
          <li key={l.slice(0, 40)} className="flex gap-2.5 text-sm leading-relaxed text-slate-400">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600" aria-hidden />
            <span className="min-w-0"><Glose texte={l} /></span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
