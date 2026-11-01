"""
Antigravity Engine  - Router d'API pour le Backlog d'Agents
Conçu pour une fiabilité maximale sous Windows, avec gestion transactionnelle des branches Git
et contrôle strict des budgets d'exécution.

Auteur: Équipe d'Ingénierie Domotique & IA DeepMind
"""

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.backlog_db import add_task, delete_task, get_all_tasks, get_task_by_id, get_task_stats, update_task_status
from core.budget_guard import BudgetGuard
from tools.git_safety import _run_git

# Configuration du logger
logger = logging.getLogger("antigravity.api.backlog")

# [P0-1.3] Charset strict pour les noms de branche Git. Le `(?!-)` interdit un
# tiret en tête (sinon le nom pourrait être interprété comme une option git).
# \Z (et non $) : $ matche aussi avant un \n terminal, ce qui laisserait passer
# un nom du type "branche\n" hors charset (cohérent avec core/validation.py).
_VALID_BRANCH_RE = re.compile(r"^(?!-)[\w./-]+\Z")

# Instanciation du routeur
router = APIRouter(prefix="/api/backlog", tags=["Backlog"])

# [#T396] Depuis #T327 le Deck POUSSE les branches de ses tâches sur origin, puis
# supprime la copie locale. La porte d'approbation, elle, était restée écrite pour
# un monde où la branche vivait en local : elle fusionnait/supprimait « la branche »
# sans jamais regarder si elle existait, et effaçait `git_branch` dans tous les cas.
# Résultat mesuré le 18/08 : cinq branches réelles sur origin (2,9 M tokens de
# travail, dont le TaskKanbanBoard de #T305) devenues introuvables — la base ne
# connaissait plus leur nom. Règle posée ici : **on n'efface jamais la référence
# d'une branche qui existe encore quelque part.**

# Un checkout/merge Git concurrent depuis deux PUT /tasks casse le working
# tree (PR #468 sans verrou). Toutes les ops Git de cette route passent ici.
_verrou_git_backlog = asyncio.Lock()


async def _git(args: list[str]) -> tuple:
    """`_run_git` hors de la boucle, et jamais en parallèle avec un autre Git."""
    async with _verrou_git_backlog:
        return await asyncio.to_thread(_run_git, args)


def _branche_locale_existe(branche: str) -> bool:
    """Vrai si la branche est présente dans le dépôt local."""
    code, _, _ = _run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branche}"])
    return code == 0


def _branche_distante_existe(branche: str) -> bool:
    """
    Vrai si la branche est présente sur origin.

    Un échec de la commande (pas de réseau, pas de remote) rend False : on ne
    peut alors PAS prouver que la branche a disparu, et l'appelant conserve la
    référence par prudence — mieux vaut une référence périmée qu'un travail perdu.
    """
    code, out, _ = _run_git(["ls-remote", "--heads", "origin", branche])
    return code == 0 and bool(out.strip())


# Modèles Pydantic
class TaskCreate(BaseModel):
    title: str = Field(..., description="Titre de la tâche")
    description: str = Field(..., description="Description détaillée de la tâche")
    priority: int = Field(2, description="Priorité de la tâche (1: Haute, 2: Moyenne, 3: Basse)")
    scheduled_at: float | None = Field(None, description="Timestamp de planification optionnel")
    project_id: str | None = Field(None, description="Projet cible : moteur, tab5, ecosystem")
    # [#T397] La contrainte d'unicité existait en base depuis le 21/08, mais
    # cette route ne la transmettait pas : une injection de lot passait donc
    # toujours avec `discovery_key = NULL`, sans aucune protection. Mesuré le
    # 24/08 : le lot de la semaine injecté deux fois → 13 paires de tâches
    # identiques, chacune payée deux fois.
    discovery_key: str | None = Field(
        None,
        description="Empreinte stable de la source ('lot:<f>#<t>', 'board:T396'). "
                    "Une source déjà injectée n'est pas réinsérée."
    )

