/**
 * Primitives d'interface partagées par toutes les vues.
 *
 * Objectif : une IHM lisible et honnête. Deux principes portés par ces composants :
 *  1. Chaque bloc de données affiche SA SOURCE (`source`), donc l'endpoint moteur
 *     dont il provient — on peut toujours remonter à l'origine d'un chiffre.
 *  2. Une donnée non mesurée s'affiche « — » (via `Value`), jamais un 0 qui
 *     laisserait croire à une mesure réelle.
 */
import type { ReactNode } from "react";
import { AlertTriangle, Info, Loader2 } from "lucide-react";

/* ── En-tête de page ─────────────────────────────────────────────────────── */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight text-slate-100">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

/* ── Carte de section ────────────────────────────────────────────────────── */

export function Card({
  title,
  subtitle,
  source,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: ReactNode;
  /** Endpoint moteur d'où viennent les données (traçabilité). */
  source?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/50 ${className}`}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800/80 px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold text-slate-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
            {source && <SourceTag source={source} />}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

/** Badge discret indiquant l'endpoint moteur à l'origine des données affichées. */
export function SourceTag({ source }: { source: string }) {
  return (
    <code className="mt-1 inline-block rounded bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
      {source}
    </code>
  );
}

/* ── États ───────────────────────────────────────────────────────────────── */

export function LoadingState({ label = "Chargement…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

/**
 * Erreur affichée telle quelle : on montre le message du moteur plutôt que de
 * masquer le problème derrière des données de repli.
 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-red-300">Donnée indisponible</p>
          <p className="mt-0.5 break-words text-xs text-red-400/90">{message}</p>
        </div>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded-md border border-red-800 px-2.5 py-1 text-xs text-red-300 transition hover:bg-red-900/40"
        >
          Réessayer
        </button>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>;
}

/**
 * Requête suspendue par React Query (`fetchStatus === "paused"`) : elle a été
 * initiée mais n'a pas abouti, et n'a pas non plus échoué.
 *
 * Cet état mérite son propre rendu : il n'est ni un chargement, ni une erreur,
 * et surtout pas un résultat vide. Sans lui, une vue affichait « aucune donnée »
 * alors que rien n'était revenu du serveur — exactement le genre d'affirmation
 * fausse que cette IHM doit éviter. Observé en conditions réelles sur
 * `/api/ha/entities` quand le moteur répond 503.
 */
export function PausedState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-amber-900/50 bg-amber-950/25 px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-amber-200">Requête suspendue</p>
          <p className="mt-0.5 text-xs text-amber-200/80">
            L'appel a été lancé mais n'a rien renvoyé, et n'a pas été signalé en erreur.
            Aucune donnée n'a été reçue — ce n'est pas un résultat vide.
          </p>
        </div>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded-md border border-amber-800 px-2.5 py-1 text-xs text-amber-200 transition hover:bg-amber-900/40"
        >
          Réessayer
        </button>
      )}
    </div>
  );
}

/** État minimal d'une requête, tel que les vues ont besoin de le distinguer. */
export interface QueryLike {
  status: string;
  fetchStatus: string;
  error: unknown;
  data: unknown;
  refetch?: () => unknown;
}

export type QueryPhase = "error" | "paused" | "loading" | "ready";

/**
 * Détermine la phase d'affichage d'une requête.
 *
 * Règle centrale : `ready` n'est renvoyé que si des données sont réellement
 * arrivées. Une vue ne peut donc jamais afficher son état « vide » sans avoir
 * reçu de réponse — c'était le défaut du motif `isLoading / isError / vide`.
 */
export function queryPhase(q: QueryLike): QueryPhase {
  if (q.status === "error") return "error";
  if (q.data !== undefined && q.data !== null) return "ready";
  if (q.fetchStatus === "paused") return "paused";
  return "loading";
}

/* ── Encart explicatif ───────────────────────────────────────────────────── */

/**
 * Encart pédagogique : explique ce que fait le moteur sur cet écran. Le ton est
 * volontairement factuel — pas de promesse sur des comportements non vérifiés.
 */
export function Explain({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-sky-900/50 bg-sky-950/25 px-4 py-3">
      <div className="flex items-start gap-2">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
        <div className="min-w-0 text-sm text-slate-300">
          {title && <p className="mb-1 font-medium text-sky-300">{title}</p>}
          <div className="space-y-1.5 leading-relaxed text-slate-400">{children}</div>
        </div>
      </div>
    </div>
  );
}

/** Avertissement pour les actions à conséquence réelle (coût, redémarrage…). */
export function Warn({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-900/50 bg-amber-950/25 px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
        <div className="min-w-0 text-sm leading-relaxed text-amber-200/90">{children}</div>
      </div>
    </div>
  );
}

/* ── Tuile de métrique ───────────────────────────────────────────────────── */

export function Metric({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "text-slate-100",
    good: "text-emerald-400",
    warn: "text-amber-400",
    bad: "text-red-400",
    info: "text-sky-400",
  } as const;
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 font-mono text-xl font-semibold ${tones[tone]}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-600">{hint}</p>}
    </div>
  );
}

/* ── Valeurs ─────────────────────────────────────────────────────────────── */

/**
 * Affiche une valeur potentiellement non mesurée. `null`/`undefined` → « — ».
 * C'est la garantie qu'un tiret signifie « jamais mesuré », pas « zéro ».
 */
export function Value({
  value,
  unit,
  digits,
  className = "",
}: {
  value: number | string | null | undefined;
  unit?: string;
  digits?: number;
  className?: string;
}) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-slate-600" title="Jamais mesuré">—</span>;
  }
  const text =
    typeof value === "number" && digits !== undefined ? value.toFixed(digits) : String(value);
  return (
    <span className={className}>
      {text}
      {unit && <span className="ml-0.5 text-slate-500">{unit}</span>}
    </span>
  );
}

/* ── Tableau ─────────────────────────────────────────────────────────────── */

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-x-auto rounded-lg border border-slate-800 ${className}`}>
      <table className="w-full text-left text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  onClick,
  active,
  desc,
  align = "left",
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
  desc?: boolean;
  align?: "left" | "right";
}) {
  return (
    <th
      onClick={onClick}
      className={`whitespace-nowrap px-3 py-2 text-xs font-medium text-slate-500 ${
        align === "right" ? "text-right" : "text-left"
      } ${onClick ? "cursor-pointer select-none hover:text-sky-400" : ""} ${
        active ? "text-sky-400" : ""
      }`}
    >
      {children}
      {active && <span className="ml-1">{desc ? "↓" : "↑"}</span>}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  className = "",
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${className}`}
    >
      {children}
    </td>
  );
}

/* ── Boutons ─────────────────────────────────────────────────────────────── */

export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
  type = "button",
  title,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  type?: "button" | "submit";
  title?: string;
  className?: string;
}) {
  const variants = {
    primary: "bg-sky-600 text-white hover:bg-sky-500",
    secondary: "border border-slate-700 text-slate-300 hover:bg-slate-800",
    danger: "border border-red-800 text-red-300 hover:bg-red-950/50",
    ghost: "text-slate-400 hover:text-slate-200",
  } as const;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/* ── Badge d'état ────────────────────────────────────────────────────────── */

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "bg-slate-800 text-slate-300",
    good: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30",
    warn: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30",
    bad: "bg-red-500/15 text-red-300 ring-1 ring-red-500/30",
    info: "bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/30",
  } as const;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
