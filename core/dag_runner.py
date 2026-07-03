"""
core/dag_runner.py — Exécuteur de DAG parallèle du tab5-engine.

Gère l'exécution des tâches planifiées en respectant les dépendances (topological order),
la parallélisation via asyncio (PriorityQueue réactive), le budget de tokens,
le self-healing via HealingManager, et le dispatch Swarm vers workers distants.

Historique :
- V5.0  : Logique inlinée dans engine.py (L276-L577)
- V5.5  : Extraction dans un module dédié (A1 Audit)
- V6    : Dispatch Swarm optionnel vers workers distants
- V9    : aiosqlite async, ordonnancement réactif PriorityQueue, compression contexte
- V10   : Mémoire cloisonnée hiérarchique (scoped_memory)
- V11   : Event Store, Elo scorer post-tâche
"""

import asyncio
import json
import logging
import time
from typing import Dict, TYPE_CHECKING

from core.state import TaskPayload, StateUpdate
from core.healing import HealingManager
from core.dag.context_compressor import ContextCompressor
from core.dag.swarm_dispatcher import try_swarm_dispatch
from core.dag.priority_queue import get_initial_ready_tasks, get_task_priority

if TYPE_CHECKING:
    from core.engine import Engine

logger = logging.getLogger(__name__)


