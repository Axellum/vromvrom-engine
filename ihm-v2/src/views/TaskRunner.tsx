/**
 * Vue « Lancer une tâche ».
 *
 * Réécrite : l'ancienne version affichait un panneau « Tâche en cours » codé en
 * dur (agent `executor`, phase `tool_call`, 4 250 tokens, 12 s — jamais reliés à
 * quoi que ce soit) et une file d'attente inventée sur `/api/queue`, endpoint
 * inexistant. Elle exposait aussi des contrôles (mode, agents, budget, override
 * LLM) que `POST /api/run` ignore : son modèle Pydantic n'accepte que
 * `objective`, Pydantic écarte silencieusement le reste.
 *
 * Ici, deux chemins réels et distincts :
 *  - orchestration immédiate en arrière-plan  → POST /api/run
 *  - mise en file pour traitement de fond      → /api/backlog/tasks (DreamCoder)
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Rocket, Trash2, ListPlus, Square, GitBranch } from 'lucide-react';
import { apiFetch } from '../api/client';
import {
  fetchBacklogTasks, fetchBacklogStats, createBacklogTask, deleteBacklogTask,
  type BacklogTask, type BacklogStatus,
} from '../api/backlog';
import { useEngineStore } from '../state/engineStore';
import {
  PageHeader, Card, Explain, Warn, Button, Metric, Pill,
  LoadingState, ErrorState, EmptyState,
} from '../components/ui/primitives';

const PRIORITIES = [
  { value: 1, label: 'Haute' },
  { value: 2, label: 'Moyenne' },
  { value: 3, label: 'Basse' },
];

const STATUS_TONE: Record<BacklogStatus, 'neutral' | 'good' | 'warn' | 'bad' | 'info'> = {
  pending: 'neutral',
  running: 'info',
  completed: 'good',
  failed: 'bad',
  paused: 'warn',
  abandoned: 'neutral',
};

/* ── Orchestration immédiate ──────────────────────────────────────────────── */

function RunNowCard() {
  const [objective, setObjective] = useState('');
  const status = useEngineStore((s) => s.status);
  const isRunning = status === 'running';

  const run = useMutation({
    mutationFn: (obj: string) =>
      apiFetch<{ session_id: string }>('/api/run', {
        method: 'POST',
        body: JSON.stringify({ objective: obj }),
      }),
    onSuccess: () => setObjective(''),
  });

  const stop = useMutation({
    mutationFn: () => apiFetch('/api/stop', { method: 'POST' }),
  });

  return (
    <Card
      title="Orchestration immédiate"
      subtitle="Lance le pipeline complet (Planner → DAG → Executor → Reviewer) en arrière-plan."
      source="POST /api/run"
    >
      <div className="space-y-4">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-slate-400">
            Objectif
          </label>
          <textarea
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            rows={4}
            disabled={isRunning}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500 disabled:opacity-50"
            placeholder="Ex : Analyse les logs du daemon des dernières 24 h et résume les anomalies."
          />
          <p className="mt-1.5 text-xs text-slate-600">
            L'endpoint n'accepte que l'objectif. Le choix des agents, du modèle et du
            budget est décidé par le routeur et la configuration du moteur — pour
            forcer une puissance sur une requête ponctuelle, utilisez le{' '}
            <strong>Chat</strong> et son sélecteur de tier.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button
            onClick={() => run.mutate(objective)}
            disabled={!objective.trim() || run.isPending || isRunning}
          >
            <Rocket className="h-4 w-4" />
            {run.isPending ? 'Lancement…' : 'Lancer maintenant'}
          </Button>
          {isRunning && (
            <Button variant="danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
              <Square className="h-4 w-4" />
              Arrêter
            </Button>
          )}
        </div>

        {isRunning && (
          <Warn>
            Une exécution est déjà en cours. Le moteur refuse un second lancement
            simultané (verrou <code>execution_lock</code>) — attendez la fin ou arrêtez-la.
          </Warn>
        )}
        {run.isError && <ErrorState error={run.error} />}
        {stop.isError && <ErrorState error={stop.error} />}
      </div>
    </Card>
  );
}

/* ── État live (vrai SSE) ─────────────────────────────────────────────────── */

