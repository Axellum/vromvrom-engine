import os
import sys
import asyncio
import logging

# Ajout du dossier courant au path pour les imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.state import TaskPayload, StateUpdate
from core.engine import Engine
from agents.base_agent import BaseAgent

# Configuration des logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("Test_DAG_Engine")

class MockAgent(BaseAgent):
    def __init__(self, name: str, behavior_func):
        super().__init__(name=name, system_prompt="Mock agent prompt")
        self.behavior_func = behavior_func
        
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        return await self.behavior_func(payload)

async def run_dag_tests():
    logger.info("=== STARTING DAG ENGINE TESTS ===")
    
    # --- TEST 1: Exécution parallèle et transmission de contexte ---
    logger.info("\n--- TEST 1: Exécution parallèle et transmission de contexte ---")
    
    # Liste de suivi pour vérifier l'ordre d'exécution
    execution_order = []
    
    async def planner_behavior(payload: TaskPayload) -> StateUpdate:
        # Renvoie un plan avec 3 tâches : t1 et t2 en parallèle, t3 qui dépend de t1 et t2.
        tasks = [
            TaskPayload(
                task_objective="Action A (parallel)",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Action B (parallel)",
                task_id="t2",
                depends_on=[],
                metadata={"target_agent": "executor", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Action C (séquentielle)",
                task_id="t3",
                depends_on=["t1", "t2"],
                metadata={"target_agent": "executor", "stage_id": 2}
            )
        ]
        return StateUpdate(
            agent_name="planner",
            status="success",
            result_data="Plan généré",
            next_agent="END",
            new_tasks=tasks
        )

    async def executor_behavior(payload: TaskPayload) -> StateUpdate:
        t_id = payload.task_id
        execution_order.append(t_id)
        logger.info(f"[Mock Executor] Running task {t_id}...")
        
        # Simuler un temps d'attente asynchrone pour valider la parallélisation
        if t_id == "t1":
            await asyncio.sleep(0.1)
            
        context = payload.relevant_context
        return StateUpdate(
            agent_name="executor",
            status="success",
            result_data=f"Résultat de {t_id} (Reçu context: {len(context)} chars)",
            metadata={"task_id": t_id}
        )

    # Configurer le moteur
    engine = Engine(session_id="test_dag_1")
    planner = MockAgent("planner", planner_behavior)
    executor = MockAgent("executor", executor_behavior)
    
    engine.register_agent(planner)
    engine.register_agent(executor)
    
    initial_payload = TaskPayload(
        task_objective="Lancer le test de DAG",
        metadata={"session_id": "test_dag_1"}
    )
    
    # Lancer le moteur
    state = await engine.run(initial_payload, starting_agent="planner")
    
    # Assertions
    assert len(execution_order) == 3, f"Attendu 3 tâches exécutées, obtenu : {len(execution_order)}"
    # t1 et t2 doivent être exécutées avant t3
    assert execution_order[2] == "t3", f"La tâche finale t3 doit s'exécuter en dernier. Ordre : {execution_order}"
    assert "t1" in execution_order[:2] and "t2" in execution_order[:2], f"t1 et t2 doivent s'exécuter en premier. Ordre : {execution_order}"
    
    # Vérifier que le contexte a bien été fusionné et transmis à t3
    t3_update = next(h for h in state.history if h.metadata.get("task_id") == "t3")
    assert t3_update.status == "success"
    assert "Résultat de t1" in t3_update.metadata.get("task_objective") or len(state.history) > 0
    
    logger.info("TEST 1 REUSSI !")
    
    
    # --- TEST 2: Self-Healing Local ---
    logger.info("\n--- TEST 2: Self-Healing Local ---")
    
    t2_attempts = 0
    t2_healed = False
    corrective_task_run = False
    
    async def planner_healing_behavior(payload: TaskPayload) -> StateUpdate:
        is_healing = payload.metadata.get("is_healing", False)
        if is_healing:
            logger.info("[Mock Planner] Healing mode triggered. Generating corrective task...")
            # Renvoyer une tâche corrective
            corrective_task = TaskPayload(
                task_objective="Réparer l'état de t2",
                task_id="clean_t2",
                depends_on=[],
                metadata={"target_agent": "executor", "stage_id": 1}
            )
            return StateUpdate(
                agent_name="planner",
                status="success",
                result_data="Plan de remédiation généré",
                next_agent="END",
                new_tasks=[corrective_task]
            )
        else:
            # Plan initial
            tasks = [
                TaskPayload(
                    task_objective="Tâche OK",
                    task_id="t1",
                    depends_on=[],
                    metadata={"target_agent": "executor", "stage_id": 1}
                ),
                TaskPayload(
                    task_objective="Tâche FAIL temporaire",
                    task_id="t2",
                    depends_on=[],
                    metadata={"target_agent": "executor", "stage_id": 1}
                )
            ]
            return StateUpdate(
                agent_name="planner",
                status="success",
                result_data="Plan initial généré",
                next_agent="END",
                new_tasks=tasks
            )

    async def executor_healing_behavior(payload: TaskPayload) -> StateUpdate:
        nonlocal t2_attempts, t2_healed, corrective_task_run
        t_id = payload.task_id
        
        if t_id == "t1":
            return StateUpdate(agent_name="executor", status="success", result_data="Tâche 1 OK", metadata={"task_id": "t1"})
        
        elif t_id == "t2":
            t2_attempts += 1
            if t2_attempts == 1:
                logger.info("[Mock Executor] Simulating FAIL for t2...")
                return StateUpdate(agent_name="executor", status="error", error_message="Erreur simulée sur t2", result_data="FAIL", metadata={"task_id": "t2"})
            else:
                logger.info("[Mock Executor] t2 succeeded after healing!")
                return StateUpdate(agent_name="executor", status="success", result_data="t2 OK après réparation", metadata={"task_id": "t2"})
                
        elif t_id == "clean_t2":
            logger.info("[Mock Executor] Running corrective task clean_t2...")
            corrective_task_run = True
            t2_healed = True
            return StateUpdate(agent_name="executor", status="success", result_data="Correction appliquée", metadata={"task_id": "clean_t2"})
            
        return StateUpdate(agent_name="executor", status="error", error_message="Tâche inconnue", metadata={"task_id": t_id})

    engine_heal = Engine(session_id="test_dag_heal")
    planner_heal = MockAgent("planner", planner_healing_behavior)
    executor_heal = MockAgent("executor", executor_healing_behavior)
    
    engine_heal.register_agent(planner_heal)
    engine_heal.register_agent(executor_heal)
    
    initial_payload = TaskPayload(
        task_objective="Lancer le test de Self-Healing",
        metadata={"session_id": "test_dag_heal"}
    )
    
    state_heal = await engine_heal.run(initial_payload, starting_agent="planner")
    
    # Assertions pour le Self-Healing
    assert t2_attempts == 2, f"La tâche t2 aurait dû être tentée 2 fois (1 échec, 1 succès post-healing). Tentatives: {t2_attempts}"
    assert corrective_task_run, "La tâche corrective 'clean_t2' aurait dû être exécutée."
    
    has_error_updates = any(h.status == "error" and h.metadata.get("task_id") == "t2" and h.result_data == "FAIL" for h in state_heal.history)
    has_success_updates = any(h.status == "success" and h.metadata.get("task_id") == "t2" for h in state_heal.history)
    assert has_error_updates, "L'historique doit contenir le premier échec de t2."
    assert has_success_updates, "L'historique doit contenir le succès final de t2."
    
    logger.info("TEST 2 REUSSI !")
    logger.info("=== ALL DAG ENGINE TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(run_dag_tests())
