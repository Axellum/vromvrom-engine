"""
core/healing.py — Module de Self-Healing (Auto-Correction) du tab5-engine.

Extrait de engine.py (Phase 1 Audit V5, Axe A1).
Gère la détection d'échec d'une tâche DAG et tente une correction automatique
via re-planification par le PlannerAgent + exécution des tâches correctives.

Historique :
- V5.0 : Logique inlinée dans engine.py (L442-L566)
- V5.5 : Extraction dans un module dédié (A1 Audit)
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from core.state import TaskPayload, ExecutionPhase
from core.errors import classify_error

if TYPE_CHECKING:
    from core.engine import Engine

logger = logging.getLogger(__name__)

# Nombre maximum de tentatives de self-healing par tâche
MAX_HEALING_RETRIES = 3


class HealingManager:
    """
    Gestionnaire de Self-Healing pour les tâches du DAG.
    
    Quand une tâche échoue, le HealingManager :
    1. Demande au Planner un plan correctif ciblé
    2. Exécute les tâches correctives par stage
    3. Remet la tâche originale en statut "pending" pour re-exécution
    
    Si la correction échoue après MAX_HEALING_RETRIES tentatives,
    la tâche est marquée définitivement en erreur.
    """

    def __init__(self, engine: "Engine"):
        self._engine = engine

    async def attempt_healing(
        self,
        task_id: str,
        task_payload: TaskPayload,
        failed_update,
        retry_count: int,
        stage_id: int,
        on_event=None,
    ) -> bool:
        """
        Tente de corriger une tâche échouée via le Planner.
        
        Args:
            task_id: Identifiant de la tâche échouée
            task_payload: Payload original de la tâche
            failed_update: StateUpdate contenant l'erreur
            retry_count: Numéro de la tentative courante (1-based)
            stage_id: Identifiant du stage dans le DAG
            on_event: Callback SSE pour l'IHM
            
        Returns:
            True si la correction a réussi, False sinon.
        """
        if retry_count > MAX_HEALING_RETRIES:
            logger.error(
                f"[SELF-HEALING] La tâche {task_id} a échoué définitivement "
                f"après {retry_count - 1} auto-corrections."
            )
            return False

        logger.warning(
            f"[SELF-HEALING] Échec tâche {task_id} "
            f"(Tentative {retry_count}/{MAX_HEALING_RETRIES}). Auto-correction locale..."
        )
        self._engine.state.current_phase = ExecutionPhase.HEALING

        planner_agent = self._engine.agents.get("planner")
        if not planner_agent:
            logger.error("Planner agent non disponible pour le Self-Healing local.")
            return False

        # Classification typée de l'erreur pour enrichir le prompt correctif
        err_msg = failed_update.error_message or str(failed_update.result_data)
        agent_error = classify_error(err_msg, source=f"task:{task_id}")
        err_category_info = f" [Catégorie: {agent_error.category.value}, Retriable: {agent_error.is_retriable}]"
        err_details = f"Tâche : '{task_payload.task_objective}' (ID: {task_id}) -> Erreur{err_category_info} : {err_msg}"

        if on_event:
            await on_event("healing_started", {
                "stage_id": stage_id,
                "retry_count": retry_count,
                "error_details": err_details,
                "error_category": agent_error.category.value,
                "is_retriable": agent_error.is_retriable,
            })

        rec_prompt = (
            f"ERREUR D'EXÉCUTION lors de la tâche '{task_payload.task_objective}' "
            f"(ID: {task_id}, Tentative {retry_count}/{MAX_HEALING_RETRIES}).\n"
            f"Catégorie d'erreur : {agent_error.category.value} ({'retriable' if agent_error.is_retriable else 'non-retriable'})\n"
            f"L'erreur suivante est survenue :\n{err_details}\n\n"
            f"Propose un plan d'action correctif sous forme d'étapes (JSON) pour résoudre cette erreur. "
            f"Le plan doit corriger localement le problème (ex: corriger un fichier, créer un dossier) "
            f"afin de permettre à la tâche d'origine d'être réexécutée."
        )

        rec_payload = TaskPayload(
            task_objective=rec_prompt,
            relevant_context=f"Session d'auto-correction locale pour la tâche {task_id}.",
            metadata={"session_id": self._engine.state.session_id, "is_healing": True},
        )

        # Invocation du Planner pour obtenir un plan correctif
        plan_update = await planner_agent.invoke(rec_payload)
        async with self._engine._history_lock:
            self._engine.state.history.append(plan_update)

        if plan_update.status == "error" or not plan_update.new_tasks:
            logger.error(
                f"[SELF-HEALING] Planner a échoué à générer un plan correctif pour {task_id}."
            )
            return False

        print(
            f"[SELF-HEALING] Exécution des tâches correctives "
            f"({len(plan_update.new_tasks)} tâches) pour {task_id}..."
        )

        # Exécution des tâches correctives regroupées par stage_id
        success = await self._execute_corrective_tasks(plan_update.new_tasks)

        if success:
            print(f"[SELF-HEALING] Remédiation réussie pour {task_id}.")
        return success

    async def _execute_corrective_tasks(self, tasks: list[TaskPayload]) -> bool:
        """
        Exécute les tâches correctives générées par le Planner, par stage.
        
        Les tâches d'un même stage sont exécutées en parallèle (asyncio.gather).
        Les stages sont exécutés séquentiellement.
        
        Returns:
            True si toutes les tâches correctives ont réussi.
        """
        # Regroupement par stage_id
        stages = {}
        for ht in tasks:
            s_id = ht.metadata.get("stage_id", 1)
            if s_id not in stages:
                stages[s_id] = []
            stages[s_id].append(ht)

        for s_id in sorted(stages.keys()):
            stage_tasks = stages[s_id]

            async def _run_single_corrective(ht_payload: TaskPayload):
                h_tname = ht_payload.metadata.get("target_agent", "executor")
                h_agent = self._engine.agents.get(h_tname)
                if not h_agent:
                    raise ValueError(f"Agent cible inconnu pour correction: '{h_tname}'")
                print(f"[SELF-HEALING] Exécution tâche corrective : {ht_payload.task_objective}")
                return await h_agent.invoke(ht_payload)

            results = await asyncio.gather(
                *(_run_single_corrective(ht) for ht in stage_tasks)
            )

            for h_upd in results:
                # Écriture thread-safe dans l'historique
                async with self._engine._history_lock:
                    self._engine.state.history.append(h_upd)
                if h_upd.status == "error":
                    logger.error(
                        f"[SELF-HEALING] Une tâche corrective a échoué : {h_upd.error_message}"
                    )
                    return False

        return True
