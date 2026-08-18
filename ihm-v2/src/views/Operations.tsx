/**
 * Vue Opérations — cockpit d'exploitation du moteur (Phase 3 HMI).
 *
 * Reflète l'état RÉEL, uniquement depuis des endpoints existants :
 * exécution en cours, circuit breakers, quotas/budget (BudgetGuard),
 * agents persistants (daemon/dreamer, avec bascule on/off réelle via
 * PUT /api/agents/{name}), vivacité des APIs externes.
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchExecutionStatus,
  fetchCircuitBreakers,
  fetchQuotas,
  fetchDaemonStatus,
  fetchDreamerStatus,
  fetchApisStatus,
  fetchVersion,
} from "../api/operations";
import { updateAgent } from "../api/agents";

const REFRESH_MS = 10_000;

function SectionCard({ title, children, subtitle }: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-slate-200">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

function StatusDot({ ok, label }: { ok: boolean | undefined; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className={`h-2 w-2 rounded-full ${ok === undefined ? "bg-slate-600" : ok ? "bg-emerald-500" : "bg-red-500"}`} />
      <span className="text-slate-300">{label}</span>
    </span>
  );
}

function ExecutionSection() {
  const { data, isError } = useQuery({
    queryKey: ["ops", "execution"],
    queryFn: fetchExecutionStatus,
    refetchInterval: REFRESH_MS,
  });

  const statusColors: Record<string, string> = {
    running: "text-sky-400",
    success: "text-emerald-400",
    error: "text-red-400",
    idle: "text-slate-400",
  };
  const status = data?.status ?? "idle";

  return (
    <SectionCard title="Exécution" subtitle="GET /api/status — état du pipeline">
      {isError ? (
        <p className="text-xs text-red-400">Endpoint injoignable.</p>
      ) : (
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-slate-500">Statut :</span>
            <span className={`font-mono font-semibold ${statusColors[status] ?? "text-slate-300"}`}>{status}</span>
          </div>
          {data?.objective && (
            <div className="text-xs text-slate-400 truncate" title={data.objective}>
              Objectif : {data.objective}
            </div>
          )}
          {data?.error_message && <p className="text-xs text-red-400">⚠ {data.error_message}</p>}
        </div>
      )}
    </SectionCard>
  );
}

function CircuitBreakersSection() {
  const [showAll, setShowAll] = useState(false);
  const { data, isError } = useQuery({
    queryKey: ["ops", "cb"],
    queryFn: fetchCircuitBreakers,
    refetchInterval: REFRESH_MS,
  });

  const unhealthy = data?.circuit_breakers.filter((b) => b.state !== "CLOSED") ?? [];
  const shown = showAll ? (data?.circuit_breakers ?? []) : unhealthy;

  return (
    <SectionCard
      title="Circuit Breakers"
      subtitle={data ? `uptime ${Math.round(data.uptime_seconds / 60)} min — registre en mémoire du serveur` : undefined}
    >
      {isError ? (
        <p className="text-xs text-red-400">Endpoint injoignable.</p>
      ) : !data ? (
        <p className="text-xs text-slate-500">Chargement…</p>
      ) : (
        <>
          <div className="mb-3 flex gap-4 text-sm">
            <span className="text-emerald-400 font-mono">{data.healthy} sains</span>
            <span className={`font-mono ${data.half_open ? "text-orange-400" : "text-slate-500"}`}>{data.half_open} half-open</span>
            <span className={`font-mono ${data.open ? "text-red-400" : "text-slate-500"}`}>{data.open} ouverts</span>
            <button onClick={() => setShowAll((v) => !v)} className="ml-auto text-xs text-sky-400 hover:text-sky-300">
              {showAll ? "Voir les problèmes" : `Tout voir (${data.total})`}
            </button>
          </div>
          {shown.length === 0 ? (
            <p className="text-xs text-slate-500">
              {data.total === 0
                ? "Aucun circuit breaker instancié (aucun appel LLM depuis le démarrage)."
                : "Tous les circuits sont fermés (sains)."}
            </p>
          ) : (
            <div className="max-h-64 overflow-y-auto rounded border border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-900">
                  <tr className="text-slate-500">
                    <th className="px-3 py-1.5">Provider</th>
                    <th className="px-3 py-1.5">État</th>
                    <th className="px-3 py-1.5">Échecs</th>
                    <th className="px-3 py-1.5">Appels</th>
                    <th className="px-3 py-1.5">429</th>
                    <th className="px-3 py-1.5">Lat. moy.</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((b) => (
                    <tr key={b.name} className="border-t border-slate-800/60">
                      <td className="px-3 py-1.5 font-mono text-sky-300">{b.name}</td>
                      <td className={`px-3 py-1.5 font-semibold ${b.state === "CLOSED" ? "text-emerald-400" : b.state === "OPEN" ? "text-red-400" : "text-orange-400"}`}>
                        {b.state}
                      </td>
                      <td className="px-3 py-1.5 text-slate-300">{b.total_failures}/{b.total_calls}</td>
                      <td className="px-3 py-1.5 text-slate-300">{b.total_calls}</td>
                      <td className="px-3 py-1.5 text-slate-300">{b.rate_limits}</td>
                      <td className="px-3 py-1.5 text-slate-300">
                        {b.avg_latency_ms != null ? `${Math.round(b.avg_latency_ms)} ms` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}

function QuotasSection() {
  const { data, isError } = useQuery({
    queryKey: ["ops", "quotas"],
    queryFn: fetchQuotas,
    refetchInterval: REFRESH_MS,
  });

  return (
    <SectionCard title="Quotas & budget (BudgetGuard)" subtitle="GET /api/backlog/quota — décisions réelles de la cascade">
      {isError ? (
        <p className="text-xs text-red-400">Endpoint injoignable.</p>
      ) : !data ? (
        <p className="text-xs text-slate-500">Chargement…</p>
      ) : (
        <div className="space-y-3">
          {Object.entries(data.providers).map(([name, q]) => {
            const pct = q.limit ? Math.min(100, (q.used / q.limit) * 100) : 0;
            const barColor = !q.available ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-emerald-500";
            return (
              <div key={name}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="font-mono text-slate-300">{name}</span>
                  <span className="text-slate-500">
                    {q.unit === "status"
                      ? (q.available ? "en ligne" : "hors ligne")
                      : `${q.used} / ${q.limit} ${q.unit} (${q.metric})`}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                  <div className={`h-full rounded-full ${barColor}`} style={{ width: `${q.unit === "status" ? (q.available ? 100 : 0) : pct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </SectionCard>
  );
}

function PersistentAgentsSection() {
  const queryClient = useQueryClient();
  const daemon = useQuery({ queryKey: ["ops", "daemon"], queryFn: fetchDaemonStatus, refetchInterval: REFRESH_MS });
  const dreamer = useQuery({ queryKey: ["ops", "dreamer"], queryFn: fetchDreamerStatus, refetchInterval: REFRESH_MS });

  const toggleMut = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) => updateAgent(name, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ops", "daemon"] });
      queryClient.invalidateQueries({ queryKey: ["ops", "dreamer"] });
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  const renderStatus = (label: string, name: "daemon" | "dreamer", status?: Record<string, unknown>) => {
    const enabled = Boolean(status?.enabled);
    const running = Boolean(status?.running ?? status?.active);
    return (
      <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-3">
        <div>
          <div className="flex items-center gap-2 text-sm text-slate-200">
            {label}
            <StatusDot ok={status ? running || enabled : undefined} label={running ? "en cours" : enabled ? "activé" : "désactivé"} />
          </div>
          {typeof status?.last_run === "string" && (
            <p className="text-[11px] text-slate-500">Dernier cycle : {String(status.last_run)}</p>
          )}
        </div>
        <button
          onClick={() => toggleMut.mutate({ name, enabled: !enabled })}
          disabled={toggleMut.isPending || !status}
          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${enabled ? "bg-sky-600" : "bg-slate-700"} disabled:opacity-50`}
          title="Écrit persistent_agents.*_enabled dans config.json"
        >
          <span className={`inline-block h-5 w-5 transform rounded-full bg-white transition ${enabled ? "translate-x-5" : "translate-x-1"}`} />
        </button>
      </div>
    );
  };

  return (
    <SectionCard title="Agents persistants" subtitle="Daemon 24/7 et Dreamer nocturne — bascule réelle (config.json)">
      <div className="space-y-3">
        {renderStatus("Daemon Sentinelle", "daemon", daemon.data)}
        {renderStatus("Dreamer / DreamCoder", "dreamer", dreamer.data)}
        {(daemon.isError || dreamer.isError) && (
          <p className="text-xs text-amber-400">Certains statuts sont injoignables (daemon/dreamer non démarrés ?).</p>
        )}
      </div>
    </SectionCard>
  );
}

function ExternalApisSection() {
  const { data, isError } = useQuery({
    queryKey: ["ops", "apis"],
    queryFn: fetchApisStatus,
    refetchInterval: 30_000,
  });

  return (
    <SectionCard title="APIs externes" subtitle="GET /api/apis-status — vivacité testée en direct">
      {isError ? (
        <p className="text-xs text-red-400">Endpoint injoignable.</p>
      ) : !data ? (
        <p className="text-xs text-slate-500">Chargement…</p>
      ) : (
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="rounded-lg bg-slate-950/60 p-3">
            <StatusDot ok={data.deepseek?.active} label="DeepSeek" />
            {data.deepseek?.real_balance_usd != null && (
              <p className="mt-1 text-xs text-slate-500">Solde : ${data.deepseek.real_balance_usd.toFixed(2)}</p>
            )}
          </div>
          <div className="rounded-lg bg-slate-950/60 p-3">
            <StatusDot ok={data.gemini?.active} label="Gemini" />
          </div>
          <div className="rounded-lg bg-slate-950/60 p-3">
            <StatusDot ok={data.claude?.real_usage_pct != null} label="Claude (abonnement)" />
            {data.claude?.real_usage_pct != null && (
              <p className="mt-1 text-xs text-slate-500">Usage : {data.claude.real_usage_pct}%</p>
            )}
          </div>
          <div className="rounded-lg bg-slate-950/60 p-3">
            <StatusDot ok={data.antigravity?.connected} label="Antigravity" />
            {data.antigravity?.plan && (
              <p className="mt-1 text-xs text-slate-500">{data.antigravity.plan}</p>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

export function Operations() {
  const { data: version } = useQuery({ queryKey: ["ops", "version"], queryFn: fetchVersion, staleTime: Infinity });

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-20">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Opérations</h1>
        <p className="text-sm text-slate-400">
          État réel du moteur{version?.version ? ` — v${version.version}` : ""} (rafraîchi toutes les 10 s)
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ExecutionSection />
        <PersistentAgentsSection />
      </div>
      <CircuitBreakersSection />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <QuotasSection />
        <ExternalApisSection />
      </div>
    </div>
  );
}
