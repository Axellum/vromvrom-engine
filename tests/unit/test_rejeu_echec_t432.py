"""
tests/unit/test_rejeu_echec_t432.py — #T432 : une tâche failed n'est plus un cimetière.

Trois propriétés mesurées le 20–22/08 :

1. Le drainage ne lisait que `pending` : une tâche `failed` restait hors file,
   et la découverte refusait de la ré-injecter (clé unique). 21 sources → 0
   candidate, nuit vide.
2. Un 429 brûlait une tentative comme un échec de code.
3. Une branche poussée n'ouvrait pas de PR : `task/70` et `task/84` orphelines.
"""

import asyncio
import io
import json
import time
import urllib.error
from unittest.mock import patch

import pytest

from agents.dreamer_agent import (
    _delai_rejeu_echec_s,
    _ouvrir_pr_tache,
    _proprietaire_depot_github,
    _tests_figent_un_chemin_machine,
    _verdict_apres_echec,
)
from core.backlog_db import (
    MAX_TENTATIVES_TACHE,
    add_task,
    get_next_task,
    init_backlog_db,
    reserver_prochaine_tache,
    update_task_status,
)


@pytest.fixture(autouse=True)
def _verrous_neufs_par_test():
    import core.backlog_db as backlog_db

    ancien = backlog_db._db_write_lock
    backlog_db._db_write_lock = asyncio.Lock()
    yield
    backlog_db._db_write_lock = ancien


@pytest.fixture
async def backlog_vide():
    await init_backlog_db()


# ── Verdict d'échec ──────────────────────────────────────────────────────────

def test_infra_remet_en_pending_sans_bruler():
    v = _verdict_apres_echec("Tous les providers ont échoué, circuits ouverts", 2)
    assert v["action"] == "liberer"
    assert v["status"] == "infra_indisponible"
    assert v["retries"] == 2
    assert v["cooldown"] is False
    assert v["scheduled_at"] is None


def test_429_remet_en_pending_sans_bruler():
    v = _verdict_apres_echec("429 Too Many Requests", 1)
    assert v["action"] == "liberer"
    assert v["cause"] == "provider"
    assert v["cooldown"] is True
    assert v["retries"] == 1


def test_tests_livres_avec_timeout_dans_le_nom_restent_un_echec_code():
    """`test_anthropic_timeout.py` contient 'timeout' : ce n'est pas un 429."""
    v = _verdict_apres_echec(
        "Le pipeline s'est déclaré terminé mais les tests livrés échouent "
        "(tests/unit/test_anthropic_timeout.py) : 1 failed",
        0,
    )
    assert v["action"] == "clore"
    assert v["status"] == "failed"
    assert v["cooldown"] is False
    assert v["retries"] == 1


def test_401_llm_brule_une_tentative_et_pose_un_horaire():
    """Clé morte : cooldown du modèle, mais la tâche avance vers l'abandon."""
    avant = time.time()
    v = _verdict_apres_echec("HTTPStatusError: Client error '401 Unauthorized'", 0)
    assert v["status"] == "failed"
    assert v["cooldown"] is True
    assert v["retries"] == 1
    assert v["scheduled_at"] >= avant + 60


def test_echec_code_programme_un_rejeu():
    avant = time.time()
    v = _verdict_apres_echec("la branche task/1 est identique à master", 0)
    assert v["status"] == "failed"
    assert v["retries"] == 1
    assert v["cooldown"] is False
    assert v["scheduled_at"] >= avant + 60


def test_troisieme_echec_code_abandonne():
    v = _verdict_apres_echec("les tests livrés échouent", MAX_TENTATIVES_TACHE - 1)
    assert v["status"] == "abandoned"
    assert v["retries"] == MAX_TENTATIVES_TACHE
    assert v["scheduled_at"] is None


def test_delai_plancher_une_minute(monkeypatch):
    monkeypatch.setenv("MOTEUR_REJEU_ECHEC_S", "1")
    assert _delai_rejeu_echec_s() == 60.0


# ── Drainage : failed redevient jouable ──────────────────────────────────────

@pytest.mark.asyncio
async def test_un_failed_sans_horaire_est_repris(backlog_vide):
    """Les échecs d'avant #T432 n'ont pas de scheduled_at : on les débloque."""
    tid = await add_task(title="Ancien échec", description="d")
    await update_task_status(tid, "failed", retries=1)

    tache = await reserver_prochaine_tache()
    assert tache is not None
    assert tache["id"] == tid


@pytest.mark.asyncio
async def test_un_failed_en_garde_n_est_pas_repris(backlog_vide):
    tid = await add_task(title="Trop tôt", description="d")
    await update_task_status(
        tid, "failed", retries=1, scheduled_at=time.time() + 3600
    )

    assert await reserver_prochaine_tache() is None
    assert await get_next_task() is None


@pytest.mark.asyncio
async def test_un_failed_dont_le_delai_est_ecoule_est_repris(backlog_vide):
    tid = await add_task(title="Délai échu", description="d")
    await update_task_status(
        tid, "failed", retries=1, scheduled_at=time.time() - 10
    )

    tache = await get_next_task()
    assert tache is not None
    assert tache["id"] == tid


@pytest.mark.asyncio
async def test_abandoned_reste_hors_file(backlog_vide):
    tid = await add_task(title="Abandonnée", description="d")
    await update_task_status(tid, "abandoned", retries=3)

    assert await reserver_prochaine_tache() is None


