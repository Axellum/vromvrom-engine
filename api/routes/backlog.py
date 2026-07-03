# -*- coding: utf-8 -*-
"""
Antigravity Engine  - Router d'API pour le Backlog d'Agents
Conçu pour une fiabilité maximale sous Windows, avec gestion transactionnelle des branches Git
et contrôle strict des budgets d'exécution.

Auteur: Équipe d'Ingénierie Domotique & IA DeepMind
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import re
from core.backlog_db import (
    add_task, 
    delete_task, 
    get_all_tasks, 
    get_task_by_id, 
    get_task_stats, 
    update_task_status
)
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

# Modèles Pydantic
class TaskCreate(BaseModel):
    title: str = Field(..., description="Titre de la tâche")
    description: str = Field(..., description="Description détaillée de la tâche")
    priority: int = Field(2, description="Priorité de la tâche (1: Haute, 2: Moyenne, 3: Basse)")
    scheduled_at: Optional[float] = Field(None, description="Timestamp de planification optionnel")

class TaskUpdate(BaseModel):
    status: Optional[str] = Field(None, description="Nouveau statut de la tâche")
    git_branch: Optional[str] = Field(None, description="Branche Git associée")

@router.get("/tasks", response_model=List[Dict[str, Any]])
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
            payload.scheduled_at
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

    if status_val == "approved":
        if branch:
            try:
                # Étape 1 : Retour sur master
                _run_git(["checkout", "master"])
                # Étape 2 : Fusion de la branche de l'agent
                _run_git(["merge", "--no-ff", branch])
            except Exception as e:
                logger.error(f"Échec de la fusion pour la branche {branch}: {e}")
                try:
                    _run_git(["merge", "--abort"])
                except Exception as abort_err:
                    logger.critical(f"Impossible d'annuler la fusion (merge --abort): {abort_err}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Échec de la fusion Git. Fusion annulée. Erreur: {str(e)}"
                )
            
            try:
                # Étape 3 : Nettoyage de la branche locale fusionnée
                _run_git(["branch", "-d", branch])
            except Exception as e:
                logger.warning(f"Impossible de supprimer la branche locale {branch}: {e}")

        # Mise à jour de la tâche en base comme complétée
        await update_task_status(id, "completed", git_branch=None)

    elif status_val == "rejected":
        if branch:
            try:
                # Retour sur master et suppression forcée de la branche rejetée
                _run_git(["checkout", "master"])
                _run_git(["branch", "-D", branch])
            except Exception as e:
                logger.error(f"Erreur lors du nettoyage de la branche rejetée {branch}: {e}")
        
        # Mise à jour de la tâche en base comme abandonnée
        await update_task_status(id, "abandoned", git_branch=None)

    elif status_val == "pending":
        # Réinitialisation complète pour ré-exécution
        await update_task_status(id, "pending", retries=0, error_message=None, git_branch=None)

    else:
        if status_val is not None:
            await update_task_status(id, status_val)

    return {"status": "success"}

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
