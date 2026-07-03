"""
core/dag/swarm_dispatcher.py — Module de dispatch asynchrone pour Swarm.
"""

import logging
from core.state import StateUpdate

logger = logging.getLogger(__name__)


async def try_swarm_dispatch(
    session_id: str,
    task_id: str,
    task_payload,
    target_name: str,
    target_agent,
    on_event=None,
) -> StateUpdate:
    """
    Tente de dispatcher la tâche vers un worker Swarm distant.
    Si aucun worker disponible ou si la tâche est locale-only, exécute localement.

    Retourne le StateUpdate.
    """
    try:
        from core.worker_registry import get_worker_registry

        worker_registry = get_worker_registry()

        task_category = (
            task_payload.metadata.get("dominant_category")
            or target_name
            or "general"
        )

        worker = worker_registry.get_available_worker(task_category)

        if worker:
            logger.info(
                f"[SWARM] 🔀 Dispatch de '{task_id}' vers worker '{worker.name}' "
                f"({worker.host}:{worker.port})"
            )
            if on_event:
                await on_event("task_dispatched", {
                    "task_id": task_id,
                    "worker_name": worker.name,
                    "worker_host": f"{worker.host}:{worker.port}",
                })

            # Construire le payload HTTP pour le worker
            dispatch_payload = {
                "task_id": task_id,
                "task_objective": task_payload.task_objective,
                "relevant_context": (task_payload.relevant_context or "")[:10000],
                "model_tier": task_payload.metadata.get("model_tier", "leger"),
                "session_id": session_id,
            }

            result = await worker_registry.dispatch_task(
                worker, dispatch_payload
            )

            if result.get("status") == "success":
                logger.info(
                    f"[SWARM] ✅ Tâche '{task_id}' exécutée par worker '{worker.name}' "
                    f"en {result.get('metadata', {}).get('elapsed_seconds', '?')}s"
                )
                return StateUpdate(
                    agent_name=f"swarm:{worker.name}",
                    status="success",
                    result_data=result.get("result_data", ""),
                    metadata=result.get("metadata", {}),
                )
            else:
                logger.warning(
                    f"[SWARM] ⚠️ Worker '{worker.name}' a échoué pour '{task_id}' : "
                    f"{result.get('error_message', 'inconnu')}. Fallback local."
                )
                # Fallback : exécution locale
                return await target_agent.invoke(task_payload)
        else:
            # Pas de worker disponible → exécution locale
            return await target_agent.invoke(task_payload)

    except ImportError:
        # worker_registry pas installé → exécution locale silencieuse
        return await target_agent.invoke(task_payload)
    except Exception as swarm_err:
        logger.warning(
            f"[SWARM] Erreur Swarm pour '{task_id}' (non bloquant) : {swarm_err}. "
            f"Exécution locale."
        )
        return await target_agent.invoke(task_payload)
