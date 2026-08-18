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
import inspect
import json
import logging
import os
import time
from typing import TYPE_CHECKING

from core.dag.context_compressor import ContextCompressor
from core.dag.map_reduce import MapReduceMixin
from core.dag.priority_queue import get_initial_ready_tasks, get_task_priority
from core.dag.subgraph import SubgraphMixin
from core.dag.swarm_dispatcher import try_swarm_dispatch
from core.healing import HealingManager
from core.state import StateUpdate, TaskPayload

if TYPE_CHECKING:
    from core.engine import Engine

logger = logging.getLogger(__name__)

# Plafond de fan-out par défaut si `max_parallel_dag_tasks` absent de config.json.
DEFAULT_MAX_PARALLEL_DAG_TASKS = 8

# Estimation du coût moyen (en tokens) d'une tâche DAG, utilisée pour dériver un
# plafond de concurrence à partir du budget restant si `avg_tokens_per_dag_task`
# absent de config.json.
DEFAULT_AVG_TOKENS_PER_DAG_TASK = 3000

# [#T295] Plafond GLOBAL sur la TAILLE TOTALE de relevant_context après toute agrégation
# 40 000 caractères ≈ 10 000 tokens — garantit que le contexte d'une tâche ne sature jamais le prompt LLM
TOTAL_CONTEXT_MAX_CHARS = 40_000

# Délai par défaut du watchdog asynchrone d'une tâche DAG, si absent de
# config.json et de l'environnement. Inchangé par rapport au comportement
# historique (120 s) pour ne surprendre personne : c'est la valeur qui
# s'appliquait en dur avant la mise en configuration (#T380).
DEFAULT_DAG_TASK_TIMEOUT_S = 120.0

# Nom de la clé dans config.json pour le délai de base (en secondes).
CONFIG_DAG_TASK_TIMEOUT_KEY = "dag_task_timeout_s"

# Nom de la clé dans config.json pour le délai par tier de modèle
# (dict `{"leger": 120, "moyen": 180, "fort": 300, ...}` en secondes).
CONFIG_DAG_TASK_TIMEOUT_BY_TIER_KEY = "dag_task_timeout_by_tier"

# Variable d'environnement surchargeant le délai de base.
ENV_DAG_TASK_TIMEOUT_S = "MOTEUR_DAG_TASK_TIMEOUT_S"

# Préfixe des variables d'environnement surchargeant le délai d'un tier donné.
# Ex. `MOTEUR_DAG_TASK_TIMEOUT_TIER_FORT_S=300` surcharge le tier "fort".
ENV_DAG_TASK_TIMEOUT_TIER_PREFIX = "MOTEUR_DAG_TASK_TIMEOUT_TIER_"

# Tiers de modèle reconnus (même vocabulaire que `config.json` → `tiers`).
_TIERS_RECONNUS = ("leger", "moyen", "fort", "automatique")


def _lire_flottant_config(
    config: dict, cle: str, defaut: float, prefixe_env: str | None = None
) -> float:
    """
    Lit une durée (en secondes) depuis config.json, puis depuis l'environnement.

    Précédence : variable d'environnement > config.json > `defaut`. Une valeur
    illisible (non numérique, négative) est ignorée avec un avertissement au
    lieu de faire planter le moteur : le watchdog reste actif avec le défaut.
    """
    valeur = None
    if prefixe_env:
        brut_env = os.environ.get(prefixe_env, "").strip()
        if brut_env:
            try:
                valeur = float(brut_env)
            except ValueError:
                logger.warning(
                    f"[DAG] ⚠️ {prefixe_env} illisible ({brut_env!r}) : valeur config/défaut retenue."
                )
    if valeur is None:
        brut_config = config.get(cle)
        if brut_config is not None:
            try:
                valeur = float(brut_config)
            except (TypeError, ValueError):
                logger.warning(
                    f"[DAG] ⚠️ config.json '{cle}' illisible ({brut_config!r}) : défaut retenu."
                )
    if valeur is None or valeur <= 0:
        return defaut
    return valeur


