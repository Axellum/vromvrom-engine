# -*- coding: utf-8 -*-
"""
tests/test_safe_io.py — Écritures JSON atomiques + verrou (P1-2.3).

Vérifie :
- safe_json_write produit un JSON relisible et n'écrase pas avant la fin
  (atomicité via os.replace) ;
- aucun fichier temporaire résiduel ;
- robustesse en concurrence (multi-thread et multi-process) : le fichier final
  reste un JSON valide, jamais tronqué.
"""

import os
import sys
import json
import glob
import threading
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.safe_io import safe_json_write, file_lock


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / "data.json"
    payload = {"accents": "éàü", "list": [1, 2, 3], "nested": {"k": "v"}}
    safe_json_write(str(p), payload)
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == payload


def test_no_leftover_temp_files(tmp_path):
    p = tmp_path / "data.json"
    safe_json_write(str(p), {"a": 1})
    # Aucun .tmp_*.json résiduel dans le répertoire
    assert glob.glob(str(tmp_path / ".tmp_*.json")) == []


def test_atomic_replace_keeps_valid_json_on_concurrent_threads(tmp_path):
    """50 threads écrivent en boucle ; le fichier reste toujours un JSON valide."""
    p = str(tmp_path / "concurrent.json")
    safe_json_write(p, {"writers": 0})

    errors = []

    def _worker(n):
        try:
            for _ in range(20):
                safe_json_write(p, {"writer": n, "payload": list(range(50))})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # Le fichier final doit être un JSON valide et complet
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    assert data["payload"] == list(range(50))


_CHILD_SCRIPT = r"""
import sys, os
sys.path.insert(0, {root!r})
from core.safe_io import safe_json_write
path = sys.argv[1]
writer_id = int(sys.argv[2])
for _ in range(40):
    safe_json_write(path, {{"writer": writer_id, "payload": list(range(100))}})
print("OK")
"""


def test_concurrent_processes_no_corruption(tmp_path):
    """[P1-2.3] Critère d'acceptation : 2 process écrivent en parallèle sans
    corrompre le fichier (toujours un JSON valide à la fin)."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = str(tmp_path / "multiproc.json")
    safe_json_write(p, {"init": True})

    script = _CHILD_SCRIPT.format(root=root)
    procs = [
        subprocess.Popen([sys.executable, "-c", script, p, str(i)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(2)
    ]
    outs = [proc.communicate(timeout=60) for proc in procs]

    for code, (out, err) in zip([pr.returncode for pr in procs], outs):
        assert code == 0, f"process échoué : {err}"

    # Le fichier final est un JSON valide et complet (jamais tronqué)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    assert data["payload"] == list(range(100))


def test_file_lock_is_mutually_exclusive(tmp_path):
    """file_lock sérialise réellement les sections critiques (pas de chevauchement)."""
    p = str(tmp_path / "lock_target")
    overlap = {"count": 0, "max": 0}
    lock_py = threading.Lock()

    def _worker():
        for _ in range(30):
            with file_lock(p):
                with lock_py:
                    overlap["count"] += 1
                    overlap["max"] = max(overlap["max"], overlap["count"])
                # Pendant qu'on tient le FileLock, personne d'autre ne doit être ici
                assert overlap["count"] == 1
                with lock_py:
                    overlap["count"] -= 1

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlap["max"] == 1
