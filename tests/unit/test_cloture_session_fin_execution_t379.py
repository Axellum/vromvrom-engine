"""
tests/unit/test_cloture_session_fin_execution_t379.py — Une exécution qui se
termine doit le dire (#T379).

Observation du 17/08/2026, 20 h : trois lots soumis au moteur, journal et base
à l'appui. Les trois DAG se sont terminés à 20:06 — un succès, deux échecs —
journalisés par `services/approval_resume_service.py` (« Demande … rejouée —
succès / échec »). Vingt minutes plus tard, la base disait encore :
sessions `status='running'`, dag_tasks 13 `pending` / 3 `error` / 3 `success`.
Plus rien ne tournait, et rien ne le disait : ces sessions seraient devenues
des « zombies » une heure plus tard par ramassage, diagnostiquées comme une
panne alors qu'elles s'étaient simplement terminées sans le dire.

Ce que ces tests verrouillent :
- LE TEST CENTRAL : une reprise après approbation qui se termine en ÉCHEC
  laisse une session au statut terminal avec une date de fin — plus jamais
  `running` (exactement le cas observé le 17/08) ;
- même chose pour une reprise qui réussit ;
- même chose pour une exception levée en cours de rejeu ;
- une session encore en cours n'est JAMAIS marquée terminée ;
- une session en attente d'approbation (HITL) reste intacte ;
- le rejet humain du plan clôture lui aussi la session ;
- le moteur NOMME la cause de l'échec (DAG, Reviewer, contrat) au lieu de la
  réduire à un booléen.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core import hitl_store, runtime_db
from core.session_history import (
    get_session_detail,
    record_session_end,
    record_session_start,
)


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path):
    """Chaque test écrit dans sa propre base runtime (motif des tests HITL)."""
    ancien = runtime_db.get_db_path()
    runtime_db.override_db_path(str(tmp_path / "runtime_test.db"))
    runtime_db.get_connection().close()  # déclenche _init_schema
    yield
    runtime_db.override_db_path(ancien)


def _dag_serialise() -> list[dict]:
    return [
        {"task_objective": "Créer le livrable", "task_id": "t1", "depends_on": [],
         "available_tools": ["write_file"], "relevant_context": "", "metadata": {},
         "status": "pending", "assigned_agent": None, "result_summary": None},
    ]


def _demande(request_id: str, session_id: str) -> None:
    """Enregistre une demande HITL pour la session, sans la décider encore."""
    hitl_store.enregistrer_demande(
        request_id=request_id, session_id=session_id,
        dag_tasks=_dag_serialise(), description="1 tâche à risque",
    )


def _brancher_moteur(
    monkeypatch,
    engine,
) -> None:
    """
    Fait monter ce moteur précis par la reprise, sans rien construire d'autre.

    `monter_moteur` et `ExecutionBudget.from_config` sont interceptés comme
    dans les tests du plancher du 17/08 : la reprise doit tourner sur un
    moteur contrôlé, jamais sur les vrais providers.
    """
    bridge = MagicMock()
    bridge.start = AsyncMock()
    bridge.stop = AsyncMock()

    def _faux_moteur(session_id, objectif, config):
        return engine, MagicMock(), bridge, MagicMock()

    monkeypatch.setattr("services.pipeline_service.monter_moteur", _faux_moteur)
    monkeypatch.setattr(
        "core.execution_budget.ExecutionBudget.from_config",
        classmethod(lambda cls, session_id: MagicMock(max_tokens=1000)),
    )
    monkeypatch.setattr("core.llm_gateway.load_config", lambda: {})


def _moteur_reel(session_id: str):
    """Engine réel sans agents : seuls le DAG et les contrôles sont exercés."""
    from core.engine import Engine
    engine = Engine(session_id=session_id)
    # L'enregistrement des skills écrit en base : hors périmètre de ces tests.
    engine._record_skills_from_dag = lambda *a, **k: None
    return engine


# ── LE test central : la reprise en ÉCHEC clôture la session (cas du 17/08) ─


@pytest.mark.asyncio
async def test_reprise_en_echec_cloture_la_session(monkeypatch):
    """
    Rejoue la scène du 17/08 : une session suspendue puis reprise, dont le DAG
    rejoué échoue, doit porter un statut terminal et une date de fin — pas
    rester `running` jusqu'au ramassage des zombies.
    """
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_93d1657b"
    record_session_start(session_id, "objectif du lot", "planner")
    # La suspension avait clos la session en attente d'approbation.
    record_session_end(session_id, "waiting_approval")
    _demande("r_echec", session_id)
    hitl_store.marquer_decision("r_echec", approuve=True)

    engine = _moteur_reel(session_id)

    async def _dag_en_erreur(**kwargs):
        return {"t1": "error"}, True

    async def _contrat_en_echec(on_event=None):
        return False  # le contrat CONFIRME l'échec du DAG

    engine._dag_runner.execute_dag = _dag_en_erreur
    engine._review_loop.contrat_seul = _contrat_en_echec
    _brancher_moteur(monkeypatch, engine)

    res = await reprendre_apres_approbation("r_echec")

    assert res["status"] == "error"
    detail = get_session_detail(session_id)
    assert detail["status"] == "error", "la session doit porter un statut terminal"
    assert detail["status"] != "running", "plus jamais « running » après la fin du DAG"
    assert detail["ended_at"] is not None, "la date de fin s'écrit au moment de l'arrêt"
    assert "Contrat d'acceptation non satisfait" in (detail["error_message"] or ""), \
        "la cause décidée par le moteur doit être lisible dans la session"


@pytest.mark.asyncio
async def test_reprise_en_succes_cloture_la_session(monkeypatch):
    """Contre-épreuve : la reprise qui réussit écrit elle aussi la clôture."""
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_1a3aa453"
    record_session_start(session_id, "objectif du lot", "planner")
    record_session_end(session_id, "waiting_approval")
    _demande("r_succes", session_id)
    hitl_store.marquer_decision("r_succes", approuve=True)

    engine = _moteur_reel(session_id)

    async def _dag_reussi(**kwargs):
        return {"t1": "success"}, False

    engine._dag_runner.execute_dag = _dag_reussi
    _brancher_moteur(monkeypatch, engine)

    res = await reprendre_apres_approbation("r_succes")

    assert res["status"] == "completed"
    detail = get_session_detail(session_id)
    assert detail["status"] == "success"
    assert detail["ended_at"] is not None


@pytest.mark.asyncio
async def test_exception_en_cours_de_rejeu_cloture_la_session(monkeypatch):
    """Une exception levée pendant le rejeu est une fin d'exécution comme une autre."""
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_b26bc903"
    record_session_start(session_id, "objectif du lot", "planner")
    record_session_end(session_id, "waiting_approval")
    _demande("r_exception", session_id)
    hitl_store.marquer_decision("r_exception", approuve=True)

    engine = _moteur_reel(session_id)

    async def _dag_explose(**kwargs):
        raise RuntimeError("perte de connexion MCP pendant le rejeu")

    engine._dag_runner.execute_dag = _dag_explose
    _brancher_moteur(monkeypatch, engine)

    res = await reprendre_apres_approbation("r_exception")

    assert res["status"] == "error"
    detail = get_session_detail(session_id)
    assert detail["status"] == "error"
    assert detail["ended_at"] is not None
    assert "perte de connexion MCP" in (detail["error_message"] or "")


