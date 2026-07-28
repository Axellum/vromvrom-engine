import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from "chart.js";
import { Doughnut } from "react-chartjs-2";

import { fetchAccounts, fetchCostPerSuccess, setManualBalance } from "../api/accounts";
import type { ProviderAccount } from "../api/accounts";
import { useUiStore } from "../state/uiStore";

ChartJS.register(ArcElement, Tooltip, Legend);

const STATUS_COLORS: Record<string, string> = {
  active: "text-emerald-400",
  manuel: "text-sky-400",
  non_suivi: "text-slate-500",
};

function AccountCard({ account }: { account: ProviderAccount }) {
  const isAlert = account.used_pct > 80;
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState(account.balance_usd !== null ? String(account.balance_usd) : "");
  const canEditManually = account.status === "non_suivi" || account.status === "manuel";

  const manualMut = useMutation({
    mutationFn: (value: number) => setManualBalance(account.provider_key ?? account.name, value),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  return (
    <div className="relative flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/50 p-5 shadow-sm">
      {isAlert && <div className="absolute top-0 left-0 h-1 w-full bg-orange-500" />}

      <div className="mb-4 flex items-start justify-between">
        <div>
          <h3 className="text-base font-bold text-slate-200">{account.name}</h3>
          <p className="text-xs text-slate-500">{account.plan}</p>
        </div>
        <span className={`flex items-center gap-1.5 text-xs font-medium ${STATUS_COLORS[account.status] ?? "text-red-400"}`}>
          ● {account.status}
        </span>
      </div>

      <div className="mb-5">
        <span className="block text-xs text-slate-400 mb-1">Solde actuel</span>
        {editing ? (
          <div className="flex items-center gap-2">
            <span className="text-lg text-slate-400">$</span>
            <input
              type="number"
              step="0.01"
              autoFocus
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && manualMut.mutate(parseFloat(inputValue))}
              className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-lg text-white"
            />
            <button
              onClick={() => !isNaN(parseFloat(inputValue)) && manualMut.mutate(parseFloat(inputValue))}
              disabled={manualMut.isPending}
              className="rounded bg-sky-600 px-2 py-1 text-xs text-white hover:bg-sky-500 disabled:opacity-50"
            >
              ✓
            </button>
            <button onClick={() => setEditing(false)} className="text-xs text-slate-500 hover:text-white">
              ✕
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-3xl font-light tracking-tight text-slate-100">
              {account.balance_usd !== null
                ? `$${account.balance_usd.toFixed(2)}`
                : account.balance_eur !== null
                  ? `${account.balance_eur.toFixed(2)}€`
                  : "N/A"}
            </span>
            {canEditManually && (
              <button onClick={() => setEditing(true)} className="text-xs text-slate-500 underline hover:text-sky-400">
                {account.status === "manuel" ? "modifier" : "saisir"}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-4 mb-6">
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-slate-400">Quota RPM</span>
            <span className="font-mono text-slate-300">{account.used_rpm} / {account.quota_rpm || "∞"}</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div 
              className={`h-full ${(account.used_rpm ?? 0) / (account.quota_rpm || 1) > 0.8 ? "bg-orange-500" : "bg-sky-500"}`}
              style={{ width: `${Math.min(100, ((account.used_rpm ?? 0) / (account.quota_rpm || 1)) * 100)}%` }}
            />
          </div>
        </div>
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-slate-400">Quota RPD</span>
            <span className="font-mono text-slate-300">{account.used_rpd} / {account.quota_rpd || "∞"}</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div 
              className={`h-full ${account.used_pct > 80 ? "bg-orange-500" : "bg-sky-500"}`} 
              style={{ width: `${Math.min(100, account.used_pct)}%` }} 
            />
          </div>
        </div>
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-slate-800 pt-4">
        <span className="text-[10px] text-slate-500">Sync: {account.last_sync}</span>
        {account.dashboard_url && (
          <button 
            onClick={() => window.open(account.dashboard_url, '_blank')}
            className="rounded border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800 hover:text-white transition"
          >
            Dashboard ↗
          </button>
        )}
      </div>
    </div>
  );
}

export function APIAccounts() {
  const { data: billing, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: fetchAccounts,
  });
  const accounts = billing?.providers ?? [];
  const totalCostMonth = billing?.total_cost_usd_month ?? 0;

  const { data: costsData } = useQuery({
    queryKey: ["costs"],
    queryFn: fetchCostPerSuccess,
  });

  const setView = useUiStore((s) => s.setView);

  if (isLoading) return <p className="text-slate-500">Chargement des comptes…</p>;

  const chartData = {
    labels: costsData?.by_provider.map(c => c.provider) || [],
    datasets: [{
      data: costsData?.by_provider.map(c => c.cost_usd) || [],
      backgroundColor: ['#0ea5e9', '#8b5cf6', '#10b981', '#f59e0b', '#64748b'],
      borderWidth: 0,
    }]
  };

  return (
    <div className="mx-auto max-w-6xl space-y-8 pb-20">
      {/* Bannière Top */}
      <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900/80 p-6 shadow-lg">
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Dépensé ce mois-ci</h2>
          <p className="mt-1 text-4xl font-light text-white">${totalCostMonth.toFixed(2)}</p>
        </div>
        <div className="h-24 w-24">
          {costsData && <Doughnut data={chartData} options={{ plugins: { legend: { display: false } }, cutout: '75%' }} />}
        </div>
      </div>

      {/* Grille Providers */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
        {accounts.map(acc => (
          <AccountCard key={acc.name} account={acc} />
        ))}
      </div>

      {/* Sections Spéciales */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Google Workspace — le moteur n'expose aucun endpoint d'état OAuth ni de
            réautorisation. Plutôt que d'afficher une liste de scopes codée en dur et
            un bouton qui échouait en 404, on indique la procédure réelle. */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-6">
          <h2 className="text-lg font-semibold text-slate-200">Google Workspace</h2>
          <p className="mt-2 text-sm text-slate-400">
            L'accès OAuth (Agenda, Gmail, Drive, Sheets) s'obtient côté serveur : le moteur
            n'expose pas de route de réautorisation.
          </p>
          <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
            <code className="text-xs text-sky-300">python setup_google_oauth.py</code>
          </div>
          <p className="mt-2 text-xs text-slate-600">
            Le jeton de rafraîchissement obtenu est écrit dans <code>google_token.json</code>.
            L'état des outils Workspace se vérifie en les appelant depuis le Chat.
          </p>
        </section>

        {/* Home Assistant — l'état réel est mesuré dans la vue Domotique & Vocal
            (GET /api/ha/health). Ici figurait une pastille « ● Connecté » codée en
            dur et un bouton « Tester Ping » sans action. */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-6">
          <h2 className="text-lg font-semibold text-slate-200">Home Assistant</h2>
          <p className="mt-2 text-sm text-slate-400">
            La liaison domotique est testée en direct, avec sa latence et le détail des erreurs.
          </p>
          <button
            onClick={() => setView('homeassistant')}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-sky-600 hover:text-sky-300"
          >
            Ouvrir Domotique &amp; Vocal
          </button>
        </section>
      </div>
    </div>
  );
}
