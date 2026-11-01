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
from typing import Any

import aiosqlite
import filelock

# Import dynamique du chemin de la base de données unifiée
from core.runtime_db import get_db_path

# Configuration du logger dédié au backlog
logger = logging.getLogger("backlog_db")

# Verrous asyncio pour sérialiser les accès au sein de la même boucle d'événements
_db_write_lock = asyncio.Lock()

# [#T432] Plafond de tentatives avant `abandoned`. Une tâche `failed` en
# dessous de ce plafond peut revenir dans la file après `scheduled_at` ;
# au-delà, revue humaine, pas de rejeu automatique.
MAX_TENTATIVES_TACHE = 3

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
    "project_id",
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
    2. Acquiert un verrou de fichier physique (filelock) de manière synchrone hors de
       l'event loop pour éviter les conflits d'accès multi-processus/multi-threads
       particulièrement sensibles sous Windows.
    3. Libère le verrou de fichier hors de l'event loop.
    """
    async with _db_write_lock:
        db_path = get_db_path()
        lock_path = db_path + ".backlog.lock"
        # [#T293] `thread_local=False` : le FileLock est créé DANS ce contexte et
        # protégé par `_db_write_lock` (asyncio.Lock) — une seule coroutine l'utilise
        # à la fois, donc son état peut être partagé entre threads sans risque.
        # Sans cela, acquire (worker A) et release (worker B) atterrissant sur des
        # threads différents rendaient la libération SILENCIEUSEMENT inopérante
        # (le compteur d'acquisition est stocké par thread par défaut) : le verrou
        # fuitait jusqu'à la mort du processus.
        lock = filelock.FileLock(lock_path, thread_local=False)
        try:
            await asyncio.to_thread(lock.acquire, timeout=10)
        except filelock.Timeout:
            logger.error(
                f"Timeout lors de l'acquisition du verrou physique sur {lock_path}. "
                "Le verrou est détenu par un autre processus."
            )
            raise TimeoutError(
                f"Le verrou de la base de données ({lock_path}) est bloqué par un autre processus."
            ) from None
        try:
            yield
        finally:
            try:
                # Libération déportée dans un thread pour éviter de bloquer l'event loop
                await asyncio.to_thread(lock.release)
                # [#T293] Un verrou non relâché doit être VISIBLE dans le journal :
                # la libération était un no-op silencieux quand l'état n'était pas
                # partagé entre threads — on vérifie donc l'état réel après coup.
                if lock.is_locked:
                    logger.error(
                        f"Le verrou {lock_path} est encore détenu après sa libération "
                        "(fuite de verrou) : les écritures suivantes échoueront."
                    )
            except Exception as e:
                logger.error(
                    f"Erreur lors de la libération du verrou {lock_path} : {e}",
                    exc_info=True,
                )


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
                        retries INTEGER DEFAULT 0,
                        discovery_key TEXT,
                        complexity TEXT
                    )
                """
                )
                # [#T397] Bases antérieures à la découverte automatique : les
                # deux colonnes sont ajoutées à chaud (ADD COLUMN uniquement,
                # aucune ligne perdue). `duplicate column name` est le cas
                # nominal au second démarrage.
                for colonne, definition in (
                    ("discovery_key", "TEXT"),
                    ("complexity", "TEXT"),
                    ("project_id", "TEXT"),
                ):
                    try:
                        await db.execute(
                            f"ALTER TABLE backlog_tasks ADD COLUMN {colonne} {definition}"
                        )
                    except Exception as e:
                        if "duplicate column name" not in str(e).lower():
                            raise
                # [#T397] Garde-fou DUR contre le rejeu : deux tâches ne peuvent
                # pas partager la même clé de découverte, même si la logique
                # applicative se trompe. L'index est partiel — les tâches créées
                # à la main (clé NULL) ne sont pas contraintes entre elles.
                await db.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_backlog_discovery_key
                    ON backlog_tasks (discovery_key)
                    WHERE discovery_key IS NOT NULL
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
    scheduled_at: float | None = None,
    discovery_key: str | None = None,
    complexity: str | None = None,
    project_id: str | None = None,
) -> int:
    """
    Insère une nouvelle tâche dans le backlog et retourne son identifiant unique.

    :param title: Titre de la tâche.
    :param description: Description détaillée ou payload de la tâche.
    :param priority: Niveau de priorité (1 = Urgent/Haute, 2 = Normale, etc.).
    :param scheduled_at: Timestamp Unix optionnel pour planifier l'exécution dans le futur.
    :param discovery_key: [#T397] Empreinte STABLE de la source pour les tâches
        découvertes automatiquement (`board:T396`, `todo:chemin#empreinte`).
        Contrainte d'unicité en base : une source déjà injectée — quel que soit
        son statut, `completed` compris — ne peut pas revenir dans la file.
        None pour les tâches créées à la main, qui restent libres de se répéter.
    :param complexity: [#T397] Niveau évalué à la découverte ('low'/'medium'/'high'),
        conservé pour le routage par tier. None si inconnu.
        :return: L'ID de la tâche créée, ou celui de la tâche existante si `discovery_key`
        est déjà présente en base (l'insertion est alors sans effet — y compris
        pour une ligne `failed` : le rejeu passe par le drainage, pas par une
        seconde insertion).
    """
    async with db_write_lock_context():
        db_path = get_db_path()
        created_at = time.time()
        try:
            async with _connect_db(db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO backlog_tasks (title, description, priority, status, created_at,
                                               scheduled_at, discovery_key, complexity, project_id)
                    VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                    (title, description, priority, created_at, scheduled_at,
                     discovery_key, complexity, project_id),
                )
                await db.commit()
                task_id = cursor.lastrowid
                logger.info(
                    f"Tâche ajoutée au backlog [ID: {task_id}] : '{title}' (Priorité: {priority})"
                )
                return task_id
        except sqlite3.IntegrityError:
            # [#T397] La clé de découverte est déjà en base : deux passages du
            # planificateur (ou deux processus) ont vu la même source. Ce n'est
            # pas une panne — on rend l'identifiant existant et on n'insère rien.
            if discovery_key is None:
                raise
            async with _connect_db(db_path) as db:
                async with db.execute(
                    "SELECT id FROM backlog_tasks WHERE discovery_key = ?",
                    (discovery_key,),
                ) as cursor:
                    row = await cursor.fetchone()
            if row is None:
                raise
            logger.info(
                f"Tâche déjà présente pour la clé de découverte '{discovery_key}' "
                f"[ID: {row[0]}] — aucune insertion."
            )
            return int(row[0])
        except Exception as e:
            logger.error(
                f"Erreur lors de l'ajout de la tâche '{title}' : {e}",
                exc_info=True,
            )
            raise


