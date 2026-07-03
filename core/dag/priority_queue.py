"""
core/dag/priority_queue.py — Gestion des priorités et de l'ordonnancement des tâches du DAG.
"""

from core.runtime_db import get_connection


def get_initial_ready_tasks(session_id: str) -> list[str]:
    """
    Récupère la liste des identifiants des tâches initialement prêtes (sans parents/dépendances non résolues).
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT t.task_id 
            FROM dag_tasks t
            WHERE t.session_id = ? 
              AND t.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM dag_edges e 
                  WHERE e.session_id = t.session_id 
                    AND e.child_task_id = t.task_id
              )
            """,
            (session_id,)
        )
        return [row[0] for row in cursor.fetchall()]


def get_task_priority(task_payload) -> int:
    """
    Calcule la priorité d'une tâche. Plus la valeur est basse, plus la tâche est prioritaire.
    Actuellement basée sur le `stage_id`.
    """
    return task_payload.metadata.get("stage_id", 1)
