"""
tests/unit/test_ajean_workflow_adapter.py — AJEAN = seul local des workflows.

23/08 Axel : Ollama/LM Studio restent déclarés (#T354) mais ne sont plus élus.
AJEAN (llama.cpp 14B, port 8080) est l'executor DreamCoder dès qu'il est sain.
Le Tab garde Cerebras (`gpt-oss-120b`) en tête de fast-path, sans réécrire
la cascade IHM (AJEAN d'abord).
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

from core.llm.ajean_adapter import (  # noqa: E402
    AJEAN_DEFAULT_MODEL,
    LLAMA_SERVER_PROFILE,
    PROVIDERS_LOCAUX_HORS_ELECTION,
    PROVIDERS_WORKFLOW,
    llama_server_argv,
    premier_eligible,
)
from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS  # noqa: E402
from services.pipeline_service import (  # noqa: E402
    FAST_PATH_PROVIDERS,
    _fast_path_providers_config,
)


def test_sonde_lan_attend_trois_secondes():
    """Deck → PC : 1 s était trop court (mesuré 23/08, sonde PRET).

    On lit le source : conftest stubbe `ajean_http_ok` (timeout 1.0) pour
    empêcher la suite unitaire de taper le llama LAN.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "core" / "llm" / "ajean_adapter.py").read_text(
        encoding="utf-8")
    assert "timeout_s: float = 3.0" in src


def test_profil_llama_server_porte_les_reglages_t353():
    """-ngl 999, contexte 32k, KV q8_0, écoute LAN, jinja."""
    argv = llama_server_argv(r"C:\models\Qwen2.5-14B-Instruct-1M-Q4_K_M.gguf")
    assert "-ngl" in argv and "999" in argv
    assert "-c" in argv and "32768" in argv
    assert "-ctk" in argv and "q8_0" in argv
    assert "-ctv" in argv and "q8_0" in argv
    assert "--host" in argv and "0.0.0.0" in argv
    assert "--port" in argv and "8080" in argv
    assert "--jinja" in argv
    assert LLAMA_SERVER_PROFILE["model"] == AJEAN_DEFAULT_MODEL
    assert LLAMA_SERVER_PROFILE["json_prompt_only"] is True


def test_providers_workflow_sont_ajean_seulement():
    assert PROVIDERS_WORKFLOW == ("ajean_pc", "ajean_deck")
    for pid in PROVIDERS_WORKFLOW:
        assert pid in OPENAI_COMPAT_PROVIDERS
    assert "ollama" in PROVIDERS_LOCAUX_HORS_ELECTION
    assert "lmstudio" in PROVIDERS_LOCAUX_HORS_ELECTION


def test_premier_eligible_prefere_ajean_pc():
    assert premier_eligible(["gemini-free", "ajean_deck", "ajean_pc"]) == "ajean_pc"
    assert premier_eligible(["mistral-free"]) is None


def test_fast_path_ihm_reste_ajean_en_tete():
    """Ne pas réécrire le vocal IHM : AJEAN d'abord, pas Cerebras."""
    assert FAST_PATH_PROVIDERS[0] == "ajean_pc"
    liste = _fast_path_providers_config()
    assert liste[0] == "ajean_pc"
    assert liste[0] != "gpt-oss-120b"


def test_fast_path_tab_commence_par_cerebras():
    liste = _fast_path_providers_config("tab5")
    assert liste[0] == "gpt-oss-120b"
    assert "ajean_pc" in liste
    # L'IHM n'est pas contaminée par l'appel Tab.
    assert _fast_path_providers_config()[0] == "ajean_pc"


@pytest.mark.asyncio
async def test_budget_guard_elit_ajean_quand_sain(monkeypatch):
    from tests.unit.test_budget_guard_cli import _garde_sans_gemini_free

    garde = _garde_sans_gemini_free(monkeypatch)
    choisi = await garde.get_available_provider(sains={"ajean_pc", "gemini-free"})
    assert choisi == "ajean_pc"


@pytest.mark.asyncio
async def test_budget_guard_n_elit_plus_ollama_ni_lmstudio(monkeypatch):
    from tests.unit.test_budget_guard_cli import _garde_sans_gemini_free

    garde = _garde_sans_gemini_free(monkeypatch)
    monkeypatch.setattr(garde, "_check_ollama_availability", lambda: True)
    monkeypatch.setattr(garde, "_check_lmstudio_availability", lambda: True)
    choisi = await garde.get_available_provider(sains={"ollama", "lmstudio"})
    assert choisi not in {"ollama", "lmstudio"}
    assert choisi is None


@pytest.mark.asyncio
async def test_choisir_provider_prefere_ajean_sur_l_elo(monkeypatch):
    from agents.dreamer_agent import _choisir_provider_tache
    from tests.unit.test_routage_dreamcoder_domaines import _BudgetGuardFactice

    bg = _BudgetGuardFactice()

    provider, raison, _exc = await _choisir_provider_tache(
        "code_generation", bg, {"ajean_pc", "gemini-free"}, set())
    assert provider == "ajean_pc"
    assert raison == "ajean_local"
    assert bg.appels == []


