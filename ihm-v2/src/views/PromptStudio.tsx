/**
 * Vue Prompt (atelier) — composer une requête au moteur avec tous les réglages
 * de force AVANT envoi (Phase 3 HMI, demande d'Axel).
 *
 * Réglages exposés — tous câblés bout en bout sur des contrats réels :
 * - Tier de force léger/moyen/fort/auto  → champ `tier` du body (#T194,
 *   apply_workload_override → get_provider_for_tier côté moteur).
 * - Override de modèle explicite          → champ `model` (prioritaire sur tier).
 * - Contexte additionnel                  → concaténé au prompt envoyé (et passé
 *   au PromptEngineer) — simple texte, pas un pseudo-réglage.
 * - Coût estimé avant envoi               → tarifs réels du catalogue
 *   (cost_input_per_m/output), tokens ≈ caractères/4, sortie hypothèse réglable.
 *   C'est une ESTIMATION, affichée comme telle.
 * - "Optimiser mon prompt"                → PromptEngineerAgent (#T192, branché
 *   via POST /api/prompt/engineer), résultat éditable avant envoi.
 *
 * Écartés volontairement (aucun contrat backend par requête aujourd'hui —
 * on n'affiche pas de faux interrupteurs) : activation RAG par requête,
 * sélection de contexte projet par requête (cf. #T193 pour ce dernier).
 */
import { useMemo, useRef, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchModels } from "../api/models";
import { engineerPrompt } from "../api/prompt";
import { executeStream } from "../api/chat";
import type { AgentStep } from "../state/chatStore";

type StudioTier = "auto" | "leger" | "moyen" | "fort";

const TIER_CHOICES: { key: StudioTier; label: string; hint: string }[] = [
  { key: "auto",  label: "Auto",  hint: "Cascade coût-optimisée (config du moteur)" },
  { key: "leger", label: "Léger", hint: "Routine — modèles routing_tier=leger" },
  { key: "moyen", label: "Moyen", hint: "Standard — routing_tier=moyen" },
  { key: "fort",  label: "Fort",  hint: "Raisonnement complexe — routing_tier=fort" },
];

/** Estimation grossière mais honnête : ~4 caractères par token. */
const estimateTokens = (text: string) => Math.max(1, Math.ceil(text.length / 4));

