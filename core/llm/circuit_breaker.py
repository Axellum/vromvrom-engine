"""
core/llm/circuit_breaker.py — Circuit Breaker pour la résilience des appels LLM.

Gère 3 états : CLOSED, OPEN, HALF_OPEN.
Permet d'intercepter les pannes API (HTTP 429, timeouts, 5xx) et de basculer
automatiquement sur les replis sans encombrer les files d'attente.

Auteur : Antigravity IDE
Date : 2026-06-16
"""

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Exception levée lorsque le disjoncteur est OUVERT et bloque l'accès."""
    pass


class CircuitBreaker:
    """
    Implémentation du pattern Circuit Breaker compatible avec la V12 et rétrocompatible
    avec les appels existants dans llm_gateway.py.
    """
    # Registre global des disjoncteurs. Les noms canoniques (_registry,
    # _registry_lock) sont ceux attendus par core/llm_gateway.get_circuit_breakers_status().
    # _registry_lock est un verrou SYNCHRONE (threading.Lock) car il est utilisé dans des
    # contextes `with` synchrones (et get_or_create est appelé depuis du code synchrone).
    _registry: dict[str, "CircuitBreaker"] = {}
    _registry_lock = threading.Lock()
    # Alias rétro-compatibles (anciens noms internes V12).
    _instances = _registry
    _global_lock = _registry_lock

    # [#T352] Délai de réouverture maximal (30 min). Au-delà, le service est
    # durablement indisponible : on ne pousse pas plus loin pour ne pas rendre un
    # service rétabli inaccessible trop longtemps (voir PR #T352 pour la justification).
    DEFAULT_MAX_RECOVERY_TIMEOUT: float = 1800.0

    @classmethod
    def get_or_create(cls, name: str, failure_threshold: int = 3, recovery_timeout: float = 30.0) -> "CircuitBreaker":
        """Récupère ou crée une instance de disjoncteur pour un modèle donné (thread-safe)."""
        with cls._registry_lock:
            if name not in cls._registry:
                cls._registry[name] = cls(name, failure_threshold, recovery_timeout)
            return cls._registry[name]

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        max_recovery_timeout: float | None = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold

        # [#T352] `recovery_timeout` reste le délai EFFECTIF courant (rétro-compatible :
        # première ouverture = valeur de base passée par l'appelant). `_base_recovery_timeout`
        # conserve la valeur d'origine pour recalculer la progression et se remettre à zéro
        # après un succès en HALF_OPEN.
        self._base_recovery_timeout = recovery_timeout
        self.recovery_timeout = recovery_timeout
        self._max_recovery_timeout = (
            max_recovery_timeout if max_recovery_timeout is not None else self.DEFAULT_MAX_RECOVERY_TIMEOUT
        )

        # [#T352] Nombre d'ouvertures CONSÉCUTIVES sans succès entre-temps. Chaque nouvelle
        # ouverture double le délai de réouverture (30 s, 1 min, 2 min…) jusqu'au plafond.
        # Un succès en HALF_OPEN remet ce compteur à zéro.
        self._consecutive_trips = 0

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_state_change = time.time()

        # Statistiques
        self.total_calls = 0
        self.total_failures = 0
        self.total_trips = 0
        self.total_rate_limits = 0

        # [#T118] Latence live par provider (moyenne mobile exponentielle, ms).
        # Alimente `get_model_routing_score()` en complément du `cascade_priority`
        # statique — pattern `lowest-latency` LiteLLM.
        self.avg_latency_ms: float | None = None
        self.last_latency_ms: float | None = None
        self._LATENCY_EMA_ALPHA = 0.3

    @property
    def state(self) -> CircuitBreakerState:
        """Retourne l'état actuel avec vérification automatique du timeout de récupération."""
        if self._state == CircuitBreakerState.OPEN and (time.time() - self._last_state_change) > self.recovery_timeout:
            # Transition implicite vers HALF_OPEN
            self._state = CircuitBreakerState.HALF_OPEN
            self._last_state_change = time.time()
            logger.info(f"[CIRCUIT BREAKER] {self.name} est passé de OPEN à HALF_OPEN (timeout de récupération expiré)")
        return self._state

    def is_open(self) -> bool:
        """Retourne True si le disjoncteur est dans l'état OPEN."""
        return self.state == CircuitBreakerState.OPEN

    def record_success(self, latency: float = 0.0) -> None:
        """Enregistre un succès et referme le disjoncteur si nécessaire.

        [#T118] `latency` (secondes) alimente une moyenne mobile exponentielle
        (`avg_latency_ms`) utilisée comme signal de latence live par le routeur.
        """
        self.total_calls += 1
        if latency and latency > 0:
            latency_ms = latency * 1000.0
            self.last_latency_ms = latency_ms
            if self.avg_latency_ms is None:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = (
                    self._LATENCY_EMA_ALPHA * latency_ms
                    + (1 - self._LATENCY_EMA_ALPHA) * self.avg_latency_ms
                )
        if self._state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"[CIRCUIT BREAKER] {self.name} refermé (CLOSED) suite à un succès en HALF_OPEN")
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            # [#T352] Un succès en HALF_OPEN prouve que le service est rétabli :
            # on remet le délai de réouverture à sa valeur de base.
            self._consecutive_trips = 0
            self.recovery_timeout = self._base_recovery_timeout
            self._last_state_change = time.time()
        elif self._state == CircuitBreakerState.CLOSED:
            self._failure_count = 0

    def record_failure(self, exception: Exception = None) -> None:
        """Enregistre un échec et ouvre le disjoncteur si le seuil est dépassé."""
        self.total_failures += 1
        self._failure_count += 1
        logger.warning(
            f"[CIRCUIT BREAKER] Échec détecté sur {self.name} "
            f"({self._failure_count}/{self.failure_threshold}) : {exception}"
        )

        if self._state == CircuitBreakerState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._trip()
        elif self._state == CircuitBreakerState.HALF_OPEN:
            # En HALF_OPEN, le moindre échec ré-ouvre immédiatement le circuit
            self._trip()

    def record_rate_limit(self) -> None:
        """Enregistre une erreur 429 Rate Limit (ouvre le circuit immédiatement)."""
        self.total_failures += 1
        self.total_rate_limits += 1
        self._failure_count = max(self._failure_count + 1, self.failure_threshold)
        logger.warning(f"[CIRCUIT BREAKER] Rate limit (429) détecté sur {self.name}. Disjoncteur déclenché.")
        self._trip()

    def _compute_recovery_timeout(self) -> float:
        """Délai de réouverture courant selon le nombre d'ouvertures consécutives.

        [#T352] Progression géométrique : première ouverture = valeur de base,
        puis double à chaque nouvelle ouverture consécutive, plafonnée à
        `_max_recovery_timeout`. Un service durablement absent voit donc son délai
        croître au lieu de payer un timeout fixe toutes les 30 secondes.
        """
        if self._consecutive_trips <= 1:
            return self._base_recovery_timeout
        exponent = self._consecutive_trips - 1
        return min(
            self._base_recovery_timeout * (2 ** exponent),
            self._max_recovery_timeout,
        )

    def _trip(self) -> None:
        """Ouvre le disjoncteur (transition vers OPEN).

        [#T352] À chaque nouvelle ouverture consécutive, le délai de réouverture
        augmente (30 s, 1 min, 2 min…) jusqu'au plafond. Le délai retenu est
        journalisé à chaque ouverture pour pouvoir vérifier le mécanisme en prod.
        """
        self._consecutive_trips += 1
        self.recovery_timeout = self._compute_recovery_timeout()
        self._state = CircuitBreakerState.OPEN
        self._last_state_change = time.time()
        self.total_trips += 1
        logger.error(
            f"[CIRCUIT BREAKER] 🚨 {self.name} a DISJONCTÉ (état OPEN) pour {self.recovery_timeout:.0f}s "
            f"(ouverture consécutive n°{self._consecutive_trips}). "
            f"Seuil d'échecs ({self.failure_threshold}) dépassé."
        )

    async def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        Exécute la fonction asynchrone func sous la protection du disjoncteur.
        """
        if self.is_open():
            self.total_failures += 1
            raise CircuitBreakerOpenException(
                f"Le disjoncteur {self.name} est OUVERT. Appel bloqué. "
                f"Récupération dans {self.recovery_timeout - (time.time() - self._last_state_change):.1f}s"
            )

        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure(e)
            raise e

    def get_stats(self) -> dict[str, Any]:
        """Retourne les métriques de santé du disjoncteur."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "base_recovery_timeout": self._base_recovery_timeout,
            "max_recovery_timeout": self._max_recovery_timeout,
            "consecutive_trips": self._consecutive_trips,
            "time_since_last_change": round(time.time() - self._last_state_change, 1),
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_trips": self.total_trips,
            "rate_limits": self.total_rate_limits,
            "avg_latency_ms": round(self.avg_latency_ms, 1) if self.avg_latency_ms is not None else None,
            "last_latency_ms": round(self.last_latency_ms, 1) if self.last_latency_ms is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """Alias attendu par llm_gateway.get_circuit_breakers_status()."""
        return self.get_stats()
