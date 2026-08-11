"""
tests/unit/test_coding_front.py — Coding front (#T204).

Vérifie la détection de complexité déterministe, le routage moyen/fort,
l'escalade automatique en filet et le forçage de tier — sans réseau
(gateway factice injectée).
"""

import pytest

from services.coding_front import (
    MIN_USABLE_RESPONSE_CHARS,
    detect_code_complexity,
    run_coding_task,
)

_REPONSE_OK = "```python\ndef tri_fusion(t):\n    return sorted(t)\n```\nExplication concise."


class _FakeProvider:
    def __init__(self, response=None, raise_error=False):
        self.response = response
        self.raise_error = raise_error
        self.calls = 0

    async def generate_async(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("cascade épuisée (simulée)")
        return self.response


class _FakeGateway:
    """Gateway factice : un provider par tier, journalise les tiers demandés."""

    def __init__(self, providers_by_tier):
        self.providers_by_tier = providers_by_tier
        self.requested_tiers = []

    def get_provider_for_tier(self, tier, config, elo_order=None):
        self.requested_tiers.append(tier)
        return f"tier-{tier}", self.providers_by_tier[tier]


# ── Détection de complexité ──────────────────────────────────────────────────

def test_prompt_court_simple():
    assert detect_code_complexity("écris une fonction de tri fusion en python") is False


def test_mot_cle_structurel_complexe():
    assert detect_code_complexity("refactore le module de routage") is True


def test_prompt_long_complexe():
    assert detect_code_complexity("x" * 300) is True


def test_multi_fichiers_complexe():
    fichiers = ["a.py", "b.py", "c.py"]
    assert detect_code_complexity("corrige le bug", fichiers) is True
    assert detect_code_complexity("corrige le bug", fichiers[:2]) is False


# ── Routage et escalade ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tache_simple_reste_au_tier_moyen():
    gw = _FakeGateway({"moyen": _FakeProvider(_REPONSE_OK)})
    result = await run_coding_task("écris un décorateur de cache", gateway=gw, config={})
    assert result["tier_used"] == "moyen"
    assert result["escalated"] is False
    assert gw.requested_tiers == ["moyen"]


@pytest.mark.asyncio
async def test_tache_complexe_va_directement_au_fort():
    gw = _FakeGateway({"fort": _FakeProvider(_REPONSE_OK)})
    result = await run_coding_task(
        "refactore l'architecture du module de cascade", gateway=gw, config={}
    )
    assert result["tier_used"] == "fort"
    assert result["complex"] is True
    assert result["escalated"] is False


@pytest.mark.asyncio
async def test_escalade_si_cascade_moyen_echoue():
    moyen = _FakeProvider(raise_error=True)
    fort = _FakeProvider(_REPONSE_OK)
    gw = _FakeGateway({"moyen": moyen, "fort": fort})
    result = await run_coding_task("écris un parseur ini", gateway=gw, config={})
    assert result["escalated"] is True
    assert result["tier_used"] == "fort"
    assert moyen.calls == 1 and fort.calls == 1


@pytest.mark.asyncio
async def test_escalade_si_reponse_moyen_vide():
    gw = _FakeGateway({
        "moyen": _FakeProvider("ok"[: MIN_USABLE_RESPONSE_CHARS - 1]),
        "fort": _FakeProvider(_REPONSE_OK),
    })
    result = await run_coding_task("écris un parseur ini", gateway=gw, config={})
    assert result["escalated"] is True
    assert result["response"] == _REPONSE_OK


@pytest.mark.asyncio
async def test_force_tier_court_circuite_la_detection():
    gw = _FakeGateway({"fort": _FakeProvider(_REPONSE_OK)})
    result = await run_coding_task(
        "écris un one-liner", force_tier="fort", gateway=gw, config={}
    )
    assert result["tier_used"] == "fort"
    assert result["complex"] is False


@pytest.mark.asyncio
async def test_echec_du_fort_remonte_une_erreur():
    gw = _FakeGateway({"fort": _FakeProvider(raise_error=False, response="")})
    with pytest.raises(RuntimeError, match="utilisable"):
        await run_coding_task("refactore toute l'architecture", gateway=gw, config={})
