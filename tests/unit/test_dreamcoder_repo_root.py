"""
tests/unit/test_dreamcoder_repo_root.py — Clone Git dédié DreamCoder (#T202).

Sur le Deck, la prod (overlay tar) n'est pas un dépôt Git légitime : DreamCoder
doit travailler dans un clone dédié configuré via
`persistent_agents.dreamcoder_repo_path`. Vérifie la résolution de racine,
le repli historique, et la propagation de repo_root aux hooks Git de l'Engine.
"""

import os
from unittest.mock import patch

from agents.dreamer_agent import _ENGINE_ROOT, _dreamcoder_repo_root
from core.engine import Engine


# ── Résolution de la racine de travail ───────────────────────────────────────

def test_repli_historique_sans_configuration():
    assert _dreamcoder_repo_root({}) == _ENGINE_ROOT
    assert _dreamcoder_repo_root(None) == _ENGINE_ROOT
    assert _dreamcoder_repo_root({"dreamcoder_repo_path": ""}) == _ENGINE_ROOT


def test_clone_dedie_configure():
    chemin = os.path.join("un", "clone", "dreamcoder")
    resolu = _dreamcoder_repo_root({"dreamcoder_repo_path": chemin})
    assert resolu == os.path.abspath(chemin)
    assert resolu != _ENGINE_ROOT


def test_defaut_present_dans_load_config():
    """La clé doit être injectée par les defaults de load_config (auto-migration)."""
    from core.llm_gateway import load_config
    pa = load_config().get("persistent_agents", {})
    assert "dreamcoder_repo_path" in pa


# ── Propagation aux hooks Git de l'Engine ────────────────────────────────────

def test_engine_stocke_repo_root():
    engine = Engine(session_id="t202-test", repo_root="/travail/dreamcoder")
    assert engine.repo_root == "/travail/dreamcoder"
    # Défaut : comportement historique inchangé
    assert Engine(session_id="t202-defaut").repo_root is None


def test_prepare_git_branch_cible_le_repo_root():
    engine = Engine(session_id="t202-branch", repo_root="/travail/dreamcoder")
    with patch("tools.git_safety.git_prepare_agent_branch", return_value="agent/run-x") as prep:
        branch = engine._prepare_git_branch()
    assert branch == "agent/run-x"
    assert prep.call_args.kwargs.get("repo_path") == "/travail/dreamcoder"


def test_prepare_git_branch_defaut_cwd():
    engine = Engine(session_id="t202-branch-defaut")
    with patch("tools.git_safety.git_prepare_agent_branch", return_value="agent/run-y") as prep:
        engine._prepare_git_branch()
    assert prep.call_args.kwargs.get("repo_path") == "."


def test_finalize_git_cible_le_repo_root():
    engine = Engine(session_id="t202-final", repo_root="/travail/dreamcoder")
    captured = {}

    def _fake_finalize(branch, success, session_id, repo_path="."):
        captured["repo_path"] = repo_path
        return "ok"

    engine._finalize_git("agent/run-x", has_error=False, tasks_status={}, git_finalize_fn=_fake_finalize)
    assert captured["repo_path"] == "/travail/dreamcoder"
