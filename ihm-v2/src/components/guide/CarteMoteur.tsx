/**
 * Carte interactive du trajet d'une demande : Entrées → Routeur → Chemins →
 * Modèles → Actions → Traces.
 *
 * Sur grand écran les six étapes se lisent de gauche à droite ; sur petit écran
 * elles s'empilent de haut en bas (aucun défilement horizontal). Chaque bloc est un
 * bouton : il ouvre son panneau d'explication. Quand un échange réel est rejoué,
 * les blocs du chemin pris sont mis en évidence et les autres atténués.
 */
import { Fragment } from 'react';
import { ArrowDown, ArrowRight } from 'lucide-react';
import { ETAPES, NOEUDS } from '../../lib/carteMoteur';
import type { NoeudDef, NoeudId } from '../../lib/carteMoteur';

interface Props {
  selection: NoeudId | null;
  onSelect: (id: NoeudId) => void;
  /** Nœuds du chemin rejoué (ordre du trajet), ou `null` hors rejeu. */
  chemin: NoeudId[] | null;
  /** Nœud où l'échange rejoué a échoué. */
  noeudEnEchec?: NoeudId | null;
  /** Aperçu de la cascade réelle (premiers modèles), affiché sur le bloc « Cascade ». */
  apercuCascade?: string | null;
}

function Bloc({
  noeud, selectionne, surligne, attenue, enEchec, onSelect, apercu,
}: {
  noeud: NoeudDef;
  selectionne: boolean;
  surligne: boolean;
  attenue: boolean;
  enEchec: boolean;
  onSelect: () => void;
  apercu?: string | null;
}) {
  const Icone = noeud.icone;
  const etat = enEchec
    ? 'border-red-500 bg-red-950/40'
    : surligne
      ? 'border-emerald-500 bg-emerald-950/30'
      : selectionne
        ? 'border-sky-500 bg-sky-950/40'
        : 'border-slate-800 bg-slate-900/60 hover:border-slate-600 hover:bg-slate-900';
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selectionne}
      aria-label={`${noeud.titre} : ${noeud.court}${surligne ? ' (chemin pris)' : ''}${enEchec ? ' (en échec)' : ''}`}
      className={`w-full min-w-0 rounded-lg border px-3 py-2.5 text-left transition-colors ${etat} ${
        selectionne ? 'ring-2 ring-sky-500/50' : ''
      } ${attenue ? 'opacity-40' : ''}`}
    >
      <span className="flex items-start gap-2">
        <Icone className={`mt-0.5 h-4 w-4 shrink-0 ${enEchec ? 'text-red-400' : surligne ? 'text-emerald-400' : 'text-sky-400'}`} aria-hidden />
        <span className="min-w-0 break-words text-sm font-medium leading-snug text-slate-100">{noeud.titre}</span>
      </span>
      <span className="mt-1 block break-words text-xs leading-snug text-slate-400">
        {noeud.id === 'cascade' && apercu ? apercu : noeud.court}
      </span>
    </button>
  );
}

export function CarteMoteur({ selection, onSelect, chemin, noeudEnEchec, apercuCascade }: Props) {
  const ensemble = chemin ? new Set<NoeudId>(chemin) : null;
  return (
    <div
      role="group"
      aria-label="Carte du trajet d’une demande, de l’entrée à la trace"
      className="flex flex-col items-stretch gap-1 xl:flex-row xl:items-stretch xl:gap-0"
    >
      {ETAPES.map((etape, i) => (
        <Fragment key={etape.id}>
          {i > 0 && (
            <div className="flex shrink-0 items-center justify-center py-1 text-slate-600 xl:w-6 xl:py-0" aria-hidden>
              <ArrowDown className="h-4 w-4 xl:hidden" />
              <ArrowRight className="hidden h-4 w-4 xl:block" />
            </div>
          )}
          <section aria-label={`Étape ${etape.numero} : ${etape.titre}`} className="min-w-0 flex-1">
            <header className="mb-2 px-0.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                {etape.numero} · {etape.titre}
              </p>
              <p className="text-xs text-slate-500">{etape.sousTitre}</p>
            </header>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              {NOEUDS.filter((n) => n.etape === etape.id).map((n) => (
                <Bloc
                  key={n.id}
                  noeud={n}
                  selectionne={selection === n.id}
                  surligne={ensemble?.has(n.id) ?? false}
                  attenue={ensemble !== null && !ensemble.has(n.id)}
                  enEchec={noeudEnEchec === n.id}
                  onSelect={() => onSelect(n.id)}
                  apercu={apercuCascade}
                />
              ))}
            </div>
          </section>
        </Fragment>
      ))}
    </div>
  );
}
