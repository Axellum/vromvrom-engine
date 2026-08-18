"""
test_dag_watchdog_configurable.py — Watchdog asynchrone configurable (#T380).

Vérifie que le délai du Circuit Breaker Asynchrone d'une tâche DAG n'est plus un
littéral unique de 120 s :
- le délai par défaut reste 120 s quand rien n'est configuré (non-régression) ;
- une tâche qui dépasse le délai de base mais reste sous le délai configuré pour
  son tier n'est PAS tuée ; la même tâche avec la configuration par défaut l'est ;
- le message de journal du déclenchement contient la durée réelle, l'agent visé
  et le délai appliqué (diagnostic) ;
- une tâche réellement bloquée est toujours interrompue (le watchdog n'est pas
  supprimé ni mis à l'infini).

Aucun appel réseau : `try_swarm_dispatch` est mocké par une simple coroutine qui
dort, et `load_config` renvoie un dict vide pour que seul l'environnement pilote
les délais (déterminisme, indépendant du config.json du poste).
"""

import asyncio
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dag_runner import (
    DEFAULT_DAG_TASK_TIMEOUT_S,
    DAGRunner,
    _delai_watchdog_seconds,
)
from core.state import GlobalState, StateUpdate, TaskPayload


class _Agent:
    """Agent mocké : ne doit jamais être invoqué (le dispatch Swarm est mocké)."""

    def __init__(self, name="executor"):
        self.name = name

    async def invoke(self, payload):
        raise AssertionError("L'agent local ne doit pas être invoqué dans ces tests.")


class _EngineMock:
    """Engine minimal pour instancier le DAGRunner et lancer _run_single_task."""

    def __init__(self):
        self.state = GlobalState(session_id="test_watchdog")
        self._history_lock = asyncio.Lock()
        self.context_manager = None
        self.agents = {"executor": _Agent()}

    async def _validate_modified_yamls(self):
        return None


@pytest.fixture(autouse=True)
def _config_vide_et_env_propre(monkeypatch):
    """Charge une config vide et neutralise les variables d'environnement du watchdog."""
    monkeypatch.setattr("core.llm_gateway.load_config", lambda *a, **k: {})
    for cle in list(os.environ):
        if cle.startswith(("MOTEUR_DAG_TASK_TIMEOUT_S", "MOTEUR_DAG_TASK_TIMEOUT_TIER_")):
            monkeypatch.delenv(cle, raising=False)


def _tache(task_id="t1", tier="fort", timeout_payload=None):
    """Fabrique une tâche DAG, tier par défaut 'fort' (le cas qui a perdu le 17/08)."""
    meta = {"target_agent": "executor", "stage_id": 1, "model_tier": tier}
    if timeout_payload is not None:
        meta["watchdog_timeout_s"] = timeout_payload
    return TaskPayload(
        task_objective=f"objectif {task_id}",
        task_id=task_id,
        depends_on=[],
        metadata=meta,
    )


async def _coroutine_qui_dort(duree_s):
    """Remplace `try_swarm_dispatch` : dort, puis renvoie un succès."""

    async def _fake(*args, **kwargs):
        await asyncio.sleep(duree_s)
        return StateUpdate(
            agent_name="executor",
            status="success",
            result_data="ok",
            metadata={},
        )

    return _fake


