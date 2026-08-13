"""
core/dag/map_reduce.py — Bloc MapReduce du DAGRunner.

Extrait de core/dag_runner.py (refactor #T303) sans changement de comportement :
- _construire_payloads_mapreduce
- execute_map_reduce
- _execute_map_reduce_fallback

`MapReduceMixin` fournit ces méthodes à `DAGRunner` par héritage ; les corps
sont strictement identiques à l'original (déplacement pur, vérifié par
comparaison AST — voir le corps de la PR #T303).
"""

import asyncio
import json
import logging

from core.dag.node_tier_policy import appliquer_tier_role
from core.state import StateUpdate, TaskPayload

logger = logging.getLogger(__name__)


class MapReduceMixin:
    """
    Méthodes MapReduce (fan-out / fan-in) du DAGRunner.

    Mixin interne, non instanciable seul : il dépend des attributs d'instance
    posés par `DAGRunner.__init__` (`_engine`, `_current_session_id`,
    `_current_queue`, `_current_tasks_by_id`, `_current_tasks_status`,
    `_current_tasks_retries`, `_new_task_event`).
    """

    # ──────────────────────────────────────────────────────────────────
    # MapReduce Node — Fan-out / Fan-in pour tâches parallèles
    # ──────────────────────────────────────────────────────────────────

    def _construire_payloads_mapreduce(
        self,
        task_payload: TaskPayload,
        chunks: list[str],
        reduce_prompt: str,
        map_task_id_prefix: str,
        parent_id: str,
    ) -> tuple[list[TaskPayload], TaskPayload]:
        """
        Construit les payloads Map (un par chunk) et le payload Reduce du DAG.

        [#T245] Le tier de modèle n'est plus hérité tel quel du parent : chaque
        moitié reçoit le tier de son rôle (maps en léger/gratuit, reduce sur le
        tier fort) via `core/dag/node_tier_policy.py`.
        """
        scope_level = task_payload.metadata.get("scope_level", 1)
        meta_map = appliquer_tier_role("map", task_payload.metadata)
        meta_reduce = appliquer_tier_role("reduce", task_payload.metadata)

        map_payloads = []
        for i, chunk in enumerate(chunks):
            map_id = f"{map_task_id_prefix}_map_{i}"
            map_payloads.append(TaskPayload(
                task_objective=(
                    f"[MAP {i+1}/{len(chunks)}] {task_payload.task_objective}\n\n"
                    f"--- CHUNK {i+1} ---\n{chunk}"
                ),
                relevant_context=task_payload.relevant_context,
                metadata={
                    **meta_map,
                    "map_index": i,
                    "map_total": len(chunks),
                    "is_map_chunk": True,
                    "scope_id": map_id,
                    "parent_scope_id": parent_id,
                    "scope_level": scope_level,
                },
                task_id=map_id,
            ))

        reduce_id = f"{map_task_id_prefix}_reduce"
        reduce_payload = TaskPayload(
            task_objective=(
                f"[REDUCE] {reduce_prompt or 'Fusionner les résultats précédents'}\n\n"
                f"Attente de {len(chunks)} tâches Map."
            ),
            relevant_context=task_payload.relevant_context,
            metadata={
                **meta_reduce,
                "is_reduce": True,
                "map_count": len(chunks),
                "scope_id": reduce_id,
                "parent_scope_id": parent_id,
                "scope_level": scope_level,
            },
            task_id=reduce_id,
            depends_on=[p.task_id for p in map_payloads],
        )

        return map_payloads, reduce_payload

    async def execute_map_reduce(
        self,
        task_payload: TaskPayload,
        chunks: list[str],
        reduce_prompt: str = "",
        on_event=None,
    ) -> StateUpdate:
        """
        Exécute un pattern MapReduce réactif sur une liste de chunks de données.
        Les tâches Map et Reduce sont injectées dynamiquement dans le DAGRunner courant.
        """
        from core.runtime_db import get_connection
        from core.state import StateUpdate

        parent_id = task_payload.task_id or "mapreduce"
        target_name = task_payload.metadata.get("target_agent", "executor")
        map_task_id_prefix = f"mapreduce_{parent_id}"

        session_id = self._current_session_id
        if not session_id or not self._current_queue:
            # Fallback en mode synchrone (V7 original) si aucun DAGRunner n'est actif
            logger.warning(
                f"[DAG] execute_map_reduce appelé en dehors d'un DAG actif pour '{parent_id}'. "
                f"Exécution en mode synchrone dégradé."
            )
            return await self._execute_map_reduce_fallback(task_payload, chunks, reduce_prompt, on_event)

        # 1. Générer les TaskPayload pour les tâches Map et la tâche Reduce
        map_payloads, reduce_payload = self._construire_payloads_mapreduce(
            task_payload, chunks, reduce_prompt, map_task_id_prefix, parent_id
        )
        map_ids = [p.task_id for p in map_payloads]
        reduce_id = reduce_payload.task_id
        tier_map = map_payloads[0].metadata.get("model_tier") if map_payloads else None
        tier_reduce = reduce_payload.metadata.get("model_tier")

        logger.info(
            f"[DAG] 🗺️ MapReduce dynamique démarré pour {parent_id} : {len(chunks)} chunks → agent '{target_name}' "
            f"(tier maps '{tier_map}' → tier reduce '{tier_reduce}')"
        )

        if on_event:
            await on_event("mapreduce_started", {
                "task_id": parent_id,
                "chunks_count": len(chunks),
                "target_agent": target_name,
                "map_tier": tier_map,
                "reduce_tier": tier_reduce,
            })

        # 2. Enregistrer toutes les tâches et les dépendances en BDD unifiée
        with get_connection() as conn:
            # Enregistrer les tâches Map
            for map_p in map_payloads:
                inputs_str = json.dumps({
                    "task_objective": map_p.task_objective,
                    "relevant_context": map_p.relevant_context,
                    "metadata": map_p.metadata
                })
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dag_tasks 
                    (task_id, session_id, status, inputs_json, depends_on_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (map_p.task_id, session_id, 'pending', inputs_str, json.dumps([]))
                )

            # Enregistrer la tâche Reduce
            reduce_inputs_str = json.dumps({
                "task_objective": reduce_payload.task_objective,
                "relevant_context": reduce_payload.relevant_context,
                "metadata": reduce_payload.metadata
            })
            conn.execute(
                """
                INSERT OR REPLACE INTO dag_tasks 
                (task_id, session_id, status, inputs_json, depends_on_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (reduce_payload.task_id, session_id, 'pending', reduce_inputs_str, json.dumps(map_ids))
            )

            # Enregistrer les arcs dans dag_edges
            for map_id in map_ids:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dag_edges
                    (session_id, parent_task_id, child_task_id)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, map_id, reduce_id)
                )

            # Déclarer les relations de scopes en BDD pour la remontée sémantique (Phase 3)
            for map_id in map_ids:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO scoped_memory
                    (session_id, scope_id, parent_scope_id, key, value_json)
                    VALUES (?, ?, ?, '__scope_metadata__', ?)
                    """,
                    (session_id, map_id, parent_id, json.dumps({"active": True}))
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO scoped_memory
                (session_id, scope_id, parent_scope_id, key, value_json)
                VALUES (?, ?, ?, '__scope_metadata__', ?)
                """,
                (session_id, reduce_id, parent_id, json.dumps({"active": True}))
            )
            conn.commit()

        # 3. Injecter en mémoire dans le DAG Runner courant
        for map_p in map_payloads:
            self._current_tasks_by_id[map_p.task_id] = map_p
            self._current_tasks_status[map_p.task_id] = 'pending'
            self._current_tasks_retries[map_p.task_id] = 0

        self._current_tasks_by_id[reduce_payload.task_id] = reduce_payload
        self._current_tasks_status[reduce_payload.task_id] = 'pending'
        self._current_tasks_retries[reduce_payload.task_id] = 0

        # 4. Pousser les tâches Map dans la PriorityQueue
        priority = task_payload.metadata.get("stage_id", 1)
        for map_id in map_ids:
            await self._current_queue.put((priority, map_id))
            logger.info(f"[DAG] [MAPREDUCE] Tâche Map éphémère poussée dans la PriorityQueue : {map_id}")

        if self._new_task_event:
            self._new_task_event.set()

        # 5. Attente asynchrone non-bloquante du nœud Reduce
        logger.info(f"[DAG] [MAPREDUCE] En attente du nœud Reduce '{reduce_id}'...")
        while True:
            status = self._current_tasks_status.get(reduce_id)
            if status in ("success", "error", "blocked"):
                logger.info(f"[DAG] [MAPREDUCE] Nœud Reduce '{reduce_id}' complété avec statut '{status}'.")
                break
            await asyncio.sleep(0.05)

        # 6. Traiter le résultat de la tâche Reduce et retourner le StateUpdate final
        if status == "success":
            # Récupérer les résultats du Reduce depuis la base
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT outputs_json, worker_id FROM dag_tasks WHERE session_id = ? AND task_id = ?",
                    (session_id, reduce_id)
                ).fetchone()

            outputs_str, worker_id = row if row else (None, None)
            result_data = json.loads(outputs_str) if outputs_str else f"MapReduce {parent_id} complété."

            if on_event:
                await on_event("mapreduce_completed", {
                    "task_id": parent_id,
                    "status": "success",
                    "map_success": len(chunks),
                    "map_errors": 0,
                })

            return StateUpdate(
                agent_name=worker_id or target_name,
                status="success",
                result_data=result_data,
                metadata={"map_success": len(chunks), "map_errors": 0},
            )
        else:
            # Échec
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT error_message FROM dag_tasks WHERE session_id = ? AND task_id = ?",
                    (session_id, reduce_id)
                ).fetchone()
            err_msg = row[0] if row else "Échec de la tâche de réduction"

            if on_event:
                await on_event("mapreduce_completed", {
                    "task_id": parent_id,
                    "status": "error",
                    "map_success": 0,
                    "map_errors": len(chunks),
                })

            return StateUpdate(
                agent_name=target_name,
                status="error",
                error_message=err_msg,
                metadata={"map_success": 0, "map_errors": len(chunks)},
            )

    async def _execute_map_reduce_fallback(
        self,
        task_payload: TaskPayload,
        chunks: list[str],
        reduce_prompt: str = "",
        on_event=None,
    ) -> StateUpdate:
        """
        Fallback synchrone en cas d'appel orphelin de MapReduce en dehors d'un DAG actif.
        """
        from core.state import StateUpdate

        target_name = task_payload.metadata.get("target_agent", "executor")
        map_task_id_prefix = f"mapreduce_{task_payload.task_id}"

        logger.info(
            f"[DAG] 🗺️ MapReduce démarré (Fallback) : {len(chunks)} chunks → agent '{target_name}'"
        )

        if on_event:
            await on_event("mapreduce_started", {
                "task_id": task_payload.task_id,
                "chunks_count": len(chunks),
                "target_agent": target_name,
            })

        # ── Phase MAP ──
        # [#T245] Même politique de tier par rôle que le chemin DAG : les deux
        # chemins doivent produire les mêmes payloads, sinon le fallback
        # ré-introduit silencieusement l'héritage qu'on vient de retirer.
        meta_map = appliquer_tier_role("map", task_payload.metadata)
        map_tasks = []
        for i, chunk in enumerate(chunks):
            chunk_payload = TaskPayload(
                task_objective=(
                    f"[MAP {i+1}/{len(chunks)}] {task_payload.task_objective}\n\n"
                    f"--- CHUNK {i+1} ---\n{chunk}"
                ),
                relevant_context=task_payload.relevant_context,
                metadata={
                    **meta_map,
                    "map_index": i,
                    "map_total": len(chunks),
                    "is_map_chunk": True,
                },
                task_id=f"{map_task_id_prefix}_map_{i}",
            )
            map_tasks.append(chunk_payload)

        async def _run_map_chunk(idx: int, payload: TaskPayload):
            target_agent = self._engine.agents.get(target_name)
            if not target_agent:
                return StateUpdate(
                    agent_name=target_name,
                    status="error",
                    error_message=f"Agent '{target_name}' introuvable",
                )
            return await target_agent.invoke(payload)

        map_results = await asyncio.gather(
            *[_run_map_chunk(i, t) for i, t in enumerate(map_tasks)],
            return_exceptions=True,
        )

        # Collecter les résultats Map réussis
        map_outputs = []
        map_errors = []
        for i, result in enumerate(map_results):
            if isinstance(result, Exception):
                map_errors.append(f"Chunk {i}: {str(result)}")
            elif result.status == "success":
                map_outputs.append(f"[Chunk {i+1}] {result.result_data}")
            else:
                map_errors.append(f"Chunk {i+1}: {result.error_message}")

        if on_event:
            await on_event("mapreduce_map_completed", {
                "task_id": task_payload.task_id,
                "success_count": len(map_outputs),
                "error_count": len(map_errors),
            })

        # ── Phase REDUCE ──
        if not map_outputs:
            return StateUpdate(
                agent_name=target_name,
                status="error",
                error_message=f"MapReduce échoué : aucun chunk réussi sur {len(chunks)}",
                metadata={"errors": map_errors},
            )

        reduce_context = "\n\n".join(map_outputs)

        if reduce_prompt:
            reduce_payload = TaskPayload(
                task_objective=(
                    f"[REDUCE] {reduce_prompt}\n\n"
                    f"Voici les résultats de {len(map_outputs)} traitements parallèles à agréger :\n\n"
                    f"{reduce_context}"
                ),
                relevant_context=task_payload.relevant_context,
                metadata={
                    **appliquer_tier_role("reduce", task_payload.metadata),
                    "is_reduce": True,
                    "map_count": len(map_outputs),
                },
                task_id=f"{map_task_id_prefix}_reduce",
            )

            target_agent = self._engine.agents.get(target_name)
            if target_agent:
                reduce_result = await target_agent.invoke(reduce_payload)

                if on_event:
                    await on_event("mapreduce_completed", {
                        "task_id": task_payload.task_id,
                        "status": reduce_result.status,
                        "map_success": len(map_outputs),
                        "map_errors": len(map_errors),
                    })

                return reduce_result

        aggregated = (
            f"=== RÉSULTATS AGRÉGÉS ({len(map_outputs)}/{len(chunks)} chunks réussis) ===\n\n"
            + reduce_context
        )

        if map_errors:
            aggregated += f"\n\n=== ERREURS ({len(map_errors)}) ===\n" + "\n".join(map_errors)

        if on_event:
            await on_event("mapreduce_completed", {
                "task_id": task_payload.task_id,
                "status": "success",
                "map_success": len(map_outputs),
                "map_errors": len(map_errors),
            })

        return StateUpdate(
            agent_name=target_name,
            status="success",
            result_data=aggregated,
            metadata={"map_success": len(map_outputs), "map_errors": len(map_errors)},
        )
