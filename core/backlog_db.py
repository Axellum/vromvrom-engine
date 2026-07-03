# -*- coding: utf-8 -*-
"""
tab5-engine Antigravity  - Système de Backlog Ultra-Fiable
Fichier : core/backlog_db.py

Ce module gère de façon asynchrone et thread-safe la table des tâches du backlog
(backlog_tasks) dans la base de données SQLite unifiée d'Antigravity.
Il implémente un mécanisme de verrouillage hybride (asyncio.Lock + filelock synchrone)
conçu spécifiquement pour éliminer les risques de deadlocks d'event loop et de
désynchronisation de threads sous Windows.
"""

import asyncio
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import aiosqlite
import filelock

# Import dynamique du chemin de la base de données unifiée
from core.runtime_db import get_db_path

# Configuration du logger dédié au backlog
logger = logging.getLogger("backlog_db")

# Verrous asyncio pour sérialiser les accès au sein de la même boucle d'événements
_db_write_lock = asyncio.Lock()

# Liste des colonnes autorisées pour les mises à jour dynamiques (sécurité SQL)
ALLOWED_UPDATE_COLUMNS = {
    "title",
    "description",
    "priority",
    "status",
    "scheduled_at",
    "git_branch",
    "result_summary",
    "tokens_used",
    "error_message",
    "retries",
}


@asynccontextmanager
async def _connect_db(db_path: str):
    """
    Établit une connexion SQLite asynchrone et configure les PRAGMA optimisés.
    WAL (Write-Ahead Logging) est activé pour autoriser les lectures concurrentes
    sans être bloqué par les écritures en cours.
    """
    async with aiosqlite.connect(db_path, timeout=10.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


@asynccontextmanager
async def db_write_lock_context():
    """
    Gestionnaire de contexte asynchrone pour les opérations d'ÉCRITURE (verrou exclusif).
    1. Acquiert un verrou asyncio pour protéger l'event loop courante.
    2. Acquiert un verrou de fichier physique (filelock) de manière synchrone sur le
       thread principal pour éviter les conflits d'accès multi-processus/multi-threads
       particulièrement sensibles sous Windows.
    3. Libère le verrou de fichier de manière asynchrone pour éviter de bloquer l'event loop.
    """
    async with _db_write_lock:
        db_path = get_db_path()
        lock_path = db_path + ".backlog.lock"
        lock = filelock.FileLock(lock_path)
        try:
            await asyncio.to_thread(lock.acquire, timeout=10)
        except filelock.Timeout:
            logger.error(
                f"Timeout lors de l'acquisition du verrou physique sur {lock_path}. "
                "Le verrou est détenu par un autre processus."
            )
            raise TimeoutError(
                f"Le verrou de la base de données ({lock_path}) est bloqué par un autre processus."
            )
        try:
            yield
        finally:
            try:
                # Libération déportée dans un thread pour éviter de bloquer l'event loop
                await asyncio.to_thread(lock.release)
            except Exception as e:
                logger.warning(f"Erreur lors de la libération du verrou : {e}")


@asynccontextmanager
async def db_read_lock_context():
    """
    Gestionnaire de contexte asynchrone pour les opérations de LECTURE (verrou partagé).
    Grâce au mode SQLite WAL, aucun verrou physique (filelock) n'est nécessaire.
    Les lectures concurrentes peuvent s'effectuer de manière totalement parallèle.
    """
    # SQLite en mode WAL gère parfaitement les lectures concurrentes avec d'autres
    # écrivains ou lecteurs. Aucun verrou n'est donc nécessaire.
    yield


# Alias pour compatibilité ascendante (ex: budget_guard.py)
db_lock_context = db_write_lock_context


async def init_backlog_db() -> None:
    """
    Initialise la table `backlog_tasks` et ses index associés si elle n'existe pas.
    Garantit la structure requise pour le tab5-engine Antigravity.
    """
    logger.info("Initialisation de la table backlog_tasks...")
    async with db_write_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS backlog_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        priority INTEGER DEFAULT 2,
                        status TEXT DEFAULT 'pending',
                        created_at REAL NOT NULL,
                        scheduled_at REAL,
                        git_branch TEXT,
                        result_summary TEXT,
                        tokens_used INTEGER DEFAULT 0,
                        error_message TEXT,
                        retries INTEGER DEFAULT 0
                    )
                """
                )
                # Index pour optimiser la recherche des tâches éligibles par priorité et statut
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_backlog_status_priority_schedule 
                    ON backlog_tasks (status, priority, scheduled_at)
                """
                )
                await db.commit()
                logger.info(
                    "Table backlog_tasks initialisée avec succès (SQLite)."
                )
        except Exception as e:
            logger.error(
                f"Erreur lors de l'initialisation de la base de données du backlog : {e}",
                exc_info=True,
            )
            raise


async def add_task(
    title: str,
    description: str,
    priority: int = 2,
    scheduled_at: Optional[float] = None,
) -> int:
    """
    Insère une nouvelle tâche dans le backlog et retourne son identifiant unique.

    :param title: Titre de la tâche.
    :param description: Description détaillée ou payload de la tâche.
    :param priority: Niveau de priorité (1 = Urgent/Haute, 2 = Normale, etc.).
    :param scheduled_at: Timestamp Unix optionnel pour planifier l'exécution dans le futur.
    :return: L'ID de la tâche créée.
    """
    async with db_write_lock_context():
        db_path = get_db_path()
        created_at = time.time()
        try:
            async with _connect_db(db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO backlog_tasks (title, description, priority, status, created_at, scheduled_at)
                    VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                    (title, description, priority, created_at, scheduled_at),
                )
                await db.commit()
                task_id = cursor.lastrowid
                logger.info(
                    f"Tâche ajoutée au backlog [ID: {task_id}] : '{title}' (Priorité: {priority})"
                )
                return task_id
        except Exception as e:
            logger.error(
                f"Erreur lors de l'ajout de la tâche '{title}' : {e}",
                exc_info=True,
            )
            raise


