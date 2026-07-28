/**
 * Vue « Agents autonomes ».
 *
 * Nouvelle section : le moteur travaille en continu sans l'utilisateur (Daemon
 * Sentinelle toutes les 10 min, Dreamer nocturne, DreamCoder sur le backlog,
 * Auditeur périodique). Jusqu'ici, seul un interrupteur on/off était exposé dans
 * la vue Opérations — impossible de savoir ce que ces agents avaient fait, ni
 * pourquoi.
 *
 * L'objectif de cette page est de rendre l'autonomie lisible : ce qui tourne,
 * quand, ce que ça a trouvé, et ce que ça coûte.
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Shield, Moon, Search as SearchIcon, Play, AlertTriangle, CheckCircle2, Clock,
} from 'lucide-react';
import {
  fetchDaemonStatus, fetchDaemonLogs, fetchDreamerStatus, triggerDreamer,
  fetchPersistentConfig, updatePersistentConfig,
  type DaemonAnomaly,
} from '../api/autonomy';
import {
  PageHeader, Card, Explain, Warn, Value, Button, Metric, Pill, Table, Th, Td,
  LoadingState, ErrorState, EmptyState, PausedState, queryPhase,
} from '../components/ui/primitives';

const REFRESH_MS = 15_000;

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMin = Math.round((Date.now() - then) / 60000);
  if (diffMin < 1) return "à l'instant";
  if (diffMin < 60) return `il y a ${diffMin} min`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `il y a ${h} h`;
  return `il y a ${Math.floor(h / 24)} j`;
}

function anomalyTone(status: string): 'good' | 'warn' | 'bad' | 'info' {
  if (status === 'ok') return 'good';
  if (status === 'warning') return 'warn';
  if (status === 'error') return 'bad';
  return 'info';
}

/* ── Interrupteur générique sur persistent_agents ─────────────────────────── */

