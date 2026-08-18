/**
 * Vue Chat — interaction conversationnelle avec le tab5-engine.
 *
 * - Envoi vers POST /api/execute/stream (SSE progressif).
 * - Affichage des étapes intermédiaires (agents en cours).
 * - Sélecteur de tier (local / léger / fort) dans l'en-tête.
 * - Jauge de tokens depuis le store global (SSE /api/stream).
 */
import { useEffect, useRef, useState, useCallback, lazy, Suspense } from "react";
import { useChatStore, type Tier } from "../state/chatStore";
import { useEngineStore } from "../state/engineStore";
import { executeStream } from "../api/chat";
import { SessionHistory } from "../components/common/SessionHistory";
import { RealTimePanel } from "../components/common/RealTimePanel";
import { ThinkingBlock } from "../components/openfox_prep/ThinkingBlock";
import { ToolCallDisplay } from "../components/openfox_prep/ToolCallDisplay";

const MarkdownRenderer = lazy(() =>
  import("../components/common/MarkdownRenderer").then((m) => ({ default: m.MarkdownRenderer }))
);

// ─── Tier selector ────────────────────────────────────────────────────────────

/**
 * [#T194] Sélecteur de force du workload — envoyé dans le champ `tier` du body
 * (backend : apply_workload_override → get_provider_for_tier). L'ancien
 * sélecteur passait le tier dans source.mode, que le backend ignorait
 * silencieusement (ModeType ne connaît pas "leger") : il ne faisait RIEN.
 */
const TIERS: { key: Tier; label: string; hint: string }[] = [
  { key: "auto",  label: "Auto",  hint: "Cascade coût-optimisée (défaut config)" },
  { key: "leger", label: "Léger", hint: "Routine, local/free (routing_tier=leger)" },
  { key: "moyen", label: "Moyen", hint: "Standard (routing_tier=moyen)" },
  { key: "fort",  label: "Fort",  hint: "Raisonnement complexe (routing_tier=fort)" },
];

