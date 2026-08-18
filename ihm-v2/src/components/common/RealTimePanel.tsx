/**
 * RealTimePanel — Volet latéral droit de la vue Chat (T84).
 *
 * Affiche en temps réel (SSE via engineStore) :
 *  - Statut de connexion
 *  - Modèle LLM routé + raison (ML Router / Elo / tier)
 *  - Étapes DAG en cours (agent, tâche)
 *  - Budget tokens restant (cloud vs local)
 */
import { useEngineStore } from "../../state/engineStore";
import type { EngineEvent } from "../../types/engine";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function ConnectionDot({ state }: { state: string }) {
  const color =
    state === "open"
      ? "bg-emerald-400"
      : state === "connecting"
      ? "bg-amber-400 animate-pulse"
      : "bg-red-500";
  const label = state === "open" ? "Connecté" : state === "connecting" ? "Connexion…" : "Déconnecté";
  return (
    <span className="flex items-center gap-1.5 text-xs text-slate-400">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function extractRoutingInfo(events: EngineEvent[]): { model: string; reason: string } | null {
  for (const e of events) {
    if (e.event === "task_started" || e.event === "agent_started") {
      const d = e.data ?? {};
      const model = (d.model_used ?? d.resolved_model ?? d.model_tier ?? "") as string;
      const reason = (d.routing_type ?? d.routing_reason ?? d.target_agent ?? "") as string;
      if (model || reason) return { model: model || "—", reason: reason || "—" };
    }
  }
  return null;
}

function extractActiveSteps(events: EngineEvent[]): { agent: string; task: string }[] {
  const started: Record<string, string> = {};
  const completed = new Set<string>();
  for (const e of [...events].reverse()) {
    const d = e.data ?? {};
    const agent = (d.target_agent ?? d.agent_name ?? "") as string;
    const task  = (d.task_objective ?? "") as string;
    if (e.event === "task_completed" || e.event === "agent_completed") {
      if (agent) completed.add(agent);
    } else if (e.event === "task_started" || e.event === "agent_started") {
      if (agent && !completed.has(agent)) started[agent] = task;
    }
  }
  return Object.entries(started).map(([agent, task]) => ({ agent, task }));
}

function extractBudget(events: EngineEvent[]): { used: number; limit: number } | null {
  for (const e of events) {
    if (e.event === "tokens_updated" || e.event === "quotas_updated") {
      const d = e.data ?? {};
      const used  = (d.tokens_used ?? d.total_tokens ?? 0) as number;
      const limit = (d.tokens_limit ?? 128_000) as number;
      if (used > 0) return { used, limit };
    }
  }
  return null;
}

// ─── Composant principal ──────────────────────────────────────────────────────

interface RealTimePanelProps {
  onClose?: () => void;
}

export function RealTimePanel({ onClose }: RealTimePanelProps) {
  const events     = useEngineStore((s) => s.events);
  const status     = useEngineStore((s) => s.status);
  const connection = useEngineStore((s) => s.connection);

  const routing  = extractRoutingInfo(events);
  const steps    = extractActiveSteps(events);
  const budget   = extractBudget(events);

  const recentEvents = events.slice(0, 12);

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col gap-3 border-l border-slate-800 pl-4 text-xs">
      {/* En-tête */}
      <div className="flex items-center justify-between">
        <span className="font-semibold text-slate-300">Moteur temps réel</span>
        {onClose && (
          <button
            onClick={onClose}
            className="text-slate-600 hover:text-slate-400"
            title="Fermer"
          >
            ✕
          </button>
        )}
      </div>

      {/* Connexion + statut */}
      <div className="flex items-center justify-between rounded-md bg-slate-800/60 px-2 py-1.5">
        <ConnectionDot state={connection} />
        <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
          status === "running"  ? "bg-sky-900/60 text-sky-400"   :
          status === "success"  ? "bg-emerald-900/60 text-emerald-400" :
          status === "error"    ? "bg-red-900/60 text-red-400"   :
          "bg-slate-700/60 text-slate-400"
        }`}>
          {status}
        </span>
      </div>

      {/* Routage LLM */}
      <section>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Routage LLM
        </p>
        {routing ? (
          <div className="rounded-md bg-slate-800/60 px-2 py-1.5 space-y-0.5">
            <p className="text-slate-200 font-medium truncate" title={routing.model}>
              {routing.model}
            </p>
            <p className="text-slate-500 truncate" title={routing.reason}>
              {routing.reason}
            </p>
          </div>
        ) : (
          <p className="text-slate-600 italic">En attente…</p>
        )}
      </section>

      {/* Étapes DAG actives */}
      <section>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Agents actifs
        </p>
        {steps.length > 0 ? (
          <ul className="space-y-1">
            {steps.map((s) => (
              <li key={s.agent} className="flex items-start gap-1.5 rounded-md bg-slate-800/60 px-2 py-1">
                <span className="mt-0.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-sky-400 animate-pulse" />
                <div className="min-w-0">
                  <p className="font-medium text-slate-300 truncate">{s.agent}</p>
                  {s.task && (
                    <p className="text-slate-500 truncate text-[10px]" title={s.task}>{s.task}</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-600 italic">Aucun agent actif</p>
        )}
      </section>

      {/* Budget tokens */}
      {budget && (
        <section>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Tokens
          </p>
          <div className="rounded-md bg-slate-800/60 px-2 py-1.5">
            <div className="flex justify-between text-[10px] mb-1">
              <span className="text-slate-400">{(budget.used / 1000).toFixed(1)}k</span>
              <span className="text-slate-600">{(budget.limit / 1000).toFixed(0)}k max</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
              <div
                className={`h-full rounded-full transition-all ${
                  budget.used / budget.limit > 0.8
                    ? "bg-red-500"
                    : budget.used / budget.limit > 0.5
                    ? "bg-amber-500"
                    : "bg-emerald-500"
                }`}
                style={{ width: `${Math.min((budget.used / budget.limit) * 100, 100)}%` }}
              />
            </div>
          </div>
        </section>
      )}

      {/* Flux d'événements récents */}
      <section className="flex-1 min-h-0">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Événements
        </p>
        <ul className="space-y-0.5 overflow-y-auto max-h-40 pr-1">
          {recentEvents.length === 0 ? (
            <li className="text-slate-600 italic">Aucun événement</li>
          ) : (
            recentEvents.map((e, i) => (
              <li key={i} className="flex items-baseline gap-1">
                <span className="text-slate-600 font-mono text-[9px] flex-shrink-0">
                  {new Date(e.receivedAt).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                <span className={`truncate ${
                  e.event.includes("error") || e.event.includes("fail")
                    ? "text-red-400"
                    : e.event.includes("completed") || e.event.includes("success")
                    ? "text-emerald-400"
                    : "text-slate-400"
                }`}>
                  {e.event}
                </span>
              </li>
            ))
          )}
        </ul>
      </section>
    </aside>
  );
}
