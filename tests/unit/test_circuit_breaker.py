"""
tests/unit/test_circuit_breaker.py — Tests de non-régression du Circuit Breaker.

Couvre notamment le bug C1 (audit V12) : le registre global doit exposer
`_registry`, `_registry_lock` et `to_dict()`, attendus par
core/llm_gateway.get_circuit_breakers_status().
"""

import pytest

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


# ──────────────────────────────────────────────────────────────────
# [#T118] Latence live (moyenne mobile exponentielle par provider)
# ──────────────────────────────────────────────────────────────────

def test_avg_latency_none_before_any_success():
    """Aucune latence enregistrée avant le premier succès mesuré."""
    cb = CircuitBreaker.get_or_create("latency-model-fresh")
    assert cb.avg_latency_ms is None
    assert cb.last_latency_ms is None


def test_record_success_sets_initial_latency():
    """Le premier succès avec latence initialise avg_latency_ms directement."""
    cb = CircuitBreaker.get_or_create("latency-model-init")
    cb.record_success(latency=0.5)  # 500ms
    assert cb.last_latency_ms == 500.0
    assert cb.avg_latency_ms == 500.0


def test_record_success_without_latency_does_not_update_avg():
    """record_success() sans latence (ex: via call()) ne doit pas toucher avg_latency_ms."""
    cb = CircuitBreaker.get_or_create("latency-model-noop")
    cb.record_success()  # latency=0.0 par défaut
    assert cb.avg_latency_ms is None


def test_avg_latency_converges_with_ema():
    """Des latences répétées font converger la moyenne mobile vers la valeur stable."""
    cb = CircuitBreaker.get_or_create("latency-model-ema")
    for _ in range(20):
        cb.record_success(latency=2.0)  # 2000ms constant
    assert cb.avg_latency_ms == pytest.approx(2000.0, abs=1.0)


def test_avg_latency_reacts_to_spike():
    """Un pic de latence doit faire monter la moyenne sans l'égaler immédiatement (lissage EMA)."""
    cb = CircuitBreaker.get_or_create("latency-model-spike")
    for _ in range(10):
        cb.record_success(latency=0.2)  # baseline rapide (200ms)
    baseline = cb.avg_latency_ms
    cb.record_success(latency=5.0)  # pic (5000ms)
    assert cb.avg_latency_ms > baseline
    assert cb.avg_latency_ms < 5000.0  # lissé, pas un saut brutal


def test_to_dict_exposes_latency_fields():
    """to_dict()/get_stats() exposent avg_latency_ms et last_latency_ms pour le dashboard."""
    cb = CircuitBreaker.get_or_create("latency-model-dict")
    cb.record_success(latency=1.234)
    d = cb.to_dict()
    assert d["last_latency_ms"] == 1234.0
    assert d["avg_latency_ms"] == 1234.0


# ──────────────────────────────────────────────────────────────────
# [#T118] get_live_latency_penalty — intégration dans le score de routage
# ──────────────────────────────────────────────────────────────────

def test_live_latency_penalty_zero_without_measurement():
    """Pas de pénalité tant qu'aucune latence n'a été mesurée pour ce modèle."""
    from core.llm_gateway import get_live_latency_penalty
    assert get_live_latency_penalty("routing-latency-model-unmeasured") == 0.0


def test_live_latency_penalty_scales_with_latency():
    """Un modèle plus lent doit recevoir une pénalité plus élevée qu'un modèle rapide."""
    from core.llm_gateway import get_live_latency_penalty

    fast_cb = CircuitBreaker.get_or_create("routing-latency-model-fast")
    fast_cb.record_success(latency=0.2)  # 200ms

    slow_cb = CircuitBreaker.get_or_create("routing-latency-model-slow")
    slow_cb.record_success(latency=4.0)  # 4000ms

    fast_penalty = get_live_latency_penalty("routing-latency-model-fast")
    slow_penalty = get_live_latency_penalty("routing-latency-model-slow")

    assert fast_penalty > 0.0
    assert slow_penalty > fast_penalty


def test_live_latency_penalty_is_capped():
    """La pénalité doit rester plafonnée (tie-breaker, pas un facteur dominant)."""
    from core.llm_gateway import get_live_latency_penalty, LIVE_LATENCY_MAX_PENALTY

    very_slow_cb = CircuitBreaker.get_or_create("routing-latency-model-very-slow")
    very_slow_cb.record_success(latency=60.0)  # 60s, extrême

    assert get_live_latency_penalty("routing-latency-model-very-slow") == LIVE_LATENCY_MAX_PENALTY
