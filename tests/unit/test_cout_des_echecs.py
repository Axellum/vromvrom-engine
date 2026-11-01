"""
tests/unit/test_cout_des_echecs.py — une tâche qui échoue a quand même dépensé (#T416).

Relevé le 19/08 sur la base de production du Deck : les **38 tâches en échec**
portaient toutes `tokens_used = 0`. Un échec à 1,1 M de jetons se lisait
« gratuit », et le rendement du cycle en héritait.

Le trou était plus large que la colonne. `tokens_used` et `cost` étaient
assignés APRÈS le retour du pipeline, et `BudgetGuard.record_usage` juste
derrière. Quand le pipeline **lève** — timeout d'enveloppe, provider mort — on
saute par-dessus les deux :

    - la ligne de tâche garde 0 ;
    - **le BudgetGuard ne voit rien non plus**, donc le plafond quotidien est
      aveugle aux échecs. Une série de timeouts pouvait brûler le budget sans
      jamais l'entamer sur le papier.

Ces tests figent les deux moitiés : la lecture bornée de la consommation, et son
imputation sur le chemin d'échec.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents import dreamer_agent

# ── Lecture de la consommation ──────────────────────────────────────────────

@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base runtime isolée — jamais la `moteur_runtime.db` de prod dans les tests."""
    from core import runtime_db

    monkeypatch.setattr(runtime_db, "_DB_PATH", str(tmp_path / "runtime_test.db"))
    with runtime_db.get_connection() as conn:
        conn.commit()


def _poser_usage(session_id: str, horodatage: float, cout: float, jetons: int = 10):
    from core.runtime_db import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, horodatage, "m", jetons, 0, jetons, cout))
        conn.commit()


def test_le_cout_est_borne_au_passage_courant(base):
    """
    [#T393] Le `session_id` d'une tâche est `task_<id>`, stable d'un rejeu à
    l'autre. Sans la borne, un quatrième passage ramasserait le coût des trois
    précédents — facteur 8 mesuré le 18/08.
    """
    debut = 1_787_000_000.0
    _poser_usage("task_9", debut - 3600, cout=9.99)   # passage précédent
    _poser_usage("task_9", debut + 10, cout=0.0042)   # ce passage-ci

    _jetons, cout = dreamer_agent._consommation_de_la_tache("task_9", debut)

    assert cout == pytest.approx(0.0042)


def test_une_session_sans_consommation_rend_zero(base):
    assert dreamer_agent._consommation_de_la_tache("task_inconnue", 0.0) == (0, 0.0)


def test_une_base_illisible_ne_leve_pas(monkeypatch):
    """La comptabilité ne doit jamais faire échouer la tâche qu'elle mesure."""
    def explose(*_a, **_k):
        raise RuntimeError("base verrouillée")

    monkeypatch.setattr("core.runtime_db.get_connection", explose)
    monkeypatch.setattr("core.token_tracker.get_session_total_tokens", explose)

    assert dreamer_agent._consommation_de_la_tache("task_1", 0.0) == (0, 0.0)


# ── Le chemin d'échec impute ce qu'il a dépensé ─────────────────────────────

def _bg_factice():
    bg = MagicMock()
    bg.get_scoped_spend_usd = AsyncMock(return_value=0.0)
    bg.record_usage = AsyncMock()
    bg.config = {"total_daily_budget_usd": 1.0}
    return bg


async def _executer_avec_pipeline_qui_leve(maj, bg, erreur: Exception):
    """Joue une tâche dont le pipeline lève, comme un timeout d'enveloppe."""
    tache = {"id": 42, "title": "T", "description": "d", "retries": 0}
    routeur = MagicMock()
    routeur.analyze_request = AsyncMock(return_value=({}, "planner"))
    app_state = MagicMock()
    app_state.get_shared_router.return_value = routeur

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch.object(dreamer_agent, "_consommation_de_la_tache",
                      return_value=(1_134_881, 0.2899)), \
         patch("core.backlog_db.update_task_status", new=maj), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("core.app_state.get_app_state", return_value=app_state), \
         patch("tools.git_safety.git_prepare_agent_branch", return_value="task/42_1787000000"), \
         patch("tools.git_safety._run_git", return_value=(0, "", "")), \
         patch("tools.git_safety.git_rollback_checkpoint", return_value=None), \
         patch("services.pipeline_service.run_full_pipeline", new=AsyncMock(side_effect=erreur)), \
         patch("core.budget_guard.mark_provider_failed"):
        return await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)


