"""
services/approval_resume_service.py — Reprise d'un plan après approbation humaine (#T267).

C'est la seconde moitié du HITL non bloquant : `core/engine.py` suspend la session
et persiste le DAG, ce module le rejoue quand l'humain a tranché.

DEUX RÈGLES DE SÉCURITÉ, PAS DE CONFORT
1. Le DAG rejoué est **exactement** celui qui a été stocké. Il n'est jamais
   re-planifié : approuver le plan A et exécuter un plan B re-généré viderait
   l'approbation de son sens (et c'est d'autant plus vrai ici que le HITL ne se
   déclenche QUE sur les plans à risque — write_file, run_terminal, suppression).
2. La branche Git éphémère de la session suspendue n'est finalisée qu'ICI :
   fusionnée si la reprise réussit, annulée si le plan est rejeté. Tant que la
   décision n'est pas prise, elle reste en place — c'est le travail en attente.
"""

import logging
from typing import Any

from core import hitl_store

logger = logging.getLogger(__name__)


async def _nettoyer_mcp(mcp_bridge) -> None:
    """Ferme le bridge MCP sans jamais faire échouer la reprise pour autant."""
    try:
        await mcp_bridge.stop()
    except Exception as e:
        logger.debug(f"[REPRISE] Arrêt MCP ignoré : {e}")


def _finaliser_git(engine, git_branch: str | None, succes: bool, tasks_status: dict) -> None:
    """
    Finalise la branche éphémère laissée en place par la session suspendue.

    `_finalize_git` attend `has_error` (et non « succès ») et exige, quand
    `tasks_status` est fourni, que TOUTES les tâches soient en `success` pour
    fusionner — on lui passe donc les statuts réels, sans les maquiller.
    """
    if not git_branch:
        return
    try:
        from tools.git_safety import git_finalize_agent_branch
        engine._finalize_git(git_branch, not succes, tasks_status, git_finalize_agent_branch)
    except Exception as e:
        logger.error(f"[REPRISE] Finalisation Git impossible sur '{git_branch}' : {e}")


