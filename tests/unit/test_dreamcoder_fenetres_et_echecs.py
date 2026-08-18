"""
tests/unit/test_dreamcoder_fenetres_et_echecs.py — Budget de temps et comptage
des échecs d'une tâche autonome.

Deux défauts mesurés en préparant la session autonome du 18/08 :

1. `_process_one_dreamcoder_task` annonçait 10 minutes par tâche
   (`wait_for(timeout=600)`) mais appelait `run_full_pipeline` SANS
   `timeout_seconds` : le pipeline reprenait son défaut de 120 s, hérité du
   chemin interactif. L'enveloppe était décorative — quatrième occurrence du
   défaut que `core/fenetres_execution.py` existe pour empêcher.

2. Une branche Git impossible à créer laissait la tâche en 'failed' avec
   `retries` à 0 : jamais reprise (get_next_task ne lit que 'pending'), jamais
   abandonnée. Constaté sur le Deck — deux tâches figées depuis juillet.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.fenetres_execution import (
    MARGE_ENVELOPPE_DREAMCODER_S,
    duree_enveloppe_dreamcoder_s,
    duree_planner_s,
    duree_tache_dreamcoder_s,
    verifier_emboitement,
)


# ── La hiérarchie des fenêtres ───────────────────────────────────────────────

def test_fenetre_de_tache_par_defaut():
    """600 s : trois tâches tiennent dans un cycle de 30 min."""
    assert duree_tache_dreamcoder_s() == 600.0


def test_fenetre_surchargeable_par_env():
    with patch.dict(os.environ, {"MOTEUR_DREAMCODER_TACHE_TIMEOUT_S": "300"}):
        assert duree_tache_dreamcoder_s() == 300.0


def test_valeur_illisible_retombe_sur_le_defaut():
    with patch.dict(os.environ, {"MOTEUR_DREAMCODER_TACHE_TIMEOUT_S": "dix minutes"}):
        assert duree_tache_dreamcoder_s() == 600.0


def test_enveloppe_strictement_au_dessus_de_la_tache():
    """
    L'enveloppe doit laisser le pipeline rendre son erreur de timeout et clore
    sa session ; tuée trop tôt, elle laisserait une session zombie (#T361).
    """
    assert duree_enveloppe_dreamcoder_s() > duree_tache_dreamcoder_s()
    assert duree_enveloppe_dreamcoder_s() - duree_tache_dreamcoder_s() == MARGE_ENVELOPPE_DREAMCODER_S


def test_une_tache_depasse_son_propre_planner():
    """C'est exactement ce qui était faux : 120 s de pipeline pour un Planner plus long."""
    assert duree_tache_dreamcoder_s() > duree_planner_s()
    assert verifier_emboitement() == []


def test_emboitement_signale_une_fenetre_trop_courte():
    """Une tâche plus courte que son Planner doit être dénoncée, pas subie."""
    with patch.dict(os.environ, {"MOTEUR_DREAMCODER_TACHE_TIMEOUT_S": "10"}):
        violations = verifier_emboitement()

    assert any("Tâche DreamCoder" in v for v in violations)


# ── Le câblage réel dans la tâche ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_pipeline_recoit_la_fenetre_de_nuit():
    """
    Le bug était l'ABSENCE du paramètre : le pipeline retombait sur 120 s sans
    que rien ne le signale. Ce test échoue si quelqu'un le retire à nouveau.
    """
    from agents import dreamer_agent

    tache = {"id": 77, "title": "Tâche pilote", "description": "faire un truc", "retries": 0}
    bg = MagicMock()
    bg.get_scoped_spend_usd = AsyncMock(return_value=0.0)
    bg.record_usage = AsyncMock()
    bg.config = {"total_daily_budget_usd": 1.0}

    routeur = MagicMock()
    routeur.analyze_request = AsyncMock(return_value=({}, "planner"))
    app_state = MagicMock()
    app_state.get_shared_router.return_value = routeur

    pipeline = AsyncMock(return_value={"status": "completed", "response": "fait"})

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch("core.backlog_db.update_task_status", new=AsyncMock()), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("core.app_state.get_app_state", return_value=app_state), \
         patch("tools.git_safety.git_prepare_agent_branch", return_value="task/77_1787000000"), \
         patch("tools.git_safety.git_generate_semantic_commit_msg", return_value="feat: x"), \
         patch("tools.git_safety._run_git", return_value=(0, "", "")), \
         patch("tools.git_safety.git_rollback_checkpoint", return_value=None), \
         patch.object(dreamer_agent, "_pousser_branche_tache",
                      return_value={"pushed": True, "raison": "ok", "detail": ""}), \
         patch("services.pipeline_service.run_full_pipeline", new=pipeline), \
         patch("core.token_tracker.get_session_total_tokens", return_value=42), \
         patch("core.budget_guard.mark_provider_failed"):
        await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)

    assert pipeline.await_count == 1
    assert pipeline.await_args.kwargs["timeout_seconds"] == duree_tache_dreamcoder_s()


# ── Une branche impossible à créer est un échec qui se compte ────────────────

@pytest.mark.asyncio
async def test_echec_de_branche_incremente_les_tentatives():
    """
    Sans incrément, la tâche sort de 'pending' pour toujours : ni reprise, ni
    abandon. C'est l'état dans lequel deux tâches du Deck étaient figées.
    """
    from agents import dreamer_agent

    tache = {"id": 3, "title": "T", "description": "d", "retries": 0}
    bg = MagicMock()
    bg.get_scoped_spend_usd = AsyncMock(return_value=0.0)
    bg.config = {"total_daily_budget_usd": 1.0}
    maj = AsyncMock()

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch("core.backlog_db.update_task_status", new=maj), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("tools.git_safety.git_prepare_agent_branch",
               return_value="Erreur : dépôt de travail = worktree Git lié."):
        res = await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)

    assert res["status"] == "failed"
    assert maj.await_args.kwargs["retries"] == 1


@pytest.mark.asyncio
async def test_troisieme_echec_de_branche_abandonne():
    """Au 3ᵉ essai, la tâche est abandonnée — elle ne boucle pas chaque nuit."""
    from agents import dreamer_agent

    tache = {"id": 3, "title": "T", "description": "d", "retries": 2}
    bg = MagicMock()
    bg.get_scoped_spend_usd = AsyncMock(return_value=0.0)
    bg.config = {"total_daily_budget_usd": 1.0}
    maj = AsyncMock()

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch("core.backlog_db.update_task_status", new=maj), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("tools.git_safety.git_prepare_agent_branch",
               return_value="Erreur : dépôt de travail = worktree Git lié."):
        res = await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)

    assert res["status"] == "abandoned"
    assert maj.await_args.args[1] == "abandoned"
