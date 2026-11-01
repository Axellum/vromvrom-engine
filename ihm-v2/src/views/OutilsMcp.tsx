/**
 * Outils MCP : ce que le moteur offre à Claude ou Cursor, et ce qu'il utilise lui-même.
 *
 * Tout vient de `GET /api/mcp/overview`. Rien n'est supposé : si le moteur n'expose
 * pas cette route, la page le dit ; un champ absent s'affiche « — ».
 */
import { useState } from 'react';
import { ArrowLeftRight, Check, Copy, Plug, ServerCog, Wrench } from 'lucide-react';
import { fetchMcpOverview } from '../api/mcp';
import type { McpOverview, McpServeurConsomme } from '../api/mcp';
import { useRouteQuery } from '../hooks/useRouteQuery';
import { Card, EmptyState, Pill } from '../components/ui/primitives';
import { GuideHeader } from '../components/guide/GuideHeader';
import { DataGate } from '../components/guide/etats';
import { Glose } from '../components/guide/Glose';
import { Terme } from '../components/guide/Terme';

const ROUTE = '/api/mcp/overview';

/** Copie un texte dans le presse-papiers ; l'échec (contexte non sécurisé) est silencieux. */
function BoutonCopier({ texte }: { texte: string }) {
  const [copie, setCopie] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copier le point d’entrée"
      title="Copier"
      onClick={() => {
        // Le moteur ajoute parfois un commentaire entre parenthèses (« … (transport stdio) ») : on ne copie que la commande.
        void navigator.clipboard
          ?.writeText(texte.replace(/\s*\([^)]*\)\s*$/, ''))
          .then(() => {
            setCopie(true);
            window.setTimeout(() => setCopie(false), 1500);
          })
          .catch(() => setCopie(false));
      }}
      className="shrink-0 rounded-md border border-slate-700 p-1.5 text-slate-400 transition-colors hover:border-sky-600 hover:text-sky-300"
    >
      {copie ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
    </button>
  );
}

function Schema() {
  const Boite = ({ titre, sous }: { titre: string; sous: string }) => (
    <div className="min-w-0 flex-1 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2.5 text-center">
      <p className="text-sm font-semibold text-slate-100">{titre}</p>
      <p className="text-xs text-slate-500">{sous}</p>
    </div>
  );
  const Lien = ({ etiquette }: { etiquette: string }) => (
    <div className="flex shrink-0 flex-col items-center justify-center px-1 py-1 text-slate-500 sm:flex-row sm:py-0" aria-hidden>
      <ArrowLeftRight className="h-4 w-4 rotate-90 sm:rotate-0" />
      <span className="text-[11px] font-medium uppercase tracking-wide sm:ml-1">{etiquette}</span>
    </div>
  );
  return (
    <figure className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex flex-col items-stretch sm:flex-row sm:items-center">
        <Boite titre="Claude, Cursor…" sous="applications d’IA" />
        <Lien etiquette="MCP" />
        <Boite titre="Moteur IA" sous="mémoire, maison, agents" />
        <Lien etiquette="MCP" />
        <Boite titre="Outils externes" sous="serveurs MCP que le moteur appelle" />
      </div>
      <figcaption className="mt-3 text-xs leading-relaxed text-slate-500">
        À gauche, le moteur est l’outil (colonne « Ce que le moteur offre »). À droite, le moteur est le client (colonne « Ce que le
        moteur utilise »). Le même protocole sert dans les deux sens.
      </figcaption>
    </figure>
  );
}

