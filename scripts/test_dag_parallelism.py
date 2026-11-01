"""
T3 — Test parallélisme DAG à moindre frais.

Monte le moteur réel (monter_moteur), puis compare :
  A) 8 tâches indépendantes exécutées SÉQUENTIELLEMENT (agent executor réel)
  B) les mêmes 8 tâches via DAGRunner.execute_dag (fan-out parallèle)

Toutes les tâches sont triviales ("réponds en une phrase") et forcées sur
deepseek-v4-flash (config littérale) : coût mesurable ~$0.14/M, aucun outil.
Mesure : temps total, temps max par tâche, ratio parallèle/séquentiel,
plafond de concurrence effectif (max_parallel_dag_tasks).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.env_bootstrap import bootstrap_env  # noqa: E402

bootstrap_env()

import services.pipeline_service as pipeline_service  # noqa: E402
from core.state import TaskPayload

NB_TASKS = 8
OUT = Path(__file__).resolve().parent.parent / "test_output" / "dag_parallel_results.json"

OBJECTIVES = [
    "Réponds en une phrase en français : que fait un circuit breaker ?",
    "Réponds en une phrase en français : que fait un fallback provider ?",
    "Réponds en une phrase en français : que fait un backoff exponentiel ?",
    "Réponds en une phrase en français : qu'est-ce qu'un DAG ?",
    "Réponds en une phrase en français : qu'est-ce qu'un token ?",
    "Réponds en une phrase en français : qu'est-ce qu'un semaphore ?",
    "Réponds en une phrase en français : qu'est-ce qu'une file prioritaire ?",
    "Réponds en une phrase en français : qu'est-ce qu'un score Elo ?",
]


def make_tasks(session_id: str) -> list[TaskPayload]:
    return [
        TaskPayload(
            task_objective=obj,
            task_id=f"p{i}",
            depends_on=[],
            metadata={"target_agent": "executor", "session_id": session_id, "stage_id": 0},
        )
        for i, obj in enumerate(OBJECTIVES[:NB_TASKS])
    ]


async def run_sequential(engine) -> tuple[float, list[float]]:
    agent = engine.agents["executor"]
    durations = []
    for t in make_tasks("t3_seq"):
        t0 = time.perf_counter()
        update = await agent.invoke(t)
        durations.append((time.perf_counter() - t0) * 1000)
        status = update.status if update else "?"
        print(f"    [seq] {t.task_id} {status} {durations[-1]:.0f} ms")
    return sum(durations), durations


async def run_parallel(engine, config) -> tuple[float, dict, list[float]]:
    runner = engine._dag_runner
    cap = runner._compute_concurrency_cap(None)
    print(f"    Plafond de concurrence calculé : {cap} (max_parallel_dag_tasks config = {config.get('max_parallel_dag_tasks', 'défaut 8')})")
    tasks = make_tasks("t3_par")
    t0 = time.perf_counter()
    statuses, has_error = await runner.execute_dag(tasks, max_session_tokens=500_000)
    total = (time.perf_counter() - t0) * 1000
    print(f"    has_error={has_error}")
    return total, statuses, []


async def main():
    config = {
        "planner_model": "deepseek-v4-flash",
        "executor_model": "deepseek-v4-flash",
        "antigravity_model": "deepseek-v4-flash",
        "reviewer_model": "deepseek-v4-flash",
        "ha_model": "deepseek-v4-flash",
        "max_parallel_dag_tasks": 6,
    }
    print(f"Montage du moteur (executor = deepseek-v4-flash)...")
    engine, _registry, _bridge, _gateway = pipeline_service.monter_moteur(
        "t3_parallel_test", "Test parallélisme", config
    )

    print(f"\n=== A) SÉQUENTIEL ({NB_TASKS} tâches) ===")
    t_seq, _ = await run_sequential(engine)
    print(f"    Total séquentiel : {t_seq:.0f} ms")

    print(f"\n=== B) PARALLÈLE via DAGRunner ({NB_TASKS} tâches indépendantes) ===")
    t_par, statuses, _ = await run_parallel(engine, config)
    print(f"    Total parallèle   : {t_par:.0f} ms")

    ratio = t_seq / t_par if t_par > 0 else float("inf")
    print(f"\n    Ratio séquentiel/parallèle : {ratio:.2f}x")
    print(f"    Statuts : {statuses}")

    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nb_tasks": NB_TASKS,
        "model": "deepseek-v4-flash",
        "sequential_total_ms": round(t_seq, 2),
        "parallel_total_ms": round(t_par, 2),
        "speedup_ratio": round(ratio, 2),
        "task_statuses": statuses,
        "max_parallel_dag_tasks": config["max_parallel_dag_tasks"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n📊 Résultats : {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
