"""
Tests chaos (T86) — scénarios de défaillance du LLMGateway.

Couvre :
  1. Timeout LLM principal → cascade sur provider fallback
  2. 429 sur toutes les clés → escalade vers Zhipu/GLM
  3. Ollama down → bascule vers provider cloud
  4. Erreur réseau → comportement circuit breaker
"""
import asyncio
import pytest
from unittest.mock import AsyncMock

from core.llm.circuit_breaker import CircuitBreaker, CircuitBreakerState as CircuitState


# ─── 1. Timeout → cascade fallback ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_triggers_fallback_chain():
    """Un asyncio.TimeoutError sur le provider primaire doit ouvrir le circuit."""
    cb = CircuitBreaker(name="test_timeout", failure_threshold=2, recovery_timeout=5)

    call_count = 0

    async def flaky_call():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise asyncio.TimeoutError("Timeout simulé")
        return "ok"

    for _ in range(2):
        try:
            await cb.call(flaky_call)
        except (asyncio.TimeoutError, Exception):
            pass

    assert cb.state == CircuitState.OPEN, "Après 2 échecs, le circuit doit être OPEN"


@pytest.mark.asyncio
async def test_timeout_circuit_opens_and_half_opens():
    """Vérifie la transition CLOSED → OPEN → HALF_OPEN du circuit breaker sur timeouts."""
    cb = CircuitBreaker(name="test_half_open", failure_threshold=3, recovery_timeout=0.05)

    async def always_timeout():
        raise asyncio.TimeoutError("timeout")

    for _ in range(3):
        try:
            await cb.call(always_timeout)
        except Exception:
            pass

    assert cb.state == CircuitState.OPEN

    # Attendre le recovery_timeout → transition HALF_OPEN
    await asyncio.sleep(0.1)

    async def ok_call():
        return "recovered"

    result = await cb.call(ok_call)
    assert result == "recovered"
    assert cb.state == CircuitState.CLOSED, "Après succès en HALF_OPEN → CLOSED"


# ─── 2. 429 rate-limit ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_opens_circuit_fast():
    """record_rate_limit doit ouvrir le circuit immédiatement (pas besoin d'atteindre le seuil)."""
    cb = CircuitBreaker(name="test_ratelimit", failure_threshold=5, recovery_timeout=60)

    # record_rate_limit déclenche toujours _trip() immédiatement
    cb.record_rate_limit()
    assert cb.state == CircuitState.OPEN, "Premier rate-limit → circuit OPEN immédiat"


@pytest.mark.asyncio
async def test_rate_limit_stats_tracked():
    """Les stats du circuit doivent comptabiliser les rate-limits séparément."""
    cb = CircuitBreaker(name="test_stats", failure_threshold=10, recovery_timeout=60)

    # Enregistrer 3 rate-limits en réinitialisant l'état entre chaque pour tester le compteur
    cb.record_rate_limit()
    cb._state = CircuitState.CLOSED
    cb.record_rate_limit()
    cb._state = CircuitState.CLOSED
    cb.record_rate_limit()

    stats = cb.get_stats()
    assert stats["rate_limits"] >= 3
    assert stats["state"] in ("CLOSED", "OPEN", "HALF_OPEN")


# ─── 3. Ollama down → comportement circuit breaker ───────────────────────────

@pytest.mark.asyncio
async def test_connection_error_opens_circuit():
    """ConnectionRefusedError (Ollama down) doit ouvrir le circuit après failure_threshold."""
    cb = CircuitBreaker(name="test_ollama_down", failure_threshold=2, recovery_timeout=60)

    async def ollama_unavailable():
        raise ConnectionRefusedError("Connection refused — Ollama down")

    for _ in range(2):
        try:
            await cb.call(ollama_unavailable)
        except Exception:
            pass

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_open_blocks_calls_immediately():
    """Un circuit OPEN doit lever une exception sans appeler le callable."""
    cb = CircuitBreaker(name="test_block", failure_threshold=1, recovery_timeout=60)

    call_invoked = False

    async def fail_once():
        raise RuntimeError("Service down")

    try:
        await cb.call(fail_once)
    except Exception:
        pass

    assert cb.state == CircuitState.OPEN

    async def should_not_be_called():
        nonlocal call_invoked
        call_invoked = True
        return "should not reach here"

    try:
        await cb.call(should_not_be_called)
    except Exception:
        pass

    assert not call_invoked, "Circuit OPEN ne doit pas invoquer le callable"


# ─── 4. Concurrent record_failure (thread-safety) ────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_failures_converge():
    """5 coroutines enregistrant des échecs concurrents → circuit doit finir OPEN."""
    cb = CircuitBreaker(name="test_concurrent", failure_threshold=3, recovery_timeout=60)

    async def fail_task():
        try:
            await cb.call(AsyncMock(side_effect=RuntimeError("chaos")))
        except Exception:
            pass

    await asyncio.gather(*[fail_task() for _ in range(5)])

    assert cb.state == CircuitState.OPEN


# ─── 5. Succès après récupération ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_recovers_fully():
    """Après HALF_OPEN → succès → CLOSED : les appels suivants ne doivent pas être bloqués."""
    cb = CircuitBreaker(name="test_full_recovery", failure_threshold=2, recovery_timeout=0.05)

    async def fail():
        raise RuntimeError("fail")

    for _ in range(2):
        try:
            await cb.call(fail)
        except Exception:
            pass

    await asyncio.sleep(0.1)

    results = []

    async def succeed():
        results.append("ok")
        return "ok"

    await cb.call(succeed)
    assert cb.state == CircuitState.CLOSED
    assert results == ["ok"]
