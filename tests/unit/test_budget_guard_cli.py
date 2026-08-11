"""
tests/unit/test_budget_guard_cli.py — Profil d'hôte sans CLI dans BudgetGuard (#T248).

Points verrouillés :
1. `MOTEUR_DISABLE_CLI_PROVIDERS` exclut gemini-cli-abo / claude-cli-abo du
   round-robin même si les binaires sont détectés ;
2. la détection de binaire est cross-plateforme : les chemins AppData Windows
   ne sont testés que sous Windows ;
3. le collecteur de tokens CLI ne journalise plus en WARNING l'absence du
   répertoire Antigravity (état normal sur un hôte sans CLI, scan ~10 min →
   1440 WARNING/jour en prod sur le Deck).

Aucun accès base : sqlite3 et le verrou de lecture sont monkeypatchés.
"""
import contextlib
import os
import shutil
import sys
import types

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

import core.budget_guard as budget_guard  # noqa: E402
from core.budget_guard import BudgetGuard, _binary_present, _cli_providers_disabled  # noqa: E402


class _FakeCursor:
    def execute(self, query, params=None):
        pass

    def fetchone(self):
        # Les 3 requêtes (somme tokens, count requêtes, somme coût) renvoient
        # chacune une seule valeur — 0 partout = aucun historique.
        return (0,)


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


def _patch_garde_disponibles(monkeypatch):
    """BudgetGuard opérationnel sans vraie base ni verrous réels."""
    monkeypatch.setattr(budget_guard.sqlite3, "connect", lambda path: _FakeConn())

    @contextlib.asynccontextmanager
    async def _faux_verrou():
        yield

    monkeypatch.setattr(budget_guard, "db_read_lock_context", _faux_verrou)
    monkeypatch.setattr(budget_guard, "_provider_cooldown", {})
    # Aucune clé cloud par défaut : les seuls candidats possibles sont ceux
    # qu'on stubbe explicitement.
    for var in ("CEREBRAS_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_CODING_PLAN_API_KEY",
                "COHERE_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _garde_sans_gemini_free(monkeypatch):
    """Une garde dont le quota gemini-free est épuisé (0 token/h autorisé)."""
    _patch_garde_disponibles(monkeypatch)
    garde = BudgetGuard()
    garde.config["gemini_free_tokens_per_hour"] = 0
    garde._initialized = True
    return garde


# ── Interrupteur d'hôte ──────────────────────────────────────────────────────


@pytest.mark.parametrize("valeur, attendu", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("", False), ("0", False), ("false", False), ("non", False),
])
def test_table_de_verite_du_kill_switch(monkeypatch, valeur, attendu):
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", valeur)
    assert _cli_providers_disabled() is attendu


def test_kill_switch_absent_donne_faux(monkeypatch):
    monkeypatch.delenv("MOTEUR_DISABLE_CLI_PROVIDERS", raising=False)
    assert _cli_providers_disabled() is False


# ── Détection de binaire cross-plateforme ────────────────────────────────────


def test_chemins_appdata_consideres_seulement_sous_windows(monkeypatch, tmp_path):
    faux_cmd = tmp_path / "antigravity.cmd"
    faux_cmd.write_text("")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    monkeypatch.setattr(budget_guard.sys, "platform", "win32")
    assert _binary_present(("antigravity",), windows_appdata_candidates=(str(faux_cmd),)) is True

    monkeypatch.setattr(budget_guard.sys, "platform", "linux")
    assert _binary_present(("antigravity",), windows_appdata_candidates=(str(faux_cmd),)) is False


def test_recherche_path_generique_sur_toute_plateforme(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert _binary_present(("claude", "claude.cmd")) is True
    assert _binary_present(("binaire-inexistant",)) is False


# ── Exclusion du round-robin ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_desactives_exclut_les_providers_cli(monkeypatch):
    """Kill-switch actif : même binaires présents, aucun provider CLI proposé."""
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", "1")
    garde = _garde_sans_gemini_free(monkeypatch)
    monkeypatch.setattr(garde, "_check_gemini_cli_availability", lambda: True)
    monkeypatch.setattr(garde, "_check_claude_cli_availability", lambda: True)
    monkeypatch.setenv("CEREBRAS_API_KEY", "cle-de-test")

    choisi = await garde.get_available_provider()
    assert choisi == "cerebras-free"  # premier candidat non-CLI du round-robin


@pytest.mark.asyncio
async def test_cli_actives_propose_le_cli_si_binaire_present(monkeypatch):
    """Sans kill-switch, un binaire détecté entre bien dans le round-robin."""
    monkeypatch.delenv("MOTEUR_DISABLE_CLI_PROVIDERS", raising=False)
    garde = _garde_sans_gemini_free(monkeypatch)
    monkeypatch.setattr(garde, "_check_gemini_cli_availability", lambda: True)
    monkeypatch.setattr(garde, "_check_claude_cli_availability", lambda: False)

    choisi = await garde.get_available_provider()
    assert choisi == "gemini-cli-abo"


# ── Collecteur de tokens CLI : plus de WARNING sur hôte sans CLI ─────────────


def test_absence_repertoire_antigravity_ne_logue_plus_en_warning(monkeypatch, caplog):
    from core import cli_token_collector
    monkeypatch.setattr(cli_token_collector, "ANTIGRAVITY_BRAIN_DIR",
                        cli_token_collector.Path("/chemin/qui/n/existe/pas"))
    import logging
    with caplog.at_level(logging.DEBUG):
        sessions = cli_token_collector.scan_antigravity_conversations()
    assert sessions == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG for r in caplog.records)
