"""
tools/dag_map_reduce.py — Outil `map_reduce` exposé aux agents (#T245).

`DAGRunner.execute_map_reduce()` existe depuis la V7 mais **n'avait aucun
appelant en production** (vérifié le 10/08 : 0 tâche `mapreduce` en base de
prod, seul `tests/test_dag_reactive.py` l'invoquait). Le fan-out le plus
rentable du moteur — N maps en tier gratuit, un reduce sur le tier fort — était
donc du code mort. Cet outil lui donne son déclencheur : un agent qui tient un
travail divisible (N fichiers, N entités, N pistes) le confie au DAG au lieu de
le traiter en série dans sa propre boucle ReAct.

Garde-fous, tous explicites (jamais de troncature silencieuse) :
- entre 2 et 12 chunks — en dessous le fan-out ne sert à rien, au-dessus l'agent
  doit regrouper lui-même ;
- **un seul MapReduce en vol à la fois** par moteur. Cela empêche la récursion
  (un chunk qui rappellerait l'outil) et, accessoirement, la famine du pool :
  une tâche parente qui attend ses enfants occupe un slot des 8 disponibles.
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

MIN_CHUNKS = 2
MAX_CHUNKS = 12

DESCRIPTION = (
    "Traite en PARALLÈLE une liste de fragments indépendants puis agrège les résultats "
    "(pattern MapReduce du DAG). À utiliser dès qu'un travail est divisible : plusieurs "
    "fichiers à analyser, plusieurs entités à interroger, plusieurs pistes à explorer. "
    f"Les maps tournent sur le tier léger (gratuit) et l'agrégation sur le tier fort. "
    f"Args: objectif (l'action à appliquer à CHAQUE fragment), chunks (liste de "
    f"{MIN_CHUNKS} à {MAX_CHUNKS} fragments), reduce_prompt (consigne d'agrégation, optionnel)."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "objectif": {
            "type": "string",
            "description": "L'action à appliquer à chaque fragment, formulée une seule fois.",
        },
        # Union array|string délibérée : la validation du registre s'exécute AVANT
        # la fonction, donc un modèle qui sérialise sa liste en chaîne JSON (cas
        # courant des petits modèles gratuits du tier léger) serait rejeté avant
        # d'atteindre la tolérance de _normaliser_chunks.
        "chunks": {
            "type": ["array", "string"],
            "items": {"type": "string"},
            "description": (
                f"Les fragments indépendants à traiter en parallèle ({MIN_CHUNKS} à {MAX_CHUNKS}) — "
                f"tableau JSON de chaînes."
            ),
        },
        "reduce_prompt": {
            "type": "string",
            "description": "Consigne d'agrégation des résultats (défaut : fusion simple).",
        },
    },
    "required": ["objectif", "chunks"],
}


def _normaliser_chunks(chunks) -> tuple[list[str] | None, str | None]:
    """Accepte une liste ou une chaîne JSON (les LLM envoient les deux)."""
    if isinstance(chunks, str):
        try:
            chunks = json.loads(chunks)
        except json.JSONDecodeError:
            return None, (
                "Erreur : 'chunks' doit être une liste de fragments (tableau JSON), "
                "pas une chaîne. Exemple : [\"fichier A\", \"fichier B\"]."
            )
    if not isinstance(chunks, list):
        return None, "Erreur : 'chunks' doit être une liste de fragments."

    fragments = [str(c) for c in chunks if str(c).strip()]
    if len(fragments) < MIN_CHUNKS:
        return None, (
            f"Erreur : {len(fragments)} fragment(s) fourni(s), il en faut au moins {MIN_CHUNKS} "
            f"pour que la parallélisation ait un intérêt. Traite ce cas directement."
        )
    if len(fragments) > MAX_CHUNKS:
        return None, (
            f"Erreur : {len(fragments)} fragments fournis, maximum {MAX_CHUNKS}. "
            f"Regroupe-les en {MAX_CHUNKS} lots au plus et rappelle l'outil."
        )
    return fragments, None


def register_map_reduce_tool(registry, engine) -> None:
    """
    Enregistre `map_reduce` sur le registre, lié au DAGRunner du moteur fourni.

    Même schéma d'injection de dépendance que `register_rag_tool` : seul
    l'appelant qui possède l'instance (ici le moteur) peut câbler l'outil.
    """

    async def map_reduce(objectif: str, chunks, reduce_prompt: str = "") -> str:
        from core.state import TaskPayload

        runner = getattr(engine, "_dag_runner", None)
        if runner is None:
            return "Erreur : aucun DAGRunner disponible sur ce moteur, MapReduce impossible."

        fragments, erreur = _normaliser_chunks(chunks)
        if erreur:
            return erreur

        # Un seul MapReduce en vol : anti-récursion et anti-famine du pool.
        if getattr(runner, "_mapreduce_tool_en_vol", 0) > 0:
            return (
                "Erreur : un MapReduce est déjà en cours d'exécution. Attends son résultat "
                "avant d'en lancer un autre, ou traite ces fragments directement."
            )
        runner._mapreduce_tool_en_vol = 1

        task_id = f"tool_mapreduce_{int(time.time() * 1000)}"
        payload = TaskPayload(
            task_objective=objectif,
            relevant_context="",
            task_id=task_id,
            metadata={
                "task_id": task_id,
                "target_agent": "executor",
                "stage_id": 1,
                "session_id": getattr(getattr(engine, "state", None), "session_id", None),
            },
        )

        logger.info(f"[MAP_REDUCE] Outil déclenché : {len(fragments)} fragments → {task_id}")
        try:
            resultat = await runner.execute_map_reduce(payload, fragments, reduce_prompt)
        except Exception as err:
            logger.error(f"[MAP_REDUCE] Échec de l'exécution : {err}")
            return f"Erreur pendant le MapReduce : {err}"
        finally:
            runner._mapreduce_tool_en_vol = 0

        if getattr(resultat, "status", None) != "success":
            return (
                f"MapReduce échoué : {getattr(resultat, 'error_message', 'raison inconnue')}"
            )
        return str(getattr(resultat, "result_data", "") or "")

    registry.register_mcp_tool("map_reduce", map_reduce, DESCRIPTION, SCHEMA)
    logger.info("[REGISTRY_SETUP] Outil map_reduce enregistré (fan-out DAG).")