async def get_next_task() -> Optional[Dict[str, Any]]:
    """
    Récupère la tâche 'pending' la plus prioritaire éligible pour exécution immédiate.
    Une tâche est éligible si son statut est 'pending' et que son paramètre `scheduled_at`
    est soit nul, soit inférieur ou égal au timestamp actuel.

    Tri : Priorité croissante (1 est plus prioritaire que 2), puis date de création croissante.

    :return: Un dictionnaire représentant la tâche, ou None si aucune tâche n'est disponible.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        now = time.time()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                async with db.execute(
                    """
                    SELECT * FROM backlog_tasks
                    WHERE status = 'pending' AND (scheduled_at IS NULL OR scheduled_at <= ?)
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1
                """,
                    (now,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)
                    return None
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération de la tâche suivante : {e}",
                exc_info=True,
            )
            raise


async def update_task_status(task_id: int, status: str, **kwargs) -> bool:
    """
    Met à jour le statut d'une tâche spécifique ainsi que d'autres champs optionnels passés en kwargs.

    :param task_id: Identifiant de la tâche à mettre à jour.
    :param status: Nouveau statut ('pending', 'running', 'completed', 'failed', 'paused', 'abandoned').
    :param kwargs: Autres champs à mettre à jour (ex: git_branch, tokens_used, error_message, etc.).
    :return: True si la mise à jour a affecté au moins une ligne, False sinon.
    """
    allowed_statuses = {
        "pending",
        "running",
        "completed",
        "failed",
        "paused",
        "abandoned",
    }
    if status not in allowed_statuses:
        raise ValueError(
            f"Statut invalide '{status}'. Statuts autorisés : {allowed_statuses}"
        )

    # Validation stricte des clés passées en kwargs pour éviter les injections SQL
    for key in kwargs:
        if key not in ALLOWED_UPDATE_COLUMNS:
            raise ValueError(
                f"Tentative de mise à jour d'une colonne non autorisée ou inexistante : '{key}'"
            )

    async with db_write_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                fields = ["status = ?"]
                values = [status]

                for key, val in kwargs.items():
                    fields.append(f"{key} = ?")
                    values.append(val)

                values.append(task_id)
                query = (
                    f"UPDATE backlog_tasks SET {', '.join(fields)} WHERE id = ?"
                )

                cursor = await db.execute(query, tuple(values))
                await db.commit()
                success = cursor.rowcount > 0
                if success:
                    logger.debug(
                        f"Tâche [ID: {task_id}] mise à jour avec succès. Statut: {status}."
                    )
                else:
                    logger.warning(
                        f"Aucune tâche trouvée avec l'ID {task_id} pour mise à jour."
                    )
                return success
        except Exception as e:
            logger.error(
                f"Erreur lors de la mise à jour de la tâche [ID: {task_id}] : {e}",
                exc_info=True,
            )
            raise


async def get_all_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retourne la liste des tâches du backlog triées par date de création décroissante.

    :param limit: Nombre maximum de tâches à retourner.
    :return: Liste de dictionnaires représentant les tâches.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                async with db.execute(
                    "SELECT * FROM backlog_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération de la liste des tâches : {e}",
                exc_info=True,
            )
            raise


async def get_task_stats() -> Dict[str, int]:
    """
    Retourne un résumé statistique du nombre de tâches regroupées par statut.

    :return: Dictionnaire contenant le décompte pour chaque statut standard.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                async with db.execute(
                    "SELECT status, COUNT(*) as count FROM backlog_tasks GROUP BY status"
                ) as cursor:
                    rows = await cursor.fetchall()

                    # Initialisation de tous les statuts standards à 0
                    stats = {
                        "pending": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "paused": 0,
                        "abandoned": 0,
                    }
                    for row in rows:
                        status, count = row[0], row[1]
                        if status in stats:
                            stats[status] = count
                    return stats
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération des statistiques du backlog : {e}",
                exc_info=True,
            )
            raise


async def get_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    """
    Récupère une tâche spécifique par son identifiant unique.

    :param task_id: Identifiant de la tâche.
    :return: Dictionnaire représentant la tâche, ou None si non trouvée.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                async with db.execute(
                    "SELECT * FROM backlog_tasks WHERE id = ?", (task_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)
                    return None
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération de la tâche [ID: {task_id}] : {e}",
                exc_info=True,
            )
            raise


async def delete_task(task_id: int) -> bool:
    """
    Supprime définitivement une tâche de la base de données.

    :param task_id: Identifiant de la tâche à supprimer.
    :return: True si la suppression a été effectuée, False sinon.
    """
    async with db_write_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM backlog_tasks WHERE id = ?", (task_id,)
                )
                await db.commit()
                success = cursor.rowcount > 0
                if success:
                    logger.info(
                        f"Tâche [ID: {task_id}] supprimée avec succès de la base de données."
                    )
                else:
                    logger.warning(
                        f"Tentative de suppression échouée : aucune tâche avec l'ID {task_id}."
                    )
                return success
        except Exception as e:
            logger.error(
                f"Erreur lors de la suppression de la tâche [ID: {task_id}] : {e}",
                exc_info=True,
            )
            raise