@pytest.mark.asyncio
async def test_reprise_avortee_dag_vide_cloture_la_session(monkeypatch):
    """Une reprise sans DAG exploitable s'arrête définitivement — et le dit."""
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_dag_vide"
    record_session_start(session_id, "objectif du lot", "planner")
    record_session_end(session_id, "waiting_approval")
    hitl_store.enregistrer_demande(
        request_id="r_vide_close", session_id=session_id, dag_tasks=[], description="",
    )
    hitl_store.marquer_decision("r_vide_close", approuve=True)

    res = await reprendre_apres_approbation("r_vide_close")

    assert res["status"] == "vide"
    detail = get_session_detail(session_id)
    assert detail["status"] == "error"
    assert detail["ended_at"] is not None


# ── Garde-fous : jamais de clôture pour une exécution qui n'est pas finie ────


@pytest.mark.asyncio
async def test_session_en_cours_jamais_marquee_terminee():
    """
    Une demande encore `pending` n'est pas une fin d'exécution : la reprise
    refuse de rejouer et la session reste `running`, sans date de fin.
    """
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_encours"
    record_session_start(session_id, "objectif en cours", "planner")
    _demande("r_encore", session_id)  # jamais approuvée

    res = await reprendre_apres_approbation("r_encore")

    assert res["status"] == "non_approuve"
    detail = get_session_detail(session_id)
    assert detail["status"] == "running", "une session en cours n'est jamais close"
    assert detail["ended_at"] is None


