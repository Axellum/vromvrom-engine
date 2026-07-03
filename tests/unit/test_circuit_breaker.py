"""
tests/unit/test_circuit_breaker.py — Tests de non-régression du Circuit Breaker.

Couvre notamment le bug C1 (audit V12) : le registre global doit exposer
`_registry`, `_registry_lock` et `to_dict()`, attendus par
core/llm_gateway.get_circuit_breakers_status().
"""

from core.llm.circuit_breaker import CircuitBreaker, CircuitBreakerState


def test_registry_attributes_exist():
    """Les attributs canoniques attendus par llm_gateway existent."""
    assert hasattr(CircuitBreaker, "_registry")
    assert hasattr(CircuitBreaker, "_registry_lock")
    # Les anciens alias restent disponibles pour rétro-compatibilité.
    assert CircuitBreaker._instances is CircuitBreaker._registry
    assert CircuitBreaker._global_lock is CircuitBreaker._registry_lock


def test_get_or_create_registers_instance():
    """get_or_create enregistre l'instance dans _registry et est idempotent."""
    cb = CircuitBreaker.get_or_create("test-model-c1")
    assert CircuitBreaker._registry["test-model-c1"] is cb
    # Idempotence : un second appel retourne la même instance.
    assert CircuitBreaker.get_or_create("test-model-c1") is cb


def test_to_dict_alias():
    """to_dict() existe et reflète get_stats()."""
    cb = CircuitBreaker.get_or_create("test-model-c1-dict")
    d = cb.to_dict()
    assert d == cb.get_stats()
    assert d["name"] == "test-model-c1-dict"
    assert d["state"] == CircuitBreakerState.CLOSED.value


def test_registry_iteration_with_lock():
    """Reproduit le pattern exact de get_circuit_breakers_status (with lock + to_dict)."""
    CircuitBreaker.get_or_create("iter-model")
    status = {}
    with CircuitBreaker._registry_lock:
        for name, cb in CircuitBreaker._registry.items():
            status[name] = cb.to_dict()
    assert "iter-model" in status


def test_trip_after_threshold():
    """Le disjoncteur s'ouvre après avoir dépassé le seuil d'échecs."""
    cb = CircuitBreaker.get_or_create("trip-model", failure_threshold=2)
    assert not cb.is_open()
    cb.record_failure(Exception("boom"))
    cb.record_failure(Exception("boom"))
    assert cb.is_open()
