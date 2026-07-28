/**
 * Vue « Installation ».
 *
 * Nouvelle section : rien dans l'IHM ne permettait de répondre à « mon
 * installation est-elle complète ? ». Chaque contrôle affiché ici a été
 * réellement effectué par le serveur, et échoue avec une remédiation précise.
 */
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertTriangle, XCircle, Info, RefreshCw, Terminal } from 'lucide-react';
import { fetchSetupDiagnostics, type CheckStatus, type SetupCheck } from '../api/setup';
import {
  PageHeader, Card, Explain, Button, Metric,
  LoadingState, ErrorState, PausedState, queryPhase,
} from '../components/ui/primitives';

const ICONS: Record<CheckStatus, React.ElementType> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
  info: Info,
};

const COLORS: Record<CheckStatus, string> = {
  ok: 'text-emerald-400',
  warn: 'text-amber-400',
  error: 'text-red-400',
  info: 'text-slate-500',
};

const BORDERS: Record<CheckStatus, string> = {
  ok: 'border-slate-800',
  warn: 'border-amber-900/50',
  error: 'border-red-900/60',
  info: 'border-slate-800',
};

function CheckRow({ check }: { check: SetupCheck }) {
  const Icon = ICONS[check.status];
  return (
    <div className={`rounded-lg border ${BORDERS[check.status]} bg-slate-950/50 px-4 py-3`}>
      <div className="flex items-start gap-3">
        <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${COLORS[check.status]}`} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-200">{check.label}</p>
          <p className="mt-0.5 break-words text-xs text-slate-400">{check.detail}</p>
          {check.fix && (
            <div className="mt-2 rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2">
              <p className="flex items-start gap-1.5 text-xs text-slate-300">
                <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-sky-400" />
                <span>{check.fix}</span>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function Setup() {
  const q = useQuery({ queryKey: ['setup', 'diagnostics'], queryFn: fetchSetupDiagnostics });
  const phase = queryPhase(q);

  const summary = q.data?.summary;

  return (
    <div className="mx-auto max-w-4xl pb-20">
      <PageHeader
        title="Installation"
        description="L'état réel de votre installation, contrôle par contrôle. Chaque échec indique quoi faire."
        actions={
          <Button variant="secondary" onClick={() => q.refetch()} disabled={q.isFetching}>
            <RefreshCw className={`h-4 w-4 ${q.isFetching ? 'animate-spin' : ''}`} />
            Relancer
          </Button>
        }
      />

      <div className="space-y-6">
        {phase === 'loading' ? (
          <Card><LoadingState label="Contrôles en cours (connexions réelles)…" /></Card>
        ) : phase === 'paused' ? (
          <Card><PausedState onRetry={() => q.refetch()} /></Card>
        ) : phase === 'error' ? (
          <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>
        ) : (
          <>
            {summary && (
              <Card
                title={summary.operational ? 'Installation opérationnelle' : 'Installation incomplète'}
                subtitle={
                  summary.operational
                    ? "Aucun contrôle bloquant en échec. Les avertissements signalent des capacités optionnelles inactives."
                    : `${summary.errors} contrôle(s) bloquant(s) empêchent une partie du moteur de fonctionner.`
                }
                source="GET /api/setup/diagnostics"
              >
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Metric label="Contrôles" value={summary.total} />
                  <Metric label="Conformes" value={summary.ok} tone="good" />
                  <Metric
                    label="Avertissements"
                    value={summary.warnings}
                    tone={summary.warnings ? 'warn' : 'neutral'}
                  />
                  <Metric
                    label="Bloquants"
                    value={summary.errors}
                    tone={summary.errors ? 'bad' : 'good'}
                  />
                </div>
              </Card>
            )}

            {q.data?.groups.map((group) => {
              const worst: CheckStatus = group.checks.some((c) => c.status === 'error')
                ? 'error'
                : group.checks.some((c) => c.status === 'warn')
                  ? 'warn'
                  : 'ok';
              const Icon = ICONS[worst];
              return (
                <Card
                  key={group.id}
                  title={group.label}
                  actions={<Icon className={`h-5 w-5 ${COLORS[worst]}`} />}
                >
                  <div className="space-y-2">
                    {group.checks.map((c) => (
                      <CheckRow key={c.name} check={c} />
                    ))}
                  </div>
                </Card>
              );
            })}

            <Explain title="Installer le moteur depuis zéro">
              <p>Sur une machine vierge, dans l'ordre :</p>
              <ol className="ml-4 list-decimal space-y-1">
                <li>
                  Créer un environnement Python et installer les dépendances
                  (<code>fastapi</code>, <code>uvicorn</code>, <code>pydantic</code>,{' '}
                  <code>aiohttp</code>, <code>filelock</code>…).
                </li>
                <li>
                  Copier <code>.env.example</code> en <code>.env</code>, puis renseigner au
                  minimum <code>MOTEUR_API_KEY</code> et une clé de provider.
                </li>
                <li>
                  Peupler les bases : <code>seed_models_db.py</code>,{' '}
                  <code>seed_memory_db.py</code>, puis <code>populate_embeddings.py</code>.
                </li>
                <li>
                  Optionnel : <code>setup_google_oauth.py</code> pour les outils Google
                  Workspace.
                </li>
                <li>
                  Démarrer <code>gui_server.py</code>, puis revenir sur cette page : tous les
                  contrôles doivent être au vert.
                </li>
              </ol>
            </Explain>
          </>
        )}
      </div>
    </div>
  );
}

export default Setup;