async def reprendre_apres_approbation(
    request_id: str, on_event_callback=None
) -> dict[str, Any]:
    """
    Rejoue le DAG approuvé d'une demande HITL.

    Doit être appelée APRÈS `hitl_store.marquer_decision(request_id, True)` :
    c'est cette transition (acceptée seulement depuis `pending`) qui garantit
    qu'un double clic sur « Approuver » ne relance pas deux fois le même plan.

    Returns:
        dict avec `status` ∈ {completed, error, introuvable, non_approuve, vide}.
    """
    demande = hitl_store.lire_demande(request_id)
    if not demande:
        logger.warning(f"[REPRISE] Demande {request_id} introuvable.")
        return {"status": "introuvable", "request_id": request_id}

    if demande["status"] != hitl_store.STATUT_APPROUVE:
        logger.warning(
            f"[REPRISE] Demande {request_id} en statut '{demande['status']}' — "
            f"seule une demande approuvée est rejouable."
        )
        return {"status": "non_approuve", "request_id": request_id, "statut": demande["status"]}

    taches_brutes = demande.get("dag_tasks") or []
    if not taches_brutes:
        # DAG vide ou illisible : ne rien exécuter, et le dire. Rejouer « rien »
        # en annonçant un succès serait le pire des retours.
        logger.error(f"[REPRISE] Demande {request_id} sans DAG exploitable — reprise abandonnée.")
        hitl_store.marquer_reprise(request_id, succes=False)
        return {"status": "vide", "request_id": request_id}

    from core.execution_budget import ExecutionBudget
    from core.llm_gateway import load_config
    from core.state import TaskPayload
    from services.pipeline_service import monter_moteur

    session_id = demande["session_id"]
    objectif = demande.get("objective") or ""

    # Reconstruction fidèle des tâches approuvées.
    taches = [TaskPayload(**t) for t in taches_brutes]

    logger.info(
        f"[REPRISE] [T267] Rejeu du DAG approuvé {request_id} : "
        f"{len(taches)} tâche(s), session {session_id}, branche {demande.get('git_branch') or '—'}."
    )

    config = load_config()
    engine, registry, mcp_bridge, _gateway = monter_moteur(session_id, objectif, config)
    if on_event_callback:
        engine.on_event = on_event_callback

    # [#T253] Restaurer le contrat d'acceptation dans l'historique du moteur.
    # `ReviewLoop._criteres_du_plan()` le cherche dans `state.history` — or ce
    # moteur vient d'être monté, son historique est VIDE : sans cette
    # restauration, le contrat posé par le Planner meurt avec la session
    # suspendue et n'est jamais évalué (mesuré en prod le 11/08 : un plan
    # approuvé, 2 critères émis, 0 vérifié). On reconstitue le porteur plutôt
    # que d'ajouter un chemin parallèle : la reprise doit ressembler au chemin
    # normal, pas le contourner.
    criteres = demande.get("criteres") or []
    if criteres:
        from core.acceptance_contract import CLE_CONTRAT
        from core.state import StateUpdate
        engine.state.history.append(StateUpdate(
            agent_name="planner",
            status="success",
            result_data="Plan approuvé par l'humain — contrat restauré pour la reprise.",
            metadata={CLE_CONTRAT: criteres},
        ))
        logger.info(
            f"[REPRISE] [T253] Contrat d'acceptation restauré : {len(criteres)} critère(s)."
        )
    else:
        logger.info("[REPRISE] [T253] Aucun contrat d'acceptation associé à cette demande.")

    tasks_status: dict = {}
    has_error = True
    try:
        await mcp_bridge.start(registry, user_prompt=objectif)
        budget = ExecutionBudget.from_config(session_id)
        # [#T253] `executer_dag_et_controles` et NON `execute_dag` : la reprise doit
        # passer par exactement les mêmes contrôles que le chemin normal — revue
        # post-DAG, contrat d'acceptation, enregistrement des skills. Appeler
        # `execute_dag` directement les sautait tous : mesuré le 11/08, un plan
        # approuvé par un humain fusionnait sans qu'aucun de ses 2 critères
        # d'acceptation ne soit évalué. C'est pourtant le plan qui en a le plus
        # besoin, puisque c'est celui qui touche à des fichiers.
        tasks_status, has_error = await engine.executer_dag_et_controles(
            dag_tasks=taches,
            objectif=objectif,
            max_session_tokens=budget.max_tokens,
            budget=budget,
        )
    except Exception as e:
        has_error = True
        logger.error(f"[REPRISE] Échec de l'exécution reprise {request_id} : {e}")
    finally:
        await _nettoyer_mcp(mcp_bridge)

    _finaliser_git(engine, demande.get("git_branch"), not has_error, tasks_status)
    hitl_store.marquer_reprise(request_id, succes=not has_error)

    logger.info(
        f"[REPRISE] [T267] Demande {request_id} rejouée — "
        f"{'échec' if has_error else 'succès'} ({len(tasks_status)} tâche(s))."
    )
    return {
        "status": "error" if has_error else "completed",
        "request_id": request_id,
        "session_id": session_id,
        "tasks_status": tasks_status,
    }


def annuler_apres_rejet(request_id: str) -> dict[str, Any]:
    """
    Nettoie la branche éphémère d'un plan rejeté.

    Sans cette étape, un rejet laisserait la branche de la session suspendue
    pendante indéfiniment — le dépôt de travail resterait dessus, et le
    déploiement suivant hériterait d'un état non validé.
    """
    demande = hitl_store.lire_demande(request_id)
    if not demande:
        return {"status": "introuvable", "request_id": request_id}

    git_branch = demande.get("git_branch")
    if not git_branch:
        return {"status": "rien_a_annuler", "request_id": request_id}

    try:
        from core.llm_gateway import load_config
        from services.pipeline_service import monter_moteur
        config = load_config()
        engine, _registry, _bridge, _gw = monter_moteur(demande["session_id"], "", config)
        # has_error=True → `git_finalize_agent_branch` annule au lieu de fusionner.
        _finaliser_git(engine, git_branch, False, {})
        logger.info(f"[REPRISE] Branche '{git_branch}' annulée après rejet de {request_id}.")
        return {"status": "annule", "request_id": request_id, "branche": git_branch}
    except Exception as e:
        logger.error(f"[REPRISE] Annulation impossible pour {request_id} : {e}")
        return {"status": "erreur", "request_id": request_id, "erreur": str(e)}
