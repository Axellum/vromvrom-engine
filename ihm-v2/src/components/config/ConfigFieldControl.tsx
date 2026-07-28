/**
 * Rend le contrôle d'édition adapté au type d'un ConfigField.
 *
 * Types gérés : toggle, number, text, time, select (énumération fermée) et
 * list (liste ordonnée de chaînes — tiers de modèles, scopes, exclusions).
 * Le type `list` est essentiel : les listes de modèles par tier définissent
 * l'ordre exact de la cascade, et l'ordre compte.
 */
import { useState } from "react";
import { GripVertical, X, Plus, ArrowUp, ArrowDown } from "lucide-react";
import type { ConfigField } from "../../types/config";

interface Props {
  field: ConfigField;
  value: unknown;
  dirty: boolean;
  onChange: (value: unknown) => void;
}

export function ConfigFieldControl({ field, value, dirty, onChange }: Props) {
  const effective = value ?? field.fallback;

  return (
    <div className="flex flex-col gap-1 py-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-slate-200">{field.label}</span>
        {dirty && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" title="Modifié" />}
        <code className="ml-auto text-[10px] text-slate-600">{field.path}</code>
      </div>
      <p className="text-xs text-slate-500">{field.help}</p>
      {field.impact && (
        <p className="text-xs text-slate-600">
          <span className="text-slate-500">Effet :</span> {field.impact}
        </p>
      )}

      {field.type === "toggle" ? (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(effective)}
          onClick={() => onChange(!effective)}
          className={`mt-1 flex h-6 w-11 items-center rounded-full px-0.5 transition ${
            effective ? "bg-sky-600" : "bg-slate-700"
          }`}
        >
          <span
            className={`h-5 w-5 rounded-full bg-white transition ${effective ? "translate-x-5" : ""}`}
          />
        </button>
      ) : field.type === "select" ? (
        <select
          value={effective === undefined || effective === null ? "" : String(effective)}
          onChange={(e) => {
            const raw = e.target.value;
            const opt = field.options?.find((o) => String(o.value) === raw);
            onChange(opt ? opt.value : raw);
          }}
          className="mt-1 w-full max-w-md rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-slate-100 outline-none focus:border-sky-500"
        >
          {field.options?.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.type === "list" ? (
        <ListEditor
          items={Array.isArray(effective) ? (effective as string[]) : []}
          ordered={field.ordered}
          placeholder={field.placeholder}
          onChange={onChange}
        />
      ) : (
        <input
          type={field.type === "number" ? "number" : field.type === "time" ? "time" : "text"}
          value={effective === undefined || effective === null ? "" : String(effective)}
          placeholder={field.placeholder}
          min={field.min}
          max={field.max}
          step={field.step}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(field.type === "number" ? (raw === "" ? "" : Number(raw)) : raw);
          }}
          className="mt-1 w-full max-w-md rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-slate-100 outline-none focus:border-sky-500"
        />
      )}
    </div>
  );
}

/**
 * Éditeur de liste de chaînes. Quand `ordered` est vrai, l'ordre est signifiant
 * (cas des tiers : le moteur essaie les modèles dans cet ordre) et des flèches
 * de réordonnancement sont proposées.
 */
function ListEditor({
  items,
  ordered,
  placeholder,
  onChange,
}: {
  items: string[];
  ordered?: boolean;
  placeholder?: string;
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const move = (from: number, to: number) => {
    if (to < 0 || to >= items.length) return;
    const next = [...items];
    const [it] = next.splice(from, 1);
    next.splice(to, 0, it);
    onChange(next);
  };

  return (
    <div className="mt-1 space-y-2">
      {items.length === 0 ? (
        <p className="text-xs italic text-slate-600">Liste vide.</p>
      ) : (
        <ol className="space-y-1">
          {items.map((item, i) => (
            <li
              key={`${item}-${i}`}
              className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-800/60 px-2 py-1"
            >
              {ordered && (
                <>
                  <GripVertical className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                  <span className="w-5 shrink-0 text-right font-mono text-[10px] text-slate-500">
                    {i + 1}
                  </span>
                </>
              )}
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-200">{item}</span>
              {ordered && (
                <>
                  <button
                    type="button"
                    onClick={() => move(i, i - 1)}
                    disabled={i === 0}
                    className="shrink-0 text-slate-600 hover:text-sky-400 disabled:opacity-30"
                    title="Monter (essayé plus tôt)"
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => move(i, i + 1)}
                    disabled={i === items.length - 1}
                    className="shrink-0 text-slate-600 hover:text-sky-400 disabled:opacity-30"
                    title="Descendre"
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={() => onChange(items.filter((_, j) => j !== i))}
                className="shrink-0 text-slate-600 hover:text-red-400"
                title="Retirer"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim()) {
              e.preventDefault();
              onChange([...items, draft.trim()]);
              setDraft("");
            }
          }}
          placeholder={placeholder ?? "Ajouter une entrée…"}
          className="w-full max-w-xs rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-100 outline-none focus:border-sky-500"
        />
        <button
          type="button"
          onClick={() => {
            if (draft.trim()) {
              onChange([...items, draft.trim()]);
              setDraft("");
            }
          }}
          disabled={!draft.trim()}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-1.5 text-xs text-slate-300 transition hover:bg-slate-800 disabled:opacity-40"
        >
          <Plus className="h-3.5 w-3.5" />
          Ajouter
        </button>
      </div>
    </div>
  );
}