function TierSelector({ value, onChange, disabled }: {
  value: Tier;
  onChange: (t: Tier) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex gap-1">
      {TIERS.map((t) => (
        <button
          key={t.key}
          disabled={disabled}
          onClick={() => onChange(t.key)}
          title={t.hint}
          className={`rounded-md px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
            value === t.key
              ? "bg-sky-600 text-white"
              : "bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ─── Token gauge ──────────────────────────────────────────────────────────────

function TokenGauge() {
  const engineState = useEngineStore((s) => s.engineState);
  const tokens: number = (engineState as Record<string, unknown> | null)?.tokens_used as number ?? 0;
  const limit = 128_000;
  const pct = Math.min((tokens / limit) * 100, 100);
  const color = pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-emerald-500";

  if (!tokens) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-slate-500" title={`${tokens.toLocaleString()} tokens`}>
      <span>Ctx</span>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span>{tokens > 1000 ? `${(tokens / 1000).toFixed(1)}k` : tokens}</span>
    </div>
  );
}

// ─── Agent step badge ─────────────────────────────────────────────────────────

function StepBadge({ agent, status }: { agent: string; status: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
      {agent}
      {status ? ` — ${status}` : ""}
    </span>
  );
}

// ─── Message bubble ───────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: ReturnType<typeof useChatStore.getState>["messages"][0] }) {
  const isUser = msg.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[78%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
        isUser
          ? "bg-sky-600/80 text-white"
          : "bg-slate-800/80 text-slate-100"
      }`}>
        {/* Étapes intermédiaires */}
        {!isUser && msg.steps && msg.steps.length > 0 && msg.status === "streaming" && (
          <div className="mb-2 flex flex-wrap gap-1">
            {msg.steps.map((s, i) => (
              <StepBadge key={i} agent={s.agent} status={s.status} />
            ))}
          </div>
        )}

        {/* [#T291] Raisonnement du modèle — alimenté par les événements SSE
            `thinking` / `thinking_done` (#T346). Tenu hors du contenu rendu. */}
        {!isUser && (msg.thinkingText || msg.thinkingStreaming) && (
          <ThinkingBlock
            thinkingText={msg.thinkingText ?? ""}
            isStreaming={msg.thinkingStreaming ?? false}
            durationMs={msg.thinkingDurationMs}
          />
        )}

        {/* [#T291] Appels d'outils — `tool_call` puis `tool_result` (#T346). */}
        {!isUser && msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mb-1">
            {msg.toolCalls.map((tc) => (
              <ToolCallDisplay key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {/* Contenu */}
        {msg.status === "streaming" && !msg.content ? (
          <span className="flex gap-1 text-slate-400">
            <span className="animate-bounce">●</span>
            <span className="animate-bounce [animation-delay:0.15s]">●</span>
            <span className="animate-bounce [animation-delay:0.3s]">●</span>
          </span>
        ) : msg.status === "error" ? (
          <span className="text-red-400">⚠ {msg.error ?? "Erreur"}</span>
        ) : isUser ? (
          <span className="whitespace-pre-wrap text-sm">{msg.content}</span>
        ) : (
          <Suspense fallback={<span className="text-slate-400 text-sm whitespace-pre-wrap">{msg.content}</span>}>
            <MarkdownRenderer content={msg.content} />
          </Suspense>
        )}

        {/* Agents utilisés */}
        {!isUser && msg.status === "done" && msg.agentsUsed && msg.agentsUsed.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1 border-t border-slate-700/50 pt-2">
            {msg.agentsUsed.map((a) => (
              <span key={a} className="rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] text-slate-400">
                {a}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Vue principale ───────────────────────────────────────────────────────────

export function Chat() {
  const messages    = useChatStore((s) => s.messages);
  const streaming   = useChatStore((s) => s.streaming);
  const tier        = useChatStore((s) => s.tier);
  const setTier     = useChatStore((s) => s.setTier);
  const addUser     = useChatStore((s) => s.addUserMessage);
  const addAsst     = useChatStore((s) => s.addAssistantMessage);
  const appendStep  = useChatStore((s) => s.appendStep);
  const appendThink = useChatStore((s) => s.appendThinking);
  const finishThink = useChatStore((s) => s.finishThinking);
  const startTool   = useChatStore((s) => s.startToolCall);
  const resolveTool = useChatStore((s) => s.resolveToolCall);
  const finalize    = useChatStore((s) => s.finalizeMessage);
  const failMsg     = useChatStore((s) => s.failMessage);
  const setStream   = useChatStore((s) => s.setStreaming);
  const clearHist   = useChatStore((s) => s.clearHistory);
  const newSession  = useChatStore((s) => s.newSession);
  const loadFromBackend = useChatStore((s) => s.loadFromBackend);
  const hydrated    = useChatStore((s) => s.hydrated);

  const [input, setInput] = useState("");
  const [showHistory, setShowHistory] = useState(true);
  const [showPanel, setShowPanel] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef  = useRef<AbortController | null>(null);

  // Chargement de l'historique persisté au montage (T83)
  useEffect(() => {
    if (!hydrated) loadFromBackend();
  }, [hydrated, loadFromBackend]);

  // Auto-scroll vers le bas
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    addUser(text);
    const assistantId = addAsst();
    setStream(true);

    abortRef.current = new AbortController();

    // [#T194] "web" est un SourceType reconnu (source_router.py) ; le tier part
    // dans le champ dédié du body, plus dans source.mode (qui était ignoré).
    const source = { type: "web", mode: "default" };
    const workload = tier === "auto" ? undefined : { tier: tier as "leger" | "moyen" | "fort" };

    try {
      await executeStream(
        text,
        source,
        {
          onStep:         (step)                  => appendStep(assistantId, step),
          onThinking:     (text)                  => appendThink(assistantId, text),
          onThinkingDone: (durationMs)            => finishThink(assistantId, durationMs),
          onToolCall:     (call)                  => startTool(assistantId, call),
          onToolResult:   (result)                => resolveTool(assistantId, result),
          onDone:         (response, agentsUsed)  => finalize(assistantId, response, agentsUsed),
          onError:        (message)               => failMsg(assistantId, message),
        },
        abortRef.current.signal,
        workload,
      );
    } catch (e: unknown) {
      if ((e as Error)?.name !== "AbortError") {
        failMsg(assistantId, (e as Error)?.message ?? "Erreur réseau");
      }
    } finally {
      setStream(false);
      abortRef.current = null;
    }
  }, [input, streaming, tier, addUser, addAsst, appendStep, appendThink, finishThink,
      startTool, resolveTool, finalize, failMsg, setStream]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
  };

  return (
    <div className="flex h-full gap-0">
      {/* Sidebar historique (masquable) */}
      {showHistory && (
        <SessionHistory onSelectObjective={(obj) => setInput(obj)} />
      )}

      {/* Colonne principale */}
      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
      {/* En-tête */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="rounded p-1 text-slate-600 hover:text-slate-400 transition"
            title={showHistory ? "Masquer l'historique" : "Afficher l'historique"}
          >
            {showHistory ? "◀" : "▶"}
          </button>
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Chat</h1>
            <p className="text-xs text-slate-500">Pipeline agents — streaming SSE</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <TokenGauge />
          <TierSelector value={tier} onChange={setTier} disabled={streaming} />
          <button
            onClick={() => setShowPanel((v) => !v)}
            className={`rounded p-1 text-xs transition ${showPanel ? "text-sky-400" : "text-slate-600 hover:text-slate-400"}`}
            title={showPanel ? "Masquer panneau temps réel" : "Afficher panneau temps réel"}
          >
            ⚡
          </button>
          <button
            onClick={newSession}
            disabled={streaming}
            className="rounded px-2 py-1 text-xs text-slate-500 hover:text-slate-300 disabled:opacity-40"
            title="Nouvelle conversation"
          >
            + Nouveau
          </button>
          {messages.length > 0 && (
            <button
              onClick={clearHist}
              disabled={streaming}
              className="rounded px-2 py-1 text-xs text-slate-500 hover:text-slate-300 disabled:opacity-40"
              title="Effacer l'affichage (sans supprimer)"
            >
              Effacer
            </button>
          )}
        </div>
      </div>

      {/* Zone messages */}
      <div className="flex-1 overflow-y-auto py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-600">
            <span className="text-4xl">💬</span>
            <p className="text-sm">Pose une question au moteur…</p>
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              {["État du système domotique", "Quels agents sont actifs ?", "Lance une analyse HA"].map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); }}
                  className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-400 hover:border-slate-500 hover:text-slate-200 transition"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 px-2">
            {messages.map((m) => (
              <MessageBubble key={m.id} msg={m} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Zone saisie */}
      <div className="border-t border-slate-800 pt-4">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={streaming}
            placeholder="Écris ton message… (Entrée pour envoyer, Maj+Entrée pour sauter une ligne)"
            rows={2}
            className="flex-1 resize-none rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 outline-none focus:border-sky-600/60 focus:ring-1 focus:ring-sky-600/30 disabled:opacity-50"
          />
          {streaming ? (
            <button
              onClick={cancel}
              className="rounded-xl bg-red-600/80 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 transition"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim()}
              className="rounded-xl bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Envoyer
            </button>
          )}
        </div>
        <p className="mt-1.5 text-[11px] text-slate-600">
          Tier : <span className="text-slate-500">{TIERS.find((t) => t.key === tier)?.hint}</span>
        </p>
      </div>
      </div>{/* fin colonne principale */}

      {/* Panneau temps réel (T84) */}
      {showPanel && (
        <RealTimePanel onClose={() => setShowPanel(false)} />
      )}
    </div>
  );
}
