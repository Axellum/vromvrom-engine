/**
 * Panneau d'un nœud de la carte : ce que c'est (2 phrases), pourquoi c'est utile,
 * les chiffres réels quand une route les donne, et où le régler.
 *
 * Les requêtes de chiffres ne partent que quand le panneau est ouvert sur le nœud
 * concerné. Quand aucune route ne donne de chiffre pour un nœud, le panneau le dit.
 */
import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { fetchGuideCascade, fetchGuideOverview } from '../../api/guide';
import { fetchHaHealth, fetchVocalAudit, fetchVocalStats } from '../../api/ha';
import { fetchMcpOverview } from '../../api/mcp';
import { fetchCircuitBreakers } from '../../api/operations';
import { useRouteQuery } from '../../hooks/useRouteQuery';
import { echangesRecents, latencesParChemin } from '../../lib/audit';
import { LIBELLE_CHEMIN, cheminDepuisRouting } from '../../lib/carteMoteur';
import type { CheminId, NoeudDef, NoeudId } from '../../lib/carteMoteur';
import { formatDureeMs, formatNombre, formatUsd, pluriel } from '../../lib/format';
import { Pill } from '../ui/primitives';
import { DataGate } from './etats';
import { Glose } from './Glose';
import { LienVue } from './LienVue';

/** Au-delà, la liste d'un niveau est résumée (le niveau « automatique » regroupe tous les modèles actifs). */
const MODELES_PAR_NIVEAU = 8;

/* ── Briques d'affichage ─────────────────────────────────────────────────── */

function Ligne({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-slate-800/60 py-1.5 text-sm last:border-b-0">
      <dt className="text-slate-400">{label}</dt>
      <dd className="min-w-0 break-words text-right font-mono text-slate-100">{children}</dd>
    </div>
  );
}

function Chiffres({ children, source }: { children: ReactNode; source: string }) {
  return (
    <div>
      <dl>{children}</dl>
      <p className="mt-2 break-words text-[11px] text-slate-500">
        Source : <code className="font-mono">{source}</code>
      </p>
    </div>
  );
}

function PasDeChiffre({ raison }: { raison: string }) {
  return (
    <p className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2.5 text-sm text-slate-400">
      Aucun chiffre pour ce bloc : {raison}
    </p>
  );
}

/* ── Données par nœud ────────────────────────────────────────────────────── */

function DonneesVocal() {
  const q = useRouteQuery(['vocal', 'stats', '7d'], () => fetchVocalStats('7d'));
  return (
    <DataGate query={q} route="/api/vocal/stats" compact>
      {(s) => (
        <Chiffres source="GET /api/vocal/stats?period=7d">
          <Ligne label="Échanges vocaux (7 jours)">{formatNombre(s.total_exchanges)}</Ligne>
          <Ligne label={s.latency_ms.p50 != null ? 'Durée médiane côté moteur' : 'Durée moyenne côté moteur'}>
            {formatDureeMs(s.latency_ms.p50 ?? s.latency_ms.avg)}
          </Ligne>
          {s.latency_ms.echantillon != null && (
            <Ligne label="Échanges dont la durée est mesurée">
              {formatNombre(s.latency_ms.echantillon)} / {formatNombre(s.total_exchanges)}
            </Ligne>
          )}
          {s.by_device.length > 0 && (
            <Ligne label="Appareils">
              {s.by_device.slice(0, 3).map((d) => `${d.device_id} (${d.n})`).join(', ')}
            </Ligne>
          )}
        </Chiffres>
      )}
    </DataGate>
  );
}