@pytest.mark.asyncio
async def test_failed_au_plafond_reste_hors_file(backlog_vide):
    """retries >= 3 en `failed` (jamais passé à abandoned) : revue humaine."""
    tid = await add_task(title="Plafond atteint", description="d")
    await update_task_status(tid, "failed", retries=MAX_TENTATIVES_TACHE)

    assert await reserver_prochaine_tache() is None
    assert await get_next_task() is None


@pytest.mark.asyncio
async def test_completed_reste_hors_file(backlog_vide):
    tid = await add_task(title="Livrée", description="d")
    await update_task_status(tid, "completed")

    assert await reserver_prochaine_tache() is None


@pytest.mark.asyncio
async def test_pending_plus_urgente_passe_avant_un_failed(backlog_vide):
    failed_id = await add_task(title="Vieil échec", description="d", priority=2)
    await update_task_status(failed_id, "failed", retries=1)
    pending_id = await add_task(title="Neuve urgente", description="d", priority=1)

    tache = await reserver_prochaine_tache()
    assert tache["id"] == pending_id


# ── Porte chemins machine ────────────────────────────────────────────────────

def test_constante_deck_detectee(tmp_path):
    f = tmp_path / "test_wake.py"
    f.write_text(
        'INDEX_PATH = "/home/deck/dev_station/moteur_agents_dc3/docs/x.json"\n',
        encoding="utf-8",
    )
    assert _tests_figent_un_chemin_machine(str(tmp_path), ["test_wake.py"]) == [
        "test_wake.py"
    ]


def test_exemple_dans_un_appel_n_est_pas_une_constante(tmp_path):
    """`est_exclu("/home/deck/...")` n'assigne rien : ce n'est pas le bug #445."""
    f = tmp_path / "test_exclu.py"
    f.write_text(
        'assert est_exclu("/opt/vromvrom-engine/ihm-v2/node_modules/x")\n',
        encoding="utf-8",
    )
    assert _tests_figent_un_chemin_machine(str(tmp_path), ["test_exclu.py"]) == []


# ── Ouverture de PR ──────────────────────────────────────────────────────────

def test_slug_github_ssh_et_https():
    assert _proprietaire_depot_github(
        "git@github.com:Axellum/moteur_agents.git"
    ) == ("Axellum", "moteur_agents")
    assert _proprietaire_depot_github(
        "https://github.com/Axellum/moteur_agents.git"
    ) == ("Axellum", "moteur_agents")
    assert _proprietaire_depot_github(
        "https://x-access-token:ghs_x@github.com/Axellum/moteur_agents.git"
    ) == ("Axellum", "moteur_agents")
    assert _proprietaire_depot_github("no-push://le-deck") is None


def test_sans_jeton_pas_de_pr(tmp_path, monkeypatch):
    monkeypatch.delenv("MOTEUR_GITHUB_PUSH_TOKEN", raising=False)
    res = _ouvrir_pr_tache(str(tmp_path), "task/1_1", "t", "c")
    assert res["ouverte"] is False
    assert res["raison"] == "jeton_absent"


def test_pr_ouverte_par_l_api(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTEUR_GITHUB_PUSH_TOKEN", "ghp_test")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"html_url": "https://github.com/Axellum/moteur_agents/pull/450"}
            ).encode()

    with patch(
        "tools.git_safety._run_git",
        return_value=(0, "git@github.com:Axellum/moteur_agents.git", ""),
    ), patch("urllib.request.urlopen", return_value=_Resp()):
        res = _ouvrir_pr_tache(str(tmp_path), "task/84_1", "[auto] T432", "corps")

    assert res["ouverte"] is True
    assert res["raison"] == "ok"
    assert res["url"].endswith("/pull/450")


def test_pr_deja_ouverte_est_un_succes(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTEUR_GITHUB_PUSH_TOKEN", "ghp_test")
    corps = b'{"message":"Validation Failed","errors":[{"message":"A pull request already exists"}]}'
    err = urllib.error.HTTPError(
        "https://api.github.com/repos/a/b/pulls",
        422,
        "Unprocessable Entity",
        hdrs={},
        fp=io.BytesIO(corps),
    )
    with patch(
        "tools.git_safety._run_git",
        return_value=(0, "https://github.com/Axellum/moteur_agents.git", ""),
    ), patch("urllib.request.urlopen", side_effect=err):
        res = _ouvrir_pr_tache(str(tmp_path), "task/1_1", "t", "c")

    assert res["ouverte"] is True
    assert res["raison"] == "deja_ouverte"


@pytest.mark.asyncio
async def test_running_orpheline_avec_retry_redevient_failed(backlog_vide):
    from core.backlog_db import get_task_by_id, recuperer_taches_running_orphelines

    tid = await add_task(title="Zombie qualité", description="d")
    await update_task_status(
        tid,
        "running",
        retries=1,
        error_message="livrable est insuffisant",
        git_branch="task/50_1",
    )
    stats = await recuperer_taches_running_orphelines()
    assert stats["failed"] == 1
    tache = await get_task_by_id(tid)
    assert tache["status"] == "failed"
    assert tache["git_branch"] == "task/50_1"
    assert "insuffisant" in (tache["error_message"] or "")


@pytest.mark.asyncio
async def test_running_orpheline_sans_retry_redevient_pending(backlog_vide):
    from core.backlog_db import get_task_by_id, recuperer_taches_running_orphelines

    tid = await add_task(title="Coupée net", description="d")
    await update_task_status(tid, "running")
    stats = await recuperer_taches_running_orphelines()
    assert stats["pending"] == 1
    tache = await get_task_by_id(tid)
    assert tache["status"] == "pending"
