import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchAgents, toggleAgent, updateAgent, deleteAgent, createAgent } from "../api/agents";
import type { AgentConfig } from "../api/agents";
import { fetchModels } from "../api/models";
import { ToolPermissionSelector } from "../components/openfox_prep/ToolPermissionSelector";

const TIER_OPTIONS = ["leger", "moyen", "fort", "automatique"];

const tierColors: Record<string, string> = {
  leger: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  moyen: "bg-sky-500/20 text-sky-400 border-sky-500/30",
  fort: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  automatique: "bg-slate-700/40 text-slate-300 border-slate-600/40",
};

function AgentCard({ agent, models }: { agent: AgentConfig; models: string[] }) {
  const queryClient = useQueryClient();

  const [isEditingPrompt, setIsEditingPrompt] = useState(false);
  const [promptValue, setPromptValue] = useState(agent.system_prompt ?? "");
  const [actionError, setActionError] = useState<string | null>(null);
  // [#T290] Édition des permissions d'outils. La valeur initiale vient du serveur
  // (rendu par le GET) : null = tous, [] = aucun, liste = partiel.
  const [isEditingPermissions, setIsEditingPermissions] = useState(false);
  const [allowedTools, setAllowedTools] = useState<string[] | null>(agent.allowed_tools ?? null);

  const invalidate = () => {
    setActionError(null);
    queryClient.invalidateQueries({ queryKey: ["agents"] });
  };
  const onError = (err: Error) => setActionError(err.message);

  const toggleMut = useMutation({ mutationFn: () => toggleAgent(agent.name), onSuccess: invalidate, onError });
  const updateMut = useMutation({
    mutationFn: (patch: Parameters<typeof updateAgent>[1]) => updateAgent(agent.name, patch),
    onSuccess: invalidate,
    onError,
  });
  const deleteMut = useMutation({ mutationFn: () => deleteAgent(agent.name), onSuccess: invalidate, onError });

  // Le "modèle" d'un agent est soit un tier (résolu par la cascade), soit un id
  // littéral — le select liste d'abord les tiers puis les modèles du catalogue.
  const modelValue = agent.model ?? "";

  return (
    <div className="flex flex-col rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-bold text-slate-200">{agent.name}</h3>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tierColors[agent.tier] || "bg-slate-800 text-slate-400"}`}>
              {agent.tier}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
            <span>{agent.label}</span>
            {agent.source === "workflow" && (
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px]" title="Défini dans l'éditeur de workflow — se gère là-bas">
                workflow
              </span>
            )}
          </div>
        </div>
        {agent.can_disable ? (
          <button
            onClick={() => toggleMut.mutate()}
            disabled={toggleMut.isPending}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${agent.enabled ? "bg-sky-600" : "bg-slate-700"} disabled:opacity-50`}
          >
            <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${agent.enabled ? "translate-x-5" : "translate-x-0"}`} />
          </button>
        ) : (
          <span className="rounded bg-slate-800/80 px-2 py-1 text-[10px] text-slate-500" title="Agent cœur du pipeline : toujours actif">
            cœur
          </span>
        )}
      </div>

      {agent.model !== null && agent.source !== "workflow" && (
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-slate-400">
            Modèle / tier (config.json)
          </label>
          <select
            value={modelValue}
            onChange={(e) => updateMut.mutate({ model: e.target.value })}
            disabled={updateMut.isPending}
            className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none disabled:opacity-50"
          >
            <optgroup label="Tiers (cascade)">
              {TIER_OPTIONS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </optgroup>
            <optgroup label="Modèles du catalogue">
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </optgroup>
            {/* Valeur actuelle hors listes (ex : modèle retiré du catalogue) */}
            {modelValue && !TIER_OPTIONS.includes(modelValue) && !models.includes(modelValue) && (
              <option value={modelValue}>{modelValue}</option>
            )}
          </select>
        </div>
      )}

      {agent.interval_minutes != null && (
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-slate-400">
            Intervalle (minutes)
          </label>
          <input
            type="number"
            min={1}
            defaultValue={agent.interval_minutes}
            onBlur={(e) => {
              const v = parseInt(e.target.value, 10);
              if (Number.isFinite(v) && v > 0 && v !== agent.interval_minutes) {
                updateMut.mutate({ interval_minutes: v });
              }
            }}
            className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-1 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
          />
        </div>
      )}

      {/* [#T290] Permissions d'outils — agents custom issus de config.json
          uniquement. Les custom `source: "workflow"` sont kind=custom aussi,
          mais updateAgent → 404 (gestion dans l'éditeur) ; même gate que
          Supprimer / sélecteur modèle (`source === "config"`). */}
      {agent.kind === "custom" && agent.source === "config" && (
        <div className="mb-4">
          {isEditingPermissions ? (
            <div className="space-y-3">
              <ToolPermissionSelector allowedTools={allowedTools} onChange={setAllowedTools} />
              <div className="flex items-center justify-between">
                <button
                  onClick={() => {
                    setIsEditingPermissions(false);
                    setAllowedTools(agent.allowed_tools ?? null);
                  }}
                  className="text-xs text-slate-400 hover:text-slate-200"
                >
                  Annuler
                </button>
                <button
                  onClick={() => {
                    updateMut.mutate({ allowed_tools: allowedTools });
                    setIsEditingPermissions(false);
                  }}
                  disabled={updateMut.isPending}
                  className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50"
                >
                  {updateMut.isPending ? "Sauvegarde…" : "Sauvegarder"}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2">
              <div className="text-xs text-slate-400">
                <span className="font-medium text-slate-300">Permissions d'outils : </span>
                {(() => {
                  const at = agent.allowed_tools;
                  if (at === null || at === undefined) return "tous les outils";
                  if (Array.isArray(at) && at.length === 0) return "aucun outil";
                  return `${(at as string[]).length} outil(s) (partiel)`;
                })()}
              </div>
              <button
                onClick={() => {
                  setAllowedTools(agent.allowed_tools ?? null);
                  setIsEditingPermissions(true);
                }}
                className="text-xs font-medium text-sky-400 hover:text-sky-300"
              >
                Éditer
              </button>
            </div>
          )}
        </div>
      )}

      <div className="mt-auto space-y-3 border-t border-slate-800 pt-3">
        {actionError && <p className="text-xs text-red-400">⚠ {actionError}</p>}
        {isEditingPrompt ? (
          <div className="space-y-2">
            <textarea
              value={promptValue}
              onChange={(e) => setPromptValue(e.target.value)}
              className="h-40 w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-xs font-mono text-slate-300 focus:border-sky-500 focus:outline-none"
            />
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-600">
                {agent.prompt_source === "markdown"
                  ? "Fichier Markdown contexte_ia (#T188)"
                  : agent.prompt_source === "config"
                    ? "custom_agents de config.json"
                    : "Défaut codé — la sauvegarde crée le Markdown"}
              </span>
              <div className="flex gap-2">
                <button onClick={() => setIsEditingPrompt(false)} className="text-xs text-slate-400 hover:text-slate-200">
                  Annuler
                </button>
                <button
                  onClick={() => {
                    updateMut.mutate({ system_prompt: promptValue });
                    setIsEditingPrompt(false);
                  }}
                  className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500"
                >
                  Sauvegarder
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            {agent.prompt_editable ? (
              <button
                onClick={() => {
                  setPromptValue(agent.system_prompt ?? "");
                  setIsEditingPrompt(true);
                }}
                className="text-xs font-medium text-sky-400 hover:text-sky-300"
              >
                Éditer prompt système
              </button>
            ) : (
              <span className="text-xs text-slate-600" title="Prompt défini dans le code Python de l'agent">
                Prompt géré dans le code
              </span>
            )}
            {agent.is_custom && agent.source === "config" && (
              <button
                onClick={() => {
                  if (confirm(`Supprimer l'agent custom '${agent.name}' ?`)) deleteMut.mutate();
                }}
                className="text-xs font-medium text-red-400 hover:text-red-300"
              >
                Supprimer
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CreateAgentModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [tier, setTier] = useState("automatique");
  const [prompt, setPrompt] = useState("");
  // [#T290] Permissions d'outils : null = tous (défaut), [] = aucun, liste = partiel.
  const [allowedTools, setAllowedTools] = useState<string[] | null>(null);

  const createMut = useMutation({
    mutationFn: () =>
      createAgent({
        name: name.trim(),
        label: label.trim() || undefined,
        tier,
        system_prompt: prompt.trim() || undefined,
        allowed_tools: allowedTools,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
    },
  });

  const nameValid = /^[a-z][a-z0-9_]{1,40}$/.test(name.trim());

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
        <h3 className="mb-4 text-lg font-bold text-white">Nouvel Agent Custom</h3>
        <p className="mb-4 text-xs text-slate-500">
          Enregistré dans <code>config.json</code> (section custom_agents) et chargé par le
          moteur au prochain pipeline — clone d'ExecutorAgent avec tous les outils.
        </p>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Nom (snake_case)</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="mon_agent_custom"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
            />
            {name && !nameValid && (
              <p className="mt-1 text-[10px] text-amber-400">snake_case, commence par une lettre, 2-41 caractères.</p>
            )}
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Libellé</label>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Agent spécialisé…"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Tier</label>
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
            >
              {TIER_OPTIONS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Prompt système (optionnel)</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-xs font-mono text-slate-300 focus:border-sky-500 focus:outline-none"
            />
          </div>
          {/* [#T290] Sélecteur de permissions d'outils (trois états : tous / aucun / partiel) */}
          <ToolPermissionSelector allowedTools={allowedTools} onChange={setAllowedTools} />
        </div>
        {createMut.isError && (
          <p className="mt-3 text-xs text-red-400">⚠ {(createMut.error as Error).message}</p>
        )}
        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800">
            Annuler
          </button>
          <button
            onClick={() => createMut.mutate()}
            disabled={!nameValid || createMut.isPending}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
          >
            {createMut.isPending ? "Création…" : "Créer"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AgentsManager() {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const { data: agents = [], isLoading, isError, error } = useQuery({
    queryKey: ["agents"],
    queryFn: fetchAgents,
  });

  const { data: modelsData } = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
  });

  const modelNames = modelsData?.filter((m) => m.enabled).map((m) => m.id) || [];
  const activeCount = agents.filter((a) => a.enabled).length;

  const coreAgents = agents.filter((a) => a.kind === "core");
  const persistentAgents = agents.filter((a) => a.kind === "persistent");
  const customAgents = agents.filter((a) => a.kind === "custom");

  if (isLoading) return <p className="text-slate-500">Chargement des agents…</p>;
  if (isError) return <p className="text-red-400">Erreur : {(error as Error).message}</p>;

  return (
    <div className="mx-auto max-w-6xl space-y-8 pb-20">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Agents</h1>
          <p className="text-sm text-slate-400">{activeCount} actifs / {agents.length} total</p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500"
        >
          + Créer un agent
        </button>
      </div>

      <section>
        <h2 className="mb-4 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          Agents du pipeline
        </h2>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {coreAgents.map((agent) => (
            <AgentCard key={agent.name} agent={agent} models={modelNames} />
          ))}
        </div>
      </section>

      {persistentAgents.length > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/30 p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-300 uppercase tracking-wider">
            Agents Persistants (Daemon / Dreamer)
          </h2>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {persistentAgents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} models={modelNames} />
            ))}
          </div>
        </section>
      )}

      {customAgents.length > 0 && (
        <section>
          <h2 className="mb-4 text-sm font-semibold text-slate-300 uppercase tracking-wider">
            Agents Custom
          </h2>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {customAgents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} models={modelNames} />
            ))}
          </div>
        </section>
      )}

      {isModalOpen && <CreateAgentModal onClose={() => setIsModalOpen(false)} />}
    </div>
  );
}
