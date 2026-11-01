/**
 * Démo vocale & maison : de « Okay Nabu » à la réponse sur l'écran et à l'action
 * dans la maison.
 *
 * Deux sources, jamais mélangées :
 *  - le contenu vérifié du guide de la démo, daté du 01/11/2026 (lib/demoVocale.ts) ;
 *  - l'état en direct, lu maintenant auprès du moteur : sa santé, Home Assistant
 *    (test réel), les statistiques et le journal d'audit vocaux.
 * Le moteur ne voit ni l'écran, ni la reconnaissance vocale, ni la synthèse vocale :
 * la page l'écrit plutôt que d'afficher un voyant décoratif.
 */
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { ListChecks, Mic, RefreshCw } from 'lucide-react';
import { fetchHaHealth, fetchVocalAudit, fetchVocalStats } from '../api/ha';
import { fetchHealthz } from '../api/vocal';
import { useRouteQuery } from '../hooks/useRouteQuery';
import { echangesRecents, latenceMesuree, latencesParChemin } from '../lib/audit';
import { cheminDepuisRouting } from '../lib/carteMoteur';
import { DATE_MESURE } from '../lib/demoVocale';
import { formatDureeMs, formatNombre, ilYA, parseDate } from '../lib/format';
import { Button, Card, Pill, Warn } from '../components/ui/primitives';
import { GuideHeader } from '../components/guide/GuideHeader';
import { DataGate } from '../components/guide/etats';
import { LienVue } from '../components/guide/LienVue';
import { EssayerPhrase } from '../components/guide/EssayerPhrase';
import {
  ChecklistTournage, DureesMesurees, ListeDepannage, ListeLimites, RetourALaNormale, TableauReglages,
  TrajetPhrase, TroisChemins,
} from '../components/guide/DemoSections';
import type { ControleEnDirect } from '../components/guide/DemoSections';

const SECTIONS = [
  { id: 'trajet', titre: 'Le trajet' },
  { id: 'direct', titre: 'En direct' },
  { id: 'chemins', titre: 'Trois chemins' },
  { id: 'essai', titre: 'Essayer une phrase' },
  { id: 'regler', titre: 'Régler' },
  { id: 'tournage', titre: 'Tournage' },
  { id: 'depannage', titre: 'Dépannage' },
];

function allerA(id: string) {
  const reduit = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.getElementById(`demo-${id}`)?.scrollIntoView({ behavior: reduit ? 'auto' : 'smooth', block: 'start' });
}

