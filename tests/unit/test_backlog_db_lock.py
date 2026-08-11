"""
tests/unit/test_backlog_db_lock.py — Régression #T293 : le verrou d'écriture est relâché.

Symptôme de production : `GET /api/billing` répond 200 une fois, puis TOUS les
appels répondent 500 « Le verrou de la base de données (...backlog.lock) est bloqué
par un autre processus », définitivement. Cause racine prouvée en isolation :
`db_write_lock_context()` crée un `filelock.FileLock` SANS `thread_local=False`,
or ce paramètre est `True` par défaut — le compteur d'acquisition est stocké par
thread. `asyncio.to_thread` pouvant exécuter l'acquire et le release sur des
workers DIFFÉRENTS du pool, le release devenait un no-op silencieux (aucune
exception, donc l'ancien `except Exception` n'avait rien à journaliser) et le
verrou fuitait jusqu'à la mort du processus.

Ces tests verrouillent :
1. le release fonctionne même depuis un AUTRE thread que l'acquire (régression) ;
2. la vraie fonction supporte 20 contextes séquentiels + 20 concurrents ;
3. l'exclusion MULTI-PROCESSUS reste intacte (garde-fou n°1 : un vrai
   sous-processus ne doit pas acquérir pendant que le contexte est actif) ;
4. un verrou non relâché est VISIBLE dans le journal (ERROR, plus de warning
   avalé silencieusement).
"""

import asyncio
import concurrent.futures
import logging
import subprocess
import sys

import filelock
import pytest

from core.backlog_db import db_write_lock_context
from core.runtime_db import get_db_path, override_db_path

# Script exécuté par le sous-processus du test d'exclusion multi-processus.
# Arguments : [chemin du verrou, timeout]. Sorties : "TIMEOUT" (exit 1) si le
# verrou est détenu par un autre processus, "ACQUIS" (exit 0) sinon.
_SCRIPT_SOUS_PROCESSUS = (
    "import sys\n"
    "from filelock import FileLock, Timeout\n"
    "try:\n"
    "    FileLock(sys.argv[1]).acquire(timeout=float(sys.argv[2]))\n"
    "except Timeout:\n"
    "    print('TIMEOUT', flush=True)\n"
    "    sys.exit(1)\n"
    "print('ACQUIS', flush=True)\n"
    "sys.exit(0)\n"
)


@pytest.fixture
def db_ephemere(tmp_path):
    """Base SQLite temporaire : le verrou testé cible un fichier jetable."""
    ancien = get_db_path()
    db_path = str(tmp_path / "moteur_runtime_test.db")
    override_db_path(db_path)
    yield db_path
    override_db_path(ancien)


@pytest.fixture(autouse=True)
def _verrou_asyncio_neuf_par_test():
    """pytest-asyncio donne une boucle NEUVE à chaque test (loop_scope=function)
    alors que `_db_write_lock` est un asyncio.Lock module-level qui se lie à la
    première boucle qui l'utilise : on le recrée par test pour éviter le
    RuntimeError « bound to a different event loop » entre tests du même fichier."""
    import core.backlog_db as backlog_db

    ancien = backlog_db._db_write_lock
    backlog_db._db_write_lock = asyncio.Lock()
    yield
    backlog_db._db_write_lock = ancien