class TestResolutionDelai:
    """Résolution du délai par `_delai_watchdog_seconds`."""

    def test_defaut_120_quand_rien_configure(self):
        # Non-régression : rien configuré → 120 s, la valeur historique.
        assert DEFAULT_DAG_TASK_TIMEOUT_S == 120.0
        assert _delai_watchdog_seconds(_tache(), config={}) == 120.0

    def test_config_de_base_surcharge_le_defaut(self):
        config = {"dag_task_timeout_s": 300.0}
        assert _delai_watchdog_seconds(_tache(), config=config) == 300.0

    def test_env_de_base_surcharge_la_config(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "240")
        config = {"dag_task_timeout_s": 300.0}
        # L'env prime sur config.json.
        assert _delai_watchdog_seconds(_tache(), config=config) == 240.0

    def test_tier_fort_obtient_un_delai_plus_long_que_le_defaut(self):
        # Le cœur du correctif : une tâche de tier fort peut dépasser 120 s sans
        # être anormale (lecture de sources + synthèse LLM).
        config = {"dag_task_timeout_by_tier": {"fort": 300.0}}
        assert _delai_watchdog_seconds(_tache(tier="fort"), config=config) == 300.0

    def test_tier_non_declare_retombe_sur_le_defaut(self):
        config = {"dag_task_timeout_by_tier": {"fort": 300.0}}
        # Tier 'moyen' non déclaré dans la table → délai de base (défaut 120).
        assert _delai_watchdog_seconds(_tache(tier="moyen"), config=config) == 120.0

    def test_env_par_tier_prime_sur_config_par_tier(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_TIER_FORT_S", "400")
        config = {"dag_task_timeout_by_tier": {"fort": 300.0}}
        assert _delai_watchdog_seconds(_tache(tier="fort"), config=config) == 400.0

    def test_surcharge_payload_prioritaire(self, monkeypatch):
        # Surcharge explicite de la tâche au-dessus de tout.
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_TIER_FORT_S", "400")
        config = {"dag_task_timeout_by_tier": {"fort": 300.0}, "dag_task_timeout_s": 120.0}
        assert _delai_watchdog_seconds(_tache(timeout_payload=500), config=config) == 500.0

    def test_valeur_illisible_retombe_sur_le_defaut(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "abc")
        assert _delai_watchdog_seconds(_tache(), config={}) == 120.0


class TestComportementWatchdog:
    """Le vrai chemin `_run_single_task` respecte le délai configuré."""

    async def _executer(self, runner, tache, fake_dispatch):
        with patch("core.dag_runner.try_swarm_dispatch", fake_dispatch):
            return await runner._run_single_task(tache.task_id, tache, on_event=None)

    async def test_tache_sous_son_delai_de_tier_n_est_pas_tuee(self, monkeypatch):
        # Le test central : une tâche de tier fort qui dort 0,3 s dépasse le
        # délai de base (0,1 s) mais reste sous le délai de son tier (0,5 s).
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "0.1")
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_TIER_FORT_S", "0.5")
        runner = DAGRunner(_EngineMock())

        tache = _tache(tier="fort")
        _, t_upd = await self._executer(runner, tache, await _coroutine_qui_dort(0.3))

        assert t_upd.status == "success"

    async def test_meme_tache_avec_configuration_par_defaut_est_tuee(self, monkeypatch):
        # La même tâche, sans surcharge de tier (seul le délai de base court
        # s'applique) : 0,3 s > 0,1 s → le watchdog la tue.
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "0.1")
        runner = DAGRunner(_EngineMock())

        tache = _tache(tier="fort")
        _, t_upd = await self._executer(runner, tache, await _coroutine_qui_dort(0.3))

        assert t_upd.status == "error"
        assert "Timeout" in (t_upd.error_message or "")
        assert "0.1" in (t_upd.error_message or "")

    async def test_message_de_journal_contient_duree_agent_et_delai(self, monkeypatch, caplog):
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "0.1")
        runner = DAGRunner(_EngineMock())

        tache = _tache(task_id="t_diag", tier="fort")

        async def _bloquant(*a, **k):
            await asyncio.sleep(30.0)

        with caplog.at_level(logging.ERROR, logger="core.dag_runner"):
            await self._executer(runner, tache, _bloquant)

        # Le log de diagnostic doit nommer la tâche, l'agent visé et le délai.
        assert any("t_diag" in r.message for r in caplog.records)
        assert any("executor" in r.message for r in caplog.records)
        assert any("0.1" in r.message for r in caplog.records)
        # La durée réelle est un nombre (au moins un chiffre suivi de 's').
        assert any("s d'exécution" in r.message for r in caplog.records)

    async def test_tache_reellement_bloquee_est_toujours_interrompue(self, monkeypatch):
        # Une tâche qui ne rend jamais la main est interrompue ~au délai, pas
        # laissée tourner : le watchdog n'est ni supprimé ni mis à l'infini.
        monkeypatch.setenv("MOTEUR_DAG_TASK_TIMEOUT_S", "0.2")
        runner = DAGRunner(_EngineMock())

        tache = _tache(tier="fort")

        async def _bloquant(*a, **k):
            await asyncio.sleep(30.0)  # beaucoup plus que le délai

        t0 = asyncio.get_event_loop().time()
        with patch("core.dag_runner.try_swarm_dispatch", _bloquant):
            _, t_upd = await runner._run_single_task(tache.task_id, tache, on_event=None)
        ecoule = asyncio.get_event_loop().time() - t0

        assert t_upd.status == "error"
        assert "Timeout" in (t_upd.error_message or "")
        # Interrompue bien avant les 30 s de la coroutine, ~au délai configuré.
        assert ecoule < 5.0