function Titre({ id, children, sous }: { id: string; children: ReactNode; sous?: ReactNode }) {
  return (
    <div>
      <h2 id={`demo-${id}`} className="scroll-mt-4 text-xl font-semibold tracking-tight text-slate-100">{children}</h2>
      {sous && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-slate-400">{sous}</p>}
    </div>
  );
}

function CarteEtat({ titre, source, children }: { titre: string; source: string; children: ReactNode }) {
  return (
    <article className="flex min-w-0 flex-col rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <h3 className="text-sm font-medium text-slate-400">{titre}</h3>
      <div className="mt-2 flex-1 text-sm">{children}</div>
      <p className="mt-3 break-words border-t border-slate-800/80 pt-2 text-[11px] text-slate-500">
        Source : <code className="font-mono">{source}</code>
      </p>
    </article>
  );
}

function Ligne({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 py-1">
      <dt className="text-slate-400">{label}</dt>
      <dd className="min-w-0 break-words text-right font-mono text-slate-100">{children}</dd>
    </div>
  );
}

export default function DemoVocale() {
  const sante = useRouteQuery(['healthz'], fetchHealthz, { refetchInterval: 30_000 });
  const ha = useRouteQuery(['ha', 'health'], fetchHaHealth, { staleTime: 60_000 });
  const stats = useRouteQuery(['vocal', 'stats', '7d'], () => fetchVocalStats('7d'));
  const audit = useRouteQuery(['vocal', 'audit', 200], () => fetchVocalAudit(200));

  const echanges = useMemo(() => (audit.data ? echangesRecents(audit.data.logs) : []), [audit.data]);
  const latences = useMemo(() => latencesParChemin(echanges), [echanges]);
  const dernier = echanges[0];

  const controle = (q: { isPending: boolean; isError: boolean; data: unknown }, ok: boolean): ControleEnDirect =>
    q.isPending ? 'attente' : q.isError || !ok ? 'ko' : 'ok';
  const controles = {
    moteur: controle(sante, sante.data?.ok === true),
    ha: controle(ha, ha.data?.reachable === true),
  };

  const pastille = (c: ControleEnDirect, ok: string, ko: string) =>
    c === 'ok' ? <Pill tone="good">{ok}</Pill> : c === 'attente' ? <Pill>vérification…</Pill> : <Pill tone="bad">{ko}</Pill>;

  const etatEnDirect: Partial<Record<number, ReactNode>> = {
    4: (
      <span className="text-slate-400">
        Ce composant vit dans Home Assistant : le moteur ne voit que les requêtes qu’il reçoit (étape 5).
      </span>
    ),
    5: (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-300">
        En direct : {pastille(controles.moteur, 'moteur en marche', 'moteur injoignable')}
        {stats.data && (
          <span>
            durée médiane côté moteur sur 7 jours : {formatDureeMs(stats.data.latency_ms.p50 ?? stats.data.latency_ms.avg)}
          </span>
        )}
      </span>
    ),
    6: (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-300">
        En direct : {pastille(controles.ha, 'Home Assistant joignable', 'Home Assistant injoignable')}
        {ha.data?.latency_ms != null && <span>{formatDureeMs(ha.data.latency_ms)}</span>}
      </span>
    ),
  };

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <GuideHeader
        cle="demo"
        icone={Mic}
        titre="Démo vocale & maison"
        objectif="De « Okay Nabu » à la réponse sur l’écran et à l’action dans la maison : chaque étape, ce qu’on en mesure, et comment la régler."
        comprendre={
          <>
            <p>
              Un écran M5Stack Tab5 sert de <strong className="text-slate-200">satellite vocal</strong> à Home Assistant : on dit
              « Okay Nabu », on parle, la phrase traverse le pipeline Assist, puis Home Assistant la confie au Moteur IA. La
              réponse s’affiche sur l’écran, elle est lue à voix haute, et les ordres agissent sur la maison.
            </p>
            <p>
              Cette page distingue deux choses : le <strong className="text-slate-200">contenu du guide</strong> (durées et
              réglages relevés le {DATE_MESURE}) et l’<strong className="text-slate-200">état en direct</strong> (lu maintenant
              auprès du moteur). Le moteur ne voit pas l’écran, la reconnaissance vocale ni la synthèse vocale : ces maillons
              sont marqués « invisibles depuis le moteur ».
            </p>
          </>
        }
        sources={[
          { route: 'GET /healthz', role: 'le moteur répond-il ?' },
          { route: 'GET /api/ha/health', role: 'test réel de Home Assistant' },
          { route: 'GET /api/vocal/stats', role: 'statistiques sur 7 jours' },
          { route: 'GET /api/vocal/audit', role: 'derniers échanges' },
          { route: 'POST /api/vocal/simulate', role: 'essai sans exécution (si le moteur l’offre)' },
          { route: 'Guide de la démo du 01/11/2026', role: 'durées, réglages, checklist' },
        ]}
      />

      <Warn>
        <p className="font-semibold">Les pipelines du moteur commandent la VRAIE maison.</p>
        <p className="mt-1">
          Lampes, volet de la serre et climatisation répondent à la voix, même pendant un essai : le volet du salon a bougé lors
          d’un « essai » le {DATE_MESURE}. Prévenez les personnes présentes avant de dire un ordre. Cette page, elle, ne commande
          rien ; sa zone d’essai ne fait que montrer le chemin choisi.
        </p>
      </Warn>

      <nav aria-label="Sections de la page" className="flex flex-wrap gap-2">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => allerA(s.id)}
            className="rounded-full border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition-colors hover:border-sky-600 hover:text-sky-300"
          >
            {s.titre}
          </button>
        ))}
      </nav>

      <section className="space-y-4" aria-labelledby="demo-trajet">
        <Titre id="trajet" sous="Huit étapes, de la voix à la réponse. Pour chacune : où elle tourne, ce qu’elle fait, ce que le moteur en voit et la durée mesurée.">
          Le trajet d’une phrase
        </Titre>
        <TrajetPhrase etatEnDirect={etatEnDirect} />
        <Card title="Combien de temps ça prend ?" subtitle={`Durées mesurées le ${DATE_MESURE}`}>
          <DureesMesurees />
        </Card>
      </section>

      <section className="space-y-4" aria-labelledby="demo-direct">
        <Titre id="direct" sous="Ce que le moteur peut constater maintenant. Chaque carte cite sa route ; un chiffre inconnu s’affiche « — ».">
          État en direct des maillons
        </Titre>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <CarteEtat titre="Moteur IA" source="GET /healthz">
            <DataGate query={sante} route="/healthz" compact>
              {(h) => (
                <dl>
                  <Ligne label="Réponse">
                    {h.ok ? <Pill tone="good">répond</Pill> : <Pill tone="bad">anormale</Pill>}
                  </Ligne>
                  <Ligne label="État">{h.status ?? '—'}</Ligne>
                  <Ligne label="Version">{h.commit ? h.commit.slice(0, 8) : '—'}</Ligne>
                </dl>
              )}
            </DataGate>
          </CarteEtat>

          <CarteEtat titre="Home Assistant" source="GET /api/ha/health (test réel)">
            <DataGate query={ha} route="/api/ha/health" compact>
              {(h) => (
                <>
                  <dl>
                    <Ligne label="Joignable">
                      <Pill tone={h.reachable ? 'good' : 'bad'}>{h.reachable ? 'oui' : 'non'}</Pill>
                    </Ligne>
                    <Ligne label="Latence">{formatDureeMs(h.latency_ms)}</Ligne>
                    <Ligne label="Entités">{formatNombre(h.entity_count)}</Ligne>
                    <Ligne label="Version">{h.version ?? '—'}</Ligne>
                  </dl>
                  {h.error && <p className="mt-2 break-words text-xs text-red-300">{h.error}</p>}
                  <Button variant="secondary" className="mt-3" onClick={() => void ha.refetch()} disabled={ha.isFetching}>
                    <RefreshCw className={`h-4 w-4 ${ha.isFetching ? 'animate-spin' : ''}`} aria-hidden />
                    Retester
                  </Button>
                </>
              )}
            </DataGate>
          </CarteEtat>

          <CarteEtat titre="Échanges vocaux, 7 jours" source="GET /api/vocal/stats?period=7d">
            <DataGate query={stats} route="/api/vocal/stats" compact>
              {(s) => (
                <dl>
                  <Ligne label="Échanges">{formatNombre(s.total_exchanges)}</Ligne>
                  <Ligne label="Durée médiane">{formatDureeMs(s.latency_ms.p50 ?? s.latency_ms.avg)}</Ligne>
                  {s.latency_ms.p90 != null && <Ligne label="90 % sous">{formatDureeMs(s.latency_ms.p90)}</Ligne>}
                  {s.latency_ms.echantillon != null && (
                    <Ligne label="Durées mesurées">
                      {formatNombre(s.latency_ms.echantillon)} / {formatNombre(s.total_exchanges)}
                    </Ligne>
                  )}
                  {s.by_mode.length > 0 && (
                    <Ligne label="Par mode">
                      {s.by_mode
                        .map((m) => `${m.source_mode === 'ha' ? 'Domotique' : m.source_mode === 'chat' ? 'Discussion' : m.source_mode ?? '?'} ${m.n}`)
                        .join(' · ')}
                    </Ligne>
                  )}
                </dl>
              )}
            </DataGate>
          </CarteEtat>

          <CarteEtat titre="Dernier échange" source="GET /api/vocal/audit?limit=200">
            <DataGate
              query={audit}
              route="/api/vocal/audit"
              compact
              estVide={() => !dernier}
              messageVide="Aucun échange vocal enregistré."
            >
              {() =>
                dernier ? (
                  <>
                    <p className="break-words font-medium text-slate-100">
                      {dernier.user_prompt ? `« ${dernier.user_prompt} »` : 'Texte non enregistré'}
                    </p>
                    <dl className="mt-1.5">
                      <Ligne label="Quand">{ilYA(parseDate(dernier.created_at))}</Ligne>
                      <Ligne label="Chemin">{cheminDepuisRouting(dernier.routing_type, dernier.source_mode).libelle}</Ligne>
                      <Ligne label="Durée moteur">
                        {latenceMesuree(dernier) !== null ? formatDureeMs(latenceMesuree(dernier)) : 'non mesurée'}
                      </Ligne>
                    </dl>
                    <p className="mt-2 text-xs">
                      <LienVue vue="comprendre">Rejouer cet échange sur la carte</LienVue>
                    </p>
                  </>
                ) : null
              }
            </DataGate>
          </CarteEtat>
        </div>
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm leading-relaxed text-slate-400">
          <strong className="text-slate-300">Ce que le moteur ne voit pas :</strong> l’écran Tab5 (connexion, volume, écoute), la
          reconnaissance vocale et la synthèse vocale, qui tournent dans Home Assistant. Pour ces maillons, la checklist ci-dessous
          indique l’élément à lire dans Home Assistant.
        </p>
      </section>

      <section className="space-y-4" aria-labelledby="demo-chemins">
        <Titre id="chemins" sous="Le pipeline choisi fixe le mode ; le moteur essaie ensuite ses raccourcis dans un ordre fixe. La voix n’atteint jamais le Planner ni les agents de code.">
          Trois chemins dans le moteur
        </Titre>
        <TroisChemins latences={latences} />
        <p className="text-sm text-slate-400">
          Pour voir ces chemins sur le schéma complet du moteur : <LienVue vue="comprendre">Comprendre le moteur</LienVue>.
        </p>
      </section>

      <section className="space-y-4" aria-labelledby="demo-essai">
        <Titre id="essai" sous="Le moteur montre le chemin qu’il choisirait pour une phrase, sans l’exécuter.">
          Essayer une phrase sans rien exécuter
        </Titre>
        <Card>
          <EssayerPhrase />
        </Card>
      </section>

      <section className="space-y-4" aria-labelledby="demo-regler">
        <Titre id="regler" sous="Convention : « Home Assistant » = son interface web ; « moteur » = les fichiers du moteur sur le Steam Deck.">
          Je veux… où et comment
        </Titre>
        <TableauReglages />
      </section>

      <section className="space-y-6" aria-labelledby="demo-tournage">
        <Titre id="tournage" sous="La préparation avant de filmer, et ce qu’il faut défaire ensuite.">
          <span className="inline-flex items-center gap-2"><ListChecks className="h-5 w-5 text-sky-400" aria-hidden />Avant, puis après un tournage</span>
        </Titre>
        <div>
          <h3 className="mb-2 text-base font-semibold text-slate-100">Avant</h3>
          <ChecklistTournage controles={controles} />
        </div>
        <div>
          <h3 className="mb-2 text-base font-semibold text-slate-100">Après : remettre à la normale</h3>
          <RetourALaNormale />
        </div>
      </section>

      <section className="space-y-4" aria-labelledby="demo-depannage">
        <Titre id="depannage" sous="Un symptôme, sa cause probable, où regarder. Cliquez pour déplier.">
          Dépannage
        </Titre>
        <ListeDepannage />
      </section>

      <ListeLimites />
    </div>
  );
}