class TaskUpdate(BaseModel):
    status: str | None = Field(None, description="Nouveau statut de la tâche")
    git_branch: str | None = Field(None, description="Branche Git associée")

@router.get("/tasks", response_model=list[dict[str, Any]])
async def get_tasks():
    """
    Récupère l'ensemble des tâches du backlog.
    """
    try:
        return await get_all_tasks()
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des tâches: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de récupérer les tâches."
        )

@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate):
    """
    Ajoute une nouvelle tâche au backlog.
    """
    try:
        task_id = await add_task(
            payload.title,
            payload.description,
            payload.priority,
            payload.scheduled_at,
            discovery_key=payload.discovery_key,
            project_id=payload.project_id,
        )
        return {"status": "success", "task_id": task_id}
    except Exception as e:
        logger.error(f"Erreur lors de la création de la tâche: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de créer la tâche."
        )

@router.put("/tasks/{id}")
async def update_task(id: int, payload: TaskUpdate):
    """
    Met à jour le statut d'une tâche et gère le cycle de vie Git associé (fusion/suppression).
    """
    task = await get_task_by_id(id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche avec l'ID {id} introuvable."
        )

    status_val = payload.status
    branch = task.get("git_branch")

    # [P0-1.3] Refuse tout nom de branche hors charset (défense en profondeur :
    # _run_git utilise déjà shell=False, mais on bloque aussi l'option-injection git).
    if branch and not _VALID_BRANCH_RE.match(branch):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nom de branche invalide : {branch!r}",
        )

    # État réel de la branche AVANT toute décision : local, distant, ou nulle part.
    # Interrogé seulement pour les deux verdicts qui touchent à Git — `ls-remote`
    # est un appel réseau, il n'a rien à faire sur un simple changement de statut.
    # [#T468+] checkout/merge sous le même verrou que ls-remote : deux PUT
    # parallèles ne doivent pas entrelacer `checkout master`.
    evalue = bool(branch) and status_val in ("approved", "rejected")
    if evalue:
        code_loc, _, _ = await _git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
        )
        locale = code_loc == 0
        code_rem, out_rem, _ = await _git(["ls-remote", "--heads", "origin", branch])
        distante = code_rem == 0 and bool(out_rem.strip())
    else:
        locale = distante = False
    rapport: dict[str, Any] = {
        "branche": branch,
        "locale": locale if evalue else None,
        "distante": distante if evalue else None,
    }

    if status_val == "approved":
        if branch and not locale and distante:
            # La branche n'est que sur origin : c'est le cas NORMAL depuis #T327.
            # Fusionner ici voudrait dire fusionner dans le dépôt de déploiement
            # du Deck, qui est en pull seul et dont `master` est protégée côté
            # GitHub — la fusion locale le ferait diverger et casserait le
            # prochain déploiement. La récolte passe par une PR, comme le reste.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"La branche '{branch}' n'existe que sur origin : elle se récolte "
                    f"par une pull request, pas par une fusion locale (master est "
                    f"protégée et ce dépôt est en pull seul). La tâche reste en l'état, "
                    f"sa branche est conservée en base."
                ),
            )

        if branch and not locale and not distante:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"La branche '{branch}' n'existe ni en local ni sur origin : rien à fusionner.",
            )

        if locale:
            # [#T396] `_run_git` RETOURNE (code, stdout, stderr) et n'élève jamais
            # d'exception : le `try/except` d'origine ne pouvait pas se déclencher.
            # Un merge en échec passait donc pour un succès, et la tâche était
            # marquée « completed » sans que rien n'ait été fusionné. On lit le
            # code de retour, seul témoin réel.
            code, _, err = await _git(["checkout", "master"])
            if code != 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Impossible de revenir sur master : {err[:200]}",
                )

            code, _, err = await _git(["merge", "--no-ff", branch])
            if code != 0:
                logger.error(f"Échec de la fusion pour la branche {branch} : {err[:300]}")
                code_abort, _, err_abort = await _git(["merge", "--abort"])
                if code_abort != 0:
                    logger.critical(f"Impossible d'annuler la fusion (merge --abort) : {err_abort[:200]}")
                # La branche n'est PAS effacée de la base : le travail existe encore.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Échec de la fusion Git. Fusion annulée. Erreur : {err[:200]}",
                )

            code, _, err = await _git(["branch", "-d", branch])
            rapport["locale_supprimee"] = code == 0
            if code != 0:
                logger.warning(f"Impossible de supprimer la branche locale {branch} : {err[:200]}")

        # Le travail est dans master : la référence peut partir, sauf si une copie
        # survit sur origin — auquel cas on la garde pour que le ménage reste possible.
        if branch:
            code_rem, out_rem, _ = await _git(["ls-remote", "--heads", "origin", branch])
            distante_apres = code_rem == 0 and bool(out_rem.strip())
        else:
            distante_apres = False
        rapport["distante_restante"] = distante_apres
        if distante_apres:
            logger.warning(
                f"Branche '{branch}' fusionnée en local mais toujours présente sur "
                f"origin : référence conservée en base pour le nettoyage."
            )
            await update_task_status(id, "completed")
        else:
            await update_task_status(id, "completed", git_branch=None)

    elif status_val == "rejected":
        if locale:
            code, _, err = await _git(["checkout", "master"])
            if code != 0:
                logger.error(f"Retour sur master impossible avant rejet de {branch} : {err[:200]}")
            code, _, err = await _git(["branch", "-D", branch])
            rapport["locale_supprimee"] = code == 0
            if code != 0:
                logger.error(f"Erreur lors du nettoyage de la branche rejetée {branch} : {err[:200]}")

        # Un rejet ne détruit PAS le travail poussé sur origin : supprimer une
        # branche distante est irréversible et ce n'est pas ce qu'un clic de revue
        # engage. Tant que la branche est là, son nom reste en base — c'est ce qui
        # permet de la retrouver, et à l'outil de ménage de la proposer.
        rapport["distante_restante"] = distante
        if distante:
            logger.warning(
                f"Tâche {id} rejetée mais la branche '{branch}' reste sur origin : "
                f"référence conservée en base (suppression distante non automatique)."
            )
            await update_task_status(id, "abandoned")
        else:
            await update_task_status(id, "abandoned", git_branch=None)

    elif status_val == "pending":
        # Réinitialisation pour ré-exécution. `git_branch` n'est PAS effacé :
        # la tâche va repartir sur une branche neuve, mais celle du tour précédent
        # peut être déjà poussée. L'effacer ici, c'est perdre sa trace — mesuré le
        # 18/08 sur les tâches 18 et 20, relancées alors que leur travail était
        # déjà sur origin. Le prochain passage en 'running' réécrira le champ.
        await update_task_status(id, "pending", retries=0, error_message=None)

    else:
        if status_val is not None:
            await update_task_status(id, status_val)

    return {"status": "success", "branche": rapport}

@router.delete("/tasks/{id}")
async def remove_task(id: int):
    """
    Supprime définitivement une tâche du backlog.
    """
    try:
        await delete_task(id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Erreur lors de la suppression de la tâche {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de supprimer la tâche."
        )

@router.get("/stats")
async def get_stats():
    """
    Récupère les statistiques globales d'exécution du backlog.
    """
    try:
        return await get_task_stats()
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des statistiques: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de récupérer les statistiques."
        )

@router.get("/quota")
async def get_quota():
    """
    Récupère l'état actuel de la consommation du budget d'API (BudgetGuard).
    """
    try:
        guard = BudgetGuard()
        return await guard.get_quota_summary()
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des quotas: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de récupérer l'état des quotas."
        )