function DonneesRepartition() {
  const q = useRouteQuery(['vocal', 'stats', '7d'], () => fetchVocalStats('7d'));
  return (
    <DataGate query={q} route="/api/vocal/stats" compact>
      {(s) => {
        const compte = new Map<string, number>();
        for (const r of s.by_routing) {
          const { chemin } = cheminDepuisRouting(r.routing_type === 'inconnu' ? null : r.routing_type);
          const cle = chemin ?? 'autre';
          compte.set(cle, (compte.get(cle) ?? 0) + r.n);
        }
        const total = [...compte.values()].reduce((a, b) => a + b, 0);
        if (total === 0) return <PasDeChiffre raison="aucun échange vocal enregistré sur 7 jours." />;
        const lignes = [...compte.entries()].sort((a, b) => b[1] - a[1]);
        return (
          <div>
            <p className="mb-2 text-sm text-slate-400">
              Chemin pris par les {formatNombre(total)} échanges vocaux des 7 derniers jours. Les demandes du Chat ou de l’API ne
              sont pas dans ce journal.
            </p>
            <ul className="space-y-2">
              {lignes.map(([cle, n]) => (
                <li key={cle}>
                  <div className="flex justify-between gap-3 text-sm">
                    <span className="min-w-0 break-words text-slate-300">
                      {cle === 'autre' ? 'Autre ou non enregistré' : LIBELLE_CHEMIN[cle as CheminId]}
                    </span>
                    <span className="shrink-0 font-mono text-slate-100">
                      {formatNombre(n)} · {Math.round((n / total) * 100)} %
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-800" aria-hidden>
                    <div className="h-full rounded-full bg-sky-500" style={{ width: `${(n / total) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-slate-500">
              Source : <code className="font-mono">GET /api/vocal/stats?period=7d</code> (champ by_routing)
            </p>
          </div>
        );
      }}
    </DataGate>
  );
}

function DonneesChemin({ chemin }: { chemin: CheminId }) {
  const stats = useRouteQuery(['vocal', 'stats', '7d'], () => fetchVocalStats('7d'));
  const audit = useRouteQuery(['vocal', 'audit', 200], () => fetchVocalAudit(200));
  if (chemin === 'p-complexe') {
    return <PasDeChiffre raison="ce chemin n’est jamais emprunté par la voix, seule source de chiffres de cette carte. Voir Opérations et Observabilité pour les tâches lancées depuis l’interface." />;
  }
  return (
    <DataGate query={stats} route="/api/vocal/stats" compact>
      {(s) => {
        const n = s.by_routing
          .filter((r) => cheminDepuisRouting(r.routing_type === 'inconnu' ? null : r.routing_type).chemin === chemin)
          .reduce((a, r) => a + r.n, 0);
        const total = s.by_routing.reduce((a, r) => a + r.n, 0);
        const lat = audit.data ? latencesParChemin(echangesRecents(audit.data.logs)).get(chemin) : undefined;
        return (
          <Chiffres source="GET /api/vocal/stats?period=7d · GET /api/vocal/audit?limit=200">
            <Ligne label="Échanges vocaux sur ce chemin (7 jours)">
              {formatNombre(n)} sur {formatNombre(total)}
            </Ligne>
            <Ligne label="Durée médiane côté moteur">
              {lat && lat.mediane !== null ? formatDureeMs(lat.mediane) : audit.isPending ? '…' : '—'}
            </Ligne>
            {lat && (
              <Ligne label="Échantillon">
                {formatNombre(lat.mesures)} {pluriel(lat.mesures, 'mesuré', 'mesurés')} sur {formatNombre(lat.total)}{' '}
                {pluriel(lat.total, 'dernier échange', 'derniers échanges')}
                {lat.echecs > 0 && ` (${formatNombre(lat.echecs)} en échec, exclus de la médiane)`}
              </Ligne>
            )}
          </Chiffres>
        );
      }}
    </DataGate>
  );
}

function DonneesCascade() {
  const q = useRouteQuery(['guide', 'cascade'], fetchGuideCascade);
  const cb = useRouteQuery(['operations', 'circuit-breakers'], fetchCircuitBreakers);
  return (
    <DataGate query={q} route="/api/guide/cascade" compact>
      {(c) => {
        const etats = new Map((cb.data?.circuit_breakers ?? []).map((x) => [x.name, x.state]));
        const liste = c.fast_path_providers ?? [];
        const tab5 = c.fast_path_providers_tab5 ?? [];
        const tab5Different = tab5.length > 0 && tab5.join('|') !== liste.join('|');
        // Le moteur lit d'abord le catalogue ; `tiers` (config.json) n'est qu'un filet de secours.
        const catalogue = Object.entries(c.tiers_catalogue ?? {});
        const duCatalogue = catalogue.length > 0;
        const tiers = duCatalogue ? catalogue : Object.entries(c.tiers ?? {});
        const apercu = (modeles: string[]) =>
          modeles.length > MODELES_PAR_NIVEAU
            ? `${modeles.slice(0, MODELES_PAR_NIVEAU).join(' → ')} … et ${modeles.length - MODELES_PAR_NIVEAU} autres`
            : modeles.join(' → ');
        const pastille = (modele: string) => {
          const etat = etats.get(modele);
          if (!etat) return null;
          return (
            <Pill tone={etat === 'CLOSED' ? 'good' : etat === 'OPEN' ? 'bad' : 'warn'}>
              {etat === 'CLOSED' ? 'circuit fermé' : etat === 'OPEN' ? 'circuit ouvert' : 'circuit semi-ouvert'}
            </Pill>
          );
        };
        return (
          <div className="space-y-4">
            {liste.length > 0 && (
              <div>
                <p className="mb-1.5 text-sm font-medium text-slate-200">Discussion et voix : ordre d’essai</p>
                <ol className="space-y-1">
                  {liste.map((m, i) => (
                    <li key={`${m}-${i}`} className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-800 text-[11px] text-sky-300">{i + 1}</span>
                      <code className="break-all font-mono text-slate-200">{m}</code>
                      {pastille(m)}
                    </li>
                  ))}
                </ol>
                {tab5Different && (
                  <p className="mt-1.5 break-words text-xs leading-relaxed text-slate-400">
                    Depuis la tablette Tab5 : <code className="font-mono">{tab5.join(' → ')}</code>
                  </p>
                )}
                {c.explication?.fast_path_providers && (
                  <p className="mt-1.5 text-xs leading-relaxed text-slate-500">{c.explication.fast_path_providers}</p>
                )}
              </div>
            )}
            {tiers.length > 0 && (
              <div>
                <p className="mb-1.5 text-sm font-medium text-slate-200">
                  {duCatalogue
                    ? 'Tâches complexes : candidats par niveau'
                    : 'Tâches complexes : liste de secours par niveau (config.json)'}
                </p>
                <dl className="space-y-2">
                  {tiers.map(([nom, modeles]) => (
                    <div key={nom}>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                        {nom}
                        {duCatalogue && c.tiers_source?.[nom] && (
                          <span className="ml-2 font-normal normal-case tracking-normal text-slate-600">
                            ({formatNombre(modeles.length)} · {c.tiers_source[nom]})
                          </span>
                        )}
                      </dt>
                      <dd className="mt-0.5 break-words font-mono text-xs leading-relaxed text-slate-300">
                        {modeles.length > 0 ? apercu(modeles) : '(vide)'}
                      </dd>
                    </div>
                  ))}
                </dl>
                {duCatalogue && (
                  <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
                    Ce sont les candidats avant tri : le moteur les reclasse à chaque demande (qualité, coût, quota, latence),
                    donc l’ordre exact n’est pas figé.
                    {(c.exclus_de_la_cascade?.length ?? 0) > 0 &&
                      ` ${formatNombre(c.exclus_de_la_cascade?.length)} modèle(s) sont exclus de la cascade par la politique de routage.`}
                  </p>
                )}
                {c.explication?.tiers && <p className="mt-1.5 text-xs leading-relaxed text-slate-500">{c.explication.tiers}</p>}
              </div>
            )}
            {c.note && <p className="text-xs leading-relaxed text-amber-300/90">{c.note}</p>}
            {liste.length === 0 && tiers.length === 0 && (
              <PasDeChiffre raison="le moteur n’a renvoyé aucune liste de modèles." />
            )}
            {cb.data && (
              <p className="text-sm text-slate-400">
                Circuits de protection : {formatNombre(cb.data.healthy)} sains, {formatNombre(cb.data.open)} ouverts,{' '}
                {formatNombre(cb.data.half_open)} semi-ouverts sur {formatNombre(cb.data.total)}.
              </p>
            )}
            <p className="break-words text-[11px] text-slate-500">
              Source : <code className="font-mono">GET /api/guide/cascade</code>
              {c.source ? <> — lu dans {c.source}</> : null} · <code className="font-mono">GET /api/observability/circuit-breakers</code>
            </p>
          </div>
        );
      }}
    </DataGate>
  );
}

function DonneesHa() {
  const q = useRouteQuery(['ha', 'health'], fetchHaHealth);
  return (
    <DataGate query={q} route="/api/ha/health" compact>
      {(h) => (
        <Chiffres source="GET /api/ha/health (test en direct)">
          <Ligne label="Joignable">
            <Pill tone={h.reachable ? 'good' : 'bad'}>{h.reachable ? 'oui' : 'non'}</Pill>
          </Ligne>
          <Ligne label="Latence">{formatDureeMs(h.latency_ms)}</Ligne>
          <Ligne label="Entités visibles">{formatNombre(h.entity_count)}</Ligne>
          <Ligne label="Version">{h.version ?? '—'}</Ligne>
          {h.error && <Ligne label="Erreur"><span className="text-red-300">{h.error}</span></Ligne>}
        </Chiffres>
      )}
    </DataGate>
  );
}

function DonneesMcp() {
  const q = useRouteQuery(['mcp', 'overview'], fetchMcpOverview);
  return (
    <DataGate query={q} route="/api/mcp/overview" compact>
      {(m) => (
        <Chiffres source="GET /api/mcp/overview">
          <Ligne label="Le moteur offre ses outils à Claude / Cursor">
            {m.expose?.disponible == null ? '—' : m.expose.disponible ? 'oui' : 'non'}
          </Ligne>
          <Ligne label="Outils offerts">{m.expose?.outils ? formatNombre(m.expose.outils.length) : '—'}</Ligne>
          <Ligne label="Serveurs MCP utilisés par le moteur">{m.consomme ? formatNombre(m.consomme.length) : '—'}</Ligne>
          <Ligne label="… dont actifs">
            {m.consomme ? formatNombre(m.consomme.filter((c) => c.actif).length) : '—'}
          </Ligne>
        </Chiffres>
      )}
    </DataGate>
  );
}

function DonneesTraces() {
  const q = useRouteQuery(['guide', 'overview'], fetchGuideOverview);
  return (
    <DataGate query={q} route="/api/guide/overview" compact>
      {(o) => (
        <Chiffres source="GET /api/guide/overview → usage">
          <Ligne label="Demandes réelles (7 jours)">{formatNombre(o.usage?.appels_reels_7j)}</Ligne>
          <Ligne label="Sondes de santé (7 jours, à part)">{formatNombre(o.usage?.appels_sondes_7j)}</Ligne>
          <Ligne label="Dépense (30 jours)">{formatUsd(o.usage?.cout_30j_usd)}</Ligne>
        </Chiffres>
      )}
    </DataGate>
  );
}

function DonneesNoeud({ id }: { id: NoeudId }) {
  switch (id) {
    case 'in-vocal': return <DonneesVocal />;
    case 'in-chat': return <PasDeChiffre raison="aucune route ne compte les messages du Chat séparément des autres demandes." />;
    case 'in-api': return <PasDeChiffre raison="aucune route ne compte les appels de l’API séparément des autres demandes." />;
    case 'in-mcp':
    case 'act-outils': return <DonneesMcp />;
    case 'routeur': return <DonneesRepartition />;
    case 'p-commande':
    case 'p-question':
    case 'p-discussion':
    case 'p-complexe': return <DonneesChemin chemin={id} />;
    case 'cascade': return <DonneesCascade />;
    case 'act-ha': return <DonneesHa />;
    case 'act-memoire': return <PasDeChiffre raison="aucune route ne donne encore l’état de la mémoire (nombre de souvenirs, taille)." />;
    case 'traces': return <DonneesTraces />;
  }
}

/* ── Panneau ─────────────────────────────────────────────────────────────── */

export function PanneauNoeud({ noeud, onFermer }: { noeud: NoeudDef; onFermer: () => void }) {
  const Icone = noeud.icone;
  return (
    <section
      aria-labelledby="titre-panneau-noeud"
      className="rounded-xl border border-sky-800/60 bg-slate-900/70"
    >
      <header className="flex items-start justify-between gap-3 border-b border-slate-800 px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-500/15 text-sky-300">
            <Icone className="h-5 w-5" aria-hidden />
          </span>
          <h2 id="titre-panneau-noeud" className="min-w-0 break-words text-lg font-semibold text-slate-100">{noeud.titre}</h2>
        </div>
        <button
          type="button"
          onClick={onFermer}
          aria-label="Fermer le panneau"
          className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <X className="h-5 w-5" />
        </button>
      </header>
      <div className="grid gap-6 p-5 lg:grid-cols-2">
        <div className="space-y-5">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Ce que c’est</h3>
            <p className="mt-1 text-sm leading-relaxed text-slate-400"><Glose texte={noeud.quoi} /></p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Pourquoi c’est utile</h3>
            <p className="mt-1 text-sm leading-relaxed text-slate-400"><Glose texte={noeud.utile} /></p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Où le régler</h3>
            <ul className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
              {noeud.reglages.map((r) => (
                <li key={r.vue}><LienVue vue={r.vue}>{r.texte}</LienVue></li>
              ))}
            </ul>
          </div>
        </div>
        <div>
          <h3 className="mb-1.5 text-sm font-semibold text-slate-200">Chiffres réels</h3>
          <DonneesNoeud id={noeud.id} />
        </div>
      </div>
    </section>
  );
}
