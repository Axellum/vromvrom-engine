/**
 * Types du contrat moteur ↔ IHM.
 *
 * Reflètent les modèles Pydantic / sérialiseurs du backend :
 *  - execution_state         → core/app_state.py (GET /api/status)
 *  - GlobalState sérialisé   → core/serializers.py:global_state_to_dict
 *  - StateUpdate / TaskPayload → core/state.py
 *
 * À terme, ces types seront générés automatiquement depuis les schémas Pydantic
 * (shared/schemas.py, cf. spec §1). Pour l'instant ils sont maintenus à la main.
 */

export type EngineStatus =
  | "idle"
  | "running"
  | "success"
  | "error"
  | "completed"
  | "stopped";

export interface TaskPayload {
  task_objective: string;
  relevant_context?: string;
  available_tools?: string[];
  metadata?: Record<string, unknown>;
  task_id?: string;
  depends_on?: string[];
}

export interface StateUpdate {
  agent_name: string;
  status: string;
  result_data?: unknown;
  next_agent?: string | null;
  error_message?: string | null;
  new_tasks?: TaskPayload[];
  metadata?: Record<string, unknown>;
}

export interface GlobalState {
  session_id?: string;
  history?: StateUpdate[];
  current_payload?: TaskPayload | null;
  shared_memory?: Record<string, unknown>;
  task_queue?: TaskPayload[];
  working_memory?: Record<string, unknown>;
}

/** Réponse de GET /api/status (core/app_state.execution_state). */
export interface ExecutionState {
  status: EngineStatus;
  objective?: string | null;
  session_id?: string | null;
  engine_state?: GlobalState | null;
  error_message?: string | null;
}

/**
 * Événement SSE du bus global GET /api/stream.
 * Format émis : `event: <event>\ndata: <json>` où le JSON contient au moins
 * `event` et `data` (cf. api/routes/streaming.py:sse_stream + broadcast_event).
 */
export interface EngineEvent {
  event: string;
  data?: Record<string, unknown>;
  engine_state?: GlobalState | null;
  status?: EngineStatus;
  /** Horodatage local d'arrivée (ajouté côté client pour l'affichage du journal). */
  receivedAt: number;
}
