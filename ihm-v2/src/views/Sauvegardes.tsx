/**
 * Sauvegardes : ce qui est protégé, la dernière sauvegarde, la liste avec contrôle
 * d'intégrité, un bouton pour en créer une (avec confirmation) et la procédure de
 * restauration pas à pas.
 *
 * Alerte rouge si la dernière sauvegarde a plus de 48 h ou n'existe pas. Rien n'est
 * déclaré « sauvegardé » sans que le moteur l'ait renvoyé : après une création, la
 * page affiche l'entrée renvoyée (fichiers, intégrité) puis relit l'inventaire.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Archive, CheckCircle2, CircleAlert, FileLock2, Info, Loader2, RotateCcw, ShieldAlert, ShieldCheck,
} from 'lucide-react';
import { creerSauvegarde, fetchSauvegardes } from '../api/sauvegardes';
import type { FichierSauvegarde, InventaireSauvegardes, Sauvegarde } from '../api/sauvegardes';
import { ApiError } from '../api/client';
import { useRouteQuery } from '../hooks/useRouteQuery';
import { useTick } from '../hooks/useTick';
import { formatDateHeure, formatNombre, formatOctets, ilYA, parseDate } from '../lib/format';
import { Button, Card, EmptyState, Pill } from '../components/ui/primitives';
import { GuideHeader } from '../components/guide/GuideHeader';
import { DataGate } from '../components/guide/etats';
import { Terme } from '../components/guide/Terme';

const ROUTE = '/api/sauvegardes';
const SEUIL_HEURES = 48;

/** Ce que le moteur sauvegarde (périmètre décrit par le module de sauvegarde du moteur). */
const PERIMETRE: Array<{ nom: string; quoi: string }> = [
  { nom: 'moteur_runtime.db', quoi: 'Journal d’activité : sessions, consommation de tokens, décisions de routage, échanges vocaux.' },
  { nom: 'memory.db', quoi: 'Mémoire du moteur : ce qu’il a retenu et peut retrouver.' },
  { nom: 'models_registry.db', quoi: 'Catalogue des modèles et leurs réglages.' },
  { nom: 'config.json', quoi: 'Réglages du moteur, modifiés à chaud depuis l’interface.' },
  { nom: 'ha_commands.json', quoi: 'Catalogue des commandes domotiques connues.' },
  { nom: 'mcp_activation_rules.json', quoi: 'Règles d’activation des outils MCP.' },
  { nom: 'agents_workflows.json', quoi: 'Workflows des agents.' },
  { nom: 'prompts/agents', quoi: 'Consignes des agents, éditables depuis l’interface.' },
];

const JAMAIS = [
  { nom: '.env', quoi: 'Le fichier des clés d’accès (fournisseurs de modèles, Home Assistant…).' },
  { nom: 'Clés, jetons, certificats', quoi: 'Tout fichier dont le nom évoque un secret : jeton, mot de passe, clé privée, certificat.' },
];

function resumeIntegrite(fichiers: FichierSauvegarde[]): { tone: 'good' | 'bad' | 'neutral'; libelle: string } {
  if (fichiers.length === 0) return { tone: 'neutral', libelle: 'aucun fichier listé' };
  if (fichiers.some((f) => f.integrite === 'ko')) return { tone: 'bad', libelle: 'anomalie détectée' };
  // Seules les bases SQLite passent un contrôle d'intégrité ; les autres fichiers n'ont
  // qu'une empreinte (integrite = null). Les exiger « ok » rendrait « intègre » inatteignable.
  const controles = fichiers.filter((f) => f.integrite != null);
  if (controles.length === 0) return { tone: 'neutral', libelle: 'non contrôlée' };
  if (controles.every((f) => f.integrite === 'ok')) return { tone: 'good', libelle: 'intègre' };
  return { tone: 'neutral', libelle: 'partiellement contrôlée' };
}

function PastilleIntegrite({ valeur }: { valeur: FichierSauvegarde['integrite'] }) {
  if (valeur === 'ok') return <Pill tone="good">intègre</Pill>;
  if (valeur === 'ko') return <Pill tone="bad">anomalie</Pill>;
  return <Pill>empreinte notée</Pill>;
}

function presentDans(nom: string, fichiers: FichierSauvegarde[] | null | undefined): boolean | null {
  if (!fichiers) return null;
  return fichiers.some((f) => f.nom === nom || f.nom.startsWith(`${nom}/`) || (nom === 'prompts/agents' && f.nom.startsWith('prompts')));
}