@pytest.mark.asyncio
async def test_choisir_provider_ajean_lan_si_sonde_pret_rate(monkeypatch):
    """Deck : sonde PRET écarte ajean_pc, llama répond encore → on le prend."""
    from agents.dreamer_agent import _choisir_provider_tache
    from tests.unit.test_routage_dreamcoder_domaines import _BudgetGuardFactice

    bg = _BudgetGuardFactice()

    async def _lan_ok(pid, timeout_s=1.0):
        return pid == "ajean_pc"

    monkeypatch.setattr(
        "core.llm.ajean_adapter.ajean_http_ok", _lan_ok)
    monkeypatch.setattr(
        "core.budget_guard.provider_en_cooldown", lambda _p: False)

    provider, raison, _exc = await _choisir_provider_tache(
        "code_generation", bg, {"gemini-free", "deepseek-free"}, set())
    assert provider == "ajean_pc"
    assert raison == "ajean_local_lan"


# ── 06/10 Axel : AJEAN du PC retiré — le réglage fait foi, pas le code ──────


def _reglage_sondes(monkeypatch, liste):
    """Simule config.json → routing_policy.providers_sondes."""
    monkeypatch.setattr("core.sante_providers.candidats_sondes", lambda: list(liste))


def test_providers_workflow_actifs_suit_le_reglage(monkeypatch):
    from core.llm.ajean_adapter import providers_workflow_actifs

    _reglage_sondes(monkeypatch, ["gemini-free", "ajean_deck"])
    assert providers_workflow_actifs() == ("ajean_deck",)
    # Ordre de préférence du code, pas celui de la liste.
    _reglage_sondes(monkeypatch, ["ajean_deck", "gemini-free", "ajean_pc"])
    assert providers_workflow_actifs() == ("ajean_pc", "ajean_deck")
    _reglage_sondes(monkeypatch, ["gemini-free"])
    assert providers_workflow_actifs() == ()


@pytest.mark.asyncio
async def test_choisir_provider_n_interroge_plus_ajean_retire(monkeypatch):
    """Sous sonde : ajean_pc retiré du réglage → pas de GET /v1/models, même s'il répondrait."""
    from agents.dreamer_agent import _choisir_provider_tache
    from tests.unit.test_routage_dreamcoder_domaines import _BudgetGuardFactice

    interroges = []

    async def _lan_ok(pid, timeout_s=1.0):
        interroges.append(pid)
        return True

    monkeypatch.setattr("core.llm.ajean_adapter.ajean_http_ok", _lan_ok)
    monkeypatch.setattr("core.budget_guard.provider_en_cooldown", lambda _p: False)
    monkeypatch.setattr("core.provider_scorer.classer_providers_pour_tache",
                        lambda p, d: (sorted(p), {}))
    _reglage_sondes(monkeypatch, ["gemini-free", "deepseek-free"])

    provider, raison, _exc = await _choisir_provider_tache(
        "code_generation", _BudgetGuardFactice(), {"gemini-free", "deepseek-free"}, set())
    assert interroges == []
    assert provider == "deepseek-free"
    assert raison == "historique_reussite"


@pytest.mark.asyncio
async def test_choisir_provider_hors_sonde_ecarte_ajean_retire(monkeypatch):
    """Hors sonde, `base` vient du registre : un AJEAN retiré n'y est plus candidat."""
    from agents.dreamer_agent import _choisir_provider_tache
    from tests.unit.test_routage_dreamcoder_domaines import _BudgetGuardFactice

    vus = []

    def _classer(providers, _domaine):
        vus.extend(providers)
        return sorted(providers), {}

    monkeypatch.setattr("core.budget_guard.provider_en_cooldown", lambda _p: False)
    monkeypatch.setattr("core.provider_scorer.classer_providers_pour_tache", _classer)
    _reglage_sondes(monkeypatch, ["gemini-free", "deepseek-free"])

    provider, _raison, _exc = await _choisir_provider_tache(
        "code_generation", _BudgetGuardFactice(), None, set())
    assert vus, "l'élection par historique doit avoir des candidats"
    assert not set(vus) & set(PROVIDERS_WORKFLOW)
    assert provider not in PROVIDERS_WORKFLOW


@pytest.mark.asyncio
async def test_budget_guard_hors_sonde_suit_le_reglage_ajean(monkeypatch):
    """Hors sonde : GET /v1/models seulement vers les AJEAN laissés par le réglage."""
    from tests.unit.test_budget_guard_cli import _garde_sans_gemini_free

    garde = _garde_sans_gemini_free(monkeypatch)
    interroges = []

    async def _lan_ok(pid, timeout_s=1.0):
        interroges.append(pid)
        return True

    monkeypatch.setattr("core.llm.ajean_adapter.ajean_http_ok", _lan_ok)

    _reglage_sondes(monkeypatch, ["gemini-free"])
    choisi = await garde.get_available_provider()
    assert interroges == []
    assert choisi not in PROVIDERS_WORKFLOW

    # Remis dans le réglage : AJEAN redevient l'executor, sans redéployer.
    _reglage_sondes(monkeypatch, ["ajean_pc", "gemini-free"])
    assert await garde.get_available_provider() == "ajean_pc"
    assert interroges == ["ajean_pc"]
