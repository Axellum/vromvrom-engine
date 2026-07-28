import { apiFetch } from "./client";

/**
 * Client du backlog de tâches — la VRAIE file d'attente du moteur.
 *
 * L'ancienne vue « Lancer » interrogeait `/api/queue`, qui n'existe nulle part
 * côté backend, et repliait sur deux tâches inventées. La file réelle est
 * `core/backlog_db` exposée par `api/routes/backlog.py` (préfixe `/api/backlog`),
 * et c'est elle que consomme le DreamCoder pour son travail de fond.
 */

export type BacklogStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "paused"
  | "abandoned";

export interface BacklogTask {
  id: number;
  title: string;
  description: string;
  /** 1 = haute, 2 = moyenne, 3 = basse. */
  priority: number;
  status: BacklogStatus;
  created_at: number;
  scheduled_at: number | null;
  git_branch: string | null;
  result_summary: string | null;
  tokens_used: number | null;
  error_message: string | null;
  retries: number;
}

export type BacklogStats = Record<BacklogStatus, number>;

export async function fetchBacklogTasks(): Promise<BacklogTask[]> {
  return apiFetch<BacklogTask[]>("/api/backlog/tasks");
}

export async function fetchBacklogStats(): Promise<BacklogStats> {
  return apiFetch<BacklogStats>("/api/backlog/stats");
}

export async function createBacklogTask(input: {
  title: string;
  description: string;
  priority?: number;
  scheduled_at?: number | null;
}): Promise<{ status: string; task_id: number }> {
  return apiFetch("/api/backlog/tasks", {
    method: "POST",
    body: JSON.stringify({
      title: input.title,
      description: input.description,
      priority: input.priority ?? 2,
      scheduled_at: input.scheduled_at ?? null,
    }),
  });
}

/**
 * Change le statut d'une tâche.
 *
 * ⚠ Côté backend, `approved` et `rejected` déclenchent de VRAIES opérations Git
 * (merge --no-ff puis suppression de branche, ou branch -D). Ce ne sont pas de
 * simples changements de libellé — l'IHM doit demander confirmation.
 */
export async function updateBacklogTask(
  id: number,
  status: "approved" | "rejected" | "pending" | BacklogStatus,
): Promise<{ status: string }> {
  return apiFetch(`/api/backlog/tasks/${id}`, {
    method: "PUT",
    body: JSON.stringify({ status }),
  });
}

export async function deleteBacklogTask(id: number): Promise<{ status: string }> {
  return apiFetch(`/api/backlog/tasks/${id}`, { method: "DELETE" });
}
