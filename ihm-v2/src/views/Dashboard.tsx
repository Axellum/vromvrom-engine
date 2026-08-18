/**
 * Dashboard temps réel — première vue de l'IHM v2.
 *
 * - Snapshot initial via GET /api/status (React Query).
 * - Mises à jour live via le bus SSE (useEngineStream → store Zustand).
 * - Lancement (POST /api/run) et arrêt (POST /api/stop) d'une tâche.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchStatus, runTask, stopExecution } from "../api/engine";
import { useEngineStore } from "../state/engineStore";
import { StatusBadge } from "../components/common/StatusBadge";

function EventRow({ event, data, receivedAt }: { event: string; data?: Record<string, unknown>; receivedAt: number }) {
  const time = new Date(receivedAt).toLocaleTimeString("fr-FR");
  const agent = (data?.agent_name as string) ?? (data?.status as string) ?? "";
  return (
    <li className="flex items-baseline gap-3 border-b border-slate-800/60 py-1.5 text-sm">
      <span className="w-20 shrink-0 font-mono text-xs text-slate-500">{time}</span>
      <span className="w-48 shrink-0 font-medium text-sky-300">{event}</span>
      <span className="truncate text-slate-400">{agent}</span>
    </li>
  );
}

export function Dashboard() {
  const [objective, setObjective] = useState("");
  const queryClient = useQueryClient();

  const status = useEngineStore((s) => s.status);
  const liveObjective = useEngineStore((s) => s.objective);
  const sessionId = useEngineStore((s) => s.sessionId);
  const engineState = useEngineStore((s) => s.engineState);
  const errorMessage = useEngineStore((s) => s.errorMessage);
  const events = useEngineStore((s) => s.events);
  const setSnapshot = useEngineStore((s) => s.setSnapshot);
  const clearEvents = useEngineStore((s) => s.clearEvents);

  // Snapshot initial : alimente le store dès le chargement.
  useQuery({
    queryKey: ["status"],
    queryFn: async () => {
      const s = await fetchStatus();
      setSnapshot(s);
      return s;
    },
    refetchInterval: 15000, // filet de sécurité si un événement SSE est manqué
  });

  const run = useMutation({
    mutationFn: () => runTask(objective.trim()),
    onSuccess: () => {
      clearEvents();
      setObjective("");
      void queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });

  const stop = useMutation({
    mutationFn: stopExecution,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["status"] }),
  });

  const isRunning = status === "running";
  const history = engineState?.history ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Dashboard</h1>
          <p className="text-sm text-slate-500">État du moteur en temps réel</p>
        </div>
        <StatusBadge status={status} />
      </header>

      {/* Lancement de tâche */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (objective.trim() && !isRunning) run.mutate();
          }}
        >
          <input
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            placeholder="Objectif de la tâche à lancer…"
            disabled={isRunning}
            className="flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-slate-100 outline-none focus:border-sky-500 disabled:opacity-50"
          />
          {isRunning ? (
            <button
              type="button"
              onClick={() => stop.mutate()}
              disabled={stop.isPending}
              className="rounded-lg bg-red-600 px-4 py-2 font-medium text-white transition hover:bg-red-500 disabled:opacity-50"
            >
              Arrêter
            </button>
          ) : (
            <button
              type="submit"
              disabled={!objective.trim() || run.isPending}
              className="rounded-lg bg-sky-600 px-4 py-2 font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
            >
              Lancer
            </button>
          )}
        </form>
        {run.isError && (
          <p className="mt-2 text-sm text-red-400">{(run.error as Error).message}</p>
        )}
      </section>

      {/* Cartes d'état */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <InfoCard label="Objectif courant" value={liveObjective || "—"} />
        <InfoCard label="Session" value={sessionId || "—"} mono />
        <InfoCard label="Étapes d'agents" value={String(history.length)} />
      </section>

      {errorMessage && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          {errorMessage}
        </div>
      )}

      {/* Historique des agents (engine_state.history) */}
      {history.length > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-300">Historique des agents</h2>
          <ul className="space-y-2">
            {history.map((h, i) => (
              <li key={i} className="flex items-baseline gap-3 text-sm">
                <span
                  className={`w-16 shrink-0 font-medium ${
                    h.status === "error" ? "text-red-400" : "text-emerald-400"
                  }`}
                >
                  {h.status}
                </span>
                <span className="w-40 shrink-0 text-sky-300">{h.agent_name}</span>
                {h.error_message && <span className="truncate text-red-400">{h.error_message}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Journal d'événements SSE */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-300">Flux d'événements ({events.length})</h2>
          {events.length > 0 && (
            <button onClick={clearEvents} className="text-xs text-slate-500 hover:text-slate-300">
              Effacer
            </button>
          )}
        </div>
        {events.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-600">En attente d'événements…</p>
        ) : (
          <ul className="max-h-96 overflow-y-auto">
            {events.map((e, i) => (
              <EventRow key={i} event={e.event} data={e.data} receivedAt={e.receivedAt} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function InfoCard({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 truncate text-lg text-slate-100 ${mono ? "font-mono text-base" : ""}`}>
        {value}
      </div>
    </div>
  );
}
