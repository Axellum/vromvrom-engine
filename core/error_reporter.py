"""
core/error_reporter.py — Reporting centralisé des exceptions « avalées » (Phase 1, #10).

L'audit V12 a relevé que des `except Exception: pass` silencieux masquent de vrais
bugs (cf. C1, resté invisible des mois). Ce module fournit un point unique pour
journaliser de façon COHÉRENTE les exceptions que l'on choisit d'ignorer dans les
chemins best-effort, sans changer le flux d'exécution.

Usage :
    from core.error_reporter import report_swallowed
    try:
        truc_optionnel()
    except Exception as e:
        report_swallowed("engine.checkpoint_final", e, level="debug")

Les erreurs sont aussi conservées dans un ring buffer borné, interrogeable par
l'IHM/diagnostic via get_recent_errors().
"""

from __future__ import annotations

import logging
import time
import traceback
from collections import deque
from threading import Lock
from typing import Deque, Dict, List

logger = logging.getLogger("moteur.swallowed")

# Ring buffer borné des dernières erreurs avalées (diagnostic).
_MAX_RECENT = 200
_recent: Deque[Dict] = deque(maxlen=_MAX_RECENT)
_lock = Lock()

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def report_swallowed(context: str, exc: BaseException, *, level: str = "warning") -> None:
    """
    Journalise une exception ignorée de façon cohérente + l'enregistre dans le buffer.

    Args:
        context: identifiant court du site (ex: "router.read_rules", "engine.langfuse_span").
        exc:     l'exception capturée.
        level:   niveau de log ("debug" pour le best-effort vraiment anodin, "warning"
                 pour ce qui devrait rarement échouer, "error" pour les anomalies).
    """
    lvl = _LEVELS.get(level, logging.WARNING)
    exc_type = type(exc).__name__
    logger.log(lvl, "[SWALLOWED] %s: %s: %s", context, exc_type, exc)
    # Trace complète seulement en DEBUG pour ne pas polluer les logs.
    logger.debug("[SWALLOWED] %s — traceback:\n%s", context, traceback.format_exc())

    with _lock:
        _recent.append({
            "ts": time.time(),
            "context": context,
            "type": exc_type,
            "message": str(exc)[:500],
            "level": level,
        })


def get_recent_errors(limit: int = 50) -> List[Dict]:
    """Retourne les dernières erreurs avalées (plus récentes d'abord)."""
    with _lock:
        items = list(_recent)
    return list(reversed(items))[:limit]


def clear_recent() -> None:
    """Vide le buffer (tests / reset diagnostic)."""
    with _lock:
        _recent.clear()
