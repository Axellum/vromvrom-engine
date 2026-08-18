/**
 * Panneau latéral historique des sessions (/api/sessions).
 *
 * - Liste des 40 dernières sessions regroupées par jour
 * - Clic → recharge l'objectif dans la zone de saisie du Chat (via callback)
 * - Indicateurs de statut colorés (success / error / running)
 */
import { useQuery } from "@tanstack/react-query";
import { fetchSessions, type Session } from "../../api/sessions";

// ─── Utilitaires ──────────────────────────────────────────────────────────────

function formatTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function dayLabel(epoch: number): string {
  const d = new Date(epoch * 1000);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);

  if (d.toDateString() === today.toDateString()) return "Aujourd'hui";
  if (d.toDateString() === yesterday.toDateString()) return "Hier";
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

function groupByDay(sessions: Session[]): Map<string, Session[]> {
  const map = new Map<string, Session[]>();
  for (const s of sessions) {
    const label = dayLabel(s.started_at);
    const group = map.get(label) ?? [];
    group.push(s);
    map.set(label, group);
  }
  return map;
}

const STATUS_DOT: Record<string, string> = {
  success: "bg-emerald-500",
  error:   "bg-red-500",
  running: "bg-amber-400 animate-pulse",
};

// ─── Composant ────────────────────────────────────────────────────────────────

interface Props {
  onSelectObjective: (objective: string) => void;
}

export function SessionHistory({ onSelectObjective }: Props) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["sessions"],
    queryFn:  () => fetchSessions(40),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const sessions = data?.sessions ?? [];
  const groups = groupByDay(sessions);

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-slate-800 bg-slate-950/40">
      {/* En-tête */}
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2.5">
        <span className="text-xs font-semibold text-slate-400">Historique</span>
        <button
          onClick={() => refetch()}
          className="text-slate-600 hover:text-slate-400 transition"
          title="Actualiser"
        >
          ↻
        </button>
      </div>

      {/* Liste */}
      <div className="flex-1 overflow-y-auto py-1">
        {isLoading && (
          <p className="px-3 py-4 text-center text-xs text-slate-600">Chargement…</p>
        )}
        {isError && (
          <p className="px-3 py-4 text-center text-xs text-red-500">Erreur de chargement</p>
        )}
        {!isLoading && sessions.length === 0 && (
          <p className="px-3 py-4 text-center text-xs text-slate-600">Aucune session</p>
        )}

        {Array.from(groups.entries()).map(([day, items]) => (
          <div key={day}>
            <p className="sticky top-0 bg-slate-950/90 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
              {day}
            </p>
            {items.map((s) => (
              <button
                key={s.session_id}
                onClick={() => s.objective && onSelectObjective(s.objective)}
                title={s.objective ?? s.session_id}
                className="w-full px-3 py-2 text-left transition hover:bg-slate-800/50 group"
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[s.status] ?? "bg-slate-600"}`}
                  />
                  <span className="flex-1 truncate text-xs text-slate-300 group-hover:text-slate-100">
                    {s.objective ?? s.session_id}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 pl-3">
                  <span className="text-[10px] text-slate-600">
                    {formatTime(s.started_at)}
                  </span>
                  {s.duration_ms != null && (
                    <span className="text-[10px] text-slate-700">
                      {/* Arrondi obligatoire : `duration_ms` est un REAL SQLite, la
                          branche < 1000 affichait le float brut (« 4.586458206176758ms »
                          observé). La branche >= 1000 arrondissait déjà, elle. */}
                      {s.duration_ms < 1000
                        ? `${Math.round(s.duration_ms)}ms`
                        : `${(s.duration_ms / 1000).toFixed(1)}s`}
                    </span>
                  )}
                  {s.starting_agent && (
                    <span className="truncate text-[10px] text-slate-700">
                      {s.starting_agent}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