function LiveStatusCard() {
  const status = useEngineStore((s) => s.status);
  const objective = useEngineStore((s) => s.objective);
  const sessionId = useEngineStore((s) => s.sessionId);
  const engineState = useEngineStore((s) => s.engineState);
  const errorMessage = useEngineStore((s) => s.errorMessage);
  const connection = useEngineStore((s) => s.connection);
  const events = useEngineStore((s) => s.events);

  const history = engineState?.history ?? [];
  const queue = engineState?.task_queue ?? [];
  const lastStep = history[history.length - 1];

  const tone =
    status === 'running' ? 'info' : status === 'error' ? 'bad' :
    status === 'success' || status === 'completed' ? 'good' : 'neutral';

  return (
    <Card
      title="Exécution en cours"
      subtitle="Alimenté par le flux temps réel du moteur, pas par un rafraîchissement périodique."
      source="GET /api/stream (SSE)"
      actions={
        <Pill tone={connection === 'open' ? 'good' : connection === 'connecting' ? 'warn' : 'bad'}>
          {connection === 'open' ? 'flux connecté' : connection === 'connecting' ? 'connexion…' : 'flux coupé'}
        </Pill>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Metric label="Statut" value={<Pill tone={tone}>{status}</Pill>} />
          <Metric label="Étapes d'agents" value={history.length} hint={`${queue.length} en file`} />
        </div>

        {objective && (
          <div>
            <p className="text-xs text-slate-500">Objectif</p>
            <p className="mt-0.5 text-sm text-slate-300">{objective}</p>
          </div>
        )}
        {sessionId && (
          <div>
            <p className="text-xs text-slate-500">Session</p>
            <p className="mt-0.5 font-mono text-xs text-slate-400">{sessionId}</p>
          </div>
        )}
        {lastStep && (
          <div>
            <p className="text-xs text-slate-500">Dernière étape</p>
            <p className="mt-0.5 text-sm">
              <span className="font-mono text-sky-300">{lastStep.agent_name}</span>{' '}
              <span className={lastStep.status === 'error' ? 'text-red-400' : 'text-emerald-400'}>
                {lastStep.status}
              </span>
            </p>
          </div>
        )}
        {errorMessage && (
          <p className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
            {errorMessage}
          </p>
        )}

        {status === 'idle' && history.length === 0 && (
          <EmptyState>Le moteur est au repos. Aucune exécution en cours.</EmptyState>
        )}

        {events.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs text-slate-500">Journal d'événements ({events.length})</p>
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/60 p-2">
              {events.slice(0, 30).map((e, i) => (
                <div key={i} className="flex items-baseline gap-2 font-mono text-[11px]">
                  <span className="text-slate-600">
                    {new Date(e.receivedAt).toLocaleTimeString('fr-FR')}
                  </span>
                  <span className="text-sky-400">{e.event}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

/* ── Backlog réel ─────────────────────────────────────────────────────────── */

function BacklogCard() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState(2);

  const tasksQ = useQuery({
    queryKey: ['backlog', 'tasks'],
    queryFn: fetchBacklogTasks,
    refetchInterval: 15_000,
  });
  const statsQ = useQuery({
    queryKey: ['backlog', 'stats'],
    queryFn: fetchBacklogStats,
    refetchInterval: 15_000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['backlog'] });
  };

  const create = useMutation({
    mutationFn: () => createBacklogTask({ title, description, priority }),
    onSuccess: () => {
      setTitle('');
      setDescription('');
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteBacklogTask(id),
    onSuccess: invalidate,
  });

  const stats = statsQ.data;

  return (
    <Card
      title="Backlog de fond"
      subtitle="Tâches traitées de façon autonome par le DreamCoder, hors session interactive."
      source="GET/POST/DELETE /api/backlog/tasks"
    >
      <div className="space-y-5">
        {stats && (
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            <Metric label="En attente" value={stats.pending} />
            <Metric label="En cours" value={stats.running} tone={stats.running ? 'info' : 'neutral'} />
            <Metric label="Terminées" value={stats.completed} tone="good" />
            <Metric label="Échouées" value={stats.failed} tone={stats.failed ? 'bad' : 'neutral'} />
            <Metric label="En pause" value={stats.paused} />
            <Metric label="Abandonnées" value={stats.abandoned} />
          </div>
        )}

        {/* Ajout */}
        <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/40 p-4">
          <p className="text-sm font-medium text-slate-300">Ajouter une tâche</p>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Titre court"
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            placeholder="Description détaillée — c'est elle qui sert de consigne à l'agent."
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
          />
          <div className="flex items-center gap-3">
            <select
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm outline-none focus:border-sky-500"
            >
              {PRIORITIES.map((p) => (
                <option key={p.value} value={p.value}>Priorité {p.label}</option>
              ))}
            </select>
            <Button
              variant="secondary"
              onClick={() => create.mutate()}
              disabled={!title.trim() || !description.trim() || create.isPending}
            >
              <ListPlus className="h-4 w-4" />
              {create.isPending ? 'Ajout…' : 'Mettre en file'}
            </Button>
          </div>
          {create.isError && <ErrorState error={create.error} />}
        </div>

        {/* Liste */}
        {tasksQ.isLoading ? (
          <LoadingState />
        ) : tasksQ.isError ? (
          <ErrorState error={tasksQ.error} onRetry={() => tasksQ.refetch()} />
        ) : (tasksQ.data?.length ?? 0) === 0 ? (
          <EmptyState>Le backlog est vide.</EmptyState>
        ) : (
          <div className="space-y-2">
            {tasksQ.data!.map((t: BacklogTask) => (
              <div key={t.id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Pill tone={STATUS_TONE[t.status] ?? 'neutral'}>{t.status}</Pill>
                      <span className="text-xs text-slate-600">
                        P{t.priority} · #{t.id}
                      </span>
                      {t.git_branch && (
                        <span className="inline-flex items-center gap-1 font-mono text-[11px] text-violet-300">
                          <GitBranch className="h-3 w-3" />
                          {t.git_branch}
                        </span>
                      )}
                      {t.retries > 0 && (
                        <span className="text-[11px] text-amber-400">{t.retries} tentative(s)</span>
                      )}
                    </div>
                    <p className="mt-1.5 text-sm font-medium text-slate-200">{t.title}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{t.description}</p>
                    {t.error_message && (
                      <p className="mt-1.5 break-words font-mono text-[11px] text-red-400">
                        {t.error_message}
                      </p>
                    )}
                    {t.result_summary && (
                      <p className="mt-1.5 text-xs text-emerald-400/80">{t.result_summary}</p>
                    )}
                    <p className="mt-1 text-[11px] text-slate-600">
                      Créée le {new Date(t.created_at * 1000).toLocaleString('fr-FR')}
                      {t.tokens_used ? ` · ${t.tokens_used.toLocaleString('fr-FR')} tokens` : ''}
                    </p>
                  </div>
                  <button
                    onClick={() => remove.mutate(t.id)}
                    disabled={remove.isPending}
                    title="Supprimer définitivement du backlog"
                    className="shrink-0 rounded-md p-1.5 text-slate-600 transition hover:bg-red-950/50 hover:text-red-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {remove.isError && <ErrorState error={remove.error} />}
      </div>
    </Card>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function TaskRunner() {
  return (
    <div className="mx-auto max-w-6xl pb-20">
      <PageHeader
        title="Lancer une tâche"
        description="Deux façons de confier du travail au moteur : tout de suite et sous vos yeux, ou en file pour un traitement autonome."
      />

      <div className="space-y-6">
        <Explain title="Immédiat ou en file : lequel choisir">
          <p>
            <strong>Orchestration immédiate</strong> — le moteur planifie et exécute
            maintenant, et vous suivez chaque étape en direct. Une seule à la fois : le
            moteur pose un verrou global pendant l'exécution.
          </p>
          <p>
            <strong>Backlog de fond</strong> — la tâche est écrite en base et reprise plus
            tard par le DreamCoder, l'agent autonome, qui travaille sur une branche Git
            dédiée. C'est le bon choix pour du travail long qui n'a pas besoin de vous.
          </p>
        </Explain>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <RunNowCard />
          <LiveStatusCard />
        </div>

        <BacklogCard />
      </div>
    </div>
  );
}