def _sql_et_params_tache_jouable(
    now: float, exclure_ids: set[int] | None = None
) -> tuple[str, list]:
    """
    [#T432] Clause d'éligibilité partagée par `get_next_task` et la réservation.

    Jouable :
      - `pending` dont `scheduled_at` est nul ou échu ;
      - `failed` de moins de `MAX_TENTATIVES_TACHE` tentatives, même règle
        d'horaire. Un `scheduled_at` NULL (échecs d'avant #T432) est échu :
        c'est le déblocage des files coincées, pas une boucle — `retries`
        continue de monter jusqu'à l'abandon.

    `completed` et `abandoned` restent hors file. `running` aussi.
    """
    clause = (
        "("
        "status = 'pending' AND (scheduled_at IS NULL OR scheduled_at <= ?)"
        " OR "
        "status = 'failed' AND COALESCE(retries, 0) < ? "
        "AND (scheduled_at IS NULL OR scheduled_at <= ?)"
        ")"
    )
    params: list = [now, MAX_TENTATIVES_TACHE, now]
    requete = f"SELECT * FROM backlog_tasks WHERE {clause}"
    if exclure_ids:
        requete += " AND id NOT IN (" + ",".join("?" for _ in exclure_ids) + ")"
        params.extend(int(i) for i in exclure_ids)
    requete += " ORDER BY priority ASC, created_at ASC LIMIT 1"
    return requete, params


