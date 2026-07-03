# -*- coding: utf-8 -*-
"""
tests/test_router_factory_wiring.py — Câblage du Router via la factory (P1-2.1).

Vérifie :
- `_elo_enabled` est piloté par la config (et non codé en dur).
- `AppState.get_shared_router()` renvoie un singleton mis en cache, construit via
  `core.factory.create_engine`, et expose `global_router` pour le legacy.
- Le Router câblé porte bien gateway / RAG / config (≠ Router nu).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_elo_enabled_defaults_true():
    """Sans config, l'Elo est actif par défaut."""
    from core.router import Router
    r = Router(default_agent="planner")
    assert r._elo_enabled is True


def test_elo_enabled_disabled_via_config():
    """[P1-2.1] La config peut désactiver l'Elo (plus de hardcode True)."""
    from core.router import Router
    r = Router(default_agent="planner", config={"elo_enabled": False})
    assert r._elo_enabled is False


def test_shared_router_is_cached_singleton(monkeypatch):
    """[P1-2.1] get_shared_router construit via la factory UNE fois et met en cache."""
    import core.app_state as app_state_mod

    # Factory factice : Router câblé minimal + sentinelles engine/config.
    from core.router import Router
    calls = {"n": 0}

    def _fake_create_engine(session_id="x", register_git_tools=True):
        calls["n"] += 1
        cfg = {"sentinel": True}
        router = Router(default_agent="planner", llm_gateway=object(), config=cfg)
        engine = object()
        return engine, router, cfg

    # La factory est importée tardivement dans la méthode → patcher le module source.
    monkeypatch.setattr("core.factory.create_engine", _fake_create_engine)

    # État neuf pour isoler le test
    state = app_state_mod.AppState()
    state._initialized = False
    state.initialize()

    r1 = state.get_shared_router()
    r2 = state.get_shared_router()

    assert r1 is r2                      # singleton
    assert calls["n"] == 1               # construit une seule fois (cache)
    assert state.global_router is r1     # compat legacy renseignée
    assert r1.llm_gateway is not None    # gateway câblé
    assert r1.config.get("sentinel")     # config câblée


def test_shared_assembly_returns_triplet(monkeypatch):
    """[P1-2.1] get_shared_assembly renvoie (engine, router, config) cohérents."""
    import core.app_state as app_state_mod
    from core.router import Router

    engine_sentinel = object()
    cfg = {"k": "v"}

    def _fake_create_engine(session_id="x", register_git_tools=True):
        return engine_sentinel, Router(default_agent="planner", config=cfg), cfg

    monkeypatch.setattr("core.factory.create_engine", _fake_create_engine)

    state = app_state_mod.AppState()
    state._initialized = False
    state.initialize()

    eng, router, config = state.get_shared_assembly()
    assert eng is engine_sentinel
    assert config is cfg
    assert state.get_shared_router() is router