function Expose({ m }: { m: McpOverview }) {
  const e = m.expose;
  if (!e) return <EmptyState>Le moteur ne décrit pas les outils qu’il offre.</EmptyState>;
  const outils = e.outils ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {e.disponible == null ? <Pill>disponibilité inconnue</Pill> : e.disponible ? <Pill tone="good">disponible</Pill> : <Pill tone="warn">non disponible</Pill>}
        <span className="text-sm text-slate-400">
          {e.disponible === false
            ? 'Le moteur n’expose pas ses outils sur cette installation.'
            : 'Claude ou Cursor peuvent s’y connecter pour utiliser le moteur comme une boîte à outils.'}
        </span>
      </div>

      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Point d’entrée</p>
        {e.point_d_entree ? (
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 font-mono text-xs text-sky-300">
              {e.point_d_entree}
            </code>
            <BoutonCopier texte={e.point_d_entree} />
          </div>
        ) : (
          <p className="text-sm text-slate-500">— (le moteur n’indique pas de point d’entrée)</p>
        )}
        <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
          C’est l’adresse ou la commande à déclarer dans Claude ou Cursor pour qu’ils voient les outils ci-dessous.
        </p>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          Outils offerts ({outils.length})
        </p>
        {outils.length === 0 ? (
          <p className="text-sm text-slate-500">Aucun outil listé.</p>
        ) : (
          <ul className="divide-y divide-slate-800/70 overflow-hidden rounded-lg border border-slate-800">
            {outils.map((o) => (
              <li key={o.nom} className="px-3 py-2.5">
                <p className="flex items-center gap-2 break-all font-mono text-sm text-slate-100">
                  <Wrench className="h-3.5 w-3.5 shrink-0 text-sky-400" aria-hidden />
                  {o.nom}
                </p>
                <p className="mt-0.5 break-words pl-5 text-sm leading-relaxed text-slate-400">
                  {o.description || <span className="text-slate-600">(pas de description)</span>}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Serveur({ s }: { s: McpServeurConsomme }) {
  const regles = s.regles ?? [];
  return (
    <li className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 break-all font-mono text-sm font-medium text-slate-100">{s.nom}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          {s.type && <Pill tone="info">{s.type}</Pill>}
          {/* null = lancé seulement si la demande contient un mot-clé (contrat de /api/mcp/overview) */}
          {s.actif == null ? (
            <Pill tone="warn">selon la demande</Pill>
          ) : s.actif ? (
            <Pill tone="good">actif</Pill>
          ) : (
            <Pill>inactif</Pill>
          )}
        </div>
      </div>
      <p className="mt-1.5 text-xs text-slate-500">Cible</p>
      <p className="break-all font-mono text-xs text-slate-300">{s.cible ?? '—'}</p>
      {regles.length > 0 ? (
        <details className="mt-2 group">
          <summary className="cursor-pointer select-none text-xs font-medium text-sky-400 hover:text-sky-300">
            {regles.length} règle{regles.length > 1 ? 's' : ''} d’activation
          </summary>
          <ul className="mt-1.5 space-y-1 border-l border-slate-800 pl-3">
            {regles.map((r, i) => (
              <li key={`${i}-${r}`} className="break-words text-xs leading-relaxed text-slate-400">{r}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="mt-2 text-xs text-slate-600">Aucune règle d’activation listée.</p>
      )}
    </li>
  );
}

function Consomme({ m }: { m: McpOverview }) {
  const serveurs = m.consomme;
  if (!Array.isArray(serveurs)) return <EmptyState>Le moteur ne décrit pas les outils qu’il utilise.</EmptyState>;
  if (serveurs.length === 0) return <EmptyState>Le moteur n’utilise aucun serveur MCP externe pour le moment.</EmptyState>;
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-400">
        {serveurs.length} serveur{serveurs.length > 1 ? 's' : ''} configuré{serveurs.length > 1 ? 's' : ''} :{' '}
        {serveurs.filter((s) => s.actif === true).length} lancé{serveurs.filter((s) => s.actif === true).length > 1 ? 's' : ''} à chaque
        demande, {serveurs.filter((s) => s.actif == null).length} selon les mots de la demande,{' '}
        {serveurs.filter((s) => s.actif === false).length} jamais. Les{' '}
        <strong className="text-slate-300">règles d’activation</strong> décident quand un outil est proposé aux agents.
      </p>
      <ul className="space-y-2.5">
        {serveurs.map((s) => <Serveur key={s.nom} s={s} />)}
      </ul>
    </div>
  );
}

export default function OutilsMcp() {
  const q = useRouteQuery(['mcp', 'overview'], fetchMcpOverview, { refetchInterval: 60_000 });
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <GuideHeader
        cle="mcp"
        icone={Plug}
        titre="Outils MCP"
        objectif="Voir quels outils le moteur offre à Claude ou Cursor, et lesquels il utilise lui-même."
        comprendre={
          <>
            <p>
              <Terme id="mcp">MCP</Terme> est une prise standard entre une application d’IA et des outils. Sans lui, chaque
              application doit apprendre à parler à chaque outil ; avec lui, un outil écrit une fois fonctionne partout.
            </p>
            <p>
              Ici, il sert <strong className="text-slate-200">dans les deux sens</strong>. Le moteur offre ses outils (lire son
              état, chercher dans sa mémoire, déléguer un raisonnement) à Claude ou Cursor : vous gardez votre éditeur et le
              moteur apporte ce qu’il sait. Et le moteur peut utiliser des outils d’autres serveurs MCP, dont l’activation est
              encadrée par des règles pour ne pas encombrer les modèles.
            </p>
          </>
        }
        sources={[{ route: 'GET /api/mcp/overview', role: 'outils offerts, serveurs utilisés, règles d’activation, notes' }]}
      />

      <Schema />

      <DataGate query={q} route={ROUTE} labelChargement="Lecture des outils MCP…">
        {(m) => (
          <>
            <div className="grid gap-6 lg:grid-cols-2">
              <Card
                title="Ce que le moteur offre à Claude et Cursor"
                subtitle="Le moteur joue le rôle d’outil."
                source="GET /api/mcp/overview → expose"
                actions={<ServerCog className="h-5 w-5 text-sky-400" aria-hidden />}
              >
                <Expose m={m} />
              </Card>
              <Card
                title="Ce que le moteur utilise"
                subtitle="Le moteur joue le rôle de client."
                source="GET /api/mcp/overview → consomme"
                actions={<Plug className="h-5 w-5 text-sky-400" aria-hidden />}
              >
                <Consomme m={m} />
              </Card>
            </div>
            {m.notes && m.notes.length > 0 && (
              <Card title="À savoir" source="GET /api/mcp/overview → notes">
                <ul className="space-y-2">
                  {m.notes.map((n, i) => (
                    <li key={`${i}-${n}`} className="flex gap-2.5 text-sm leading-relaxed text-slate-400">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600" aria-hidden />
                      <span className="min-w-0"><Glose texte={n} /></span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </>
        )}
      </DataGate>
    </div>
  );
}
