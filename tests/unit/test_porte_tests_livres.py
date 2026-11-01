"""
tests/unit/test_porte_tests_livres.py — « completed » veut dire « les tests passent » (#T405).

Mesure le 18/08 a 21h45, cycle autonome sur la PRODUCTION : trois taches
declarees `completed` -- #44, #48, #49, **1,17 M de tokens a elles trois** --
ont livre des branches dont les tests ecrits par l agent echouent :

    task/44 -> 2 echecs (tests/unit/test_antigravity_helper.py)
    task/48 -> 3 echecs (tests/unit/test_app_state_fallback_cache.py)
    task/49 -> TimeoutError (tests/unit/test_async_db_serializer.py)

Le contrat de delegation exigeait pourtant, noir sur blanc : « execute pytest et
n annonce le succes que s il est vert ». Les garde-fous existants ferment
d autres portes -- #T388 branche vide, #T392 branche identique, #T394 code qui
ne parse pas -- mais aucun ne rejoue les tests. Le Reviewer est un LLM : il rend
un avis, pas une preuve.
"""

import subprocess

import pytest

import tools.git_safety as git_safety
from agents.dreamer_agent import _fichiers_tests_modifies, _rejouer_tests_livres


class _Proc:
    def __init__(self, code, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = code, stdout, stderr


# -- Reperage des tests livres ---------------------------------------------

def test_seuls_les_fichiers_de_test_sont_retenus(monkeypatch):
    diff = chr(10).join([
        "core/app_state.py",
        "tests/unit/test_app_state_fallback_cache.py",
        "docs/travail/TACHES.md",
        "agents/executor.py",
    ])
    monkeypatch.setattr(git_safety, "_run_git", lambda *a, **k: (0, diff, ""))
    assert _fichiers_tests_modifies("/depot", "master") == [
        "tests/unit/test_app_state_fallback_cache.py"]


def test_un_test_hors_du_dossier_tests_est_vu(monkeypatch):
    """Un `test_*.py` a la racine reste un test."""
    monkeypatch.setattr(git_safety, "_run_git",
                        lambda *a, **k: (0, "test_quelque_chose.py", ""))
    assert _fichiers_tests_modifies("/depot", "master") == ["test_quelque_chose.py"]


# -- Verdict ----------------------------------------------------------------

def test_sans_test_livre_la_porte_laisse_passer():
    vert, motif = _rejouer_tests_livres("/depot", [])
    assert vert is True
    assert "aucun fichier" in motif


def test_des_tests_verts_laissent_passer(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0))
    vert, motif = _rejouer_tests_livres("/depot", ["tests/unit/test_x.py"])
    assert vert is True


def test_des_tests_rouges_bloquent(monkeypatch):
    """Le cas des trois taches du 18/08."""
    sortie = "FAILED tests/unit/test_app_state_fallback_cache.py::test_ttl - AssertionError"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, sortie))
    vert, motif = _rejouer_tests_livres("/depot", ["tests/unit/test_app_state_fallback_cache.py"])
    assert vert is False
    assert "FAILED" in motif


def test_un_test_qui_pend_est_un_echec(monkeypatch):
    """task/49 rendait un TimeoutError : un livrable qui bloque n en est pas un."""
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=420)
    monkeypatch.setattr(subprocess, "run", _timeout)
    vert, motif = _rejouer_tests_livres("/depot", ["tests/unit/test_async_db_serializer.py"])
    assert vert is False
    assert "rendent pas la main" in motif


def test_pytest_absent_ne_punit_pas_la_tache(monkeypatch):
    """On ne fait pas echouer un agent pour un outil manquant sur la machine."""
    def _absent(*a, **k):
        raise FileNotFoundError("pytest")
    monkeypatch.setattr(subprocess, "run", _absent)
    vert, motif = _rejouer_tests_livres("/depot", ["tests/unit/test_x.py"])
    assert vert is True
    assert "introuvable" in motif


def test_aucun_test_collecte_nest_pas_un_echec(monkeypatch):
    """pytest rend 5 quand il ne collecte rien — ce n est pas un test rouge."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(5))
    vert, _motif = _rejouer_tests_livres("/depot", ["tests/unit/test_x.py"])
    assert vert is True
