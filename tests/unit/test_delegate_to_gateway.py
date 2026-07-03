"""Tests de la délégation auto au gateway (#T40 partie 2/2).

`delegate_to_gateway` doit :
1. choisir le modèle le moins cher éligible du catalogue selon le budget puis l'exécuter ;
2. tomber en cascade sur le candidat suivant si un provider échoue ;
3. signaler proprement l'absence de candidat câblé.

On évite la dépendance au catalogue/gateway réels via du monkeypatch ciblé.

NB : shim pour forcer le package local `tools` (namespace) face au paquet
site-packages homonyme qui le masque sous Windows (divergence CI/local connue).
"""
import os
import sys
import types
import asyncio

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

import mcp_server  # noqa: E402


class _FakeProvider:
    """Provider minimal : renvoie un texte, ou lève si fail=True."""

    def __init__(self, label, fail=False):
        self.label = label
        self.fail = fail

    def generate(self, system, prompt, **kwargs):
        if self.fail:
            raise RuntimeError(f"boom {self.label}")
        return f"[{self.label}] {prompt}"

    async def generate_async(self, system, prompt, **kwargs):
        return self.generate(system, prompt, **kwargs)


class _FakeGateway:
    def __init__(self, providers):
        self.providers = providers


class _FakeRouter:
    def __init__(self, routing_type="standard"):
        self._rt = routing_type

    async def analyze_request(self, prompt):
        payload = types.SimpleNamespace(metadata={"routing_type": self._rt})
        return payload, "planner"


def _patch(monkeypatch, *, providers, candidates, routing_type="simple"):
    monkeypatch.setattr(mcp_server, "get_gateway", lambda: _FakeGateway(providers))
    monkeypatch.setattr(mcp_server, "get_router", lambda: _FakeRouter(routing_type))
    monkeypatch.setattr(mcp_server, "_catalog_models_for_budget", lambda budget: candidates)


def test_resolve_provider_exact_partiel_absent():
    gw = _FakeGateway({"deepseek-chat": object(), "ollama_local": object()})
    assert mcp_server._resolve_gateway_provider(gw, "deepseek-chat")[0] == "deepseek-chat"
    # Partiel : "ollama" → "ollama_local"
    assert mcp_server._resolve_gateway_provider(gw, "ollama")[0] == "ollama_local"
    # Absent → provider None
    assert mcp_server._resolve_gateway_provider(gw, "inexistant")[1] is None


def test_delegue_au_premier_candidat_eligible(monkeypatch):
    candidates = [
        {"id": "ollama_local", "tier": "local", "cost": "0$", "is_free": True, "speciality": "", "use": ""},
        {"id": "deepseek-chat", "tier": "leger", "cost": "0.14/0.28 USD/M", "is_free": False, "speciality": "", "use": ""},
    ]
    _patch(monkeypatch, providers={"ollama_local": _FakeProvider("ollama"),
                                    "deepseek-chat": _FakeProvider("ds")},
           candidates=candidates)

    out = asyncio.run(mcp_server.delegate_to_gateway("résume ceci", budget_constraint="free"))
    assert "[ollama]" in out
    assert "ollama_local" in out
    assert "deepseek-chat" not in out  # le 1er candidat a suffi


def test_fallback_cascade_si_premier_echoue(monkeypatch):
    candidates = [
        {"id": "ollama_local", "tier": "local", "cost": "0$", "is_free": True, "speciality": "", "use": ""},
        {"id": "deepseek-chat", "tier": "leger", "cost": "0.14/0.28 USD/M", "is_free": False, "speciality": "", "use": ""},
    ]
    _patch(monkeypatch, providers={"ollama_local": _FakeProvider("ollama", fail=True),
                                    "deepseek-chat": _FakeProvider("ds")},
           candidates=candidates)

    out = asyncio.run(mcp_server.delegate_to_gateway("tâche", budget_constraint="auto"))
    assert "[ds]" in out
    assert "deepseek-chat" in out
    assert "après échec" in out  # mention du fallback


def test_aucun_candidat_cable_dans_gateway(monkeypatch):
    candidates = [
        {"id": "modele-fantome", "tier": "pro", "cost": "1/2 USD/M", "is_free": False, "speciality": "", "use": ""},
    ]
    _patch(monkeypatch, providers={"deepseek-chat": _FakeProvider("ds")}, candidates=candidates)

    out = asyncio.run(mcp_server.delegate_to_gateway("tâche", budget_constraint="best"))
    assert out.startswith("❌")
    assert "câblé" in out or "câbl" in out


def test_catalogue_vide(monkeypatch):
    _patch(monkeypatch, providers={"deepseek-chat": _FakeProvider("ds")}, candidates=[])
    out = asyncio.run(mcp_server.delegate_to_gateway("tâche"))
    assert out.startswith("❌")
    assert "Aucun modèle actif" in out
