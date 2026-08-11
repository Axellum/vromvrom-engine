"""
tests/unit/test_pipeline_payload_contrat.py — Contrat de frontière du payload (#T281).

`run_full_pipeline()` annonçait `initial_payload: Any` : un dict passait la
frontière et mourait plus loin dans core/engine.py sur
`initial_payload.metadata` (AttributeError), avec un message qui ne nommait ni
le paramètre fautif ni ce qui était attendu.

Ce test verrouille le contrat durci :
- TaskPayload valide  → passe tel quel (même objet, aucune copie)
- dict complet        → validé en TaskPayload, le pipeline démarre
- dict incomplet      → refus avant tout travail, message nommant task_objective
- valeur absurde      → refus explicite, jamais d'AttributeError
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import services.pipeline_service as pipeline_service
from core.state import GlobalState, StateUpdate, TaskPayload


class _FauxEngine:
    """Moteur factice : enregistre le payload reçu et rend un état terminal."""

    def __init__(self):
        self.payload_recu = None
        self.on_event = None

    async def run(self, initial_payload, starting_agent):
        self.payload_recu = initial_payload
        return GlobalState(
            session_id="s_t281",
            history=[StateUpdate(agent_name="planner", status="success", result_data="ok")],
        )


class _FauxBridge:
    """MCPBridge factice : start/stop sans effet."""

    async def start(self, *args, **kwargs):
        pass

    async def stop(self):
        pass


def _isoler_effets_de_bord(monkeypatch, faux_engine):
    """Branche un moteur factice et neutralise BDD / threads / event store."""
    monkeypatch.setattr(
        "services.pipeline_service.monter_moteur",
        lambda *a, **k: (faux_engine, object(), _FauxBridge(), object()),
    )
    # Session history : écrit en SQLite réel — no-op en test unitaire (sync,
    # comme les vrais record_session_start/end appelés sans await).
    def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr("core.session_history.record_session_start", _noop)
    monkeypatch.setattr("core.session_history.record_session_end", _noop)

    # Scan CLI (thread de fond) et EventStore (audit) : indisponibles, les
    # blocs try/except de run_full_pipeline les ignorent déjà.
    def _indisponible(*args, **kwargs):
        raise RuntimeError("effet de bord neutralisé en test unitaire")

    monkeypatch.setattr("core.cli_token_collector.collect_all_cli_tokens", _indisponible)
    monkeypatch.setattr("core.event_store.get_event_store", _indisponible)


class TestContratPayloadEntree:
    """Le contrat de frontière de run_full_pipeline (#T281)."""

    @pytest.mark.asyncio
    async def test_dict_incomplet_refuse_avec_champ_nomme(self, monkeypatch):
        """Le dict exact du harnais ({"objective": ...}) est refusé avant tout
        travail, avec un message qui nomme task_objective — et jamais metadata."""
        monte = []

        def _marquer_monte(*args, **kwargs):
            monte.append(1)

        monkeypatch.setattr("services.pipeline_service.monter_moteur", _marquer_monte)

        res = await pipeline_service.run_full_pipeline(
            user_prompt="objectif",
            session_id="s_t281_incomplet",
            initial_payload={"objective": "objectif"},
            starting_agent="planner",
            on_event_callback=None,
            config={},
        )

        assert res["status"] == "error", res
        assert "initial_payload" in res["error"], res["error"]
        assert "task_objective" in res["error"], res["error"]
        assert "metadata" not in res["error"], res["error"]
        assert not monte, "le refus doit précéder le montage du moteur"

    @pytest.mark.asyncio
    async def test_taskpayload_valide_passe_inchange(self, monkeypatch):
        """Un TaskPayload valide arrive au moteur MÊME OBJET (identité, pas de copie)."""
        faux_engine = _FauxEngine()
        _isoler_effets_de_bord(monkeypatch, faux_engine)
        payload = TaskPayload(task_objective="objectif", metadata={"source": "test"})

        res = await pipeline_service.run_full_pipeline(
            user_prompt="objectif",
            session_id="s_t281_identique",
            initial_payload=payload,
            starting_agent="planner",
            on_event_callback=None,
            config={},
        )

        assert res["status"] == "completed", res
        assert faux_engine.payload_recu is payload, "le TaskPayload doit arriver tel quel"

    @pytest.mark.asyncio
    async def test_dict_complet_valide_demarre_le_pipeline(self, monkeypatch):
        """Un dict complet et valide est converti en TaskPayload, pipeline démarre."""
        faux_engine = _FauxEngine()
        _isoler_effets_de_bord(monkeypatch, faux_engine)

        res = await pipeline_service.run_full_pipeline(
            user_prompt="objectif",
            session_id="s_t281_dict",
            initial_payload={"task_objective": "objectif", "metadata": {"source": "test"}},
            starting_agent="planner",
            on_event_callback=None,
            config={},
        )

        assert res["status"] == "completed", res
        assert isinstance(faux_engine.payload_recu, TaskPayload)
        assert faux_engine.payload_recu.task_objective == "objectif"
        assert faux_engine.payload_recu.metadata == {"source": "test"}

    @pytest.mark.asyncio
    async def test_valeurs_absurdes_refusees(self, monkeypatch):
        """str / None / int : refus explicite qui nomme le type reçu, pas d'AttributeError."""
        for absurde in ("une chaîne", None, 42):
            res = await pipeline_service.run_full_pipeline(
                user_prompt="objectif",
                session_id=f"s_t281_absurde_{type(absurde).__name__}",
                initial_payload=absurde,
                starting_agent="planner",
                on_event_callback=None,
                config={},
            )
            assert res["status"] == "error", f"{absurde!r} doit être refusé"
            assert "initial_payload" in res["error"], res["error"]
            assert type(absurde).__name__ in res["error"], res["error"]
            assert "task_objective" in res["error"], res["error"]
