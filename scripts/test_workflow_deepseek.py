"""
Script d'exécution d'un WORKFLOW COMPLET 100% DeepSeek V4 Flash.
Variante de test_full_workflow.py : tous les agents (planner, executor,
antigravity, ha, reviewer) sont forcés sur le modèle littéral
`deepseek-v4-flash` — aucune cascade, coût mesurable pur ($0.14/$0.28/M).
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.env_bootstrap import bootstrap_env  # noqa: E402

bootstrap_env()

import services.pipeline_service as pipeline_service  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_output"
LOG_FILE = OUTPUT_DIR / "deepseek_workflow_execution.log"
RESULTS_FILE = OUTPUT_DIR / "deepseek_workflow_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("test_workflow_deepseek")


async def main():
    logger.info("=" * 70)
    logger.info("      LANCEMENT WORKFLOW 100% DEEPSEEK V4 FLASH")
    logger.info("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    objective = (
        "Réponds en UNE seule phrase française : quelle est la différence entre "
        "un modèle Flash et un modèle Pro chez DeepSeek ? Aucune action, aucune "
        "écriture : simple explication."
    )

    session_id = f"test_ds_wf_{int(time.time())}"
    logger.info(f"🎯 Objectif : '{objective}'")
    logger.info(f"🆔 Session ID : {session_id}")

    config = {
        "planner_model": "deepseek-v4-flash",
        "executor_model": "deepseek-v4-flash",
        "antigravity_model": "deepseek-v4-flash",
        "reviewer_model": "deepseek-v4-flash",
        "ha_model": "deepseek-v4-flash",
    }

    start_time = time.perf_counter()
    success = False
    error_msg = None
    output_summary = ""

    try:
        res = await pipeline_service.run_full_pipeline(
            user_prompt=objective,
            session_id=session_id,
            initial_payload={"task_objective": objective},
            starting_agent="planner",
            on_event_callback=None,
            config=config,
            timeout_seconds=300.0
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if isinstance(res, dict) and str(res.get("status", "")).lower() == "error":
            error_msg = str(res.get("error") or res)
            logger.error(f"❌ Le pipeline a retourné un statut d'erreur : {error_msg}")
        else:
            success = True
            output_summary = str(res)
            logger.info(f"✅ Workflow DeepSeek terminé en {elapsed_ms:.2f} ms")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"❌ Erreur lors du workflow DeepSeek: {error_msg}", exc_info=True)

    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "objective": objective,
        "success": success,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error_msg,
        "result_snippet": output_summary[:500] if output_summary else None
    }

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logger.info("=" * 70)
    logger.info(f"📊 Résultats sauvegardés dans : {RESULTS_FILE}")
    logger.info(f"📄 Log complet dans : {LOG_FILE}")
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
