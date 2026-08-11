"""
core/hitl_store.py — Store durable des demandes d'approbation humaine (#T267).

POURQUOI CE MODULE EXISTE
Avant #T267, une demande d'approbation ne vivait que dans la mémoire du
`HITLManager`, et l'entrée était dépilée dès que `request_approval()` rendait la
main. Or la session qui contenait l'attente était bornée à 120 s
(`services/pipeline_service.py`) alors que la fenêtre d'approbation valait 300 s
(`core/hitl.py`) : la session était tuée pendant que le HITL attendait encore.
Mesuré en production sur 24 h : 8 « Approbation demandée », **0 résolution** —
pas même l'auto-approbation prévue au timeout, parce que ce qui survenait était
une `CancelledError` externe et non le `TimeoutError` interne. Conséquence :
tout plan contenant une tâche à risque était structurellement inexécutable.

La décision de conception (Axel, 10/08) est le HITL **non bloquant** : le plan
se met en attente HORS de la fenêtre de session, et reprend à l'approbation.
Cela suppose que la demande — et surtout le DAG approuvé — survive à la session
qui l'a créée, donc qu'elle soit persistée. C'est le rôle de ce module.

RÈGLE DE SÉCURITÉ À NE PAS PERDRE
Le DAG est stocké sérialisé et rejoué **à l'identique** à la reprise. Il ne doit
jamais être re-planifié : approuver le plan A puis exécuter un plan B
re-généré viderait l'approbation humaine de son sens.
"""

import json
import logging
import time
from typing import Any

from core.runtime_db import get_connection

logger = logging.getLogger(__name__)

# Statuts possibles d'une demande. `pending` est le seul état depuis lequel une
# décision humaine est acceptée — c'est ce qui rend approve/reject idempotents.
STATUT_EN_ATTENTE = "pending"
STATUT_APPROUVE = "approved"
STATUT_REJETE = "rejected"
STATUT_REPRIS = "resumed"
STATUT_ECHEC = "failed"


def _ligne_en_dict(row: tuple, colonnes: list[str]) -> dict[str, Any]:
    """Convertit une ligne SQLite en dict, en désérialisant le DAG au passage."""
    d = dict(zip(colonnes, row, strict=False))
    try:
        d["dag_tasks"] = json.loads(d.pop("dag_tasks_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        # Une demande dont le DAG est illisible ne doit pas faire planter la
        # lecture de toute la file : elle est rendue avec un DAG vide, ce qui la
        # rend non reprenable (et visible comme telle) plutôt qu'invisible.
        logger.error(f"[HITL STORE] DAG illisible pour {d.get('request_id')} — demande non reprenable.")
        d["dag_tasks"] = []
    # [#T253] Le contrat d'acceptation suit le même chemin que le DAG : il doit
    # survivre à la session pour être évaluable à la reprise.
    try:
        d["criteres"] = json.loads(d.pop("criteres_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[HITL STORE] Contrat illisible pour {d.get('request_id')} — demande sans contrat.")
        d["criteres"] = []
    return d


_COLONNES = [
    "request_id", "session_id", "objective", "description", "plan_summary",
    "risk_level", "dag_tasks_json", "git_branch", "status", "feedback",
    "created_at", "decided_at", "resumed_at", "criteres_json",
]
# Requête écrite en dur (et non composée depuis _COLONNES) : une construction par
# f-string déclenche S608 chez ruff, et la liste sert déjà au mapping des lignes.
_SELECT = (
    "SELECT request_id, session_id, objective, description, plan_summary, "
    "risk_level, dag_tasks_json, git_branch, status, feedback, "
    "created_at, decided_at, resumed_at, criteres_json FROM hitl_pending_approvals"
)


def enregistrer_demande(
    request_id: str,
    session_id: str,
    dag_tasks: list[dict],
    objective: str = "",
    description: str = "",
    plan_summary: str = "",
    risk_level: str = "medium",
    git_branch: str | None = None,
    criteres: list | None = None,
) -> None:
    """
    Persiste une demande d'approbation et le DAG à rejouer.

    `dag_tasks` doit être une liste de dicts JSON-sérialisables (issue de
    `TaskPayload.model_dump(mode="json")`) : la fidélité prime, la reprise doit
    pouvoir reconstruire exactement les mêmes tâches.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hitl_pending_approvals
                (request_id, session_id, objective, description, plan_summary,
                 risk_level, dag_tasks_json, git_branch, status, created_at,
                 criteres_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id, session_id, objective, description, plan_summary,
                risk_level, json.dumps(dag_tasks, ensure_ascii=False),
                git_branch, STATUT_EN_ATTENTE, time.time(),
                json.dumps(criteres or [], ensure_ascii=False),
            ),
        )
        conn.commit()
    logger.info(
        f"[HITL STORE] Demande {request_id} persistée "
        f"({len(dag_tasks)} tâche(s), {len(criteres or [])} critère(s) d'acceptation, "
        f"risque={risk_level}, branche={git_branch or '—'})."
    )


def lire_demande(request_id: str) -> dict[str, Any] | None:
    """Retourne une demande (DAG désérialisé), ou None si elle n'existe pas."""
    with get_connection() as conn:
        row = conn.execute(f"{_SELECT} WHERE request_id = ?", (request_id,)).fetchone()
    return _ligne_en_dict(row, _COLONNES) if row else None


def lister_en_attente() -> list[dict[str, Any]]:
    """Retourne les demandes encore en attente de décision, plus anciennes d'abord."""
    with get_connection() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE status = ? ORDER BY created_at ASC", (STATUT_EN_ATTENTE,)
        ).fetchall()
    return [_ligne_en_dict(r, _COLONNES) for r in rows]


def marquer_decision(request_id: str, approuve: bool, feedback: str | None = None) -> bool:
    """
    Enregistre la décision humaine.

    La transition n'est acceptée QUE depuis `pending` : une demande déjà décidée
    (ou déjà reprise) ne peut pas être re-décidée. Sans cette garde, un double
    clic sur « Approuver » relancerait deux fois le même DAG à risque.

    Returns:
        True si la décision a été enregistrée, False si la demande est
        introuvable ou n'était plus en attente.
    """
    statut = STATUT_APPROUVE if approuve else STATUT_REJETE
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE hitl_pending_approvals
               SET status = ?, feedback = ?, decided_at = ?
             WHERE request_id = ? AND status = ?
            """,
            (statut, feedback, time.time(), request_id, STATUT_EN_ATTENTE),
        )
        conn.commit()
        modifiees = cur.rowcount
    if not modifiees:
        logger.warning(
            f"[HITL STORE] Décision refusée pour {request_id} : introuvable ou déjà traitée."
        )
        return False
    logger.info(f"[HITL STORE] Demande {request_id} → {statut}.")
    return True


def marquer_reprise(request_id: str, succes: bool) -> None:
    """Marque une demande approuvée comme rejouée (issue de l'exécution comprise)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE hitl_pending_approvals SET status = ?, resumed_at = ? WHERE request_id = ?",
            (STATUT_REPRIS if succes else STATUT_ECHEC, time.time(), request_id),
        )
        conn.commit()


def compter_par_statut() -> dict[str, int]:
    """Retourne le décompte des demandes par statut (observabilité)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM hitl_pending_approvals GROUP BY status"
        ).fetchall()
    return {statut: nombre for statut, nombre in rows}
