"""
Script d'exécution d'un WORKFLOW COMPLET (Full Pipeline) sur le Moteur Multi-Agents.
Exécute un objectif d'analyse long-running (Planner -> DAG -> Execution -> Reviewer)
et capture l'intégralité du déroulé et des métriques.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# [T284] Chargement unique du .env via le point de chargement du coeur : un
# script lancé depuis n'importe quel répertoire dispose des mêmes clés.
from core.env_bootstrap import bootstrap_env  # noqa: E402

bootstrap_env()

import services.pipeline_service as pipeline_service  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_output"
LOG_FILE = OUTPUT_DIR / "full_workflow_execution.log"
RESULTS_FILE = OUTPUT_DIR / "full_workflow_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("test_full_workflow")


async def main():
    logger.info("=" * 70)
    logger.info("      LANCEMENT DU TEST DE WORKFLOW COMPLET (FULL PIPELINE)")
    logger.info("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    objective = (
        "Analyse le fichier core/llm_gateway.py et identifie les 3 principales "
        "méthodes de gestion de la résilience (circuit breaker, fallback, backoff). "
        "Rédige une synthèse dans scratch/audit_resilience_gateway.txt et vérifie "
        "que le fichier est créé avec du contenu."
    )

    session_id = f"test_full_wf_{int(time.time())}"
    logger.info(f"🎯 Objectif : '{objective}'")
    logger.info(f"🆔 Session ID : {session_id}")

    config = {
        "planner_model": "moyen",
        "executor_model": "moyen",
        "antigravity_model": "moyen",
        "reviewer_model": "moyen"
    }

    start_time = time.perf_counter()
    success = False
    error_msg = None
    output_summary = ""

    try:
        res = await pipeline_service.run_full_pipeline(
            user_prompt=objective,
            session_id=session_id,
            initial_payload={"objective": objective},
            starting_agent="planner",
            on_event_callback=None,
            config=config,
            timeout_seconds=300.0
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        # [T284] Un statut d'erreur dans la réponse est un ÉCHEC, même si
        # aucune exception n'a été levée : le 11/08, success=true a été écrit
        # avec result_snippet "{'status': 'error', 'error': ...}".
        if isinstance(res, dict) and str(res.get("status", "")).lower() == "error":
            error_msg = str(res.get("error") or res)
            logger.error(f"❌ Le pipeline a retourné un statut d'erreur : {error_msg}")
        else:
            success = True
            output_summary = str(res)
            logger.info(f"✅ Workflow terminé en {elapsed_ms:.2f} ms")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"❌ Erreur lors du workflow complet: {error_msg}", exc_info=True)

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