async def get_next_task() -> dict[str, Any] | None:
    """
    Récupère la prochaine tâche jouable (pending, ou failed dont le délai de
    rejeu est échu), la plus prioritaire.

    Tri : Priorité croissante (1 est plus prioritaire que 2), puis date de création croissante.

    :return: Un dictionnaire représentant la tâche, ou None si aucune tâche n'est disponible.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        now = time.time()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                requete, params = _sql_et_params_tache_jouable(now)
                async with db.execute(requete, tuple(params)) as cursor:
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


async def reserver_prochaine_tache(exclure_ids: set[int] | None = None) -> dict[str, Any] | None:
    """
    [#T387] Prend la prochaine tâche ET la marque 'running' de façon ATOMIQUE.

    `get_next_task()` ne fait qu'un SELECT : le passage en 'running' arrive plus
    tard, après la création de la branche Git. Tant que le drainage était
    séquentiel, la fenêtre entre les deux n'avait aucune conséquence. Avec trois
    travailleurs concurrents, elle en a une immédiate : les trois lisent la même
    ligne jouable et jouent la même tâche, sur trois clones, avec trois
    branches — pour un seul résultat conservé.

    Le SELECT et l'UPDATE se font ici sous le MÊME verrou d'écriture, dans la
    même connexion : un seul travailleur peut passer une ligne donnée de
    'pending'/'failed' à 'running', les autres voient déjà 'running' et
    passent à la suivante.

    [#T432] Une ligne `failed` dont le délai de rejeu est échu est réservable
    comme une `pending`. C'est ce qui débloque une file où tout est `failed`
    et où la découverte refuse de ré-injecter (clé unique).

    :return: la tâche réservée (avec status='running'), ou None si le backlog
        est vide. Le champ `retries` est celui d'AVANT la réservation, que
        l'appelant incrémente en cas d'échec.
    :param exclure_ids: identifiants à exclure de la sélection — les tâches
        réservées plus tôt dans le même drainage puis LIBÉRÉES parce qu'aucun
        provider ne pouvait les jouer d'après l'historique. Sans cette
        exclusion, la réservation suivante reprendrait la même tâche et le
        travailleur bouclerait dessus indéfiniment.
    """
    async with db_write_lock_context():
        db_path = get_db_path()
        now = time.time()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                requete, params = _sql_et_params_tache_jouable(now, exclure_ids)
                async with db.execute(requete, tuple(params)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        return None

                tache = dict(row)
                # [#T432] `failed` redevient `running` au même titre que `pending`.
                curseur_maj = await db.execute(
                    "UPDATE backlog_tasks SET status = 'running' "
                    "WHERE id = ? AND status IN ('pending', 'failed')",
                    (tache["id"],),
                )
                await db.commit()
                if curseur_maj.rowcount == 0:
                    # Course : un autre travailleur l'a prise entre le SELECT
                    # et l'UPDATE. On ne rend rien plutôt qu'une tâche déjà
                    # en vol.
                    return None
                logger.info(
                    f"Tâche {tache['id']} réservée ('{tache['title'][:50]}') "
                    f"— {tache.get('status')} → 'running'."
                )
                return tache
        except Exception as e:
            logger.error(
                f"Erreur lors de la réservation de la tâche suivante : {e}",
                exc_info=True,
            )
            raise


# [#T402] Les messages qui atterrissent en base doivent etre expurges, comme
# ceux qui partent au journal.
#
# Le masquage existe depuis #T262 (fuite du 10/08 : une cle Gemini retrouvee en
# clair dans le journal systemd) et son premier motif couvre exactement
# `?key=...`. Mais il est installe sur les HANDLERS DE JOURNALISATION : ce qui
# est ecrit en base ne passe jamais par la.
#
# Mesure le 18/08 a 22h sur la production : `backlog_tasks.error_message`
# portait la cle Gemini en clair sur **5 lignes**, arrivees par le meme chemin
# qu'en 2026-08-10 -- `requests.raise_for_status()` leve une HTTPError dont le
# message contient l'URL complete (« 429 Client Error ... ?key=... »), et le
# drainage DreamCoder ecrit cette chaine telle quelle dans la tache.
#
# Meme secret, autre sortie. On applique donc le meme filtre au bord de la base.
def _expurger(valeur):
    """Masque les secrets d'une valeur texte avant ecriture en base."""
    if not isinstance(valeur, str):
        return valeur
    from core.log_redaction import masquer_secrets
    return masquer_secrets(valeur)


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
                    values.append(_expurger(val))

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


async def liberer_tache(task_id: int) -> bool:
    """
    Remet une tâche 'running' en 'pending' sans toucher à ses compteurs.

    Utilisé par le drainage DreamCoder quand une tâche est réservée mais
    qu'aucun provider ne peut la jouer (quota, historique d'échecs sur son
    domaine) : la tâche ne doit ni brûler un `retries`, ni rester bloquée en
    'running'. L'UPDATE est conditionnel (seulement si encore 'running') pour
    ne pas écraser un statut final posé entre-temps.

    :return: True si la tâche a bien été remise en attente.
    """
    async with db_write_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                cursor = await db.execute(
                    "UPDATE backlog_tasks SET status = 'pending', git_branch = NULL "
                    "WHERE id = ? AND status = 'running'",
                    (task_id,),
                )
                await db.commit()
                liberee = cursor.rowcount > 0
                if liberee:
                    logger.info(
                        f"Tâche [ID: {task_id}] libérée : 'running' → 'pending' "
                        "(aucun provider disponible)."
                    )
                return liberee
        except Exception as e:
            logger.error(
                f"Erreur lors de la libération de la tâche [ID: {task_id}] : {e}",
                exc_info=True,
            )
            raise


async def recuperer_taches_running_orphelines() -> dict[str, int]:
    """
    Au démarrage : une tâche encore `running` n'a plus de process.

    `retries` > 0 → `failed` immédiat (rejeu dirigé, branche et critique
    conservées). `retries` == 0 → `pending` (première tentative coupée, pas
    d'échec à imputer). `git_branch` n'est PAS effacé : le retry qualité en
    a besoin. Mesuré le 23/08 : tâche 50 coincée en `running` avec l'erreur
    du tour d'avant, hors file jusqu'au prochain reboot manuel.
    """
    maintenant = time.time()
    async with db_write_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                cur_failed = await db.execute(
                    "UPDATE backlog_tasks SET status = 'failed', scheduled_at = ? "
                    "WHERE status = 'running' AND COALESCE(retries, 0) > 0",
                    (maintenant,),
                )
                cur_pending = await db.execute(
                    "UPDATE backlog_tasks SET status = 'pending' "
                    "WHERE status = 'running' AND COALESCE(retries, 0) = 0"
                )
                await db.commit()
                n_failed = cur_failed.rowcount or 0
                n_pending = cur_pending.rowcount or 0
                if n_failed or n_pending:
                    logger.warning(
                        "[BACKLOG] %s running orpheline(s) → failed, "
                        "%s → pending (process disparu au boot).",
                        n_failed,
                        n_pending,
                    )
                return {"failed": n_failed, "pending": n_pending}
        except Exception as e:
            logger.error(
                "Erreur lors de la récupération des tâches running orphelines : %s",
                e,
                exc_info=True,
            )
            raise


async def get_all_tasks(limit: int | None = 50) -> list[dict[str, Any]]:
    """
    Retourne la liste des tâches du backlog triées par date de création décroissante.

    :param limit: Nombre maximum de tâches à retourner. `None` = aucune limite :
        nécessaire à la déduplication de l'auto-découverte, qui doit comparer
        contre TOUT le backlog — avec le plafond par défaut (50), toute tâche
        plus ancienne que la 50ᵉ ligne était ré-injectée à chaque scan.
    :return: Liste de dictionnaires représentant les tâches.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                db.row_factory = sqlite3.Row
                if limit is None:
                    requete = "SELECT * FROM backlog_tasks ORDER BY created_at DESC"
                    params: tuple = ()
                else:
                    requete = "SELECT * FROM backlog_tasks ORDER BY created_at DESC LIMIT ?"
                    params = (limit,)
                async with db.execute(requete, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération de la liste des tâches : {e}",
                exc_info=True,
            )
            raise


async def get_discovery_keys() -> set[str]:
    """
    [#T397] Retourne toutes les clés de découverte présentes en base.

    Sans limite et **sans filtre de statut** : c'est la mémoire de ce qui a déjà
    été injecté un jour. Une tâche `completed` doit rester connue, sinon elle est
    re-découverte et rejouée — le défaut mesuré le 18/08 (mêmes tâches rejouées à
    sept cycles, ~15 M tokens pour une tâche close). `get_all_tasks(limit=50)` ne
    peut pas rendre ce service : au-delà de 50 lignes, la mémoire s'efface.

    :return: Ensemble des clés non nulles.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        try:
            async with _connect_db(db_path) as db:
                async with db.execute(
                    "SELECT discovery_key FROM backlog_tasks WHERE discovery_key IS NOT NULL"
                ) as cursor:
                    rows = await cursor.fetchall()
                    return {row[0] for row in rows}
        except Exception as e:
            logger.error(
                f"Erreur lors de la lecture des clés de découverte : {e}",
                exc_info=True,
            )
            raise


async def get_task_stats() -> dict[str, int]:
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


# Statuts considérés "actifs" pour le dédoublonnage de l'auditeur
# (core/auditor_agent.py). `failed` reste ici : #T432 la rejoue PAR LE
# DRAINAGE (même ligne, même clé), pas par une ré-injection. `abandoned`
# aussi : trois échecs de code = revue humaine, pas un doublon silencieux.
NON_TERMINAL_STATUSES = ("pending", "running", "paused", "failed", "abandoned")


async def get_active_task_titles(
    statuses: tuple[str, ...] = NON_TERMINAL_STATUSES,
) -> list[str]:
    """
    Retourne les titres des tâches non terminales, pour le dédoublonnage de
    l'auditeur autonome (évite de proposer deux fois la même tâche déjà
    suivie, gelée ou abandonnée).

    :param statuses: Statuts à considérer comme "actifs" (par défaut NON_TERMINAL_STATUSES).
    :return: Liste des titres de tâches correspondants.
    """
    async with db_read_lock_context():
        db_path = get_db_path()
        placeholders = ",".join("?" * len(statuses))
        try:
            async with _connect_db(db_path) as db:
                async with db.execute(
                    f"SELECT title FROM backlog_tasks WHERE status IN ({placeholders})",
                    tuple(statuses),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception as e:
            logger.error(
                f"Erreur lors de la récupération des titres actifs pour dédoublonnage : {e}",
                exc_info=True,
            )
            raise


async def get_task_by_id(task_id: int) -> dict[str, Any] | None:
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
