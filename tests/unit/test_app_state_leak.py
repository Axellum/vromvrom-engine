
import asyncio
import pytest
from core.app_state import AppState

@pytest.mark.asyncio
async def test_execution_registry_leak_on_exception():
    """
    Vérifie que si une exception survient, l'entrée dans execution_registry
    reste bloquée (reproduction du problème).
    """
    state = AppState()
    state._initialized = False
    state.initialize()

    session_key = "test_session_leak"

    # 1. Début d'exécution
    await state.begin_execution(session_key, "Test leak")
    assert session_key in state.execution_registry

    # 2. Simulation d'une exception sans appeler end_execution
    # (Le code utilisateur aurait dû être dans un try/finally)
    try:
        raise Exception("Crash inattendu")
    except Exception:
        pass

    # 3. Vérification : l'entrée est toujours là
    assert session_key in state.execution_registry

    # 4. Tentative de nouvelle exécution : devrait échouer (409)
    result = await state.begin_execution(session_key, "Test leak 2")
    assert result is not None
    assert result["status_code"] == 409
    assert "déjà en cours" in result["detail"]
