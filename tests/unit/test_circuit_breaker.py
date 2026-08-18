"""
tests/unit/test_circuit_breaker.py — Tests de non-régression du Circuit Breaker.

Couvre notamment le bug C1 (audit V12) : le registre global doit exposer
`_registry`, `_registry_lock` et `to_dict()`, attendus par
core/llm_gateway.get_circuit_breakers_status().

Couvre aussi [#T352] : le délai de réouverture progressif (30 s, 1 min, 2 min…,
plafonné) et sa remise à la base après un succès en HALF_OPEN. Tous les tests
de ce lot utilisent un temps INJECTÉ (monkeypatch de time.time) — aucun sleep.
"""

import logging

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
    from core.llm_gateway import LIVE_LATENCY_MAX_PENALTY, get_live_latency_penalty

    very_slow_cb = CircuitBreaker.get_or_create("routing-latency-model-very-slow")
    very_slow_cb.record_success(latency=60.0)  # 60s, extrême

    assert get_live_latency_penalty("routing-latency-model-very-slow") == LIVE_LATENCY_MAX_PENALTY


# ──────────────────────────────────────────────────────────────────
# [#T352] Délai de réouverture progressif
# ──────────────────────────────────────────────────────────────────

def _horloge_injectee(monkeypatch, start: float = 1000.0):
    """Remplace time.time par une horloge contrôlée (liste mutable à incrémenter)."""
    fake_time = [start]
    monkeypatch.setattr("core.llm.circuit_breaker.time.time", lambda: fake_time[0])
    return fake_time


def test_delai_reouverture_croit_entre_ouvertures_consecutives(monkeypatch):
    """TEST CENTRAL : un service qui échoue en boucle voit son délai CROÎTRE d'une ouverture à l'autre.

    Au moins trois paliers vérifiés (30 s → 1 min → 2 min → 4 min). Les ré-ouvertures
    passent par la transition réelle OPEN → HALF_OPEN (délai écoulé) → échec → OPEN,
    pilotée par le temps injecté.
    """
    fake_time = _horloge_injectee(monkeypatch)
    cb = CircuitBreaker(name="t352-progressif", failure_threshold=1, recovery_timeout=30.0)

    # 1re ouverture (depuis CLOSED) : base inchangée
    cb.record_failure(Exception("boom"))
    assert cb.recovery_timeout == 30.0
    assert cb._consecutive_trips == 1

    # 2e ouverture consécutive : délai écoulé → HALF_OPEN → échec → ré-ouverture → 1 min
    fake_time[0] += 31.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_failure(Exception("boom"))
    assert cb.recovery_timeout == 60.0

    # 3e ouverture consécutive : 2 min
    fake_time[0] += 61.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_failure(Exception("boom"))
    assert cb.recovery_timeout == 120.0

    # 4e ouverture consécutive : 4 min
    fake_time[0] += 121.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_failure(Exception("boom"))
    assert cb.recovery_timeout == 240.0


def test_delai_reouverture_plafonne_sans_depasser_le_max():
    """Le délai croissant est plafonné : il ne dépasse jamais max_recovery_timeout."""
    cb = CircuitBreaker(
        name="t352-plafond",
        failure_threshold=1,
        recovery_timeout=30.0,
        max_recovery_timeout=180.0,
    )
    # 30, 60, 120, puis plafonné à 180 à partir de la 4e ouverture
    for _ in range(10):
        cb.record_failure(Exception("boom"))
        cb._state = CircuitBreakerState.HALF_OPEN
    assert cb._consecutive_trips == 10
    assert cb.recovery_timeout == 180.0


def test_succes_en_half_open_remet_le_delai_a_la_base(monkeypatch):
    """TEST JUMEAU : un succès en HALF_OPEN remet le délai à sa valeur de base.

    Sans cela, un incident passager pénaliserait un provider sain pendant une
    demi-heure — pire que le défaut d'origine.
    """
    fake_time = _horloge_injectee(monkeypatch)
    cb = CircuitBreaker(name="t352-jumeau", failure_threshold=1, recovery_timeout=30.0)

    # Faire croître le délai jusqu'à 2 min (3 ouvertures consécutives)
    cb.record_failure(Exception("boom"))
    fake_time[0] += 31.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_failure(Exception("boom"))
    fake_time[0] += 61.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_failure(Exception("boom"))
    assert cb.recovery_timeout == 120.0
    assert cb._consecutive_trips == 3

    # Succès en HALF_OPEN → remise à la base et fermeture
    fake_time[0] += 121.0
    assert cb.state == CircuitBreakerState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.recovery_timeout == 30.0
    assert cb._consecutive_trips == 0


def test_premiere_ouverture_inchangee_pour_appelant_sans_params():
    """La première ouverture reste à 30 s pour un appelant qui ne passe que name.

    Rétro-compatibilité : `get_or_create(name)` seul doit conserver exactement le
    comportement d'aujourd'hui (seuil 3, délai de base 30 s).
    """
    cb = CircuitBreaker.get_or_create("t352-premiere-ouverture")
    cb.record_failure(Exception("boom"))
    cb.record_failure(Exception("boom"))
    cb.record_failure(Exception("boom"))
    assert cb.state == CircuitBreakerState.OPEN
    assert cb.recovery_timeout == 30.0
    assert cb._consecutive_trips == 1


def test_delai_retenu_journalise_a_chaque_ouverture(caplog):
    """Le délai retenu est journalisé à chaque ouverture (vérifiable en prod)."""
    cb = CircuitBreaker(name="t352-log", failure_threshold=1, recovery_timeout=30.0)
    with caplog.at_level(logging.ERROR, logger="core.llm.circuit_breaker"):
        cb.record_failure(Exception("boom"))
    assert "pour 30s" in caplog.text
    assert "ouverture consécutive n°1" in caplog.text


def test_get_stats_expose_la_progression():
    """get_stats() expose les nouveaux champs de progression pour l'observabilité."""
    cb = CircuitBreaker(name="t352-stats", failure_threshold=1, recovery_timeout=30.0)
    cb.record_failure(Exception("boom"))
    stats = cb.get_stats()
    assert stats["recovery_timeout"] == 30.0
    assert stats["base_recovery_timeout"] == 30.0
    assert stats["max_recovery_timeout"] == CircuitBreaker.DEFAULT_MAX_RECOVERY_TIMEOUT
    assert stats["consecutive_trips"] == 1
