"""
tests/unit/test_reprise_controles_post_dag.py — Contrôles après reprise (#T253).

Trou trouvé en mesurant la première chaîne complète en production (11/08,
`bg_6148ea67`) : la reprise après approbation humaine appelait `execute_dag()`
directement et **sautait donc tout ce qui suit normalement le DAG** — revue
post-DAG, contrat d'acceptation, enregistrement des skills.

Constat qui l'a révélé : le Planner avait bien émis son contrat
(`[PLANNER] [T253] Contrat d'acceptation : 2 critère(s)`), le DAG a réussi et la
branche a fusionné… sans **aucune** ligne de revue au journal. Le contrat n'a
jamais été évalué.

Or un plan passé par une approbation humaine est précisément celui qui mérite le
plus d'être vérifié : c'est le seul qui touche à des fichiers.
"""

import pytest

from core.engine import Engine
from core.state import TaskPayload


def _engine(monkeypatch, tasks_status, has_error):
    """Engine réel dont seul le DAGRunner est simulé."""
    eng = Engine(session_id="chat_t253")

    async def _faux_execute_dag(**kwargs):
        return tasks_status, has_error

    monkeypatch.setattr(eng._dag_runner, "execute_dag", _faux_execute_dag)
    return eng


@pytest.mark.asyncio
async def test_dag_reussi_declenche_la_revue(monkeypatch):
    """Après un DAG en succès, la revue post-DAG doit tourner."""
    eng = _engine(monkeypatch, {"t1": "success"}, False)
    appels = []

    monkeypatch.setattr(eng, "_is_review_enabled", lambda: True)
    eng.agents["reviewer"] = object()  # présence suffisante pour ce chemin

    async def _revue(initial_objective, on_event=None):
        appels.append(initial_objective)
        return True

    monkeypatch.setattr(eng._review_loop, "run_review", _revue)
    monkeypatch.setattr(eng, "_record_skills_from_dag", lambda *a, **k: None)

    tasks_status, has_error = await eng.executer_dag_et_controles(
        [TaskPayload(task_objective="t")], "objectif d'origine", 10_000,
    )

    assert appels == ["objectif d'origine"], "la revue doit recevoir l'objectif d'origine"
    assert has_error is False
    assert tasks_status == {"t1": "success"}


@pytest.mark.asyncio
async def test_revue_qui_refuse_marque_l_erreur(monkeypatch):
    eng = _engine(monkeypatch, {"t1": "success"}, False)
    monkeypatch.setattr(eng, "_is_review_enabled", lambda: True)
    eng.agents["reviewer"] = object()

    async def _revue_refus(initial_objective, on_event=None):
        return False

    monkeypatch.setattr(eng._review_loop, "run_review", _revue_refus)

    _tasks_status, has_error = await eng.executer_dag_et_controles(
        [TaskPayload(task_objective="t")], "obj", 10_000,
    )
    assert has_error is True


@pytest.mark.asyncio
async def test_dag_en_erreur_evalue_le_contrat_seul(monkeypatch):
    """
    DAG en échec : la revue LLM est sautée, mais le contrat DOIT être évalué.

    C'est le seul mécanisme capable de prouver que l'objectif est atteint malgré
    l'échec d'une branche (#T253).
    """
    eng = _engine(monkeypatch, {"t1": "error"}, True)
    appels = []

    async def _contrat(on_event=None):
        appels.append("contrat")
        return True

    monkeypatch.setattr(eng._review_loop, "contrat_seul", _contrat)
    monkeypatch.setattr(eng, "_record_skills_from_dag", lambda *a, **k: None)

    tasks_status, has_error = await eng.executer_dag_et_controles(
        [TaskPayload(task_objective="t")], "obj", 10_000,
    )

    assert appels == ["contrat"]
    # Contrat satisfait : la session redevient un succès et les statuts sont
    # absous, sinon `_finalize_git` annulerait un travail pourtant prouvé.
    assert has_error is False
    assert tasks_status == {"t1": "success"}


@pytest.mark.asyncio
async def test_contrat_non_satisfait_laisse_l_erreur(monkeypatch):
    eng = _engine(monkeypatch, {"t1": "error"}, True)

    async def _contrat_refus(on_event=None):
        return False

    monkeypatch.setattr(eng._review_loop, "contrat_seul", _contrat_refus)

    tasks_status, has_error = await eng.executer_dag_et_controles(
        [TaskPayload(task_objective="t")], "obj", 10_000,
    )
    assert has_error is True
    assert tasks_status == {"t1": "error"}


