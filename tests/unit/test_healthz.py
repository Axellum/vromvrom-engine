"""Test de la sonde de liveness publique /healthz."""

import pytest

from api.routes.health import healthz


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    """200 = moteur vivant ; ne doit jamais lever, même app_state absent."""
    result = await healthz()
    assert result["ok"] is True
    assert result["service"] == "tab5-engine"
    assert "status" in result  # peut être None, mais la clé existe toujours
