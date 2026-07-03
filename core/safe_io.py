# -*- coding: utf-8 -*-
"""
core/safe_io.py — Écritures de fichiers sûres (P1-2.3).

Fournit :
- `safe_json_write` : écriture JSON **atomique** (fichier temporaire dans le même
  répertoire puis `os.replace`). Un lecteur voit toujours soit l'ancien fichier
  complet, soit le nouveau — jamais un fichier tronqué, même en cas de crash ou
  d'accès concurrent.
- `file_lock` : context manager renvoyant un `filelock.FileLock` sur
  `<path>.lock`, pour sérialiser les accès inter-process (conforme CLAUDE.md).

Les bases SQLite (`moteur_runtime.db`, mémoire) sont déjà protégées par le mode
WAL + `busy_timeout` (protection inter-process canonique) et n'utilisent pas ce
module : un FileLock grossier ne ferait que sérialiser inutilement les writers.
"""

import os
import json
import tempfile
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

try:
    from filelock import FileLock
    _HAS_FILELOCK = True
except ImportError:  # filelock est une dépendance, mais on dégrade proprement
    _HAS_FILELOCK = False
    FileLock = None

# Délai d'acquisition par défaut d'un verrou de fichier (secondes).
DEFAULT_LOCK_TIMEOUT = 10


@contextmanager
def file_lock(path, timeout: float = DEFAULT_LOCK_TIMEOUT):
    """
    Verrou inter-process sur `<path>.lock`. No-op si `filelock` est indisponible.

    Réentrant dans le même thread (comportement de filelock) : on peut donc
    imbriquer `file_lock(p)` autour d'un `safe_json_write(p)` lui-même verrouillé.
    """
    if not _HAS_FILELOCK:
        yield
        return
    lock = FileLock(str(path) + ".lock", timeout=timeout)
    with lock:
        yield


def safe_json_write(
    path,
    data,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    lock: bool = True,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> None:
    """
    Écrit `data` en JSON dans `path` de façon ATOMIQUE.

    Écriture dans un fichier temporaire du même répertoire (pour que `os.replace`
    reste sur le même système de fichiers), `fsync`, puis `os.replace` — opération
    atomique sous POSIX comme sous Windows.

    Args:
        path:         Chemin de destination.
        data:         Objet sérialisable en JSON.
        indent:       Indentation JSON (défaut 2).
        ensure_ascii: Échappement ASCII (défaut False = accents lisibles).
        lock:         Sérialiser via un FileLock sur `<path>.lock` (défaut True).
        timeout:      Délai d'acquisition du verrou.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    def _do_write() -> None:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)  # ← bascule atomique
        except Exception:
            # Nettoyage du temp si l'échec survient avant le replace.
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    if lock:
        with file_lock(path, timeout=timeout):
            _do_write()
    else:
        _do_write()