@pytest.mark.asyncio
async def test_la_reprise_passe_bien_par_les_controles(monkeypatch, tmp_path):
    """
    Le test qui prouve la tâche : la REPRISE appelle les contrôles, pas seulement
    `execute_dag`.

    Avant, `services/approval_resume_service` appelait `execute_dag()` en direct —
    un plan approuvé par un humain fusionnait sans qu'aucun de ses critères
    d'acceptation ne soit évalué.
    """
    from core import hitl_store, runtime_db

    ancien = runtime_db.get_db_path()
    runtime_db.override_db_path(str(tmp_path / "runtime.db"))
    runtime_db.get_connection().close()
    try:
        hitl_store.enregistrer_demande(
            request_id="r_ctrl", session_id="s_ctrl",
            dag_tasks=[{"task_objective": "écrire un fichier", "task_id": "t1"}],
            objective="objectif approuvé", description="",
        )
        hitl_store.marquer_decision("r_ctrl", approuve=True)

        appels = []

        class _FauxEngine:
            on_event = None

            async def executer_dag_et_controles(self, **kwargs):
                appels.append(kwargs["objectif"])
                return {"t1": "success"}, False

            def _finalize_git(self, *a, **k):
                pass

        class _FauxBridge:
            async def start(self, *a, **k):
                pass

            async def stop(self):
                pass

        import services.approval_resume_service as service

        monkeypatch.setattr(
            "services.pipeline_service.monter_moteur",
            lambda *a, **k: (_FauxEngine(), object(), _FauxBridge(), object()),
        )

        resultat = await service.reprendre_apres_approbation("r_ctrl")

        assert appels == ["objectif approuvé"], "la reprise doit passer par les contrôles post-DAG"
        assert resultat["status"] == "completed"
        assert hitl_store.lire_demande("r_ctrl")["status"] == hitl_store.STATUT_REPRIS
    finally:
        runtime_db.override_db_path(ancien)


@pytest.mark.asyncio
async def test_la_reprise_restaure_le_contrat(monkeypatch, tmp_path):
    """
    [#T253] Le contrat d'acceptation doit être retrouvable par la revue.

    `ReviewLoop._criteres_du_plan()` le cherche dans `engine.state.history` — or
    la reprise monte un moteur NEUF, dont l'historique est vide. Sans
    restauration, le contrat meurt avec la session suspendue : mesuré en prod le
    11/08, un plan approuvé avec 2 critères en a vu 0 évalué.
    """
    from core import hitl_store, runtime_db
    from core.acceptance_contract import CLE_CONTRAT
    from core.state import GlobalState

    ancien = runtime_db.get_db_path()
    runtime_db.override_db_path(str(tmp_path / "runtime.db"))
    runtime_db.get_connection().close()
    try:
        hitl_store.enregistrer_demande(
            request_id="r_restaure", session_id="s_restaure",
            dag_tasks=[{"task_objective": "écrire", "task_id": "t1"}],
            objective="objectif", description="",
            criteres=["le fichier doit exister", "il doit contenir OK"],
        )
        hitl_store.marquer_decision("r_restaure", approuve=True)

        class _FauxEngine:
            on_event = None

            def __init__(self):
                self.state = GlobalState(session_id="s_restaure")

            async def executer_dag_et_controles(self, **kwargs):
                return {"t1": "success"}, False

            def _finalize_git(self, *a, **k):
                pass

        class _FauxBridge:
            async def start(self, *a, **k):
                pass

            async def stop(self):
                pass

        faux = _FauxEngine()
        monkeypatch.setattr(
            "services.pipeline_service.monter_moteur",
            lambda *a, **k: (faux, object(), _FauxBridge(), object()),
        )

        import services.approval_resume_service as service
        await service.reprendre_apres_approbation("r_restaure")

        criteres_vus = [
            (u.metadata or {}).get(CLE_CONTRAT) for u in faux.state.history
        ]
        assert ["le fichier doit exister", "il doit contenir OK"] in criteres_vus
    finally:
        runtime_db.override_db_path(ancien)