@pytest.mark.asyncio
async def test_session_en_attente_d_approbation_reste_intacte():
    """
    HITL : une session suspendue peut légitimement attendre des heures.
    Tant que la décision n'est pas prise, rien ne doit toucher à son statut.
    """
    from services.approval_resume_service import reprendre_apres_approbation

    session_id = "bg_attente"
    record_session_start(session_id, "objectif suspendu", "planner")
    record_session_end(session_id, "waiting_approval")
    _demande("r_attente", session_id)  # toujours pending

    res = await reprendre_apres_approbation("r_attente")

    assert res["status"] == "non_approuve"
    detail = get_session_detail(session_id)
    assert detail["status"] == "waiting_approval", \
        "une attente d'approbation n'est pas une fin d'exécution"


@pytest.mark.asyncio
async def test_rejet_humain_cloture_la_session():
    """Rejeter le plan est une fin d'exécution définitive : la session le porte."""
    from services.approval_resume_service import annuler_apres_rejet

    session_id = "bg_rejete"
    record_session_start(session_id, "objectif rejeté", "planner")
    record_session_end(session_id, "waiting_approval")
    _demande("r_rejete", session_id)
    hitl_store.marquer_decision("r_rejete", approuve=False)

    res = annuler_apres_rejet("r_rejete")

    assert res["status"] == "rien_a_annuler"  # pas de branche Git dans ce test
    detail = get_session_detail(session_id)
    assert detail["status"] == "error"
    assert detail["ended_at"] is not None
    assert "rejeté par l'utilisateur" in (detail["error_message"] or "").lower()


# ── Le moteur nomme la cause au lieu de la réduire à un booléen ──────────────


@pytest.mark.asyncio
async def test_cause_echec_revue_rejetee():
    """Rejet du Reviewer : la cause est nommée pour la clôture de session."""
    from core.state import TaskPayload

    engine = _moteur_reel("sess_cause_revue")
    engine.agents["reviewer"] = MagicMock()  # revue activée : reviewer présent
    engine._is_review_enabled = lambda: True

    async def _dag_reussi(**kwargs):
        return {"t1": "success"}, False

    async def _revue_rejette(initial_objective, max_rounds=3, on_event=None):
        return False

    engine._dag_runner.execute_dag = _dag_reussi
    engine._review_loop.run_review = _revue_rejette

    taches = [TaskPayload(task_objective="tâche", task_id="t1")]
    _tasks_status, has_error = await engine.executer_dag_et_controles(
        dag_tasks=taches, objectif="obj", max_session_tokens=1000,
    )

    assert has_error is True
    assert "Reviewer" in (engine.cause_echec or "")


@pytest.mark.asyncio
async def test_cause_echec_contrat_confirme_l_echec():
    """DAG en erreur + contrat non satisfait : le contrat devient la cause."""
    from core.state import TaskPayload

    engine = _moteur_reel("sess_cause_contrat")

    async def _dag_en_erreur(**kwargs):
        return {"t1": "error"}, True

    async def _contrat_en_echec(on_event=None):
        return False

    engine._dag_runner.execute_dag = _dag_en_erreur
    engine._review_loop.contrat_seul = _contrat_en_echec

    taches = [TaskPayload(task_objective="tâche", task_id="t1")]
    _tasks_status, has_error = await engine.executer_dag_et_controles(
        dag_tasks=taches, objectif="obj", max_session_tokens=1000,
    )

    assert has_error is True
    assert "Contrat d'acceptation non satisfait" in (engine.cause_echec or "")


@pytest.mark.asyncio
async def test_contrat_satisfait_absout_et_efface_la_cause():
    """Contre-épreuve : contrat prouvé → succès, et plus aucune cause d'échec."""
    from core.state import TaskPayload

    engine = _moteur_reel("sess_cause_absoute")

    async def _dag_en_erreur(**kwargs):
        return {"t1": "error"}, True

    async def _contrat_satisfait(on_event=None):
        return True

    engine._dag_runner.execute_dag = _dag_en_erreur
    engine._review_loop.contrat_seul = _contrat_satisfait

    taches = [TaskPayload(task_objective="tâche", task_id="t1")]
    tasks_status, has_error = await engine.executer_dag_et_controles(
        dag_tasks=taches, objectif="obj", max_session_tokens=1000,
    )

    assert has_error is False
    assert engine.cause_echec is None
    assert tasks_status["t1"] == "success"  # absolution aussi pour la branche Git
