"""
tests/unit/test_porte_frontend_et_fenetre.py — Le TSX doit compiler (#T405) et la fenêtre doit borner (#T406).

Deux défauts mesurés le 18/08 sur le cycle autonome de production.

**#T405, volet frontend.** La tâche #38 (TaskKanbanBoard, 430 990 tokens) est
declaree `completed` et livre 384 lignes de TSX qui **ne compilent pas** : trois
erreurs `TS6133` « declared but its value is never read », `noUnusedLocals` etant
actif. C est le TROISIEME echec d affilee sur ce motif -- la version de la veille
avait deja ete rejetee pour un import inutilise. `pytest` ne voit rien du
TypeScript : sans cette porte, tout le frontend echappe a la verification.

**#T406.** `dreamcoder_max_cycle_minutes` ne bornait que l intervalle ENTRE deux
taches. Fenetre reglee a 22 minutes, cycle encore en cours a 30 sur une seule
tache : il a fallu l arreter a la main. Sur un cycle de nuit, personne n est la.
"""

import asyncio
import subprocess

import pytest

import tools.git_safety as git_safety
from agents.dreamer_agent import (
    _executer_tache_bornee,
    _fichiers_frontend_modifies,
    _verifier_frontend,
)


class _Proc:
    def __init__(self, code, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = code, stdout, stderr


# -- Reperage des fichiers frontend ----------------------------------------

def test_seuls_les_ts_et_tsx_dihm_sont_retenus(monkeypatch):
    diff = chr(10).join([
        "core/app_state.py",
        "ihm-v2/src/components/TaskKanbanBoard.tsx",
        "ihm-v2/src/api/dag.ts",
        "ihm-v2/README.md",
        "docs/travail/TACHES.md",
    ])
    monkeypatch.setattr(git_safety, "_run_git", lambda *a, **k: (0, diff, ""))
    assert _fichiers_frontend_modifies("/depot", "master") == [
        "ihm-v2/src/components/TaskKanbanBoard.tsx",
        "ihm-v2/src/api/dag.ts",
    ]


def test_une_tache_python_seule_ne_declenche_pas_la_porte(monkeypatch):
    monkeypatch.setattr(git_safety, "_run_git",
                        lambda *a, **k: (0, "core/app_state.py", ""))
    assert _fichiers_frontend_modifies("/depot", "master") == []


# -- Verdict de compilation -------------------------------------------------

def test_sans_node_modules_la_porte_est_ignoree(tmp_path):
    (tmp_path / "ihm-v2").mkdir()
    vert, motif = _verifier_frontend(str(tmp_path))
    assert vert is True
    assert "node_modules absent" in motif


def test_tsc_vert_laisse_passer(tmp_path, monkeypatch):
    (tmp_path / "ihm-v2" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0))
    vert, _motif = _verifier_frontend(str(tmp_path))
    assert vert is True


def test_tsc_rouge_bloque(tmp_path, monkeypatch):
    """Le cas exact de la tache #38."""
    (tmp_path / "ihm-v2" / "node_modules").mkdir(parents=True)
    erreurs = ("src/components/TaskKanbanBoard.tsx(21,49): error TS6133: "
               "'Value' is declared but its value is never read.")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, erreurs))
    vert, motif = _verifier_frontend(str(tmp_path))
    assert vert is False
    assert "TS6133" in motif


# -- Borne de fenetre -------------------------------------------------------

@pytest.mark.asyncio
async def test_une_tache_trop_longue_est_interrompue():
    async def _interminable():
        await asyncio.sleep(30)
        return {"status": "success"}

    # Le plancher d une minute est contourne en passant un temps restant
    # superieur : on veut mesurer l interruption, pas attendre 60 s.
    from agents import dreamer_agent
    dreamer_agent._PLANCHER_TACHE_S = 0.05
    try:
        resultat = await _executer_tache_bornee(_interminable, 0.05, 42)
    finally:
        dreamer_agent._PLANCHER_TACHE_S = 60.0
    assert resultat is None


@pytest.mark.asyncio
async def test_une_tache_qui_finit_a_temps_rend_son_resultat():
    async def _rapide():
        return {"status": "success", "task_id": 7}

    resultat = await _executer_tache_bornee(_rapide, 5.0, 7)
    assert resultat["status"] == "success"


@pytest.mark.asyncio
async def test_le_plancher_protege_une_tache_a_peine_commencee():
    """Fenetre deja ecoulee : on accorde quand meme le plancher, pas zero."""
    async def _courte():
        await asyncio.sleep(0.01)
        return {"status": "success"}

    resultat = await _executer_tache_bornee(_courte, -120.0, 1)
    assert resultat is not None