async def test_regression_release_thread_etranger(db_ephemere, monkeypatch):
    """#T293 : le release doit libérer même exécuté par un AUTRE thread.

    AVANT correctif, le FileLock était créé sans `thread_local=False` : le
    compteur d'acquisition était stocké par thread, et un release() exécuté par
    un worker différent était un no-op silencieux — le verrou fuitait. On force
    ici le scénario exact de production (acquire sur un worker, release sur un
    autre) via deux exécuteurs mono-thread dédiés : c'est le cas que le pool par
    défaut d'asyncio produit quand la charge fait tourner les workers.
    """
    pool_acquire = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="t293-acquire"
    )
    pool_release = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="t293-release"
    )
    to_thread_original = asyncio.to_thread

    def _to_thread_threads_forces(fn, *args, **kwargs):
        """Aiguille acquire → pool A et release → pool B ; passe le reste."""
        if getattr(fn, "__name__", "") == "acquire":
            return asyncio.wrap_future(pool_acquire.submit(fn, *args, **kwargs))
        if getattr(fn, "__name__", "") == "release":
            return asyncio.wrap_future(pool_release.submit(fn, *args, **kwargs))
        return to_thread_original(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread_threads_forces)
    try:
        async with db_write_lock_context():
            pass
    finally:
        pool_acquire.shutdown(wait=True)
        pool_release.shutdown(wait=True)

    # Un FileLock NEUF sur le même chemin doit acquérir sans timeout (même
    # processus) : c'est la preuve que le verrou a réellement été relâché.
    verif = filelock.FileLock(db_ephemere + ".backlog.lock")
    verif.acquire(timeout=2)
    verif.release()


async def test_verrou_relache_apres_20_contextes(db_ephemere):
    """#T293 : 20 contextes séquentiels + 20 concurrents, verrou toujours relâché.

    Reproduit la chaîne de production (GET /api/billing → BudgetGuard.initialize
    → db_write_lock_context) : à la fin, un FileLock neuf doit acquérir sans
    timeout. C'est ce scénario qui faisait fuiter le verrou en production quand
    la charge faisait atterrir acquire et release sur des workers différents.
    """
    for _ in range(20):
        async with db_write_lock_context():
            pass

    async def _contexte():
        async with db_write_lock_context():
            pass

    await asyncio.gather(*(_contexte() for _ in range(20)))

    verif = filelock.FileLock(db_ephemere + ".backlog.lock")
    verif.acquire(timeout=2)
    verif.release()


async def test_exclusion_multiprocessus_intacte(db_ephemere):
    """Garde-fou n°1 (#T293) : l'exclusion MULTI-PROCESSUS reste réelle.

    Pendant qu'un db_write_lock_context() est actif, un vrai sous-processus qui
    tente d'acquérir le même verrou avec un timeout court doit ÉCHOUER. Après la
    sortie du contexte, le même sous-processus doit acquérir immédiatement : ce
    second point rend le test auto-validant — il prouve que la procédure de
    vérification fonctionne et que le correctif n'a pas affaibli le verrou.
    """
    lock_path = db_ephemere + ".backlog.lock"
    cmd = [sys.executable, "-c", _SCRIPT_SOUS_PROCESSUS, lock_path, "1.0"]

    async with db_write_lock_context():
        # Le contexte est actif : le sous-processus ne doit PAS acquérir.
        sortie = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert "TIMEOUT" in sortie.stdout, sortie.stdout

    # Contexte sorti : le sous-processus doit acquérir (verrou réellement relâché).
    sortie2 = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert "ACQUIS" in sortie2.stdout, sortie2.stdout


async def test_fuite_de_verrou_loguee_en_erreur(db_ephemere, monkeypatch, caplog):
    """#T293 : un verrou non relâché est VISIBLE dans le journal (ERROR).

    AVANT correctif, la libération ratée était un no-op silencieux et l'ancien
    `except Exception` journalisait en warning un événement qui ne levait même
    jamais. On simule une libération en échec et on exige un ERROR explicite.
    """
    import core.backlog_db as backlog_db

    class FileLockQuiEchoueALaLiberation(filelock.FileLock):
        """Sous-classe de test : le premier release lève (défaillance réelle).

        Le `__del__` de filelock appelle aussi release(force=True) : seuls les
        appels suivants passent, pour éviter un warning de destruction.
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._release_a_echoue = False

        def release(self, force=False):
            if not self._release_a_echoue:
                self._release_a_echoue = True
                raise RuntimeError("échec simulé du release")
            super().release(force=force)

    monkeypatch.setattr(backlog_db.filelock, "FileLock", FileLockQuiEchoueALaLiberation)

    with caplog.at_level(logging.ERROR, logger="backlog_db"):
        async with db_write_lock_context():
            pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("libération du verrou" in m for m in messages), messages
