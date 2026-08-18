import type { EngineStatus } from "../../types/engine";

const STYLES: Record<EngineStatus, { label: string; cls: string }> = {
  idle: { label: "Au repos", cls: "bg-slate-700 text-slate-200" },
  running: { label: "En cours", cls: "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40" },
  success: { label: "Succès", cls: "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40" },
  completed: { label: "Terminé", cls: "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40" },
  error: { label: "Erreur", cls: "bg-red-500/20 text-red-300 ring-1 ring-red-500/40" },
  stopped: { label: "Arrêté", cls: "bg-red-500/20 text-red-300 ring-1 ring-red-500/40" },
};

export function StatusBadge({ status }: { status: EngineStatus }) {
  const s = STYLES[status] ?? STYLES.idle;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${s.cls}`}>
      {status === "running" && (
        <span className="h-2 w-2 animate-pulse rounded-full bg-current" aria-hidden />
      )}
      {s.label}
    </span>
  );
}