def _delai_watchdog_seconds(task_payload: "TaskPayload", config: dict | None = None) -> float:
    """
    Délai du watchdog asynchrone pour UNE tâche du DAG (en secondes).

    Le délai n'est plus un littéral unique : il dépend du tier de modèle de la
    tâche, pour ne pas tuer une lecture de sources + synthèse LLM de tier fort
    (ex. `explore_code`, `read_source_files`) qui dépasse 120 s sans rien avoir
    d'anormal, tout en gardant le watchdog serré sur les tâches triviales.

    Précédence :
    1. surcharge explicite de la tâche (`watchdog_timeout_s` dans les metadata) ;
    2. délai par tier : variable d'environnement `MOTEUR_DAG_TASK_TIMEOUT_TIER_<TIER>_S`,
       puis `dag_task_timeout_by_tier` de config.json ;
    3. délai de base : `MOTEUR_DAG_TASK_TIMEOUT_S`, puis `dag_task_timeout_s` de
       config.json ;
    4. défaut inchangé : 120 s.

    Le watchdog n'est JAMAIS désactivé ici : `timeout=None` transformerait un
    lot raté en lot éternel, ce qui est pire (garde-fou #T380).
    """
    if config is None:
        try:
            from core.llm_gateway import load_config
            config = load_config()
        except Exception:
            config = {}

    # 1. Surcharge explicite du payload (délai par tâche, pas par tier).
    surcharge = (task_payload.metadata or {}).get("watchdog_timeout_s")
    if surcharge is not None:
        try:
            valeur = float(surcharge)
            if valeur > 0:
                return valeur
        except (TypeError, ValueError):
            pass

    # 2. Délai par tier de modèle de la tâche.
    tier = (task_payload.metadata or {}).get("model_tier")
    if tier and str(tier).strip().lower() in _TIERS_RECONNUS:
        tier_cle = str(tier).strip().lower()
        # Variable d'environnement d'abord : `MOTEUR_DAG_TASK_TIMEOUT_TIER_FORT_S`.
        brut_env = os.environ.get(ENV_DAG_TASK_TIMEOUT_TIER_PREFIX + tier_cle.upper() + "_S", "").strip()
        if brut_env:
            try:
                valeur = float(brut_env)
                if valeur > 0:
                    return valeur
            except ValueError:
                logger.warning(
                    f"[DAG] ⚠️ {ENV_DAG_TASK_TIMEOUT_TIER_PREFIX}{tier_cle.upper()}_S "
                    f"illisible ({brut_env!r}) : défaut retenu."
                )
        # Puis config.json → `dag_task_timeout_by_tier`.
        par_tier = config.get(CONFIG_DAG_TASK_TIMEOUT_BY_TIER_KEY)
        if isinstance(par_tier, dict):
            valeur = par_tier.get(tier_cle)
            if valeur is not None:
                try:
                    flottant = float(valeur)
                    if flottant > 0:
                        return flottant
                except (TypeError, ValueError):
                    logger.warning(
                        f"[DAG] ⚠️ config.json '{CONFIG_DAG_TASK_TIMEOUT_BY_TIER_KEY}.{tier_cle}' "
                        f"illisible ({valeur!r}) : défaut retenu."
                    )

    # 3. Délai de base, puis 4. défaut.
    return _lire_flottant_config(
        config,
        CONFIG_DAG_TASK_TIMEOUT_KEY,
        DEFAULT_DAG_TASK_TIMEOUT_S,
        prefixe_env=ENV_DAG_TASK_TIMEOUT_S,
    )