function EnableToggle({ configKey, enabled }: { configKey: string; enabled: boolean }) {
  const queryClient = useQueryClient();
  const mut = useMutation({
    mutationFn: (next: boolean) => updatePersistentConfig({ [configKey]: next }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['autonomy'] });
      void queryClient.invalidateQueries({ queryKey: ['config'] });
    },
  });

  return (
    <button
      onClick={() => mut.mutate(!enabled)}
      disabled={mut.isPending}
      title={`Écrit persistent_agents.${configKey} dans config.json`}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:opacity-50 ${
        enabled ? 'bg-sky-600' : 'bg-slate-700'
      }`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-white transition ${
          enabled ? 'translate-x-5' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

/* ── Daemon Sentinelle ────────────────────────────────────────────────────── */

function DaemonCard() {
  const q = useQuery({
    queryKey: ['autonomy', 'daemon'],
    queryFn: fetchDaemonStatus,
    refetchInterval: REFRESH_MS,
  });

  return (
    <Card
      title="Daemon Sentinelle"
      subtitle="Surveille en continu la santé du système : dépôts Git, Home Assistant, mémoire, charge."
      source="GET /api/daemon/status"
      actions={q.data && <EnableToggle configKey="daemon_enabled" enabled={q.data.enabled} />}
    >
      {queryPhase(q) === 'loading' ? (
        <LoadingState />
      ) : queryPhase(q) === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : queryPhase(q) === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              label="État"
              value={<Pill tone={q.data.running ? 'good' : q.data.enabled ? 'warn' : 'neutral'}>
                {q.data.running ? 'en marche' : q.data.enabled ? 'activé, arrêté' : 'désactivé'}
              </Pill>}
            />
            <Metric label="Cycles" value={q.data.total_cycles} hint={`toutes les ${q.data.interval_minutes} min`} />
            <Metric
              label="Dernier cycle"
              value={relativeTime(q.data.last_cycle_at)}
              hint={q.data.last_cycle_duration_ms ? `${Math.round(q.data.last_cycle_duration_ms)} ms` : undefined}
            />
            <Metric
              label="Erreurs"
              value={q.data.errors_count}
              tone={q.data.errors_count > 0 ? 'bad' : 'good'}
            />
          </div>

          {q.data.last_error && (
            <p className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 font-mono text-xs text-red-300">
              {q.data.last_error}
            </p>
          )}

          <div>
            <p className="mb-2 text-xs font-medium text-slate-400">
              Constats du dernier cycle ({q.data.anomalies.length})
            </p>
            {q.data.anomalies.length === 0 ? (
              <p className="flex items-center gap-2 text-sm text-emerald-400">
                <CheckCircle2 className="h-4 w-4" /> Aucune anomalie détectée.
              </p>
            ) : (
              <div className="space-y-2">
                {q.data.anomalies.map((a: DaemonAnomaly, i) => (
                  <div
                    key={`${a.check}-${i}`}
                    className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2"
                  >
                    <AlertTriangle
                      className={`mt-0.5 h-4 w-4 shrink-0 ${
                        a.status === 'error' ? 'text-red-400' : 'text-amber-400'
                      }`}
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <code className="text-xs text-sky-300">{a.check}</code>
                        <Pill tone={anomalyTone(a.status)}>{a.status}</Pill>
                      </div>
                      <p className="mt-0.5 text-sm text-slate-300">{a.alert}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function DaemonLogsCard() {
  const q = useQuery({
    queryKey: ['autonomy', 'daemon-logs'],
    queryFn: () => fetchDaemonLogs(30),
    refetchInterval: REFRESH_MS,
  });

  return (
    <Card
      title="Historique des cycles"
      subtitle="Chaque passage du daemon et le résultat de ses vérifications."
      source="GET /api/daemon/logs"
    >
      {queryPhase(q) === 'loading' ? (
        <LoadingState />
      ) : queryPhase(q) === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : queryPhase(q) === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (q.data?.logs.length ?? 0) === 0 ? (
        <EmptyState>Aucun cycle enregistré depuis le démarrage du moteur.</EmptyState>
      ) : (
        <div className="max-h-80 overflow-y-auto">
          <Table>
            <thead className="sticky top-0 bg-slate-900">
              <tr>
                <Th>Horodatage</Th>
                <Th align="right">Durée</Th>
                <Th align="right">Constats</Th>
                <Th>Vérifications</Th>
              </tr>
            </thead>
            <tbody>
              {q.data!.logs.map((log) => (
                <tr key={log.cycle_id} className="border-t border-slate-800/60">
                  <Td className="whitespace-nowrap text-xs text-slate-400">
                    {new Date(log.timestamp).toLocaleString('fr-FR')}
                  </Td>
                  <Td align="right" className="font-mono text-xs text-slate-400">
                    {Math.round(log.duration_ms)} ms
                  </Td>
                  <Td align="right">
                    <span className={log.anomalies_count > 0 ? 'text-amber-400' : 'text-emerald-400'}>
                      {log.anomalies_count}
                    </span>
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(log.checks_summary ?? {}).map(([check, status]) => (
                        <span
                          key={check}
                          title={`${check} : ${status}`}
                          className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                            status === 'ok' ? 'bg-emerald-500/15 text-emerald-300'
                            : status === 'warning' ? 'bg-amber-500/15 text-amber-300'
                            : status === 'error' ? 'bg-red-500/15 text-red-300'
                            : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {check}
                        </span>
                      ))}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Card>
  );
}

/* ── Dreamer ──────────────────────────────────────────────────────────────── */

function DreamerCard() {
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();

  const q = useQuery({
    queryKey: ['autonomy', 'dreamer'],
    queryFn: fetchDreamerStatus,
    refetchInterval: REFRESH_MS,
  });

  const trigger = useMutation({
    mutationFn: triggerDreamer,
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: ['autonomy', 'dreamer'] });
    },
  });

  const actions = q.data?.last_report?.actions ?? {};

  return (
    <Card
      title="Dreamer & DreamCoder"
      subtitle="Consolide la mémoire la nuit, puis puise dans le backlog pour coder de façon autonome."
      source="GET /api/dreamer/status"
      actions={
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setConfirming(true)} disabled={trigger.isPending || q.data?.running}>
            <Play className="h-4 w-4" />
            {trigger.isPending ? 'Cycle en cours…' : 'Déclencher'}
          </Button>
          {q.data && <EnableToggle configKey="dreamer_enabled" enabled={q.data.enabled} />}
        </div>
      }
    >
      {queryPhase(q) === 'loading' ? (
        <LoadingState />
      ) : queryPhase(q) === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : queryPhase(q) === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data ? (
        <div className="space-y-4">
          {confirming && (
            <Warn>
              <div className="space-y-2">
                <p>
                  Un cycle manuel consomme de <strong>vrais appels LLM</strong> et peut modifier
                  la mémoire du moteur ainsi que le dépôt Git configuré pour le DreamCoder.
                </p>
                <div className="flex gap-2">
                  <Button onClick={() => trigger.mutate()} disabled={trigger.isPending}>
                    Confirmer le cycle
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirming(false)}>Annuler</Button>
                </div>
              </div>
            </Warn>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              label="État"
              value={<Pill tone={q.data.running ? 'info' : q.data.enabled ? 'good' : 'neutral'}>
                {q.data.running ? 'cycle en cours' : q.data.enabled ? 'en veille' : 'désactivé'}
              </Pill>}
            />
            <Metric label="Cycles" value={q.data.total_runs} />
            <Metric
              label="Dernier cycle"
              value={relativeTime(q.data.last_run_at)}
              hint={q.data.last_run_duration_ms ? `${Math.round(q.data.last_run_duration_ms)} ms` : undefined}
            />
            <Metric
              label="Sur inactivité"
              value={<Value value={q.data.idle_trigger_hours} unit=" h" />}
            />
          </div>

          {q.data.last_report?.summary && (
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
              <p className="text-xs text-slate-500">Résumé du dernier rapport</p>
              <p className="mt-0.5 text-sm text-slate-300">{q.data.last_report.summary}</p>
            </div>
          )}

          {Object.keys(actions).length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-slate-400">Actions du dernier cycle</p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {Object.entries(actions).map(([name, detail]) => (
                  <div key={name} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                    <p className="font-mono text-xs text-sky-300">{name}</p>
                    <div className="mt-1 space-y-0.5">
                      {Object.entries(detail ?? {}).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2 text-xs">
                          <span className="text-slate-600">{k}</span>
                          <span className={`font-mono ${k === 'error' && v ? 'text-red-400' : 'text-slate-400'}`}>
                            {v === null || v === undefined || v === '' ? '—' : String(v).slice(0, 60)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {trigger.isError && <ErrorState error={trigger.error} />}
          {trigger.isSuccess && (
            <p className="text-xs text-emerald-400">Cycle terminé — statut rafraîchi.</p>
          )}
        </div>
      ) : null}
    </Card>
  );
}

/* ── Auditeur ─────────────────────────────────────────────────────────────── */

function AuditorCard() {
  const q = useQuery({
    queryKey: ['autonomy', 'persistent-config'],
    queryFn: fetchPersistentConfig,
    refetchInterval: REFRESH_MS,
  });

  const cfg = q.data ?? {};
  const enabled = Boolean(cfg.auditor_enabled);

  return (
    <Card
      title="Agent Auditeur"
      subtitle="Analyse périodiquement le code du moteur et remonte ses constats."
      source="GET /api/persistent-agents/config"
      actions={q.data && <EnableToggle configKey="auditor_enabled" enabled={enabled} />}
    >
      {queryPhase(q) === 'loading' ? (
        <LoadingState />
      ) : queryPhase(q) === 'paused' ? (
        <PausedState onRetry={() => q.refetch()} />
      ) : queryPhase(q) === 'error' ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              label="État"
              value={<Pill tone={enabled ? 'good' : 'neutral'}>{enabled ? 'activé' : 'désactivé'}</Pill>}
            />
            <Metric label="Déclencheur" value={String(cfg.auditor_trigger_mode ?? '—')} />
            <Metric label="Seuil de tâches" value={<Value value={cfg.auditor_trigger_task_count as number} />} />
            <Metric label="Tier" value={String(cfg.auditor_model_tier ?? '—')} />
          </div>

          {Array.isArray(cfg.auditor_scopes) && (
            <div>
              <p className="mb-1.5 text-xs text-slate-500">Périmètres audités</p>
              <div className="flex flex-wrap gap-1.5">
                {(cfg.auditor_scopes as string[]).map((s) => (
                  <span key={s} className="rounded-md border border-slate-700 px-2 py-0.5 font-mono text-xs text-slate-400">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}

          <p className="text-xs text-slate-600">
            Le moteur n'expose pas de statut d'exécution pour l'auditeur : contrairement au
            daemon et au dreamer, il n'a pas d'endpoint <code>/status</code>. Ce qui est
            affiché ici est sa <strong>configuration</strong>, pas son activité. Ses constats
            arrivent dans le backlog. Les 11 réglages détaillés sont dans{' '}
            <strong>Configuration → Agents autonomes</strong>.
          </p>
        </div>
      )}
    </Card>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export function Autonomy() {
  return (
    <div className="mx-auto max-w-6xl pb-20">
      <PageHeader
        title="Agents autonomes"
        description="Ce que le moteur fait quand vous ne lui demandez rien — et ce que ça vous coûte."
      />

      <div className="space-y-6">
        <Explain title="Trois agents, trois rythmes">
          <p>
            <span className="inline-flex items-center gap-1 font-medium text-slate-300">
              <Shield className="h-3.5 w-3.5" /> Le Daemon Sentinelle
            </span>{' '}
            tourne en permanence et vérifie l'état du système à intervalle fixe. Il ne
            corrige rien : il constate et alerte.
          </p>
          <p>
            <span className="inline-flex items-center gap-1 font-medium text-slate-300">
              <Moon className="h-3.5 w-3.5" /> Le Dreamer
            </span>{' '}
            se déclenche la nuit (ou après une longue inactivité) pour consolider la mémoire.
            Sa variante DreamCoder puise dans le backlog et écrit réellement du code sur une
            branche Git dédiée.
          </p>
          <p>
            <span className="inline-flex items-center gap-1 font-medium text-slate-300">
              <SearchIcon className="h-3.5 w-3.5" /> L'Auditeur
            </span>{' '}
            relit le code du moteur par lots. Désactivé par défaut, car il consomme beaucoup
            de tokens d'entrée.
          </p>
          <p className="flex items-center gap-1.5 pt-1 text-slate-500">
            <Clock className="h-3.5 w-3.5" />
            Chaque cycle consomme des appels LLM. L'intervalle du daemon est le réglage qui
            pèse le plus sur le coût de fonctionnement au repos.
          </p>
        </Explain>

        <DaemonCard />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <DreamerCard />
          <AuditorCard />
        </div>
        <DaemonLogsCard />
      </div>
    </div>
  );
}

export default Autonomy;
