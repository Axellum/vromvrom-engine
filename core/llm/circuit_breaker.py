"""
core/llm/circuit_breaker.py — Circuit Breaker pour la résilience des appels LLM.

Gère 3 états : CLOSED, OPEN, HALF_OPEN.
Permet d'intercepter les pannes API (HTTP 429, timeouts, 5xx) et de basculer
automatiquement sur les replis sans encombrer les files d'attente.

Auteur : Antigravity IDE
Date : 2026-06-16
"""

import threading
import time
import logging
from typing import Callable, Any, Dict
from enum import Enum

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
    _registry: Dict[str, "CircuitBreaker"] = {}
    _registry_lock = threading.Lock()
    # Alias rétro-compatibles (anciens noms internes V12).
    _instances = _registry
    _global_lock = _registry_lock

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
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_state_change = time.time()

        # Statistiques
        self.total_calls = 0
        self.total_failures = 0
        self.total_trips = 0
        self.total_rate_limits = 0

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
        """Enregistre un succès et referme le disjoncteur si nécessaire."""
        self.total_calls += 1
        if self._state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"[CIRCUIT BREAKER] {self.name} refermé (CLOSED) suite à un succès en HALF_OPEN")
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
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

    def _trip(self) -> None:
        """Ouvre le disjoncteur (transition vers OPEN)."""
        self._state = CircuitBreakerState.OPEN
        self._last_state_change = time.time()
        self.total_trips += 1
        logger.error(
            f"[CIRCUIT BREAKER] 🚨 {self.name} a DISJONCTÉ (état OPEN) pour {self.recovery_timeout}s. "
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

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les métriques de santé du disjoncteur."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "time_since_last_change": round(time.time() - self._last_state_change, 1),
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_trips": self.total_trips,
            "rate_limits": self.total_rate_limits,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Alias attendu par llm_gateway.get_circuit_breakers_status()."""
        return self.get_stats()
