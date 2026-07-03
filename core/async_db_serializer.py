"""
core/async_db_serializer.py — Sérialiseur asynchrone pour toutes les opérations SQLite.
Garantit une exécution FIFO sans blocage du thread principal de FastAPI.
"""
import asyncio
import logging
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)

class AsyncDBSerializer:
    """
    Singleton thread-safe : sérialise les accès à SQLite.
    Chaque opération est encapsulée dans une coroutine et exécutée
    par un worker unique (asyncio.Task) sous forme de file FIFO.
    """

    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._queue: asyncio.Queue[tuple[Callable[[], T], asyncio.Future[T]]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._started = False

    async def start(self):
        """Démarre le worker de fond."""
        async with self._lock:
            if not self._started:
                self._worker_task = asyncio.create_task(self._worker_loop())
                self._started = True
                logger.info("[AsyncDBSerializer] Worker de fond SQLite démarré.")

    async def stop(self):
        """Arrête proprement le worker."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._started = False
            logger.info("[AsyncDBSerializer] Worker de fond SQLite arrêté.")

    async def execute(self, func: Callable[[], T]) -> T:
        """
        Soumet une fonction synchrone à exécuter sur le worker SQLite.
        Retourne un awaitable qui se résout avec le résultat.
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        await self._queue.put((func, future))
        return await future

    async def _worker_loop(self):
        """Boucle infinie : dépile les tâches et les exécute séquentiellement."""
        while True:
            try:
                func, future = await self._queue.get()
                try:
                    # Exécution synchrone dans le worker dédié
                    result = func()
                    if not future.cancelled():
                        future.set_result(result)
                except Exception as e:
                    if not future.cancelled():
                        future.set_exception(e)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                # Vidange rapide de la queue en cas d'annulation
                while not self._queue.empty():
                    try:
                        _, future = self._queue.get_nowait()
                        if not future.done():
                            future.cancel()
                    except asyncio.QueueEmpty:
                        break
                break
            except Exception as ex:
                logger.error(f"[AsyncDBSerializer] Erreur inattendue dans la boucle worker : {ex}")
                await asyncio.sleep(0.1)

    @classmethod
    def get_instance(cls) -> "AsyncDBSerializer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
