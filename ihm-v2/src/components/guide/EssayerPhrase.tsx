/**
 * « Essayer une phrase sans rien exécuter » : demande au moteur de montrer le chemin
 * qu'il choisirait pour une phrase, sans l'exécuter (`POST /api/vocal/simulate`).
 *
 * La zone n'apparaît que si le moteur expose cette route (lue dans son schéma
 * OpenAPI) ; sinon la page le dit. Aucune phrase n'est envoyée à la maison.
 */
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Play, ShieldCheck, TriangleAlert } from 'lucide-react';
import { ROUTE_SIMULATION, simulationDisponible, simulerPhrase } from '../../api/vocal';
import type { ModeVocal, SimulationVocale } from '../../api/vocal';
import { ApiError } from '../../api/client';
import { isRouteAbsente } from '../../api/helpers';
import { useRouteQuery } from '../../hooks/useRouteQuery';
import { cheminDepuisRouting } from '../../lib/carteMoteur';
import { PHRASES_ESSAI } from '../../lib/demoVocale';
import { Button, ErrorState, LoadingState, Pill } from '../ui/primitives';
import { RouteAbsente } from './etats';

/** Le moteur répond « indetermine » quand le chemin réel dépend de l'état du système (volet en mouvement, repli sur un modèle…). */
const CHEMIN_INDETERMINE = 'indetermine';

function CheminChoisi({ chemin }: { chemin: string | null | undefined }) {
  if (!chemin) return <>—</>;
  if (chemin === CHEMIN_INDETERMINE) {
    return <>Indéterminé : le chemin réel dépend de l’état du système (voir l’explication ci-dessous)</>;
  }
  const pris = cheminDepuisRouting(chemin);
  return (
    <>
      {pris.chemin ? `${pris.libelle} ` : ''}
      <code className="font-mono text-xs text-slate-400">({chemin})</code>
    </>
  );
}

function Resultat({ r }: { r: SimulationVocale }) {
  return (
    <div className="space-y-3 rounded-xl border border-sky-800/60 bg-sky-950/20 p-4">
      <p className="flex items-center gap-2 text-sm font-medium text-sky-300">
        <ShieldCheck className="h-4 w-4" aria-hidden />
        Simulation : rien n’a été envoyé à la maison.
      </p>
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-slate-500">Chemin choisi par le moteur</dt>
          <dd className="mt-0.5 break-words font-medium text-slate-100"><CheminChoisi chemin={r.chemin} /></dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Un modèle de langage serait-il appelé ?</dt>
          <dd className="mt-0.5 text-slate-100">{r.llm_requis == null ? 'inconnu' : r.llm_requis ? 'oui' : 'non'}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs text-slate-500">Action prévue</dt>
          <dd className="mt-0.5 text-slate-100">
            {r.action_prevue?.service || r.action_prevue?.entite ? (
              <span className="break-words font-mono text-xs">
                {r.action_prevue.service ?? '?'} → {r.action_prevue.entite ?? '?'}
              </span>
            ) : (
              'aucune'
            )}
          </dd>
        </div>
      </dl>
      {r.explication && <p className="text-sm leading-relaxed text-slate-300">{r.explication}</p>}
      {r.executerait_une_action ? (
        <p className="flex items-start gap-2 rounded-lg border border-amber-800/60 bg-amber-950/25 px-3 py-2 text-sm text-amber-200">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          Dite à l’écran, cette phrase agirait pour de vrai sur la maison.
        </p>
      ) : (
        <p className="text-sm text-slate-400">
          {r.executerait_une_action === false
            ? 'Dite à l’écran, cette phrase ne commanderait rien.'
            : 'Le moteur ne peut pas dire à l’avance si cette phrase agirait sur la maison.'}
        </p>
      )}
    </div>
  );
}

export function EssayerPhrase() {
  const dispo = useRouteQuery(['vocal', 'simulate', 'disponible'], simulationDisponible, { staleTime: 5 * 60_000 });
  const [texte, setTexte] = useState('');
  const [mode, setMode] = useState<ModeVocal>('ha');
  const simulation = useMutation({ mutationFn: () => simulerPhrase(texte.trim(), mode) });

  if (dispo.isPending) return <LoadingState label="Recherche de la simulation sur ce moteur…" />;
  if (dispo.data === false) return <RouteAbsente route={ROUTE_SIMULATION} />;
  if (dispo.data === null || dispo.data === undefined) {
    return (
      <div role="status" className="rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm text-slate-400">
        Impossible de savoir si ce moteur propose la simulation : son schéma (<code className="font-mono text-xs">/openapi.json</code>)
        n’a pas pu être lu. La zone d’essai n’est donc pas proposée.
      </div>
    );
  }

  const erreur = simulation.error;
  return (
    <div className="space-y-4">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (texte.trim()) simulation.mutate();
        }}
      >
        <div>
          <label htmlFor="phrase-essai" className="text-sm font-medium text-slate-200">Phrase à essayer</label>
          <input
            id="phrase-essai"
            type="text"
            value={texte}
            maxLength={300}
            onChange={(e) => setTexte(e.target.value)}
            placeholder="Par exemple : Quelle heure est-il ?"
            className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-500"
          />
        </div>
        <fieldset>
          <legend className="text-sm font-medium text-slate-200">Comme si elle passait par</legend>
          <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1.5 text-sm text-slate-300">
            <label className="inline-flex cursor-pointer items-center gap-2">
              <input type="radio" name="mode-essai" checked={mode === 'ha'} onChange={() => setMode('ha')} className="accent-sky-500" />
              le pipeline Domotique (mode ha)
            </label>
            <label className="inline-flex cursor-pointer items-center gap-2">
              <input type="radio" name="mode-essai" checked={mode === 'chat'} onChange={() => setMode('chat')} className="accent-sky-500" />
              le pipeline Discussion (mode chat)
            </label>
          </div>
        </fieldset>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={!texte.trim() || simulation.isPending}>
            <Play className="h-4 w-4" aria-hidden />
            {simulation.isPending ? 'Simulation…' : 'Voir le chemin choisi'}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>Exemples :</span>
          {PHRASES_ESSAI.map((p) => (
            <button
              key={p.texte}
              type="button"
              onClick={() => { setTexte(p.texte); setMode(p.mode); simulation.reset(); }}
              className="rounded-full border border-slate-700 px-2.5 py-1 text-slate-300 transition-colors hover:border-sky-600 hover:text-sky-300"
            >
              {p.texte}{p.note ? ` (${p.note})` : ''}
            </button>
          ))}
        </div>
      </form>

      {erreur ? (
        isRouteAbsente(erreur) ? (
          <RouteAbsente route={ROUTE_SIMULATION} />
        ) : (
          <ErrorState error={erreur instanceof ApiError ? erreur : new Error(String(erreur))} />
        )
      ) : simulation.data ? (
        <Resultat r={simulation.data} />
      ) : (
        <p className="text-sm text-slate-500">
          Le moteur indiquera le chemin qu’il choisirait <Pill tone="info">sans l’exécuter</Pill>.
        </p>
      )}
    </div>
  );
}
