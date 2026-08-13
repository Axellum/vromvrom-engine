import asyncio

from core.engine import Engine
from core.state import TaskPayload


async def main():
    print("Test d'injection de saturation...")
    engine = Engine(session_id="test_sat")

    # Simuler un historique avec une tâche réussie
    succ_task = TaskPayload(task_objective="Test", task_id="t1")
    succ_task.metadata = {"task_objective": "Test", "target_agent": "executor"}
    from core.state import StateUpdate
    update = StateUpdate(agent_name="executor", status="success", metadata=succ_task.metadata, result_data="Done")
    engine.state.history.append(update)

    # Injecter 10 tâches identiques
    tasks = []
    for i in range(10):
        tasks.append(TaskPayload(task_objective="Test", task_id=f"dup_{i}"))

    print(f"Injection de {len(tasks)} tâches dans le DAG...")

    # Run dag runner
    engine._dag_runner.running_tasks = set()
    # It takes tasks in _run_single_task.
    # Actually, execute_dag is the right entry point
    await engine._dag_runner.execute_dag(tasks, max_session_tokens=10000)
    print("DAG exécuté !")

if __name__ == "__main__":
    asyncio.run(main())
