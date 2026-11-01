/**
 * Accueil : « le moteur va-t-il bien, et par où commencer ? »
 *
 * Ordre voulu : une phrase de verdict, les alertes, six indicateurs réels (chacun
 * dit d'où vient son chiffre et ce qu'il veut dire), puis ce que fait le moteur et
 * par où commencer. Tout ce qui est chiffré vient de `/api/guide/overview` ; si
 * cette route n'existe pas sur le moteur en service, la page le dit et n'affiche
 * aucun chiffre. Les blocs explicatifs, eux, ne dépendent d'aucune route.
 */
import type { ReactNode } from 'react';
import {
  Bot, CircleCheck, Compass, House, Info, MessageSquare, RefreshCw, ShieldAlert, Sparkles,
  TriangleAlert, Mic, Map as MapIcon,
} from 'lucide-react';
import { fetchGuideOverview } from '../api/guide';
import type { GuideAlerte, GuideOverview } from '../api/guide';
import { useRouteQuery } from '../hooks/useRouteQuery';
import { useTick } from '../hooks/useTick';
import { GuideHeader } from '../components/guide/GuideHeader';
import { DataGate } from '../components/guide/etats';
import { Terme } from '../components/guide/Terme';
import { Tuile } from '../components/guide/Tuile';
import type { TonTuile } from '../components/guide/Tuile';
import { LienVue } from '../components/guide/LienVue';
import { Card, Pill, Button } from '../components/ui/primitives';
import { hashForView } from '../lib/navigation';
import type { ViewKey } from '../lib/navigation';
import {
  formatDateHeure, formatDureeMs, formatDureeSec, formatNombre, formatUsd, ilYA, parseDate,
} from '../lib/format';
import { construireVerdict } from '../lib/verdict';
import type { TonVerdict } from '../lib/verdict';

const ROUTE = '/api/guide/overview';
const SEUIL_SAUVEGARDE_H = 48;

/* ── Verdict ──────────────────────────────────────────────────────────────── */

const STYLE_VERDICT: Record<TonVerdict, { cadre: string; icone: typeof CircleCheck; couleur: string; libelle: string }> = {
  good: { cadre: 'border-emerald-800/60 bg-emerald-950/20', icone: CircleCheck, couleur: 'text-emerald-400', libelle: 'Tout va bien' },
  warn: { cadre: 'border-amber-800/60 bg-amber-950/20', icone: TriangleAlert, couleur: 'text-amber-400', libelle: 'À surveiller' },
  bad: { cadre: 'border-red-800/70 bg-red-950/25', icone: ShieldAlert, couleur: 'text-red-400', libelle: 'Action requise' },
};

function BlocVerdict({ o, onActualiser, actualisation }: { o: GuideOverview; onActualiser: () => void; actualisation: boolean }) {
  const v = construireVerdict(o);
  const s = STYLE_VERDICT[v.ton];
  const Icone = s.icone;
  const genere = parseDate(o.genere_le);
  const demarre = parseDate(o.moteur?.demarre_le);
  const commit = o.moteur?.commit ?? null;
  return (
    <section aria-label="Verdict" className={`rounded-xl border p-5 sm:p-6 ${s.cadre}`}>
      <div className="flex items-start gap-4">
        <Icone className={`mt-1 h-7 w-7 shrink-0 ${s.couleur}`} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className={`text-xs font-semibold uppercase tracking-wider ${s.couleur}`}>{s.libelle}</p>
          <p className="mt-1 text-xl font-semibold leading-snug text-slate-100 sm:text-2xl">{v.phrase}</p>
          <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
            {commit && (
              <span>
                Version <code className="font-mono text-slate-300">{commit.slice(0, 8)}</code>
              </span>
            )}
            {demarre && (
              <span>
                Démarré {ilYA(demarre)}
                {o.moteur?.uptime_s != null && <> (en marche depuis {formatDureeSec(o.moteur.uptime_s)})</>}
              </span>
            )}
            {o.home_assistant?.configure != null && (
              <span>Home Assistant : {o.home_assistant.configure ? 'configuré' : 'non configuré'}</span>
            )}
            {genere && <span title={formatDateHeure(genere)}>Chiffres calculés {ilYA(genere)}</span>}
          </p>
        </div>
        <Button variant="secondary" onClick={onActualiser} disabled={actualisation} title="Relire les chiffres du moteur">
          <RefreshCw className={`h-4 w-4 ${actualisation ? 'animate-spin' : ''}`} aria-hidden />
          <span className="hidden sm:inline">Actualiser</span>
          <span className="sr-only sm:hidden">Actualiser</span>
        </Button>
      </div>
    </section>
  );
}

