/**
 * Vue Observabilité — métriques agrégées du moteur (GET /api/metrics/telemetry).
 *
 * Sélecteur de période, bannière KPI, série temporelle tokens/coûts (Chart.js),
 * prévision budgétaire, et tableaux par modèle / par agent.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Line } from "react-chartjs-2";
import { fetchTelemetry } from "../api/observability";
import type { TelemetryPeriod } from "../types/observability";
import { ensureChartsRegistered } from "../components/charts/registerCharts";

ensureChartsRegistered();

const PERIODS: TelemetryPeriod[] = ["1h", "6h", "24h", "7d", "30d"];

function fmtUsd(n: number): string {
  return `$${(n ?? 0).toFixed(n < 1 ? 4 : 2)}`;
}
function fmtNum(n: number): string {
  return (n ?? 0).toLocaleString("fr-FR");
}

export function Observability() {
  const [period, setPeriod] = useState<TelemetryPeriod>("24h");

  const q = useQuery({
    queryKey: ["telemetry", period],
    queryFn: () => fetchTelemetry(period),
    refetchInterval: 30000,
  });

  const t = q.data;

  const chartData = useMemo(() => {
    const ts = t?.time_series ?? { labels: [], tokens: [], costs: [] };
    return {
      labels: ts.labels,
      datasets: [
        {
          label: "Tokens",
          data: ts.tokens,
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.15)",
          yAxisID: "y",
          fill: true,
          tension: 0.3,
        },
        {
          label: "Coût (USD)",
          data: ts.costs,
          borderColor: "#f59e0b",
          backgroundColor: "rgba(245, 158, 11, 0.1)",
          yAxisID: "y1",
          fill: false,
          tension: 0.3,
        },
      ],
    };
  }, [t]);

  const models = useMemo(
    () =>
      Object.entries(t?.model_stats ?? {})
        .map(([model, s]) => ({ model, ...s }))
        .sort((a, b) => b.total_tokens - a.total_tokens),
    [t],
  );

  if (q.isLoading) return <p className="text-slate-500">Chargement des métriques…</p>;
  if (q.isError) return <p className="text-red-400">Erreur : {(q.error as Error).message}</p>;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Observabilité</h1>
          <p className="text-sm text-slate-500">Tokens, coûts et activité du moteur</p>
        </div>
        <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/50 p-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`rounded-md px-3 py-1 text-sm transition ${
                period === p ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </header>

      {/* Bannière KPI */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Kpi label="Coût total" value={fmtUsd(t?.kpis.total_cost_usd ?? 0)} />
        <Kpi label="Sessions" value={fmtNum(t?.kpis.total_sessions ?? 0)} />
        <Kpi label="Tokens" value={fmtNum(t?.kpis.total_tokens ?? 0)} />
      </section>

      {/* Série temporelle */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">Tokens & coûts dans le temps</h2>
        {chartData.labels.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-600">Aucune donnée sur la période.</p>
        ) : (
          <div className="h-72">
            <Line
              data={chartData}
              options={{
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                  y: { type: "linear", position: "left", title: { display: true, text: "Tokens" } },
                  y1: {
                    type: "linear",
                    position: "right",
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: "USD" },
                  },
                },
              }}
            />
          </div>
        )}
      </section>

      {/* Prévision budgétaire */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Kpi label="Coût moyen / jour" value={fmtUsd(t?.budget_forecast.avg_daily_cost ?? 0)} sub />
        <Kpi label="Projection 7 j" value={fmtUsd(t?.budget_forecast.projected_7d ?? 0)} sub />
        <Kpi label="Projection 30 j" value={fmtUsd(t?.budget_forecast.projected_30d ?? 0)} sub />
      </section>

      {/* Tableau par modèle */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">Usage par modèle</h2>
        {models.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-600">Aucun usage sur la période.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2">Modèle</th>
                <th className="py-2 text-right">Appels</th>
                <th className="py-2 text-right">Tokens</th>
                <th className="py-2 text-right">Coût</th>
                <th className="py-2 text-right">Elo moy.</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {models.map((m) => (
                <tr key={m.model}>
                  <td className="py-1.5 font-mono text-sky-300">{m.model}</td>
                  <td className="py-1.5 text-right text-slate-300">{fmtNum(m.total_calls)}</td>
                  <td className="py-1.5 text-right text-slate-300">{fmtNum(m.total_tokens)}</td>
                  <td className="py-1.5 text-right text-slate-300">{fmtUsd(m.cost_usd)}</td>
                  <td className="py-1.5 text-right text-slate-400">{m.avg_elo ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Tableau par agent */}
      {(t?.agent_stats?.length ?? 0) > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-300">Activité par agent / modèle</h2>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2">Agent</th>
                <th className="py-2 text-right">Appels</th>
                <th className="py-2 text-right">Taux succès</th>
                <th className="py-2 text-right">Tokens moy.</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {t!.agent_stats.map((a) => (
                <tr key={a.agent}>
                  <td className="py-1.5 font-mono text-sky-300">{a.agent}</td>
                  <td className="py-1.5 text-right text-slate-300">{fmtNum(a.total_calls)}</td>
                  <td className="py-1.5 text-right text-slate-300">
                    {a.success_rate != null ? `${a.success_rate}%` : "—"}
                  </td>
                  <td className="py-1.5 text-right text-slate-300">
                    {a.avg_tokens != null ? fmtNum(Math.round(a.avg_tokens)) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <p className="text-right text-xs text-slate-600">
        Généré le {t?.generated_at ? new Date(t.generated_at).toLocaleString("fr-FR") : "—"}
      </p>
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: boolean }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 font-semibold text-slate-100 ${sub ? "text-xl" : "text-2xl"}`}>{value}</div>
    </div>
  );
}
