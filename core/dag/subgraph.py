"""
core/dag/subgraph.py — Subgraph nesting du DAGRunner.

Extrait de core/dag_runner.py (refactor #T303) sans changement de comportement :
- `execute_subgraph` (et sa constante `MAX_SUBGRAPH_DEPTH`, sans importeur externe)

`SubgraphMixin` fournit la méthode à `DAGRunner` par héritage ; le corps est
strictement identique à l'original (déplacement pur, vérifié par comparaison AST).
"""

import logging

from core.state import StateUpdate, TaskPayload

logger = logging.getLogger(__name__)

# [#T111] Profondeur maximale de récursion des subgraphs (execute_subgraph → execute_dag),
# garde-fou anti-boucle inspiré du `recursion_limit` LangGraph. Un workflow imbriqué qui
# se ré-invoque lui-même (ex: Planner qui re-génère le même subgraph) est stoppé net
# plutôt que de récurser indéfiniment.
MAX_SUBGRAPH_DEPTH = 5


class SubgraphMixin:
    """
    Subgraph nesting (workflows imbriqués, DAG récursif) du DAGRunner.

    Mixin interne, non instanciable seul : il dépend des attributs d'instance
    posés par `DAGRunner.__init__` (`_subgraph_depth`) et appelle `execute_dag`
    (fournie par `DAGRunner`).
    """

    # ──────────────────────────────────────────────────────────────────
    # Subgraph Nesting — Workflows imbriqués (DAG récursif)
    # ──────────────────────────────────────────────────────────────────

    async def execute_subgraph(
        self,
        parent_task: TaskPayload,
        sub_tasks: list[TaskPayload],
        max_session_tokens: int = 200_000,
        on_event=None,
    ) -> StateUpdate:
        """
        Exécute un sous-DAG complet comme nœud d'un DAG parent.

        Permet d'imbriquer des workflows complexes :
        - Une tâche du plan principal peut déclencher un sous-plan complet
        - Le contexte du parent est propagé aux sous-tâches
        - Les résultats sont agrégés dans un StateUpdate unique

        Args:
            parent_task: La tâche parent qui contient le subgraph
            sub_tasks: Les tâches du sous-DAG à exécuter
            max_session_tokens: Budget tokens pour le sous-DAG
            on_event: Callback SSE pour l'IHM

        Returns:
            StateUpdate agrégé du sous-DAG.
        """
        parent_id = parent_task.task_id or "subgraph"

        # [#T111] Garde-fou anti-boucle : plafonner la profondeur de récursion des
        # subgraphs (execute_subgraph → execute_dag → ... → execute_subgraph), inspiré
        # du `recursion_limit` LangGraph. Sans ce plafond, un subgraph qui se
        # ré-invoque lui-même (ex: re-plan récursif) récurserait indéfiniment.
        if self._subgraph_depth >= MAX_SUBGRAPH_DEPTH:
            logger.error(
                f"[DAG] ⛔ Profondeur maximale de subgraph atteinte ({MAX_SUBGRAPH_DEPTH}) "
                f"pour '{parent_id}' — arrêt anti-boucle."
            )
            if on_event:
                await on_event("loop_limit_exceeded", {
                    "kind": "subgraph_depth",
                    "limit": MAX_SUBGRAPH_DEPTH,
                    "parent_task_id": parent_id,
                })
            return StateUpdate(
                agent_name="dag_runner",
                status="error",
                error_message=(
                    f"Profondeur maximale de subgraph ({MAX_SUBGRAPH_DEPTH}) "
                    f"dépassée pour '{parent_id}'"
                ),
                metadata={"subgraph": parent_id},
            )

        self._subgraph_depth += 1
        try:
            logger.info(
                f"[DAG] 🔀 Subgraph '{parent_id}' démarré (profondeur {self._subgraph_depth}) : "
                f"{len(sub_tasks)} sous-tâches"
            )

            if on_event:
                await on_event("subgraph_started", {
                    "parent_task_id": parent_id,
                    "sub_task_count": len(sub_tasks),
                    "sub_tasks": [
                        {"task_id": t.task_id, "objective": t.task_objective[:80]}
                        for t in sub_tasks
                    ],
                })

            # Propager le contexte du parent dans toutes les sous-tâches
            for sub in sub_tasks:
                if parent_task.relevant_context:
                    sub.relevant_context = (
                        f"--- CONTEXTE PARENT (Subgraph '{parent_id}') ---\n"
                        f"{parent_task.relevant_context or ''}\n\n"
                        f"{sub.relevant_context or ''}"
                    ).strip()
                # Préfixer les task_id pour éviter les collisions
                if sub.task_id and not sub.task_id.startswith(f"{parent_id}_"):
                    sub.task_id = f"{parent_id}_{sub.task_id}"

            # Exécuter le sous-DAG via le même DAGRunner (récursion)
            sub_status, sub_has_error = await self.execute_dag(
                sub_tasks, max_session_tokens, on_event
            )

            # Agréger les résultats des sous-tâches
            sub_results = []
            sub_errors = []
            for task_id, status in sub_status.items():
                # Chercher le résultat dans l'historique du moteur
                matching = [
                    h for h in self._engine.state.history
                    if h.metadata and h.metadata.get("task_id") == task_id
                ]
                if matching:
                    last = matching[-1]
                    if last.status == "success":
                        sub_results.append(
                            f"[{task_id}] {last.result_data or 'OK'}"
                        )
                    else:
                        sub_errors.append(
                            f"[{task_id}] ❌ {last.error_message or 'Échec'}"
                        )

            if on_event:
                await on_event("subgraph_completed", {
                    "parent_task_id": parent_id,
                    "status": "error" if sub_has_error else "success",
                    "success_count": len(sub_results),
                    "error_count": len(sub_errors),
                })

            if sub_has_error and not sub_results:
                return StateUpdate(
                    agent_name="dag_runner",
                    status="error",
                    error_message=(
                        f"Subgraph '{parent_id}' échoué : "
                        + "; ".join(sub_errors)
                    ),
                    metadata={"subgraph": parent_id, "errors": sub_errors},
                )

            aggregated = "\n\n".join(sub_results)
            if sub_errors:
                aggregated += "\n\n--- ERREURS ---\n" + "\n".join(sub_errors)

            return StateUpdate(
                agent_name="dag_runner",
                status="success" if not sub_has_error else "partial",
                result_data=aggregated,
                metadata={
                    "subgraph": parent_id,
                    "success_count": len(sub_results),
                    "error_count": len(sub_errors),
                },
            )
        finally:
            self._subgraph_depth -= 1