class DAGRunner(MapReduceMixin, SubgraphMixin):
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

        # [#T111] Profondeur courante de récursion des subgraphs (anti-boucle)
        self._subgraph_depth = 0

    def _compute_concurrency_cap(self, budget) -> int:
        """
        [#T111] Plafonne le fan-out parallèle du DAG à
        `min(budget_restant/coût_moyen, débit_cumulé_clés_API, plafond_config)`.

        Évite qu'un stage à forte parallélisation (ex: MapReduce N chunks) ne
        vide le budget de tokens d'un coup ou ne dépasse le débit cumulé des
        clés API actives (ex: 5 clés Gemini free ≈ 75 RPM cumulé).
        """
        try:
            from core.llm_gateway import load_config
            config = load_config()
        except Exception:
            config = {}

        caps = [int(config.get("max_parallel_dag_tasks", DEFAULT_MAX_PARALLEL_DAG_TASKS) or DEFAULT_MAX_PARALLEL_DAG_TASKS)]

        # Cap par budget restant (tokens)
        remaining = budget.remaining_tokens() if budget is not None else None
        if remaining is not None:
            avg_tokens = int(config.get("avg_tokens_per_dag_task", DEFAULT_AVG_TOKENS_PER_DAG_TASK) or DEFAULT_AVG_TOKENS_PER_DAG_TASK)
            caps.append(max(1, remaining // max(avg_tokens, 1)))

        # Cap par débit cumulé des clés API actives (RPM)
        try:
            from core.models_db import get_all_api_keys
            total_rpm = sum(int(k.get("quota_rpm") or 0) for k in get_all_api_keys(hide_values=True))
            if total_rpm > 0:
                caps.append(total_rpm)
        except Exception:
            pass

        return max(1, min(caps))

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
        from core.runtime_db import get_async_connection

        if budget is None:
            from core.execution_budget import ExecutionBudget
            budget = ExecutionBudget(self._engine.state.session_id, max_tokens=max_session_tokens)

        session_id = self._engine.state.session_id
        tasks_by_id: dict[str, TaskPayload] = {t.task_id: t for t in tasks if t.task_id}
        tasks_status: dict[str, str] = {t_id: "pending" for t_id in tasks_by_id}
        tasks_retries: dict[str, int] = {t_id: 0 for t_id in tasks_by_id}
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

        running_jobs: dict[str, asyncio.Task] = {}
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

        async def _get_newly_ready_children(parent_id: str) -> list[str]:
            # Trouve les enfants du parent_id dont toutes les dépendances sont maintenant à 'success'
            # [T129] version async (aiosqlite) : évite de bloquer l'event loop FastAPI à chaque
            # résolution de nœud du DAG (avant : get_connection() synchrone dans le hot-path).
            # [T132] une seule requête groupée au lieu de 1 (liste des enfants) + N (un COUNT
            # par enfant trouvé) — un nœud avec 50 enfants faisait avant 51 aller-retours SQLite.
            async with get_async_connection() as db:
                cursor = await db.execute(
                    """
                    SELECT e.child_task_id
                    FROM dag_edges e
                    JOIN dag_tasks parent ON parent.session_id = e.session_id AND parent.task_id = e.parent_task_id
                    WHERE e.session_id = ?
                      AND e.child_task_id IN (
                          SELECT child_task_id FROM dag_edges WHERE session_id = ? AND parent_task_id = ?
                      )
                    GROUP BY e.child_task_id
                    HAVING SUM(CASE WHEN parent.status != 'success' THEN 1 ELSE 0 END) = 0
                    """,
                    (session_id, session_id, parent_id)
                )
                rows = cursor.fetchall()
                rows = await rows if inspect.isawaitable(rows) else rows
                return [row[0] for row in rows]

        # Boucle principale réactive
        while (not queue.empty() or running_jobs) and not has_error:
            # 1. Lancer les tâches prêtes dans la limite du plafond de concurrence
            # [#T111] Fan-out borné par budget restant + débit cumulé des clés API actives ;
            # les tâches non lancées cette passe restent en queue et repartiront dès
            # qu'un job se termine (running_jobs se libère).
            concurrency_cap = self._compute_concurrency_cap(budget)
            while not queue.empty() and len(running_jobs) < concurrency_cap:
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
                        newly_ready_ids = await _get_newly_ready_children(finished_id)
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

        # [#T295] Préservation et réinitialisation du contexte d'origine au rejeu (Self-Healing)
        # Garantit qu'un rejeu de Self-Healing repart du contexte d'origine sans ré-accumuler les tentatives précédentes.
        if "_original_relevant_context" not in task_payload.metadata:
            task_payload.metadata["_original_relevant_context"] = task_payload.relevant_context or ""
        else:
            task_payload.relevant_context = task_payload.metadata["_original_relevant_context"]

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

        # [#T295] Plafond GLOBAL sur la TAILLE TOTALE du contexte agrégé (40 000 chars ≈ 10K tokens)
        if task_payload.relevant_context and len(task_payload.relevant_context) > TOTAL_CONTEXT_MAX_CHARS:
            original_len = len(task_payload.relevant_context)
            logger.warning(
                f"[DAG] ⚠️ Plafond global de contexte dépassé pour la tâche '{task_id}' "
                f"({original_len:,} chars > {TOTAL_CONTEXT_MAX_CHARS:,} chars max). Troncature appliquée."
            )
            task_payload.relevant_context = (
                task_payload.relevant_context[:TOTAL_CONTEXT_MAX_CHARS]
                + "\n\n[... Troncature appliquée par le plafond global de contexte du DAG (40k chars max) ...]"
            )

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
        # [#T380] Le délai du watchdog est configurable (config.json + env) et peut
        # être allongé par tier de modèle : une lecture de sources + synthèse LLM de
        # tier fort dépasse 120 s sans rien avoir d'anormal (ex. `explore_code`,
        # `read_source_files`, perdus le 17/08 sur ce watchdog unique).
        timeout_s = _delai_watchdog_seconds(task_payload)
        t0 = time.monotonic()
        try:
            # Dispatch Swarm : tenter le déport vers un worker distant
            # Ajout du Circuit Breaker Asynchrone (Watchdog configurable)
            t_upd = await asyncio.wait_for(
                try_swarm_dispatch(
                    self._engine.state.session_id, task_id, task_payload, target_name, target_agent, on_event
                ),
                timeout=timeout_s
            )
        except TimeoutError:
            duree_reelle = time.monotonic() - t0
            logger.error(
                f"[DAG] ⏱️ Watchdog déclenché : délai de {timeout_s}s dépassé pour la tâche "
                f"'{task_id}' (Agent: '{target_name}') après {duree_reelle:.1f}s d'exécution."
            )
            t_upd = StateUpdate(
                agent_name=target_name,
                status="error",
                error_message=(
                    f"Timeout de {timeout_s}s dépassé (Circuit Breaker Asynchrone) "
                    f"— durée réelle {duree_reelle:.1f}s, agent '{target_name}'"
                ),
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

