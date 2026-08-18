"""Tests du verrou d'exécution PAR SESSION (#T324).

Remplace l'ancien verrou global unique : deux sessions DIFFÉRENTES peuvent
s'exécuter en parallèle, une même session ne s'exécute jamais deux fois
(409 doublon) et un plafond global de concurrence reste appliqué. `execution_state`
garde exactement sa forme (4 clés) en vue agrégée : `status == "running"` dès
qu'au moins une exécution tourne.

Critères d'acceptation couverts ici :
  1. Deux exécutions de la MÊME session en parallèle → la seconde reçoit 409.
  2. Deux exécutions de sessions DIFFÉRENTES en parallèle → les deux passent.
  3. Au-delà du plafond de concurrence → 409, message distinct du doublon.
  4. Une exécution qui lève une exception libère bien son entrée (session
     suivante acceptée).
  5. GET /api/status renvoie toujours les quatre mêmes clés, et
     `status == "running"` dès qu'une exécution tourne.
"""

import pytest

from core.app_state import (
    MSG_DOUBLON_SESSION,
    MSG_PLAFOND_CONCURRENCE,
    _cle_execution,
    get_app_state,
)
from core.source_router import ModeType, RequestSource, SourceType


@pytest.fixture
def etat():
    """Renvoie le singleton AppState réinitialisé (registre vide, plafond 3)."""
    import core.app_state as app_state

    # Réinitialise le singleton pour isoler chaque test (pas de fuite entre eux).
    app_state._app_state_instance._initialized = False
    st = get_app_state()
    st.execution_registry.clear()
    st.execution_state.update({
        "status": "idle", "objective": "", "engine_state": None, "error_message": None,
    })
    st.max_concurrency = 3
    return st


def _requete_source(device_id: str | None = None) -> RequestSource:
    """RequestSource du Tab5 HA (source de repli de la clé d'exécution)."""
    return RequestSource(
        type=SourceType.TAB5, mode=ModeType.HA, tts_enabled=True, device_id=device_id
    )


# ── 1. Même session → jamais deux en parallèle (garde-fou anti-doublon) ──

@pytest.mark.asyncio
async def test_meme_session_refusee_deux_fois(etat):
    """Deux exécutions de la MÊME session : la seconde reçoit 409 doublon."""
    refus1 = await etat.begin_execution("session_A", "objectif 1")
    assert refus1 is None  # première acceptée

    refus2 = await etat.begin_execution("session_A", "objectif 2")
    assert refus2 is not None
    assert refus2["status_code"] == 409
    assert refus2["detail"] == MSG_DOUBLON_SESSION

    # Après libération, la même session redevient acceptée.
    await etat.end_execution("session_A")
    refus3 = await etat.begin_execution("session_A", "objectif 3")
    assert refus3 is None


@pytest.mark.asyncio
async def test_repli_source_device_identique_protege_du_doublon(etat):
    """Sans session_id, la clé retombe sur la source : un même device (Tab5)
    qui rejoue une commande vocale est bloqué en doublon (garde-fou)."""
    src1 = _requete_source(device_id="tab5-001")
    src2 = _requete_source(device_id="tab5-001")
    assert _cle_execution(None, src1) == _cle_execution(None, src2)

    refus1 = await etat.begin_execution(_cle_execution(None, src1), "ouvre le volet")
    assert refus1 is None
    refus2 = await etat.begin_execution(_cle_execution(None, src2), "ouvre le volet")
    assert refus2 is not None
    assert refus2["status_code"] == 409
    assert refus2["detail"] == MSG_DOUBLON_SESSION


# ── 2. Sessions différentes → parallélisme autorisé ──

@pytest.mark.asyncio
async def test_sessions_differentes_en_parallele(etat):
    """Deux sessions DIFFÉRENTES s'exécutent en parallèle (c'est le test qui
    échouait sur master : le verrou global renvoyait 409 à la seconde)."""
    refus_a = await etat.begin_execution("session_A", "objectif A")
    refus_b = await etat.begin_execution("session_B", "objectif B")
    assert refus_a is None
    assert refus_b is None

    # Les deux entrées coexistent dans le registre.
    assert set(etat.execution_registry) == {"session_A", "session_B"}

    # La vue agrégée reste "running" tant qu'au moins une tourne.
    assert etat.execution_state["status"] == "running"

    await etat.end_execution("session_A")
    # B tourne encore → toujours "running".
    assert etat.execution_state["status"] == "running"
    assert "session_B" in etat.execution_registry

    await etat.end_execution("session_B")
    assert etat.execution_state["status"] == "success"
    assert etat.execution_registry == {}


# ── 3. Plafond de concurrence global ──

@pytest.mark.asyncio
async def test_au_dela_du_plafond_409_message_distinct(etat):
    """Au-delà du plafond → 409 avec un message distinct de celui du doublon."""
    etat.max_concurrency = 2
    assert await etat.begin_execution("session_A", "A") is None
    assert await etat.begin_execution("session_B", "B") is None

    refus = await etat.begin_execution("session_C", "C")
    assert refus is not None
    assert refus["status_code"] == 409
    assert refus["detail"] == MSG_PLAFOND_CONCURRENCE
    assert refus["detail"] != MSG_DOUBLON_SESSION

    # Une session libérée → la suivante redevient acceptée.
    await etat.end_execution("session_A")
    assert await etat.begin_execution("session_C", "C") is None


# ── 4. Exception → l'entrée est libérée (garde-fou anti-fuite) ──

@pytest.mark.asyncio
async def test_exception_libère_l_entrée(etat):
    """Une exécution qui lève libère bien son entrée : la session suivante passe."""
    async def _execution_qui_leve(session_key):
        refus = await etat.begin_execution(session_key, "objectif")
        assert refus is None
        try:
            raise RuntimeError("panne simulée")
        finally:
            await etat.end_execution(session_key, status="error", error_message="panne simulée")

    with pytest.raises(RuntimeError):
        await _execution_qui_leve("session_A")
    # L'entrée a été retirée → la même session est réutilisable.
    refus = await etat.begin_execution("session_A", "objectif 2")
    assert refus is None
    assert etat.execution_state["status"] == "running"
    assert etat.execution_state["error_message"] is None


# ── 5. GET /api/status : les 4 clés + running dès qu'une exécution tourne ──

def test_status_renvoie_toujours_les_quatre_cles(etat):
    """Le contrat public de /api/status : exactement les 4 clés historiques."""
    from api.routes.execution import get_status

    etat.execution_state.update({
        "status": "running", "objective": "obj", "engine_state": None, "error_message": None,
    })
    corps = get_status()
    assert set(corps.keys()) == {"status", "objective", "engine_state", "error_message"}


@pytest.mark.asyncio
async def test_status_running_des_qu_une_execution_tourne(etat):
    """status == "running" dès qu'une exécution tourne, même si une autre vient
    de se terminer (vue agrégée)."""
    from api.routes.execution import get_status

    await etat.begin_execution("session_A", "A")
    await etat.begin_execution("session_B", "B")
    await etat.end_execution("session_B")

    # Une seule exécution (A) tourne encore → "running".
    assert get_status()["status"] == "running"

    await etat.end_execution("session_A")
    assert get_status()["status"] == "success"