/* ── Alertes ──────────────────────────────────────────────────────────────── */

const ORDRE_NIVEAU: Record<string, number> = { critique: 0, attention: 1, info: 2 };

function BlocAlertes({ alertes }: { alertes: GuideAlerte[] | null | undefined }) {
  if (!Array.isArray(alertes)) {
    return (
      <p className="text-sm text-slate-500">
        Les alertes ne sont pas fournies par ce moteur : impossible de dire s’il y a un point à traiter.
      </p>
    );
  }
  if (alertes.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-400">
        <CircleCheck className="h-4 w-4 text-emerald-400" aria-hidden />
        Aucune alerte signalée par le moteur.
      </p>
    );
  }
  const triees = [...alertes].sort((a, b) => (ORDRE_NIVEAU[a.niveau ?? ''] ?? 3) - (ORDRE_NIVEAU[b.niveau ?? ''] ?? 3));
  return (
    <ul className="space-y-2">
      {triees.map((a, i) => {
        const niveau = a.niveau === 'critique' || a.niveau === 'attention' ? a.niveau : 'info';
        const tons = {
          critique: { cadre: 'border-red-900/70 bg-red-950/25', pill: 'bad' as const, libelle: 'Critique', Icone: ShieldAlert, couleur: 'text-red-400' },
          attention: { cadre: 'border-amber-900/60 bg-amber-950/20', pill: 'warn' as const, libelle: 'Attention', Icone: TriangleAlert, couleur: 'text-amber-400' },
          info: { cadre: 'border-slate-800 bg-slate-900/40', pill: 'info' as const, libelle: 'Information', Icone: Info, couleur: 'text-sky-400' },
        }[niveau];
        return (
          <li key={`${i}-${a.texte}`} className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${tons.cadre}`}>
            <tons.Icone className={`mt-0.5 h-4 w-4 shrink-0 ${tons.couleur}`} aria-hidden />
            <div className="min-w-0">
              <Pill tone={tons.pill}>{tons.libelle}</Pill>
              <p className="mt-1 break-words text-sm text-slate-200">{a.texte}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* ── Indicateurs ──────────────────────────────────────────────────────────── */

function age(v: unknown): { date: Date | null; heures: number | null } {
  const date = parseDate(v);
  return { date, heures: date ? (Date.now() - date.getTime()) / 3_600_000 : null };
}

function tonRatio(ok: number | null | undefined, total: number | null | undefined): TonTuile {
  if (ok == null || total == null || total === 0) return 'neutral';
  if (ok === 0) return 'bad';
  return ok / total < 0.5 ? 'warn' : 'good';
}

function Indicateurs({ o }: { o: GuideOverview }) {
  const f = o.fournisseurs;
  const m = o.modeles;
  const u = o.usage;
  const v = o.vocal;
  const s = o.sauvegardes;
  const enPanne = f?.en_panne ?? [];
  const sauv = age(s?.derniere);

  const tonSauvegarde: TonTuile =
    s == null ? 'neutral' : sauv.heures === null ? 'bad' : sauv.heures > SEUIL_SAUVEGARDE_H ? 'bad' : 'good';
  const valeurSauvegarde = s == null ? null : sauv.date ? ilYA(sauv.date) : 'Aucune';

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <Tuile
        titre={<>Fournisseurs <Terme id="fournisseur">utilisables</Terme></>}
        valeur={f?.utilisables != null && f?.total != null ? `${f.utilisables} / ${f.total}` : null}
        ton={tonRatio(f?.utilisables, f?.total)}
        detail={
          <>
            {enPanne.length > 0 ? (
              <span className="block">
                En panne : {enPanne.slice(0, 3).map((p) => (p.raison ? `${p.nom} (${p.raison})` : p.nom)).join(', ')}
                {enPanne.length > 3 && ` et ${enPanne.length - 3} autre${enPanne.length - 3 > 1 ? 's' : ''}`}
              </span>
            ) : f?.utilisables != null ? (
              <span className="block">Aucun fournisseur en panne.</span>
            ) : null}
            {(f?.non_testes ?? 0) > 0 && (
              <span className="mt-1 block text-slate-400">
                {formatNombre(f?.non_testes)} sans test récent : état inconnu, pas forcément en panne.
              </span>
            )}
            {f?.note && <span className="mt-1 block text-amber-300/90">{f.note}</span>}
          </>
        }
        signification={
          <>
            Un fournisseur héberge des modèles. « Utilisable » veut dire qu’un test récent d’un de ses modèles a réussi.
            Tant qu’il en reste un d’utilisable, le moteur continue de répondre : c’est le rôle de la{' '}
            <Terme id="cascade">cascade</Terme>.
          </>
        }
        source="/api/guide/overview → fournisseurs"
        lien={{ vue: 'llm', texte: 'Voir les modèles' }}
      />
      <Tuile
        titre="Modèles actifs"
        valeur={m?.actifs != null && m?.total != null ? `${m.actifs} / ${m.total}` : null}
        signification={
          <>
            Un modèle actif peut être choisi par le moteur ; les autres sont mis de côté au catalogue. Le moteur
            demande un <Terme id="tier">niveau</Terme>, pas un modèle précis.
          </>
        }
        source="/api/guide/overview → modeles"
        lien={{ vue: 'llm', texte: 'Activer ou désactiver' }}
      />
      <Tuile
        titre="Demandes réelles (7 jours)"
        valeur={u?.appels_reels_7j != null ? formatNombre(u.appels_reels_7j) : null}
        detail={
          <>
            {u?.appels_sondes_7j != null && (
              <span>
                + {formatNombre(u.appels_sondes_7j)} <Terme id="sonde-de-sante">sondes de santé</Terme>, comptées à part
              </span>
            )}
            {u?.appels_24h != null && <span className="block text-slate-400">{formatNombre(u.appels_24h)} sur les dernières 24 h</span>}
          </>
        }
        signification={
          <>
            Chaque fois qu’un utilisateur ou un agent a eu besoin d’un modèle. Les sondes de santé sont de petits tests
            automatiques : elles ne comptent pas comme de l’activité. Le moteur les repère comme les appels sans session
            ni agent, donc ce chiffre est plutôt un minimum.
          </>
        }
        source="/api/guide/overview → usage"
        lien={{ vue: 'observability', texte: 'Voir le détail' }}
      />
      <Tuile
        titre="Dépense (30 jours)"
        valeur={u?.cout_30j_usd != null ? formatUsd(u.cout_30j_usd) : null}
        signification="Dépense cumulée enregistrée par le moteur sur les 30 derniers jours, en dollars, sondes de santé comprises (elles consomment de vrais tokens). Le détail fournisseur par fournisseur est dans Comptes & coûts."
        source="/api/guide/overview → usage.cout_30j_usd"
        lien={{ vue: 'accounts', texte: 'Comptes & coûts' }}
      />
      <Tuile
        titre="Échanges vocaux (7 jours)"
        valeur={v?.echanges_7j != null ? formatNombre(v.echanges_7j) : null}
        detail={
          v ? (
            <>
              {v.latence_p50_ms != null && (
                <span>
                  <Terme id="mediane">Médiane</Terme> : {formatDureeMs(v.latence_p50_ms)} côté moteur
                </span>
              )}
              {parseDate(v.dernier_echange) && (
                <span className="block text-slate-400">Dernier échange {ilYA(parseDate(v.dernier_echange))}</span>
              )}
            </>
          ) : undefined
        }
        signification="Phrases dites à l’écran de la maison et traitées par le moteur. La durée mesurée ne compte pas la reconnaissance vocale ni la lecture de la réponse, qui se font ailleurs."
        source="/api/guide/overview → vocal"
        lien={{ vue: 'demo', texte: 'Voir la démo vocale' }}
      />
      <Tuile
        titre="Dernière sauvegarde"
        valeur={valeurSauvegarde}
        ton={tonSauvegarde}
        detail={
          s ? (
            <>
              {s.nombre != null && <span>{formatNombre(s.nombre)} sauvegarde{s.nombre > 1 ? 's' : ''} conservée{s.nombre > 1 ? 's' : ''}</span>}
              {tonSauvegarde === 'bad' && (
                <span className="block font-medium text-red-300">
                  {sauv.date ? `Plus de ${SEUIL_SAUVEGARDE_H} h : à refaire.` : 'Aucune sauvegarde : les données ne sont pas protégées.'}
                </span>
              )}
            </>
          ) : undefined
        }
        signification={
          <>
            Copie de la mémoire et des réglages du moteur, pour pouvoir les retrouver après une panne ou une erreur.
            Au-delà de {SEUIL_SAUVEGARDE_H} h sans sauvegarde, la tuile passe au rouge.
          </>
        }
        source="/api/guide/overview → sauvegardes"
        lien={{ vue: 'sauvegardes', texte: 'Gérer les sauvegardes' }}
      />
    </div>
  );
}

/* ── Boucles de fond ──────────────────────────────────────────────────────── */

function BouclesDeFond({ o }: { o: GuideOverview }) {
  const boucles = o.boucles;
  if (!Array.isArray(boucles) || boucles.length === 0) return null;
  return (
    <Card
      title="Ce que le moteur fait tout seul"
      subtitle={
        <>
          Les <Terme id="boucle-de-fond">boucles de fond</Terme> travaillent sans que vous le demandiez. Chacune a un réglage qui
          l’active ou la coupe.
        </>
      }
      source="GET /api/guide/overview → boucles"
    >
      <ul className="grid gap-3 sm:grid-cols-2">
        {boucles.map((b) => (
          <li key={b.nom} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 break-words text-sm font-medium text-slate-100">{b.nom}</p>
              {b.active == null ? <Pill>état inconnu</Pill> : b.active ? <Pill tone="good">active</Pill> : <Pill>coupée</Pill>}
            </div>
            {b.description && <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{b.description}</p>}
            {b.reglage && (
              <p className="mt-2 break-words text-xs text-slate-500">
                Réglage : <code className="font-mono text-slate-400">{b.reglage}</code>
              </p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ── Contenu explicatif (indépendant des routes) ─────────────────────────── */

interface CarteMoteur {
  titre: string;
  icone: typeof Mic;
  resume: ReactNode;
  details: ReactNode;
  liens: Array<{ vue: ViewKey; texte: string }>;
}

const CE_QUE_FAIT_LE_MOTEUR: CarteMoteur[] = [
  {
    titre: 'Domotique vocale',
    icone: Mic,
    resume: 'Vous parlez à l’écran de la maison ; le moteur comprend et agit sur la maison.',
    details: (
      <>
        Les ordres simples (« allume le salon ») sont reconnus par des règles et exécutés sans{' '}
        <Terme id="llm">modèle de langage</Terme> : c’est le <Terme id="fast-path">fast-path</Terme>, de l’ordre de 0,1 à
        0,2 seconde côté moteur (mesure du 01/11/2026). Le modèle ne sert que pour la discussion libre.
      </>
    ),
    liens: [
      { vue: 'demo', texte: 'Démo vocale & maison' },
      { vue: 'homeassistant', texte: 'Domotique & vocal' },
    ],
  },
  {
    titre: 'Assistant multi-modèles économe',
    icone: MessageSquare,
    resume: 'Une question, plusieurs modèles disponibles : le moteur essaie d’abord le moins cher.',
    details: (
      <>
        Il parcourt une <Terme id="cascade">cascade</Terme> : modèle local ou gratuit d’abord, puis le suivant en cas de panne
        ou de <Terme id="quota">quota</Terme> épuisé. Le <Terme id="budget-guard">BudgetGuard</Terme> coupe l’accès aux
        fournisseurs payants une fois le plafond du jour atteint.
      </>
    ),
    liens: [
      { vue: 'comprendre', texte: 'Comprendre le trajet' },
      { vue: 'llm', texte: 'Modèles & routage' },
    ],
  },
  {
    titre: 'Agents et automatisation',
    icone: Bot,
    resume: 'Pour un travail complexe, plusieurs agents se répartissent les rôles.',
    details: (
      <>
        Un <Terme id="planner">Planner</Terme> découpe la demande en <Terme id="dag">plan</Terme>, des{' '}
        <Terme id="agent">agents</Terme> exécutent les étapes, un <Terme id="reviewer">Reviewer</Terme> relit, et une{' '}
        <Terme id="hitl">validation humaine</Terme> peut être exigée avant d’agir. Des agents autonomes travaillent aussi tout
        seuls.
      </>
    ),
    liens: [
      { vue: 'agents', texte: 'Agents' },
      { vue: 'autonomy', texte: 'Agents autonomes' },
    ],
  },
];

const PAR_OU_COMMENCER: Array<{ vue: ViewKey; titre: string; texte: string; icone: typeof Compass }> = [
  { vue: 'comprendre', titre: 'Comprendre le moteur', texte: 'Suivez le trajet d’une demande sur la carte, puis rejouez un vrai échange vocal.', icone: MapIcon },
  { vue: 'demo', titre: 'Voir la démo vocale', texte: 'De « Okay Nabu » à l’action dans la maison, avec l’état de chaque maillon.', icone: Mic },
  { vue: 'chat', titre: 'Parler au moteur', texte: 'Posez une question dans le Chat : le moteur choisit le modèle qui répond.', icone: Sparkles },
];

function CeQueFaitLeMoteur() {
  return (
    <section aria-labelledby="titre-ce-que-fait">
      <h2 id="titre-ce-que-fait" className="mb-3 text-lg font-semibold text-slate-100">Ce que fait le moteur</h2>
      <div className="grid gap-4 lg:grid-cols-3">
        {CE_QUE_FAIT_LE_MOTEUR.map((c) => {
          const Icone = c.icone;
          return (
            <article key={c.titre} className="flex min-w-0 flex-col rounded-xl border border-slate-800 bg-slate-900/50 p-5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-500/15 text-sky-300">
                <Icone className="h-5 w-5" aria-hidden />
              </span>
              <h3 className="mt-3 text-base font-semibold text-slate-100">{c.titre}</h3>
              <p className="mt-1 text-sm font-medium text-slate-300">{c.resume}</p>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">{c.details}</p>
              <ul className="mt-auto flex flex-wrap gap-x-4 gap-y-1 pt-4 text-sm">
                {c.liens.map((l) => (
                  <li key={l.vue}>
                    <LienVue vue={l.vue}>{l.texte}</LienVue>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ParOuCommencer() {
  return (
    <section aria-labelledby="titre-commencer">
      <h2 id="titre-commencer" className="mb-3 text-lg font-semibold text-slate-100">Par où commencer</h2>
      <ol className="grid gap-3 md:grid-cols-3">
        {PAR_OU_COMMENCER.map((p, i) => {
          const Icone = p.icone;
          return (
            <li key={p.vue}>
              <a
                href={hashForView(p.vue)}
                className="group flex h-full items-start gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4 transition-colors hover:border-sky-700 hover:bg-slate-900"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky-600 text-sm font-semibold text-white">
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-100 group-hover:text-sky-300">
                    <Icone className="h-4 w-4 shrink-0" aria-hidden />
                    {p.titre}
                  </span>
                  <span className="mt-1 block text-sm leading-relaxed text-slate-400">{p.texte}</span>
                </span>
              </a>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/* ── Vue ──────────────────────────────────────────────────────────────────── */

export default function Accueil() {
  const q = useRouteQuery(['guide', 'overview'], fetchGuideOverview, { refetchInterval: 30_000 });
  // Le temps avance sans nouvelle donnée : un rendu par minute garde les « il y a… » justes.
  useTick(60_000);

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <GuideHeader
        cle="accueil"
        icone={House}
        titre="Accueil"
        objectif="Savoir en dix secondes si le moteur va bien, comprendre ce qu’il fait et choisir où aller ensuite."
        comprendre={
          <>
            <p>
              Le <strong className="text-slate-200">Moteur IA</strong> est un serveur qui reçoit des demandes (par la voix, le
              chat, des outils comme Claude ou Cursor, ou son interface web), décide comment y répondre au meilleur coût, puis
              agit : il commande la maison, interroge un modèle de langage ou confie un travail à des agents.
            </p>
            <p>
              Cette page lit l’état réel du moteur. Quand un chiffre est inconnu, elle écrit « — » et non zéro. Quand le moteur
              en service ne fournit pas une donnée, elle le dit au lieu de la remplacer par une estimation.
            </p>
          </>
        }
        sources={[{ route: 'GET /api/guide/overview', role: 'verdict, alertes, indicateurs, boucles de fond' }]}
      />

      <DataGate
        query={q}
        route={ROUTE}
        labelChargement="Lecture de l’état du moteur…"
      >
        {(o) => (
          <>
            <BlocVerdict o={o} onActualiser={() => void q.refetch()} actualisation={q.isFetching} />
            <section aria-labelledby="titre-alertes" className="space-y-3">
              <h2 id="titre-alertes" className="text-lg font-semibold text-slate-100">Alertes</h2>
              <BlocAlertes alertes={o.alertes} />
            </section>
            <section aria-labelledby="titre-indicateurs" className="space-y-3">
              <h2 id="titre-indicateurs" className="text-lg font-semibold text-slate-100">Les six chiffres à connaître</h2>
              <Indicateurs o={o} />
            </section>
            <BouclesDeFond o={o} />
          </>
        )}
      </DataGate>

      <CeQueFaitLeMoteur />
      <ParOuCommencer />
      <p className="flex items-center gap-2 text-xs text-slate-500">
        <Info className="h-3.5 w-3.5" aria-hidden />
        Besoin d’un terme ? Les mots soulignés en pointillés ouvrent leur définition au survol, au toucher ou au clavier.
      </p>
    </div>
  );
}
