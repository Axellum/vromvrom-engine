"""
core/vocal_audit.py — Audit des requêtes vocales Tab5/Assist (STT → moteur).

Persiste chaque appel /api/execute avec source voice/tab5 dans vocal_audit_log
(moteur_runtime.db) pour corréler transcription STT, routing et réponse TTS.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_VOCAL_SOURCE_TYPES = frozenset({"voice", "tab5"})


def is_vocal_request(source_type: str) -> bool:
    return source_type in _VOCAL_SOURCE_TYPES


def log_vocal_request(
    *,
    session_id: str,
    user_prompt: str,
    source_type: str,
    source_mode: str,
    tts_enabled: bool,
    device_id: str | None = None,
) -> None:
    """Enregistre la requête entrante (avant routage)."""
    if not is_vocal_request(source_type):
        return
    _insert_row(
        session_id=session_id,
        user_prompt=user_prompt,
        source_type=source_type,
        source_mode=source_mode,
        tts_enabled=tts_enabled,
        device_id=device_id,
        routing_type=None,
        agents_used=None,
        response_text=None,
        latency_ms=None,
        phase="request",
    )
    logger.info(
        "[VOCAL_AUDIT] request session=%s mode=%s prompt=%r",
        session_id,
        source_mode,
        user_prompt[:120],
    )


def log_vocal_response(
    *,
    session_id: str,
    user_prompt: str,
    source_type: str,
    source_mode: str,
    routing_type: str | None,
    agents_used: list[str] | None,
    response_text: str | None,
    latency_ms: float | None,
    tts_enabled: bool = False,
    device_id: str | None = None,
) -> None:
    """Enregistre la réponse finale (après routage)."""
    if not is_vocal_request(source_type):
        return
    _insert_row(
        session_id=session_id,
        user_prompt=user_prompt,
        source_type=source_type,
        source_mode=source_mode,
        tts_enabled=tts_enabled,
        device_id=device_id,
        routing_type=routing_type,
        agents_used=",".join(agents_used or []),
        response_text=(response_text or "")[:500],
        latency_ms=latency_ms,
        phase="response",
    )
    logger.info(
        "[VOCAL_AUDIT] response session=%s routing=%s agents=%s latency=%.0fms response=%r",
        session_id,
        routing_type,
        agents_used,
        latency_ms or 0,
        (response_text or "")[:80],
    )


def _insert_row(**fields: Any) -> None:
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO vocal_audit_log (
                    session_id, user_prompt, source_type, source_mode, tts_enabled,
                    device_id, routing_type, agents_used, response_text, latency_ms, phase
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields.get("session_id"),
                    fields.get("user_prompt", "")[:1000],
                    fields.get("source_type"),
                    fields.get("source_mode"),
                    1 if fields.get("tts_enabled") else 0,
                    fields.get("device_id"),
                    fields.get("routing_type"),
                    fields.get("agents_used"),
                    fields.get("response_text"),
                    fields.get("latency_ms"),
                    fields.get("phase", "response"),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("[VOCAL_AUDIT] Persistance ignorée : %s", exc)


def get_recent_vocal_logs(limit: int = 50) -> list[dict]:
    """Retourne les derniers événements vocaux (diagnostic STT/moteur)."""
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            conn.row_factory = _dict_row_factory
            rows = conn.execute(
                """
                SELECT id, created_at, session_id, user_prompt, source_type, source_mode,
                       routing_type, agents_used, response_text, latency_ms, phase
                FROM vocal_audit_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return list(rows)
    except Exception as exc:
        logger.warning("[VOCAL_AUDIT] Lecture impossible : %s", exc)
        return []


def _dict_row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class VocalAuditTimer:
    """Context manager léger pour mesurer la latence d'un /api/execute vocal.

    #T270 — `elapsed_ms` est une PROPRIÉTÉ, pas un attribut posé à la sortie.

    Auparavant la valeur n'était calculée que dans `__exit__`, or les 14 appels à
    `log_vocal_response(..., latency_ms=timer.elapsed_ms)` la lisent **à l'intérieur**
    du bloc `with` — donc avant `__exit__`. Ils enregistraient tous `0.0`. Mesuré en
    prod le 10/08/2026 : **394 réponses à `latency_ms = 0`** contre 26 valeurs réelles,
    et zéro NULL. Conséquence en cascade : `/api/vocal/stats` filtrait `IS NOT NULL`,
    laissait donc passer les 394 zéros, et annonçait **123 ms** de latence moyenne
    là où la vraie valeur est **1991 ms** — un facteur 16, dans le sens flatteur.

    En propriété, lire pendant le bloc rend le temps écoulé à cet instant, et lire
    après rend le total figé à la sortie. Les appels existants deviennent corrects
    sans être modifiés, et un futur chemin de routage ne peut plus se tromper —
    c'est le point : la mesure ne doit pas dépendre de l'endroit où on la lit.
    """

    def __init__(self):
        self._t0 = 0.0
        self._fige: float | None = None

    def __enter__(self):
        self._t0 = time.perf_counter()
        self._fige = None
        return self

    def __exit__(self, *args):
        self._fige = (time.perf_counter() - self._t0) * 1000.0

    @property
    def elapsed_ms(self) -> float:
        if self._fige is not None:
            return self._fige
        if not self._t0:
            # Timer jamais démarré (instancié hors d'un `with`) : pas de mesure.
            return 0.0
        return (time.perf_counter() - self._t0) * 1000.0
