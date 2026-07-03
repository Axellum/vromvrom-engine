"""Tests de FallbackProvider.generate_async / generate_structured_async (D5).

Vérifie que la variante async du FallbackProvider :
- appelle provider.generate_async() (pas la version sync)
- cascades sur le provider suivant en cas d'échec
- applique le circuit breaker (CB ouvert → skip)
- retourne le résultat du premier provider disponible
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm.providers.deepseek import FallbackProvider
from core.llm.circuit_breaker import CircuitBreaker


def _make_provider(response="ok", fail=False):
    p = MagicMock()
    if fail:
        p.generate_async = AsyncMock(side_effect=RuntimeError("boom"))
        p.generate_structured_async = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        p.generate_async = AsyncMock(return_value=response)
        p.generate_structured_async = AsyncMock(return_value={"result": response})
    return p


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    CircuitBreaker._registry.clear()
    yield
    CircuitBreaker._registry.clear()


@pytest.mark.asyncio
async def test_generate_async_retourne_premier_succes():
    p = _make_provider("Voici une réponse suffisamment longue pour passer le seuil.")
    fb = FallbackProvider([("modele-a", p)])
    result = await fb.generate_async("sys", "user", use_semantic_cache=False)
    assert "suffisamment longue" in result
    p.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_generate_async_cascade_sur_echec():
    p1 = _make_provider(fail=True)
    p2 = _make_provider("Réponse du provider de secours, longue pour passer le seuil.")
    fb = FallbackProvider([("modele-1", p1), ("modele-2", p2)])
    result = await fb.generate_async("sys", "user", use_semantic_cache=False)
    assert "secours" in result
    p1.generate_async.assert_called()
    p2.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_generate_async_tous_echecs():
    p1 = _make_provider(fail=True)
    p2 = _make_provider(fail=True)
    fb = FallbackProvider([("m1", p1), ("m2", p2)])
    with pytest.raises(RuntimeError):
        await fb.generate_async("sys", "user", use_semantic_cache=False)


@pytest.mark.asyncio
async def test_generate_async_cb_ouvert_skip():
    """Un provider avec CB ouvert est ignoré, on passe au suivant."""
    cb = CircuitBreaker.get_or_create("modele-cb")
    for _ in range(cb.failure_threshold):
        cb.record_failure(RuntimeError("x"))
    assert cb.is_open()

    p_skip = _make_provider("ne doit pas etre appelé — provider court-circuité")
    p_ok = _make_provider("Réponse valide du provider de secours après circuit ouvert.")
    fb = FallbackProvider([("modele-cb", p_skip), ("modele-ok", p_ok)])
    result = await fb.generate_async("sys", "user", use_semantic_cache=False)
    assert "secours" in result
    p_skip.generate_async.assert_not_called()


@pytest.mark.asyncio
async def test_generate_structured_async_basique():
    p = _make_provider("structuré")
    fb = FallbackProvider([("modele-s", p)])
    result = await fb.generate_structured_async("sys", "user", schema={})
    assert result == {"result": "structuré"}
    p.generate_structured_async.assert_called_once()


@pytest.mark.asyncio
async def test_generate_structured_async_cascade():
    p1 = _make_provider(fail=True)
    p2 = _make_provider("data")
    fb = FallbackProvider([("m1", p1), ("m2", p2)])
    result = await fb.generate_structured_async("sys", "user", schema={})
    assert result == {"result": "data"}
