/**
 * « Rejouer une vraie demande » : liste des derniers échanges vocaux réels
 * (journal d'audit) et bandeau décrivant le chemin pris par celui qu'on choisit.
 */
import { CircleAlert, RotateCcw } from 'lucide-react';
import type { VocalAuditEntry } from '../../api/ha';
import { latenceMesuree } from '../../lib/audit';
import { LIBELLE_CHEMIN, cheminDepuisRouting } from '../../lib/carteMoteur';
import type { CheminPris } from '../../lib/carteMoteur';
import { formatDateHeure, formatDureeMs, ilYA, parseDate } from '../../lib/format';
import { Button, Pill } from '../ui/primitives';

function PastilleChemin({ pris }: { pris: CheminPris }) {
  const tone = pris.echec ? 'bad' : pris.chemin === null ? 'neutral' : 'info';
  return <Pill tone={tone}>{pris.chemin ? LIBELLE_CHEMIN[pris.chemin] : pris.libelle}{pris.echec && pris.chemin ? ' — échec' : ''}</Pill>;
}

export function ListeRejeu({
  echanges, selectionId, onSelect,
}: {
  echanges: VocalAuditEntry[];
  selectionId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <ul className="divide-y divide-slate-800/70 overflow-hidden rounded-lg border border-slate-800">
      {echanges.slice(0, 12).map((e) => {
        const pris = cheminDepuisRouting(e.routing_type, e.source_mode);
        const actif = e.id === selectionId;
        const latence = latenceMesuree(e);
        return (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onSelect(e.id)}
              aria-pressed={actif}
              className={`flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors sm:flex-row sm:items-center sm:gap-4 ${
                actif ? 'bg-emerald-950/30' : 'hover:bg-slate-900/70'
              }`}
            >
              <span className="shrink-0 text-xs text-slate-500 sm:w-28" title={formatDateHeure(parseDate(e.created_at))}>
                {ilYA(parseDate(e.created_at))}
              </span>
              <span className="min-w-0 flex-1 break-words text-sm text-slate-200">
                {e.user_prompt ? `« ${e.user_prompt} »` : <span className="text-slate-500">(texte non enregistré)</span>}
              </span>
              <span className="flex shrink-0 flex-wrap items-center gap-2">
                <PastilleChemin pris={pris} />
                <span className="font-mono text-xs text-slate-400">{latence !== null ? formatDureeMs(latence) : 'durée non mesurée'}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function BandeauRejeu({
  echange, pris, onQuitter,
}: {
  echange: VocalAuditEntry;
  pris: CheminPris;
  onQuitter: () => void;
}) {
  const latence = latenceMesuree(echange);
  return (
    <section
      aria-label="Échange rejoué"
      className={`rounded-xl border p-4 sm:p-5 ${pris.echec ? 'border-red-800/70 bg-red-950/20' : 'border-emerald-800/60 bg-emerald-950/20'}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Échange rejoué sur la carte</p>
        <Button variant="secondary" onClick={onQuitter}>
          <RotateCcw className="h-4 w-4" aria-hidden />
          Quitter le rejeu
        </Button>
      </div>
      <p className="mt-2 break-words text-lg font-semibold text-slate-100">
        {echange.user_prompt ? `« ${echange.user_prompt} »` : 'Texte non enregistré'}
      </p>
      <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-xs text-slate-500">Chemin pris</dt>
          <dd className="mt-0.5 flex items-center gap-1.5 text-slate-100">
            {pris.echec && <CircleAlert className="h-4 w-4 text-red-400" aria-hidden />}
            {pris.libelle}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Durée côté moteur</dt>
          <dd className="mt-0.5 font-mono text-slate-100">{latence !== null ? formatDureeMs(latence) : 'non mesurée'}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Mode demandé</dt>
          <dd className="mt-0.5 text-slate-100">
            {echange.source_mode === 'ha' ? 'Domotique (ha)' : echange.source_mode === 'chat' ? 'Discussion (chat)' : echange.source_mode ?? '—'}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Type de routage enregistré</dt>
          <dd className="mt-0.5 break-all font-mono text-xs text-slate-300">{echange.routing_type ?? '—'}</dd>
        </div>
      </dl>
      <p className="mt-3 text-sm leading-relaxed text-slate-300">{pris.explication}</p>
      {echange.response_text && (
        <p className="mt-3 break-words rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2 text-sm text-slate-400">
          <span className="font-medium text-slate-300">Réponse du moteur : </span>
          {echange.response_text.length > 220 ? `${echange.response_text.slice(0, 220)}…` : echange.response_text}
        </p>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Les blocs en vert sont ceux que cet échange a traversés ; les autres sont atténués.
      </p>
    </section>
  );
}
