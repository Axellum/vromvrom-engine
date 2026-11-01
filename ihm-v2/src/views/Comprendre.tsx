/**
 * Comprendre le moteur : le trajet d'une demande sur une carte cliquable, puis le
 * rejeu d'un vrai échange vocal pour voir le chemin réellement pris.
 *
 * La carte est un schéma fixe ; ce qui est vivant, c'est l'ordre réel des modèles
 * (`/api/guide/cascade`), les chiffres du panneau de chaque bloc et le rejeu
 * (`/api/vocal/audit`). Quand une route manque, la page le dit sans rien inventer.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Map as MapIcon, MousePointerClick } from 'lucide-react';
import { fetchGuideCascade } from '../api/guide';
import { fetchVocalAudit } from '../api/ha';
import { useRouteQuery } from '../hooks/useRouteQuery';
import { echangesRecents } from '../lib/audit';
import { NOEUD_PAR_ID, cheminDepuisRouting } from '../lib/carteMoteur';
import type { NoeudId } from '../lib/carteMoteur';
import { GLOSSAIRE } from '../lib/glossaire';
import type { TermeDef } from '../lib/glossaire';
import { Card } from '../components/ui/primitives';
import { GuideHeader } from '../components/guide/GuideHeader';
import { DataGate } from '../components/guide/etats';
import { CarteMoteur } from '../components/guide/CarteMoteur';
import { PanneauNoeud } from '../components/guide/PanneauNoeud';
import { BandeauRejeu, ListeRejeu } from '../components/guide/Rejeu';
import { LienVue } from '../components/guide/LienVue';

function defilerVers(el: HTMLElement | null) {
  if (!el) return;
  const reduit = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduit ? 'auto' : 'smooth', block: 'nearest' });
}

function GlossaireComplet() {
  const entrees = (Object.values(GLOSSAIRE) as TermeDef[]).sort((a, b) => a.terme.localeCompare(b.terme, 'fr'));
  return (
    <details className="group rounded-xl border border-slate-800 bg-slate-900/50">
      <summary className="cursor-pointer select-none rounded-xl px-5 py-4 text-base font-semibold text-slate-100 marker:text-sky-400">
        Les mots du moteur <span className="text-sm font-normal text-slate-500">({entrees.length} termes)</span>
      </summary>
      <dl className="grid gap-x-8 gap-y-4 border-t border-slate-800 px-5 py-4 md:grid-cols-2">
        {entrees.map((e) => (
          <div key={e.terme} className="min-w-0">
            <dt className="text-sm font-semibold text-sky-300">{e.terme}</dt>
            <dd className="mt-0.5 text-sm leading-relaxed text-slate-400">{e.definition}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export default function Comprendre() {
  const [selection, setSelection] = useState<NoeudId | null>(null);
  const [rejeuId, setRejeuId] = useState<number | null>(null);
  const panneau = useRef<HTMLDivElement>(null);
  const carte = useRef<HTMLDivElement>(null);

  const cascade = useRouteQuery(['guide', 'cascade'], fetchGuideCascade);
  const audit = useRouteQuery(['vocal', 'audit', 200], () => fetchVocalAudit(200));

  const echanges = useMemo(() => (audit.data ? echangesRecents(audit.data.logs) : []), [audit.data]);
  const rejoue = rejeuId !== null ? echanges.find((e) => e.id === rejeuId) ?? null : null;
  const pris = rejoue ? cheminDepuisRouting(rejoue.routing_type, rejoue.source_mode) : null;

  const premiers = cascade.data?.fast_path_providers?.slice(0, 3) ?? [];
  const apercuCascade = premiers.length > 0 ? `${premiers.join(' → ')}${(cascade.data?.fast_path_providers?.length ?? 0) > 3 ? ' …' : ''}` : null;

  useEffect(() => {
    if (selection !== null) defilerVers(panneau.current);
  }, [selection]);

  const rejouer = (id: number) => {
    setRejeuId(id);
    defilerVers(carte.current);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <GuideHeader
        cle="comprendre"
        icone={MapIcon}
        titre="Comprendre le moteur"
        objectif="Suivre le trajet d’une demande, de l’entrée à la réponse, et voir par où passent vraiment les échanges."
        comprendre={
          <>
            <p>
              Une demande entre par l’une de quatre portes. Le <strong className="text-slate-200">routeur</strong> choisit
              le chemin le moins coûteux qui suffit : un ordre simple est exécuté par des règles, une question ouverte part vers
              un modèle, un grand travail est confié à plusieurs agents. Tout se termine par une trace.
            </p>
            <p>
              <strong className="text-slate-200">Cliquez sur un bloc</strong> pour l’expliquer et voir ses chiffres réels.
              Plus bas, <strong className="text-slate-200">rejouez un vrai échange vocal</strong> : la carte met en évidence
              le chemin qu’il a réellement pris. Les mots soulignés en pointillés ont une définition au survol.
            </p>
          </>
        }
        sources={[
          { route: 'GET /api/guide/cascade', role: 'ordre réel des modèles' },
          { route: 'GET /api/vocal/audit', role: 'rejeu des échanges' },
          { route: 'GET /api/vocal/stats', role: 'répartition des chemins' },
          { route: 'GET /api/ha/health', role: 'Home Assistant' },
          { route: 'GET /api/mcp/overview', role: 'outils MCP' },
          { route: 'GET /api/observability/circuit-breakers', role: 'circuits de protection' },
        ]}
      />

      {rejoue && pris && (
        <BandeauRejeu
          echange={rejoue}
          pris={pris}
          onQuitter={() => setRejeuId(null)}
        />
      )}

      <div ref={carte}>
        <Card
          title="Le trajet d’une demande"
          subtitle="De gauche à droite sur grand écran, de haut en bas sur téléphone."
        >
          <CarteMoteur
            selection={selection}
            onSelect={(id) => setSelection((s) => (s === id ? null : id))}
            chemin={pris?.noeuds ?? null}
            noeudEnEchec={pris?.echec ? pris.chemin : null}
            apercuCascade={apercuCascade}
          />
          <p className="mt-4 flex items-center gap-2 text-xs text-slate-500">
            <MousePointerClick className="h-3.5 w-3.5 shrink-0" aria-hidden />
            Cliquez sur un bloc pour ouvrir son explication. Vert : chemin pris par l’échange rejoué. Rouge : l’échange a échoué ici.
          </p>
        </Card>
      </div>

      <div ref={panneau}>
        {selection ? (
          <PanneauNoeud noeud={NOEUD_PAR_ID[selection]} onFermer={() => setSelection(null)} />
        ) : (
          <p className="rounded-xl border border-dashed border-slate-800 px-5 py-6 text-center text-sm text-slate-500">
            Aucun bloc ouvert. Choisissez-en un sur la carte : par exemple{' '}
            <button type="button" onClick={() => setSelection('routeur')} className="font-medium text-sky-400 hover:text-sky-300">
              le routeur
            </button>{' '}
            ou{' '}
            <button type="button" onClick={() => setSelection('cascade')} className="font-medium text-sky-400 hover:text-sky-300">
              la cascade de modèles
            </button>
            .
          </p>
        )}
      </div>

      <Card
        title="Rejouer une vraie demande"
        subtitle="Les derniers échanges vocaux réellement traités par le moteur. Choisissez-en un : la carte montre le chemin qu’il a pris."
        source="GET /api/vocal/audit?limit=200"
      >
        <DataGate
          query={audit}
          route="/api/vocal/audit"
          labelChargement="Lecture du journal vocal…"
          estVide={() => echanges.length === 0}
          messageVide="Aucun échange vocal enregistré pour le moment. Dites une phrase à l’écran, ou essayez la démo vocale."
        >
          {() => (
            <div className="space-y-3">
              <ListeRejeu echanges={echanges} selectionId={rejeuId} onSelect={rejouer} />
              <p className="text-xs text-slate-500">
                Seuls les échanges vocaux sont journalisés ainsi. Pour comprendre la voix de bout en bout, voir{' '}
                <LienVue vue="demo">Démo vocale & maison</LienVue>.
              </p>
            </div>
          )}
        </DataGate>
      </Card>

      <GlossaireComplet />
    </div>
  );
}