@pytest.mark.asyncio
async def test_un_echec_porte_ses_jetons_en_base():
    """C'est la colonne qui valait 0 sur les 38 tâches en échec du 18-19/08."""
    maj, bg = AsyncMock(), _bg_factice()

    resultat = await _executer_avec_pipeline_qui_leve(
        maj, bg, TimeoutError("l'enveloppe de 600 s a expiré"))

    assert resultat["status"] == "failed"
    assert maj.await_args.kwargs["tokens_used"] == 1_134_881
    assert resultat["tokens_used"] == 1_134_881
    assert resultat["cost_usd"] == pytest.approx(0.2899)


@pytest.mark.asyncio
async def test_un_echec_est_impute_au_budget():
    """
    Le vrai trou : `record_usage` était derrière le retour du pipeline. Un
    pipeline qui lève laissait le plafond quotidien aveugle à la dépense.
    """
    maj, bg = AsyncMock(), _bg_factice()

    await _executer_avec_pipeline_qui_leve(maj, bg, TimeoutError("expiré"))

    assert bg.record_usage.await_count == 1
    assert bg.record_usage.await_args.kwargs["tokens"] == 1_134_881
    assert bg.record_usage.await_args.kwargs["cost"] == pytest.approx(0.2899)


@pytest.mark.asyncio
async def test_pas_de_double_comptage_quand_le_pipeline_a_repondu():
    """
    Un échec de QUALITÉ (branche vide, tests rouges) survient après le
    `record_usage` du chemin nominal. Le compter deux fois gonflerait le budget
    exactement comme le faisait le coût non borné de #T393.
    """
    maj, bg = AsyncMock(), _bg_factice()
    tache = {"id": 43, "title": "T", "description": "d", "retries": 0}
    routeur = MagicMock()
    routeur.analyze_request = AsyncMock(return_value=({}, "planner"))
    app_state = MagicMock()
    app_state.get_shared_router.return_value = routeur

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch.object(dreamer_agent, "_consommation_de_la_tache", return_value=(500, 0.05)), \
         patch("core.backlog_db.update_task_status", new=maj), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("core.app_state.get_app_state", return_value=app_state), \
         patch("tools.git_safety.git_prepare_agent_branch", return_value="task/43_1787000000"), \
         patch("tools.git_safety._run_git", return_value=(0, "", "")), \
         patch("tools.git_safety.git_rollback_checkpoint", return_value=None), \
         patch("services.pipeline_service.run_full_pipeline",
               new=AsyncMock(return_value={"status": "echec", "error": "tests rouges"})), \
         patch("core.budget_guard.mark_provider_failed"):
        resultat = await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)

    assert resultat["status"] == "failed"
    assert bg.record_usage.await_count == 1, "la consommation ne doit être imputée qu'une fois"
    assert maj.await_args.kwargs["tokens_used"] == 500


@pytest.mark.asyncio
async def test_une_tache_sans_depense_n_impute_rien():
    """
    Une panne qui tue la tâche avant le premier appel LLM n'a rien coûté :
    inventer une imputation à 0 encombrerait le budget sans rien mesurer.
    """
    maj, bg = AsyncMock(), _bg_factice()
    tache = {"id": 44, "title": "T", "description": "d", "retries": 0}
    routeur = MagicMock()
    routeur.analyze_request = AsyncMock(return_value=({}, "planner"))
    app_state = MagicMock()
    app_state.get_shared_router.return_value = routeur

    with patch.object(dreamer_agent, "_dreamcoder_repo_root", return_value="/travail"), \
         patch.object(dreamer_agent, "_consommation_de_la_tache", return_value=(0, 0.0)), \
         patch("core.backlog_db.update_task_status", new=maj), \
         patch("core.llm_gateway.load_config", return_value={}), \
         patch("core.app_state.get_app_state", return_value=app_state), \
         patch("tools.git_safety.git_prepare_agent_branch", return_value="task/44_1787000000"), \
         patch("tools.git_safety._run_git", return_value=(0, "", "")), \
         patch("tools.git_safety.git_rollback_checkpoint", return_value=None), \
         patch("services.pipeline_service.run_full_pipeline",
               new=AsyncMock(side_effect=TimeoutError("expiré"))), \
         patch("core.budget_guard.mark_provider_failed"):
        await dreamer_agent._process_one_dreamcoder_task(
            tache, {}, "deepseek-free", "master", bg)

    assert bg.record_usage.await_count == 0
    assert maj.await_args.kwargs["tokens_used"] == 0