/* ── Alerte et dernière sauvegarde ───────────────────────────────────────── */

function derniereSauvegarde(inv: InventaireSauvegardes): { entree: Sauvegarde | null; date: Date | null } {
  const entree = (inv.sauvegardes ?? []).find((s) => s.valide !== false) ?? null;
  return { entree, date: parseDate(inv.derniere ?? entree?.cree_le) };
}

function BandeauAlerte({ inv }: { inv: InventaireSauvegardes }) {
  const { entree, date } = derniereSauvegarde(inv);
  const heures = date ? (Date.now() - date.getTime()) / 3_600_000 : null;
  const anomalie = entree ? resumeIntegrite(entree.fichiers ?? []).tone === 'bad' : false;
  if (heures === null) {
    return (
      <div role="alert" className="flex items-start gap-3 rounded-xl border border-red-800/70 bg-red-950/30 p-4">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-red-400" aria-hidden />
        <div>
          <p className="font-semibold text-red-200">Aucune sauvegarde.</p>
          <p className="mt-0.5 text-sm text-red-200/80">
            Si le disque tombe en panne ou qu’une erreur écrase la mémoire du moteur, rien ne permettra de la retrouver. Créez une
            sauvegarde maintenant.
          </p>
        </div>
      </div>
    );
  }
  if (heures > SEUIL_HEURES || anomalie) {
    return (
      <div role="alert" className="flex items-start gap-3 rounded-xl border border-red-800/70 bg-red-950/30 p-4">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-red-400" aria-hidden />
        <div>
          <p className="font-semibold text-red-200">
            {heures > SEUIL_HEURES
              ? `La dernière sauvegarde date de plus de ${SEUIL_HEURES} h (${ilYA(date)}).`
              : 'La dernière sauvegarde présente une anomalie d’intégrité.'}
          </p>
          <p className="mt-0.5 text-sm text-red-200/80">
            {heures > SEUIL_HEURES
              ? 'Elle est trop ancienne pour protéger le travail récent : refaites-en une.'
              : 'Ne vous y fiez pas avant de l’avoir recontrôlée ; créez-en une nouvelle.'}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-3 rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-4">
      <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" aria-hidden />
      <p className="text-sm text-emerald-100">
        <span className="font-semibold">Les données sont protégées :</span> dernière sauvegarde {ilYA(date)}, moins de {SEUIL_HEURES} h.
      </p>
    </div>
  );
}

function CarteDerniere({ inv }: { inv: InventaireSauvegardes }) {
  const { entree, date } = derniereSauvegarde(inv);
  const fichiers = entree?.fichiers ?? [];
  const integ = resumeIntegrite(fichiers);
  return (
    <Card title="Dernière sauvegarde" source="GET /api/sauvegardes">
      {entree ? (
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Créée</dt>
            <dd className="mt-0.5 text-slate-100">{ilYA(date)}</dd>
            <dd className="text-xs text-slate-500">{formatDateHeure(date)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Taille</dt>
            <dd className="mt-0.5 font-mono text-slate-100">{formatOctets(entree.taille_octets)}</dd>
            <dd className="text-xs text-slate-500">{formatNombre(fichiers.length)} fichiers</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500"><Terme id="integrite">Intégrité</Terme></dt>
            <dd className="mt-0.5"><Pill tone={integ.tone}>{integ.libelle}</Pill></dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Version du moteur</dt>
            <dd className="mt-0.5 font-mono text-slate-100">{entree.commit ? entree.commit.slice(0, 8) : '—'}</dd>
          </div>
          <div className="sm:col-span-2 lg:col-span-4">
            <dt className="text-xs text-slate-500">Dossier de sauvegarde</dt>
            <dd className="mt-0.5 break-all font-mono text-xs text-slate-300">{inv.dossier ?? '—'}</dd>
          </div>
          <div className="sm:col-span-2 lg:col-span-4">
            <dt className="text-xs text-slate-500"><Terme id="retention">Rétention</Terme></dt>
            <dd className="mt-0.5 text-slate-300">
              {inv.retention != null ? `${formatNombre(inv.retention)} sauvegardes conservées ; les plus anciennes sont supprimées après une nouvelle sauvegarde réussie.` : '—'}
            </dd>
          </div>
        </dl>
      ) : (
        <EmptyState>Aucune sauvegarde dans {inv.dossier ?? 'le dossier prévu'}.</EmptyState>
      )}
    </Card>
  );
}

/* ── Périmètre ───────────────────────────────────────────────────────────── */

function CartePerimetre({ inv }: { inv: InventaireSauvegardes }) {
  const { entree } = derniereSauvegarde(inv);
  return (
    <Card
      title="Ce qui est sauvegardé… et ce qui ne l’est jamais"
      subtitle="Une colonne de contrôle compare ce périmètre aux fichiers réellement présents dans la dernière sauvegarde."
    >
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-200">Sauvegardé</h3>
          <ul className="divide-y divide-slate-800/70 overflow-hidden rounded-lg border border-slate-800">
            {PERIMETRE.map((p) => {
              const present = presentDans(p.nom, entree?.fichiers);
              return (
                <li key={p.nom} className="flex items-start gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="break-all font-mono text-sm text-slate-100">{p.nom}</p>
                    <p className="text-sm leading-relaxed text-slate-400">{p.quoi}</p>
                  </div>
                  <span className="shrink-0 pt-0.5 text-xs">
                    {present === null ? (
                      <span className="text-slate-600" title="Pas de sauvegarde à comparer">—</span>
                    ) : present ? (
                      <span className="inline-flex items-center gap-1 text-emerald-400"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden />présent</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-amber-400"><CircleAlert className="h-3.5 w-3.5" aria-hidden />absent</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-200">Jamais sauvegardé</h3>
          <ul className="space-y-2">
            {JAMAIS.map((p) => (
              <li key={p.nom} className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2.5">
                <FileLock2 className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
                <div className="min-w-0">
                  <p className="break-all font-mono text-sm text-slate-100">{p.nom}</p>
                  <p className="text-sm leading-relaxed text-slate-400">{p.quoi}</p>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-slate-400">
            <strong className="text-slate-300">Pourquoi :</strong> une sauvegarde qui contiendrait les clés serait un second endroit à
            protéger, et à voir fuir. Après une restauration, le <code className="font-mono text-xs">.env</code> actuel reste en
            place : les clés ne sont pas touchées.
          </p>
        </div>
      </div>
    </Card>
  );
}

/* ── Création ────────────────────────────────────────────────────────────── */

function messageErreur(err: unknown): { texte: string; info: boolean } {
  if (err instanceof ApiError && err.status === 409) {
    return { texte: 'Une sauvegarde est déjà en cours. Patientez quelques instants, puis actualisez la liste.', info: true };
  }
  if (err instanceof ApiError && err.status === 404) {
    return { texte: 'Donnée non disponible : route /api/sauvegardes absente sur ce moteur.', info: true };
  }
  return { texte: err instanceof Error ? err.message : String(err), info: false };
}

function Creation({ inv }: { inv: InventaireSauvegardes }) {
  const qc = useQueryClient();
  const [confirmer, setConfirmer] = useState(false);
  const creation = useMutation({
    mutationFn: creerSauvegarde,
    onSettled: () => {
      setConfirmer(false);
      void qc.invalidateQueries({ queryKey: ['sauvegardes'] });
      void qc.invalidateQueries({ queryKey: ['guide', 'overview'] });
    },
  });
  const creee = creation.data;
  const integ = creee ? resumeIntegrite(creee.fichiers ?? []) : null;
  const err = creation.error ? messageErreur(creation.error) : null;

  return (
    <Card title="Créer une sauvegarde maintenant" subtitle="À faire avant une modification risquée, ou si la dernière est trop ancienne.">
      {!confirmer ? (
        <Button onClick={() => { creation.reset(); setConfirmer(true); }} disabled={creation.isPending}>
          <Archive className="h-4 w-4" aria-hidden />
          Créer une sauvegarde maintenant
        </Button>
      ) : (
        <div role="alertdialog" aria-labelledby="titre-confirmation" className="space-y-3 rounded-xl border border-amber-800/60 bg-amber-950/20 p-4">
          <p id="titre-confirmation" className="font-semibold text-amber-100">Créer une sauvegarde maintenant ?</p>
          <p className="text-sm leading-relaxed text-amber-100/80">
            Le moteur va copier ses bases, ses réglages et les consignes des agents dans{' '}
            <code className="break-all font-mono text-xs">{inv.dossier ?? 'le dossier de sauvegarde'}</code>. Les bases sont copiées à chaud
            : le moteur reste utilisable. Les clés et le <code className="font-mono text-xs">.env</code> ne sont pas copiés.
            {inv.retention != null && ` Une fois ${formatNombre(inv.retention)} sauvegardes atteintes, les plus anciennes sont supprimées après une copie réussie.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => creation.mutate()} disabled={creation.isPending}>
              {creation.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Archive className="h-4 w-4" aria-hidden />}
              {creation.isPending ? 'Sauvegarde en cours…' : 'Oui, créer la sauvegarde'}
            </Button>
            <Button variant="secondary" onClick={() => setConfirmer(false)} disabled={creation.isPending}>Annuler</Button>
          </div>
        </div>
      )}

      {err && (
        <p
          role="alert"
          className={`mt-3 rounded-lg border px-3 py-2.5 text-sm ${
            err.info ? 'border-slate-700 bg-slate-900/60 text-slate-300' : 'border-red-900/60 bg-red-950/30 text-red-300'
          }`}
        >
          {err.info ? <Info className="mr-1.5 inline h-4 w-4 align-text-bottom" aria-hidden /> : <CircleAlert className="mr-1.5 inline h-4 w-4 align-text-bottom" aria-hidden />}
          {err.texte}
        </p>
      )}

      {creee && integ && (
        <div className={`mt-3 rounded-lg border px-3 py-2.5 text-sm ${integ.tone === 'bad' ? 'border-red-900/60 bg-red-950/30' : 'border-emerald-800/60 bg-emerald-950/20'}`} role="status">
          <p className={integ.tone === 'bad' ? 'font-medium text-red-200' : 'font-medium text-emerald-100'}>
            {integ.tone === 'bad'
              ? `Sauvegarde créée mais marquée en anomalie : ${creee.id}.`
              : `Sauvegarde créée par le moteur : ${creee.id}.`}
          </p>
          <p className="mt-0.5 text-slate-300">
            {formatNombre(creee.fichiers?.length ?? 0)} fichiers, {formatOctets(creee.taille_octets)}, contrôle d’intégrité :{' '}
            <strong>{integ.libelle}</strong>. Elle apparaît dans la liste ci-dessous.
          </p>
        </div>
      )}
    </Card>
  );
}

/* ── Liste ───────────────────────────────────────────────────────────────── */

function ListeSauvegardes({ liste }: { liste: Sauvegarde[] }) {
  if (liste.length === 0) return <EmptyState>Aucune sauvegarde pour le moment.</EmptyState>;
  return (
    <ul className="space-y-2.5">
      {liste.map((s) => {
        const fichiers = s.fichiers ?? [];
        const integ = s.valide === false ? { tone: 'bad' as const, libelle: 'manifeste illisible' } : resumeIntegrite(fichiers);
        const date = parseDate(s.cree_le);
        return (
          <li key={s.id} className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-100">{formatDateHeure(date)} <span className="font-normal text-slate-500">· {ilYA(date)}</span></p>
                <p className="mt-0.5 break-all font-mono text-xs text-slate-500">{s.id}</p>
              </div>
              <Pill tone={integ.tone}>{integ.libelle}</Pill>
            </div>
            <p className="mt-2 text-sm text-slate-400">
              {formatOctets(s.taille_octets)} · {formatNombre(fichiers.length)} fichiers · version {s.commit ? s.commit.slice(0, 8) : '—'}
            </p>
            {fichiers.length > 0 && (
              <details className="group mt-2">
                <summary className="cursor-pointer select-none text-xs font-medium text-sky-400 hover:text-sky-300">Voir les fichiers</summary>
                <ul className="mt-2 divide-y divide-slate-800/70 overflow-hidden rounded-lg border border-slate-800">
                  {fichiers.map((f) => (
                    <li key={f.nom} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2">
                      <span className="min-w-0 break-all font-mono text-xs text-slate-200">{f.nom}</span>
                      <span className="flex shrink-0 items-center gap-3">
                        <span className="font-mono text-xs text-slate-500">{formatOctets(f.taille_octets)}</span>
                        <PastilleIntegrite valeur={f.integrite} />
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* ── Restauration ────────────────────────────────────────────────────────── */

const ETAPES_RESTAURATION: Array<{ titre: string; detail: string }> = [
  { titre: 'Choisir la sauvegarde', detail: 'Dans la liste, repérez la plus récente dont l’intégrité est « intègre » et notez son identifiant (la date).' },
  { titre: 'Recontrôler cette copie', detail: 'Depuis le dossier du moteur : python -m core.sauvegarde --verifier <identifiant> (python3 sur le Steam Deck). La commande recalcule les empreintes et l’intégrité des bases ; elle doit annoncer que tout est sain.' },
  { titre: 'Arrêter le moteur', detail: 'Pour que plus rien n’écrive dans les bases pendant la copie. En production, c’est une décision à valider avec le responsable du moteur.' },
  { titre: 'Mettre l’état actuel de côté', detail: 'Copiez les fichiers actuels (bases, réglages) dans un dossier daté, sans rien supprimer : c’est votre retour arrière.' },
  { titre: 'Copier la sauvegarde à la place de l’original', detail: 'Pour chaque fichier de la sauvegarde, remplacez l’original par la copie, dans le dossier du moteur. Ne copiez pas le fichier manifeste.json, qui ne sert qu’à l’inventaire. Si une base remplacée a à côté d’elle des fichiers du même nom terminés par -wal ou -shm, mettez-les de côté avec l’ancien état : ils appartiennent à l’ancien fichier. Le .env n’est pas dans la sauvegarde et n’est pas touché.' },
  { titre: 'Redémarrer et vérifier', detail: 'Relancez le moteur, puis ouvrez l’Accueil : le verdict doit être au vert, la mémoire et les réglages doivent être ceux de la date choisie. Ne supprimez l’état mis de côté qu’après cette vérification.' },
];

function Restauration() {
  return (
    <Card
      title="Restaurer, pas à pas"
      subtitle="Il n’y a volontairement pas de bouton « restaurer » : écraser l’état vivant d’un clic est trop risqué. La restauration se fait à la main, avec un retour arrière."
    >
      <ol className="space-y-3">
        {ETAPES_RESTAURATION.map((e, i) => (
          <li key={e.titre} className="flex gap-3">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-800 text-sm font-semibold text-sky-300">{i + 1}</span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-100">{e.titre}</p>
              <p className="mt-0.5 break-words text-sm leading-relaxed text-slate-400">{e.detail}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="mt-4 flex items-start gap-2 text-xs leading-relaxed text-slate-500">
        <RotateCcw className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        La procédure est la même sur un poste de développement et en production ; seule la production demande un accord préalable.
      </p>
    </Card>
  );
}

/* ── Vue ─────────────────────────────────────────────────────────────────── */

export default function Sauvegardes() {
  const q = useRouteQuery(['sauvegardes'], fetchSauvegardes, { refetchInterval: 60_000 });
  useTick(60_000);
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <GuideHeader
        cle="sauvegardes"
        icone={Archive}
        titre="Sauvegardes"
        objectif="Savoir ce qui est protégé, vérifier que la dernière sauvegarde est récente et saine, en créer une, et savoir comment restaurer."
        comprendre={
          <>
            <p>
              Le moteur retient beaucoup de choses : sa mémoire, ses réglages, l’historique de son activité. Une sauvegarde en est une
              copie datée, mise à l’abri pour la retrouver après une panne ou une erreur.
            </p>
            <p>
              Chaque base copiée est relue en entier par SQLite : l’<Terme id="integrite">intégrité</Terme> « intègre » veut dire que les
              bases copiées sont lisibles et complètes. Les autres fichiers reçoivent une empreinte, recontrôlée avant une restauration. Une sauvegarde de plus de {SEUIL_HEURES} h, ou absente, déclenche une alerte rouge. Les clés d’accès ne sont
              jamais copiées.
            </p>
          </>
        }
        sources={[
          { route: 'GET /api/sauvegardes', role: 'inventaire, intégrité, dossier, rétention' },
          { route: 'POST /api/sauvegardes', role: 'création (après confirmation)' },
        ]}
      />

      <DataGate query={q} route={ROUTE} labelChargement="Lecture de l’inventaire des sauvegardes…">
        {(inv) => (
          <>
            <BandeauAlerte inv={inv} />
            <CarteDerniere inv={inv} />
            <CartePerimetre inv={inv} />
            <Creation inv={inv} />
            <Card
              title={`Sauvegardes disponibles (${formatNombre((inv.sauvegardes ?? []).length)})`}
              subtitle="De la plus récente à la plus ancienne."
              source="GET /api/sauvegardes → sauvegardes"
            >
              <ListeSauvegardes liste={inv.sauvegardes ?? []} />
            </Card>
          </>
        )}
      </DataGate>

      <Restauration />
    </div>
  );
}