export function PromptStudio() {
  const [prompt, setPrompt] = useState("");
  const [context, setContext] = useState("");
  const [tier, setTier] = useState<StudioTier>("auto");
  const [modelOverride, setModelOverride] = useState("");
  const [outputTokens, setOutputTokens] = useState(800);

  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [agentsUsed, setAgentsUsed] = useState<string[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: fetchModels });

  // Modèles proposables en override : actifs ET câblés dans le gateway.
  const usableModels = useMemo(
    () => models.filter((m) => m.enabled && m.is_wired),
    [models],
  );

  // Modèle de référence pour l'estimation de coût : l'override s'il est choisi,
  // sinon le premier modèle actif+câblé du tier choisi ("modèle probable" — la
  // cascade peut en choisir un autre selon Elo/quotas, affiché comme tel).
  const costModel = useMemo(() => {
    if (modelOverride) return usableModels.find((m) => m.id === modelOverride) ?? null;
    if (tier === "auto") return null;
    return usableModels.find((m) => m.routing_tier === tier) ?? null;
  }, [modelOverride, tier, usableModels]);

  const fullPrompt = context.trim() ? `${prompt.trim()}\n\nContexte :\n${context.trim()}` : prompt.trim();
  const inputTokens = estimateTokens(fullPrompt);

  const estimatedCost = useMemo(() => {
    if (!costModel || costModel.cost_input_per_m == null || costModel.cost_output_per_m == null) return null;
    return (
      (inputTokens / 1_000_000) * costModel.cost_input_per_m +
      (outputTokens / 1_000_000) * costModel.cost_output_per_m
    );
  }, [costModel, inputTokens, outputTokens]);

  const engineerMut = useMutation({
    mutationFn: () =>
      engineerPrompt(prompt.trim(), context.trim(), tier === "auto" ? undefined : tier),
    onSuccess: (data) => setPrompt(data.optimized_prompt),
  });

  const send = async () => {
    if (!fullPrompt || sending) return;
    setSending(true);
    setSteps([]);
    setResult(null);
    setAgentsUsed([]);
    setSendError(null);
    abortRef.current = new AbortController();

    const workload = modelOverride
      ? { model: modelOverride }
      : tier === "auto"
        ? undefined
        : { tier: tier as "leger" | "moyen" | "fort" };

    try {
      await executeStream(
        fullPrompt,
        { type: "web", mode: "default" },
        {
          onStep: (step) => setSteps((s) => [...s, step]),
          onDone: (response, agents) => {
            setResult(response);
            setAgentsUsed(agents);
          },
          onError: (message) => setSendError(message),
        },
        abortRef.current.signal,
        workload,
      );
    } catch (e: unknown) {
      if ((e as Error)?.name !== "AbortError") {
        setSendError((e as Error)?.message ?? "Erreur réseau");
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-20">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Atelier Prompt</h1>
        <p className="text-sm text-slate-400">
          Composer, calibrer la force, estimer le coût, optimiser — puis envoyer au moteur.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* ── Colonne principale : prompt + contexte + résultat ── */}
        <div className="space-y-4 lg:col-span-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Prompt</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={8}
              placeholder="Décris ta demande au moteur…"
              className="w-full resize-y rounded-xl border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-100 placeholder-slate-500 outline-none focus:border-sky-600/60 focus:ring-1 focus:ring-sky-600/30"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Contexte additionnel (optionnel — concaténé au prompt)
            </label>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={3}
              placeholder="Extraits de code, contraintes, environnement…"
              className="w-full resize-y rounded-xl border border-slate-700 bg-slate-900/70 p-3 text-xs font-mono text-slate-300 placeholder-slate-600 outline-none focus:border-sky-600/60"
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => engineerMut.mutate()}
              disabled={!prompt.trim() || engineerMut.isPending || sending}
              title="PromptEngineerAgent (#T192) : réécrit la demande en prompt expert structuré"
              className="rounded-lg border border-purple-700/60 bg-purple-600/20 px-4 py-2 text-sm font-medium text-purple-300 transition hover:bg-purple-600/30 disabled:opacity-40"
            >
              {engineerMut.isPending ? "Optimisation…" : "✨ Optimiser mon prompt"}
            </button>
            {sending ? (
              <button
                onClick={() => abortRef.current?.abort()}
                className="rounded-lg bg-red-600/80 px-4 py-2 text-sm font-medium text-white hover:bg-red-600"
              >
                Stop
              </button>
            ) : (
              <button
                onClick={send}
                disabled={!fullPrompt}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
              >
                Envoyer au moteur
              </button>
            )}
            {engineerMut.isError && (
              <span className="text-xs text-red-400">⚠ {(engineerMut.error as Error).message}</span>
            )}
          </div>

          {(sending || result || sendError) && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              {steps.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1">
                  {steps.map((s, i) => (
                    <span key={i} className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">
                      {s.agent}{s.status ? ` — ${s.status}` : ""}
                    </span>
                  ))}
                </div>
              )}
              {sending && !result && (
                <p className="animate-pulse text-sm text-slate-400">Le moteur travaille…</p>
              )}
              {sendError && <p className="text-sm text-red-400">⚠ {sendError}</p>}
              {result && (
                <>
                  <pre className="whitespace-pre-wrap break-words text-sm text-slate-100">{result}</pre>
                  {agentsUsed.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1 border-t border-slate-800 pt-2">
                      {agentsUsed.map((a) => (
                        <span key={a} className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">{a}</span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* ── Colonne réglages ── */}
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-300">Force du workload</h3>
            <div className="grid grid-cols-2 gap-2">
              {TIER_CHOICES.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTier(t.key)}
                  disabled={sending || !!modelOverride}
                  title={t.hint}
                  className={`rounded-lg px-3 py-2 text-xs font-medium transition disabled:opacity-40 ${
                    tier === t.key && !modelOverride
                      ? "bg-sky-600 text-white"
                      : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-slate-600">
              {modelOverride ? "Ignoré : un modèle explicite est choisi." : TIER_CHOICES.find((t) => t.key === tier)?.hint}
            </p>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-300">Override de modèle</h3>
            <select
              value={modelOverride}
              onChange={(e) => setModelOverride(e.target.value)}
              disabled={sending}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
            >
              <option value="">— aucun (tier/cascade) —</option>
              {usableModels.map((m) => (
                <option key={m.id} value={m.id}>{m.id}</option>
              ))}
            </select>
            <p className="mt-2 text-[11px] text-slate-600">
              Modèles actifs et câblés dans le gateway uniquement.
            </p>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-300">Coût estimé</h3>
            <div className="space-y-2 text-xs text-slate-400">
              <div className="flex justify-between">
                <span>Tokens d'entrée (≈ car./4)</span>
                <span className="font-mono text-slate-200">{fullPrompt ? inputTokens.toLocaleString() : 0}</span>
              </div>
              <div>
                <div className="flex justify-between">
                  <span>Hypothèse de sortie</span>
                  <span className="font-mono text-slate-200">{outputTokens.toLocaleString()} tokens</span>
                </div>
                <input
                  type="range"
                  min={128}
                  max={8192}
                  step={128}
                  value={outputTokens}
                  onChange={(e) => setOutputTokens(parseInt(e.target.value, 10))}
                  className="mt-1 w-full accent-sky-500"
                />
              </div>
              <div className="border-t border-slate-800 pt-2">
                {costModel ? (
                  <>
                    <div className="flex justify-between">
                      <span>{modelOverride ? "Modèle" : "Modèle probable"}</span>
                      <span className="max-w-[55%] truncate font-mono text-slate-300" title={costModel.id}>
                        {costModel.id}
                      </span>
                    </div>
                    <div className="mt-1 flex justify-between text-sm">
                      <span className="text-slate-300">Estimation</span>
                      <span className="font-mono font-semibold text-emerald-400">
                        {estimatedCost != null
                          ? estimatedCost < 0.000001 && estimatedCost > 0
                            ? "< $0.000001"
                            : `$${estimatedCost.toFixed(6)}`
                          : "tarif inconnu"}
                      </span>
                    </div>
                    {!modelOverride && (
                      <p className="mt-1 text-[11px] text-slate-600">
                        La cascade peut choisir un autre modèle du tier (Elo/quotas).
                      </p>
                    )}
                  </>
                ) : (
                  <p className="text-[11px] text-slate-600">
                    {tier === "auto"
                      ? "Mode auto : modèle choisi par la cascade à l'exécution — pas d'estimation fiable."
                      : "Aucun modèle actif câblé pour ce tier."}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
