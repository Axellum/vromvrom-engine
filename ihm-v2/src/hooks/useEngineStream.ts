/**
 * Abonnement au flux d'événements global du moteur (GET /api/stream) via SSE.
 *
 * Particularité : le flux est protégé par require_auth et authentifié par un
 * ticket éphémère à USAGE UNIQUE (cf. api/client.buildStreamUrl). Comme un
 * EventSource peut se reconnecter tout seul (et réutiliserait alors un ticket
 * déjà consommé → 401), on gère la reconnexion à la main : à chaque coupure on
 * ferme, on attend un court backoff, puis on redemande un ticket frais.
 */
import { useEffect } from "react";
import { buildStreamUrl, isAuthError } from "../api/client";
import { useEngineStore } from "../state/engineStore";
import type { EngineEvent } from "../types/engine";

interface Options {
  /** Désactive l'abonnement (ex: tant qu'aucune clé API n'est saisie). */
  enabled?: boolean;
}

const RECONNECT_MS = 2000;

export function useEngineStream({ enabled = true }: Options = {}): void {
  const applyEvent = useEngineStore((s) => s.applyEvent);
  const setConnection = useEngineStore((s) => s.setConnection);

  useEffect(() => {
    if (!enabled) return;

    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const handle = (raw: MessageEvent, eventName: string) => {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = raw.data ? JSON.parse(raw.data) : {};
      } catch {
        parsed = { raw: raw.data };
      }
      const evt: EngineEvent = {
        event: (parsed.event as string) ?? eventName,
        data: (parsed.data as Record<string, unknown>) ?? parsed,
        engine_state: (parsed.engine_state as EngineEvent["engine_state"]) ?? null,
        status: parsed.status as EngineEvent["status"],
        receivedAt: Date.now(),
      };
      applyEvent(evt);
    };

    // Types d'événements nommés émis par le bus (api/routes/streaming.py + spec §1).
    const NAMED = [
      "ping",
      "agent_started",
      "agent_completed",
      "task_started",
      "task_completed",
      "orchestration_completed",
      "quotas_updated",
      "apis_status_updated",
      "tokens_updated",
      "approval_required",
      "approval_user_action",
    ];

    async function connect() {
      if (cancelled) return;
      setConnection("connecting");
      try {
        const url = await buildStreamUrl("/api/stream");
        if (cancelled) return;
        source = new EventSource(url);

        source.onopen = () => setConnection("open");
        // Événements génériques (sans champ `event:` nommé).
        source.onmessage = (e) => handle(e, "message");
        for (const name of NAMED) {
          source.addEventListener(name, (e) => handle(e as MessageEvent, name));
        }
        source.onerror = () => {
          // EventSource tenterait de réutiliser un ticket consommé → on reprend la main.
          source?.close();
          source = null;
          setConnection("closed");
          if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_MS);
        };
      } catch (err) {
        setConnection("closed");
        if (isAuthError(err)) {
          // apiFetch (dans buildStreamUrl) a déjà notifié le handler global d'auth.
          return; // pas de reconnexion en boucle sur une clé invalide
        }
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_MS);
      }
    }

    void connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [enabled, applyEvent, setConnection]);
}