class DAGRunner:
    """
    Exécute un DAG (Directed Acyclic Graph) de tâches en parallèle.
    
    Responsabilités :
    - Résoudre les dépendances entre tâches (topological order)
    - Lancer les tâches prêtes en parallèle (asyncio.create_task)
    - Vérifier le budget de tokens avant chaque lancement
    - Déléguer les échecs au HealingManager pour self-correction
    - Diffuser les événements SSE pour l'IHM (stage_started, task_completed, etc.)
    """

    def __init__(self, engine: "Engine"):
        self._engine = engine
        self._healer = HealingManager(engine)
        self._compressor = ContextCompressor(None)
        
        # Contexte de session courant pour le spawning dynamique (Phase 2)
        self._current_session_id = None
        self._current_queue = None
        self._current_tasks_by_id = None
        self._current_tasks_status = None
        self._current_tasks_retries = None
        self._current_running_jobs = None
        self._new_task_event = None

    async def execute_dag(
        self,
        tasks: list[TaskPayload],
        max_session_tokens: int,
        on_event=None,
        budget=None,
    ) -> tuple[dict, bool]:
        """
        Exécute le DAG de tâches et retourne l'état final.
        Utilise un ordonnancement réactif (PriorityQueue) et enregistre l'état dans la base de données unifiée.

        [P2-3.4] `budget` (ExecutionBudget) plafonne tokens/durée/coût sur toute la
        requête. Si absent, un budget tokens-seul est construit depuis
        `max_session_tokens` (rétro-compat, ex. appels internes subgraph).
        """
        from core.runtime_db import get_connection, get_async_connection

        if budget is None:
            from core.execution_budget import ExecutionBudget
            budget = ExecutionBudget(self._engine.state.session_id, max_tokens=max_session_tokens)

        session_id = self._engine.state.session_id
        tasks_by_id: Dict[str, TaskPayload] = {t.task_id: t for t in tasks if t.task_id}
        tasks_status: Dict[str, str] = {t_id: "pending" for t_id in tasks_by_id}
        tasks_retries: Dict[str, int] = {t_id: 0 for t_id in tasks_by_id}
        has_error = False

        # Sauvegarder le contexte précédent (pour le support des subgraphs récursifs)
        prev_session_id = self._current_session_id
        prev_queue = self._current_queue
        prev_tasks_by_id = self._current_tasks_by_id
        prev_tasks_status = self._current_tasks_status
        prev_tasks_retries = self._current_tasks_retries
        prev_running_jobs = self._current_running_jobs
        prev_new_task_event = self._new_task_event

        # Enregistrer le contexte de la session courante pour permettre le spawning dynamique (Phase 2)
        self._current_session_id = session_id
        self._current_tasks_by_id = tasks_by_id
        self._current_tasks_status = tasks_status
        self._current_tasks_retries = tasks_retries

        # 1. Enregistrer le graphe et initialiser les états en BDD
        # Écriture async via aiosqlite — n'bloque pas la boucle asyncio
        async with get_async_connection() as db:
            # Nettoyer l'ancien état s'il existe pour cette session/tâche (évite les duplications)
            for t_id in tasks_by_id:
                await db.execute(
                    "DELETE FROM dag_tasks WHERE session_id = ? AND task_id = ?",
                    (session_id, t_id)
                )
                await db.execute(
                    "DELETE FROM dag_edges WHERE session_id = ? AND (parent_task_id = ? OR child_task_id = ?)",
                    (session_id, t_id, t_id)
                )
            
            # Insérer les nouvelles tâches et dépendances
            for t in tasks:
                depends_on_str = json.dumps(t.depends_on or [])
                inputs_str = json.dumps({
                    "task_objective": t.task_objective,
                    "relevant_context": t.relevant_context,
                    "metadata": t.metadata
                })
                await db.execute(
                    """
                    INSERT INTO dag_tasks 
                    (task_id, session_id, status, inputs_json, depends_on_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (t.task_id, session_id, 'pending', inputs_str, depends_on_str)
                )
                
                # Insérer les arcs (edges) de dépendances dans la table dag_edges
                for parent in (t.depends_on or []):
                    if parent in tasks_by_id: # On ne crée d'arcs que vers des tâches de ce DAG
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO dag_edges
                            (session_id, parent_task_id, child_task_id)
                            VALUES (?, ?, ?)
                            """,
                            (session_id, parent, t.task_id)
                        )
            
            # Migrer les variables de la mémoire de travail globale existantes (Phase 3)
            if self._engine.state.working_memory:
                for key, val in self._engine.state.working_memory.items():
                    val_json = json.dumps(val)
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO scoped_memory
                        (session_id, scope_id, parent_scope_id, key, value_json)
                        VALUES (?, 'global', NULL, ?, ?)
                        """,
                        (session_id, key, val_json)
                    )
            await db.commit()

        # Grouper les tâches par stage_id pour les événements SSE
        stages = {}
        for t in tasks:
            s_id = t.metadata.get("stage_id", 1)
            if s_id not in stages:
                stages[s_id] = []
            stages[s_id].append(t)

        # Diffuser les événements stage_started
        if on_event:
            for s_id in sorted(stages.keys()):
                await on_event("stage_started", {
                    "stage_id": s_id,
                    "tasks": [
                        {"task_objective": t.task_objective, "metadata": t.metadata}
                        for t in stages[s_id]
                    ],
                })

        # Créer la PriorityQueue asynchrone
        queue = asyncio.PriorityQueue()
        self._current_queue = queue
        self._new_task_event = asyncio.Event()

        # Identifier les tâches initialement prêtes (sans parents)
        initial_ready_ids = get_initial_ready_tasks(session_id)

        for t_id in initial_ready_ids:
            payload = tasks_by_id[t_id]
            priority = get_task_priority(payload)
            await queue.put((priority, t_id))

        running_jobs: Dict[str, asyncio.Task] = {}
        self._current_running_jobs = running_jobs

        # Fonctions d'aide locales pour gérer la BDD et le déverrouillage réactif
        # Version async : n'bloque plus la boucle asyncio pendant les écritures DAG
        async def _update_task_status_db(t_id: str, status: str, worker_id: str = None, outputs_json: str = None, error_message: str = None, started_at: float = None, ended_at: float = None):
            """Met à jour le statut d'une tâche DAG en BDD de façon ASYNC (aiosqlite)."""
            fields = ["status = ?"]
            params = [status]
            if worker_id is not None:
                fields.append("worker_id = ?")
                params.append(worker_id)
            if outputs_json is not None:
                fields.append("outputs_json = ?")
                params.append(outputs_json)
            if error_message is not None:
                fields.append("error_message = ?")
                params.append(error_message)
            if started_at is not None:
                fields.append("started_at = ?")
                params.append(started_at)
            if ended_at is not None:
                fields.append("ended_at = ?")
                params.append(ended_at)
            params.extend([session_id, t_id])
            async with get_async_connection() as db:
                await db.execute(
                    f"UPDATE dag_tasks SET {', '.join(fields)} WHERE session_id = ? AND task_id = ?",
                    tuple(params)
                )
                await db.commit()

        def _get_newly_ready_children(parent_id: str) -> list[str]:
            # Trouve les enfants du parent_id dont toutes les dépendances sont maintenant à 'success'
            with get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT child_task_id 
                    FROM dag_edges 
                    WHERE session_id = ? AND parent_task_id = ?
                    """,
                    (session_id, parent_id)
                )
                children = [row[0] for row in cursor.fetchall()]
                
                ready_children = []
                for child_id in children:
                    # Vérifier si tous les parents de cet enfant sont à 'success'
                    cursor_parents = conn.execute(
                        """
                        SELECT COUNT(*) 
                        FROM dag_edges e
                        JOIN dag_tasks parent ON parent.session_id = e.session_id AND parent.task_id = e.parent_task_id
                        WHERE e.session_id = ? 
                          AND e.child_task_id = ?
                          AND parent.status != 'success'
                        """,
                        (session_id, child_id)
                    )
                    unresolved_count = cursor_parents.fetchone()[0]
                    if unresolved_count == 0:
                        ready_children.append(child_id)
                return ready_children

        # Boucle principale réactive
        while (not queue.empty() or running_jobs) and not has_error:
            # 1. Lancer toutes les tâches prêtes dans la queue
            while not queue.empty():
                try:
                    priority, t_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # [P2-3.4] Garde-fou budget global (tokens + durée + coût) avant chaque tâche
                violation = budget.check()
                if violation:
                    logger.warning(
                        f"[DAG] ⛔ BUDGET DÉPASSÉ pendant le DAG ({violation['reason']}) : "
                        f"{violation['value']} / {violation['limit']} {violation['metric']}. "
                        f"Arrêt des tâches restantes."
                    )
                    if on_event:
                        await on_event("budget_exceeded", budget.event_payload(violation, blocked=t_id))
                    # Mettre la tâche en blocked
                    await _update_task_status_db(
                        t_id, 'blocked',
                        error_message=f"Budget dépassé ({violation['reason']})"
                    )
                    tasks_status[t_id] = "blocked"
                    has_error = True
                    queue.task_done()
                    break

                # Passer le statut à 'running'
                tasks_status[t_id] = "running"
                await _update_task_status_db(t_id, 'running', started_at=time.time())

                task_payload = tasks_by_id[t_id]
                job = asyncio.create_task(
                    self._run_single_task(t_id, task_payload, on_event)
                )
                running_jobs[t_id] = job
                queue.task_done()

            if has_error:
                break

            # 2. S'il n'y a pas de jobs lancés et que la queue est vide, mais qu'il reste des tâches 'pending' : blocage !
            if not running_jobs:
                pending_count = sum(1 for s in tasks_status.values() if s == "pending")
                if pending_count > 0:
                    logger.error(
                        f"[DAG] Blocage détecté : {pending_count} tâche(s) "
                        f"restée(s) en attente sans dépendance résoluble."
                    )
                    has_error = True
                break

            # 3. Attendre la fin d'au moins un job ou l'injection de nouvelles tâches
            # [PERF-2] Remplacement du timeout par un asyncio.Event pour éviter la busy loop.
            # Cela permet de réveiller la boucle immédiatement lors d'injections à chaud (MapReduce / subgraph)
            # PENDANT qu'un job tourne, sans saturer le CPU.
            event_task = asyncio.create_task(self._new_task_event.wait())
            wait_tasks = list(running_jobs.values()) + [event_task]

            done, _ = await asyncio.wait(
                wait_tasks,
                return_when=asyncio.FIRST_COMPLETED
            )

            if event_task in done:
                self._new_task_event.clear()
            else:
                event_task.cancel()

            done_jobs = [t for t in done if t != event_task]

            for completed_job in done_jobs:
                try:
                    finished_id, finished_update = completed_job.result()
                    
                    # Sécurité : vérifier que finished_id est bien dans running_jobs
                    if finished_id in running_jobs:
                        del running_jobs[finished_id]
                    else:
                        continue

                    # T79 : _history_lock protège l'append — safe même en parallèle
                    # (asyncio mono-thread, pas de race ; dict[key]=val sur task_id unique serait
                    # atomique en CPython, mais le Lock est maintenu pour la sémantique d'ordre).
                    async with self._engine._history_lock:
                        self._engine.state.history.append(finished_update)

                    stage_id = finished_update.metadata.get("stage_id", 1)

                    if finished_update.status == "success":
                        tasks_status[finished_id] = "success"
                        
                        # Mettre à jour en BDD
                        outputs_str = json.dumps(finished_update.result_data)
                        await _update_task_status_db(
                            finished_id, 'success',
                            worker_id=finished_update.agent_name,
                            outputs_json=outputs_str,
                            ended_at=time.time()
                        )

                        if on_event:
                            await on_event("task_completed", {
                                "stage_id": stage_id,
                                "task_objective": finished_update.metadata.get("task_objective", ""),
                                "target_agent": finished_update.metadata.get("target_agent", ""),
                                "status": "success",
                                "result_data": finished_update.result_data,
                                "error_message": None,
                            })

                        # Déverrouiller de manière réactive les tâches descendantes
                        newly_ready_ids = _get_newly_ready_children(finished_id)
                        for child_id in newly_ready_ids:
                            if tasks_status.get(child_id) == "pending":
                                child_payload = tasks_by_id[child_id]
                                pr = child_payload.metadata.get("stage_id", 1)
                                await queue.put((pr, child_id))
                                self._new_task_event.set()

                    else:
                        # Tâche en échec → Self-Healing
                        tasks_retries[finished_id] += 1
                        curr_retry = tasks_retries[finished_id]

                        healing_ok = await self._healer.attempt_healing(
                            task_id=finished_id,
                            task_payload=tasks_by_id[finished_id],
                            failed_update=finished_update,
                            retry_count=curr_retry,
                            stage_id=stage_id,
                            on_event=on_event,
                        )

                        if healing_ok:
                            # Le healing a fonctionné : on réinitialise l'état et on la réinsère dans la queue
                            tasks_status[finished_id] = "pending"
                            await _update_task_status_db(finished_id, 'pending')
                            
                            pr = tasks_by_id[finished_id].metadata.get("stage_id", 1)
                            await queue.put((pr, finished_id))
                            self._new_task_event.set()

                            if on_event:
                                await on_event("healing_completed", {
                                    "stage_id": stage_id,
                                    "status": "success",
                                })
                        else:
                            # Échec définitif
                            tasks_status[finished_id] = "error"
                            await _update_task_status_db(
                                finished_id, 'error',
                                error_message=finished_update.error_message,
                                ended_at=time.time()
                            )
                            has_error = True

                            if on_event:
                                await on_event("healing_completed", {
                                    "stage_id": stage_id,
                                    "status": "error",
                                })
                                await on_event("task_completed", {
                                    "stage_id": stage_id,
                                    "task_objective": finished_update.metadata.get("task_objective", ""),
                                    "target_agent": finished_update.metadata.get("target_agent", ""),
                                    "status": "error",
                                    "result_data": finished_update.result_data,
                                    "error_message": finished_update.error_message,
                                })
                            break

                except Exception as task_exc:
                    logger.error(
                        f"[DAG] Exception lors du traitement d'une tâche terminée : {task_exc}"
                    )
                    has_error = True
                    break

        # Nettoyage : annuler les jobs restants en cas d'erreur
        if running_jobs:
            for job in running_jobs.values():
                job.cancel()
            await asyncio.gather(*running_jobs.values(), return_exceptions=True)

        # Restaurer le contexte précédent (nettoyage ou remontée de récursion)
        self._current_session_id = prev_session_id
        self._current_queue = prev_queue
        self._current_tasks_by_id = prev_tasks_by_id
        self._current_tasks_status = prev_tasks_status
        self._current_tasks_retries = prev_tasks_retries
        self._current_running_jobs = prev_running_jobs
        self._new_task_event = prev_new_task_event

        return tasks_status, has_error

    async def _run_single_task(
        self, task_id: str, task_payload: TaskPayload, on_event=None
    ) -> tuple[str, StateUpdate]:
        """
        Exécute une tâche unique du DAG : résout l'agent cible,
        agrège le contexte des dépendances, et invoque l'agent.
        """
        target_name = task_payload.metadata.get("target_agent", "executor")
        target_agent = self._engine.agents.get(target_name)
        if not target_agent:
            raise ValueError(f"Agent cible inconnu: '{target_name}'")

        # Agrégation du contexte des dépendances directes résolues
        # Compression intelligente : les résultats volumineux (ex: 695K chars
        # de code lu) sont compressés AVANT d'être injectés comme contexte,
        # pour éviter l'erreur 413 Entity Too Large des APIs LLM.
        parent_summaries = []
        for dep in (task_payload.depends_on or []):
            parent_update = next(
                (
                    h for h in reversed(self._engine.state.history)
                    if h.metadata and h.metadata.get("task_id") == dep
                    and h.status == "success"
                ),
                None,
            )
            if parent_update and parent_update.result_data:
                raw = str(parent_update.result_data)
                compressed = self._compress_context(raw, dep)
                parent_summaries.append(f"Résultat tâche parent '{dep}' : {compressed}")

        if parent_summaries:
            dep_context = (
                "\n\n--- CONTEXTE DES DÉPENDANCES RÉSOLUES ---\n"
                + "\n".join(parent_summaries)
            )
            task_payload.relevant_context = (
                (task_payload.relevant_context or "") + "\n\n" + dep_context
            ).strip()

        # Injection de la mémoire cloisonnée hiérarchique (Phase 3)
        from core.runtime_db import get_all_scoped_vars, get_connection
        session_id = self._engine.state.session_id
        scope_id = task_payload.metadata.get("scope_id", "global")
        is_strict = (task_payload.metadata.get("scope_level") == 3)

        if is_strict:
            # Uniquement le scope immédiat
            scoped_vars = {}
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT key, value_json FROM scoped_memory WHERE session_id = ? AND scope_id = ?",
                    (session_id, scope_id)
                )
                for row in cursor.fetchall():
                    key, val_json = row
                    try:
                        scoped_vars[key] = json.loads(val_json) if val_json else None
                    except Exception:
                        scoped_vars[key] = val_json
                        
            # Récupérer et compacter le scope global
            global_vars = {}
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT key, value_json FROM scoped_memory WHERE session_id = ? AND scope_id = 'global'",
                    (session_id,)
                )
                for row in cursor.fetchall():
                    key, val_json = row
                    try:
                        global_vars[key] = json.loads(val_json) if val_json else None
                    except Exception:
                        global_vars[key] = val_json
            
            compacted_global = {}
            for k, v in global_vars.items():
                compacted_global[k] = f"[{type(v).__name__} (longueur: {len(str(v))})]"
        else:
            scoped_vars = get_all_scoped_vars(session_id, scope_id)
            compacted_global = None

        if scoped_vars:
            scoped_items = []
            for key, value in scoped_vars.items():
                if key != "__scope_metadata__":
                    scoped_items.append(f"  - {key}: {str(value)[:500]}")
            if scoped_items:
                scoped_context = (
                    f"\n\n--- MÉMOIRE DE TRAVAIL CLOISONNÉE (Scope: {scope_id}) ---\n"
                    + "\n".join(scoped_items)
                )
                task_payload.relevant_context = (
                    (task_payload.relevant_context or "") + scoped_context
                ).strip()

        if compacted_global:
            global_items = []
            for key, value in compacted_global.items():
                global_items.append(f"  - {key}: {value}")
            if global_items:
                global_context = (
                    "\n\n--- CONTEXTE GLOBAL COMPRESSÉ (Cloisonnement Niveau 3) ---\n"
                    + "\n".join(global_items)
                )
                task_payload.relevant_context = (
                    (task_payload.relevant_context or "") + global_context
                ).strip()

        print(f"\n[DAG] -> Exécution de la tâche : {task_id} (Agent: {target_name.upper()})")
        print(f"[DAG] Objectif : {task_payload.task_objective}")

        # Idempotence / Déduplication
        async with self._engine._history_lock:
            identical_past_task = next(
                (
                    h for h in reversed(self._engine.state.history)
                    if h.metadata 
                    and h.metadata.get("task_objective") == task_payload.task_objective
                    and h.metadata.get("target_agent") == target_name
                    and h.status == "success"
                ),
                None
            )
        if identical_past_task:
            logger.info(f"[DAG] ♻️ Déduplication : La tâche identique '{task_id}' a déjà été exécutée avec succès.")
            t_upd = StateUpdate(
                agent_name=target_name,
                status="success",
                result_data=identical_past_task.result_data,
                metadata=identical_past_task.metadata.copy(),
            )
            t_upd.metadata["task_id"] = task_id
            t_upd.metadata["deduplicated"] = True
            if on_event:
                await on_event("task_started", {
                    "stage_id": task_payload.metadata.get("stage_id", 1),
                    "task_objective": task_payload.task_objective,
                    "target_agent": target_name,
                })
            return task_id, t_upd

        if on_event:
            await on_event("task_started", {
                "stage_id": task_payload.metadata.get("stage_id", 1),
                "task_objective": task_payload.task_objective,
                "target_agent": target_name,
            })

        # Invocation protégée — les erreurs HTTP (413, 429, timeout)
        # ne doivent pas crasher tout le DAG mais mettre la tâche en erreur individuelle.
        try:
            # Dispatch Swarm : tenter le déport vers un worker distant
            # Ajout du Circuit Breaker Asynchrone (Watchdog 120s)
            t_upd = await asyncio.wait_for(
                try_swarm_dispatch(
                    self._engine.state.session_id, task_id, task_payload, target_name, target_agent, on_event
                ),
                timeout=120.0
            )
        except asyncio.TimeoutError:
            logger.error(f"[DAG] ⏱️ Watchdog déclenché : Timeout de 120s dépassé pour la tâche '{task_id}' (Agent: '{target_name}')")
            t_upd = StateUpdate(
                agent_name=target_name,
                status="error",
                error_message="Timeout de 120s dépassé (Circuit Breaker Asynchrone)",
                result_data=None,
                metadata={},
            )
        except Exception as invoke_exc:
            logger.error(
                f"[DAG] Exception non-catchée lors de l'invocation de '{target_name}' "
                f"pour la tâche '{task_id}' : {invoke_exc}"
            )
            t_upd = StateUpdate(
                agent_name=target_name,
                status="error",
                error_message=f"Exception lors de l'invocation : {str(invoke_exc)[:500]}",
                result_data=None,
                metadata={},
            )

        # Validation YAML post-exécution si succès
        if t_upd.status == "success":
            yaml_err = await self._engine._validate_modified_yamls()
            if yaml_err:
                t_upd.status = "error"
                t_upd.error_message = yaml_err
                t_upd.result_data = f"Erreur de validation de configuration YAML : {yaml_err}"

        # Enrichir les metadata du StateUpdate
        if not t_upd.metadata:
            t_upd.metadata = {}
        t_upd.metadata["task_id"] = task_id
        t_upd.metadata["task_objective"] = task_payload.task_objective
        t_upd.metadata["stage_id"] = task_payload.metadata.get("stage_id", 1)
        t_upd.metadata["target_agent"] = target_name

        # Mise à jour du score Elo après chaque tâche DAG
        # Le domaine et le modèle utilisé sont extraits des metadata du payload
        try:
            from core.elo_scorer import update_elo
            elo_domain = task_payload.metadata.get("dominant_category") or "general"
            # Le modèle utilisé est enregistré par le FallbackProvider dans le tracker,
            # on utilise le tier comme proxy si le modèle exact n'est pas renseigné
            elo_model = (
                task_payload.metadata.get("model_used")
                or task_payload.metadata.get("model_tier")
                or target_name
            )
            elo_success = (t_upd.status == "success")
            # [#T60] Écriture SQLite sous verrou → déléguée à un thread pour
            # ne pas micro-geler l'event loop sur le hot-path du DAG.
            await asyncio.to_thread(update_elo, elo_model, elo_domain, elo_success)
        except Exception as _elo_err:
            logger.debug(f"[DAG] [ELO] Mise à jour Elo échouée (non bloquant) : {_elo_err}")

        return task_id, t_upd

    def _compress_context(self, raw_data: str, dep_id: str) -> str:
        """
        Compresse le contexte en déléguant au ContextCompressor.
        """
        self._compressor.context_manager = self._engine.context_manager
        return self._compressor.compress_context(raw_data, dep_id)

    # ──────────────────────────────────────────────────────────────────
    # MapReduce Node — Fan-out / Fan-in pour tâches parallèles
    # ──────────────────────────────────────────────────────────────────

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
        from core.state import TaskPayload, StateUpdate
        from core.runtime_db import get_connection

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

        logger.info(
            f"[DAG] 🗺️ MapReduce dynamique démarré pour {parent_id} : {len(chunks)} chunks → agent '{target_name}'"
        )

        if on_event:
            await on_event("mapreduce_started", {
                "task_id": parent_id,
                "chunks_count": len(chunks),
                "target_agent": target_name,
            })

        # 1. Générer les TaskPayload pour les tâches Map et la tâche Reduce
        map_payloads = []
        map_ids = []
        for i, chunk in enumerate(chunks):
            map_id = f"{map_task_id_prefix}_map_{i}"
            map_ids.append(map_id)
            
            chunk_payload = TaskPayload(
                task_objective=(
                    f"[MAP {i+1}/{len(chunks)}] {task_payload.task_objective}\n\n"
                    f"--- CHUNK {i+1} ---\n{chunk}"
                ),
                relevant_context=task_payload.relevant_context,
                metadata={
                    **task_payload.metadata,
                    "map_index": i,
                    "map_total": len(chunks),
                    "is_map_chunk": True,
                    "scope_id": map_id,
                    "parent_scope_id": parent_id,
                    "scope_level": task_payload.metadata.get("scope_level", 1),
                },
                task_id=map_id,
            )
            map_payloads.append(chunk_payload)

        # Créer la tâche Reduce
        reduce_id = f"{map_task_id_prefix}_reduce"
        reduce_payload = TaskPayload(
            task_objective=(
                f"[REDUCE] {reduce_prompt or 'Fusionner les résultats précédents'}\n\n"
                f"Attente de {len(chunks)} tâches Map."
            ),
            relevant_context=task_payload.relevant_context,
            metadata={
                **task_payload.metadata,
                "is_reduce": True,
                "map_count": len(chunks),
                "scope_id": reduce_id,
                "parent_scope_id": parent_id,
                "scope_level": task_payload.metadata.get("scope_level", 1),
            },
            task_id=reduce_id,
            depends_on=map_ids,
        )

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
        map_tasks = []
        for i, chunk in enumerate(chunks):
            chunk_payload = TaskPayload(
                task_objective=(
                    f"[MAP {i+1}/{len(chunks)}] {task_payload.task_objective}\n\n"
                    f"--- CHUNK {i+1} ---\n{chunk}"
                ),
                relevant_context=task_payload.relevant_context,
                metadata={
                    **task_payload.metadata,
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
                    **task_payload.metadata,
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

        logger.info(
            f"[DAG] 🔀 Subgraph '{parent_id}' démarré : "
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
