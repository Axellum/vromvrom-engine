/**
 * Vue Configuration — édition complète de config.json (merge POST /api/config)
 * + état lecture seule des secrets .env (GET /api/keys).
 *
 * Pilotée par le registre CONFIG_SECTIONS (types/config.ts) : chaque champ est
 * édité selon son type, le dirty-tracking compare à la valeur chargée, et seules
 * les clés de premier niveau modifiées sont envoyées (merge partiel côté backend).
 *
 * Organisée en 4 groupes (routage / budgets / autonomie / performance) plutôt
 * qu'en liste continue : le registre couvre désormais ~45 réglages, une page
 * unique déroulante devenait illisible.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { fetchConfig, fetchKeys, updateConfig, type EngineConfig } from "../api/config";
import { CONFIG_SECTIONS, CONFIG_GROUPS, SECRET_LABELS } from "../types/config";
import type { ConfigSection } from "../types/config";
import { ConfigFieldControl } from "../components/config/ConfigFieldControl";
import { getByPath, setByPath } from "../lib/dotpath";
import {
  PageHeader, Card, Explain, Button, Pill, LoadingState, ErrorState, EmptyState,
} from "../components/ui/primitives";

export function Configuration() {
  const queryClient = useQueryClient();
  // Brouillon local des modifications (chemin pointé → valeur).
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [group, setGroup] = useState<ConfigSection["group"]>("routage");
  const [filter, setFilter] = useState("");

  const configQ = useQuery({ queryKey: ["config"], queryFn: fetchConfig });
  const keysQ = useQuery({ queryKey: ["keys"], queryFn: fetchKeys });

  const loaded = configQ.data;
  const dirtyPaths = Object.keys(draft);

  const save = useMutation({
    mutationFn: () => {
      // Reconstruit un patch ne contenant que les clés de 1er niveau touchées,
      // en fusionnant le brouillon dans une copie de la config chargée.
      let merged: EngineConfig = { ...(loaded ?? {}) };
      for (const path of dirtyPaths) {
        merged = setByPath(merged, path, draft[path]);
      }
      const touchedTopKeys = new Set(dirtyPaths.map((p) => p.split(".")[0]));
      const patch: EngineConfig = {};
      for (const k of touchedTopKeys) patch[k] = merged[k];
      return updateConfig(patch);
    },
    onSuccess: () => {
      setDraft({});
      void queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const valueFor = useMemo(
    () => (path: string) => (path in draft ? draft[path] : getByPath(loaded, path)),
    [draft, loaded],
  );

  function onFieldChange(path: string, value: unknown) {
    setDraft((d) => {
      const original = getByPath(loaded, path);
      const next = { ...d };
      // Si on revient à la valeur d'origine, on retire l'entrée (plus "dirty").
      if (JSON.stringify(value) === JSON.stringify(original)) delete next[path];
      else next[path] = value;
      return next;
    });
  }

  // Recherche transversale : un filtre non vide traverse tous les groupes.
  const needle = filter.trim().toLowerCase();
  const visibleSections = useMemo(() => {
    const base = needle
      ? CONFIG_SECTIONS
      : CONFIG_SECTIONS.filter((s) => s.group === group);
    if (!needle) return base;
    return base
      .map((s) => ({
        ...s,
        fields: s.fields.filter(
          (f) =>
            f.label.toLowerCase().includes(needle) ||
            f.path.toLowerCase().includes(needle) ||
            f.help.toLowerCase().includes(needle),
        ),
      }))
      .filter((s) => s.fields.length > 0);
  }, [group, needle]);

  const dirtyCountByGroup = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const path of dirtyPaths) {
      const section = CONFIG_SECTIONS.find((s) => s.fields.some((f) => f.path === path));
      if (section) counts[section.group] = (counts[section.group] ?? 0) + 1;
    }
    return counts;
  }, [dirtyPaths]);

  const totalFields = CONFIG_SECTIONS.reduce((n, s) => n + s.fields.length, 0);

  if (configQ.isLoading) return <LoadingState label="Chargement de la configuration…" />;
  if (configQ.isError) {
    return (
      <div className="mx-auto max-w-4xl">
        <ErrorState error={configQ.error} onRetry={() => configQ.refetch()} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl pb-28">
      <PageHeader
        title="Configuration"
        description={
          <>
            {totalFields} réglages de <code>config.json</code>, rechargés à chaud par le moteur.
            Chaque champ indique ce qu'il fait et l'effet concret de sa modification.
          </>
        }
      />

      <div className="space-y-6">
        {/* Recherche + navigation par groupe */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Rechercher un réglage (nom, chemin, description)…"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm outline-none focus:border-sky-500"
            />
          </div>
        </div>

        {!needle && (
          <div className="flex flex-wrap gap-2">
            {CONFIG_GROUPS.map((g) => (
              <button
                key={g.id}
                onClick={() => setGroup(g.id)}
                title={g.description}
                className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                  group === g.id
                    ? "bg-sky-600 text-white"
                    : "border border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                {g.label}
                {dirtyCountByGroup[g.id] ? (
                  <span className="ml-2 rounded-full bg-amber-500/25 px-1.5 text-xs text-amber-300">
                    {dirtyCountByGroup[g.id]}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        )}

        {!needle && (
          <Explain>
            <p>{CONFIG_GROUPS.find((g) => g.id === group)?.description}</p>
          </Explain>
        )}

        {visibleSections.length === 0 ? (
          <EmptyState>Aucun réglage ne correspond à « {filter} ».</EmptyState>
        ) : (
          visibleSections.map((section) => (
            <Card key={section.id} title={section.title} subtitle={section.description}>
              <div className="divide-y divide-slate-800/60">
                {section.fields.map((field) => (
                  <ConfigFieldControl
                    key={field.path}
                    field={field}
                    value={valueFor(field.path)}
                    dirty={field.path in draft}
                    onChange={(v) => onFieldChange(field.path, v)}
                  />
                ))}
              </div>
            </Card>
          ))
        )}

        {/* Secrets .env — lecture seule (aucun endpoint d'écriture côté backend). */}
        <Card
          title="Clés API & secrets"
          subtitle="Présence des secrets définis dans le .env du moteur. Lecture seule — ils s'éditent côté serveur, jamais via l'IHM."
          source="GET /api/keys"
        >
          {keysQ.isLoading ? (
            <LoadingState />
          ) : keysQ.isError ? (
            <ErrorState error={keysQ.error} onRetry={() => keysQ.refetch()} />
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {Object.entries(keysQ.data?.keys ?? {}).map(([name, present]) => (
                <div
                  key={name}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-800/40 px-3 py-2"
                >
                  <span className="text-sm text-slate-300">{SECRET_LABELS[name] ?? name}</span>
                  <Pill tone={present ? "good" : "neutral"}>
                    {present ? "configurée" : "absente"}
                  </Pill>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Barre de sauvegarde flottante */}
      {dirtyPaths.length > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-30 border-t border-slate-700 bg-slate-900/95 backdrop-blur">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-6 py-3">
            <span className="text-sm text-amber-300">
              {dirtyPaths.length} modification{dirtyPaths.length > 1 ? "s" : ""} non enregistrée
              {dirtyPaths.length > 1 ? "s" : ""}
            </span>
            <div className="flex items-center gap-3">
              {save.isError && (
                <span className="text-sm text-red-400">{(save.error as Error).message}</span>
              )}
              {save.isSuccess && <span className="text-sm text-emerald-400">Enregistré ✓</span>}
              <Button variant="ghost" onClick={() => setDraft({})}>Annuler</Button>
              <Button onClick={() => save.mutate()} disabled={save.isPending}>
                {save.isPending ? "Enregistrement…" : "Enregistrer"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
