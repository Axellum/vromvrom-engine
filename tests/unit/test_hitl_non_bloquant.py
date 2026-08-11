"""
tests/unit/test_hitl_non_bloquant.py — HITL non bloquant (#T267).

Ce que ces tests verrouillent, et pourquoi :
- une demande d'approbation SURVIT à la session qui l'a créée (elle ne vivait
  qu'en mémoire avant, et était dépilée aussitôt) ;
- le point d'approbation rend un TROISIÈME état (« en attente ») au lieu de
  bloquer 300 s dans une session bornée à 120 s ;
- ⚠️ une session suspendue NE FINALISE PAS sa branche Git — c'est le point
  dangereux du chantier : `_finalize_git` est appelée dans un `finally` et ne
  connaît que merge/rollback, donc sans garde elle détruirait ou fusionnerait
  du travail non approuvé ;
- le DAG rejoué à la reprise est exactement celui qui a été approuvé.
"""

import pytest

from core import hitl_store, runtime_db
from core.hitl import DECISION_APPROUVE, DECISION_EN_ATTENTE, HITLManager
from core.state import TaskPayload


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path):
    """
    Chaque test écrit dans sa propre base runtime.

    Le chemin d'origine est RESTAURÉ à la sortie : sans cela, les tests suivants
    de la suite continueraient d'écrire dans un `tmp_path` détruit — une fuite
    d'état entre fichiers de test, exactement le genre de dépendance à l'ordre
    d'exécution qui a produit les faux négatifs de #T261/#T268.
    """
    ancien = runtime_db.get_db_path()
    runtime_db.override_db_path(str(tmp_path / "runtime_test.db"))
    runtime_db.get_connection().close()  # déclenche _init_schema
    yield
    runtime_db.override_db_path(ancien)


def _dag_exemple() -> list[dict]:
    return [
        {"task_objective": "Écrire le fichier A", "task_id": "t1", "depends_on": [],
         "available_tools": ["write_file"], "relevant_context": "", "metadata": {},
         "status": "pending", "assigned_agent": None, "result_summary": None},
        {"task_objective": "Vérifier A", "task_id": "t2", "depends_on": ["t1"],
         "available_tools": ["read_file"], "relevant_context": "", "metadata": {},
         "status": "pending", "assigned_agent": None, "result_summary": None},
    ]


# ── Store durable ────────────────────────────────────────────────────────────

def test_contrat_acceptation_persiste():
    """[#T253] Le contrat doit survivre à la session, comme le DAG."""
    hitl_store.enregistrer_demande(
        request_id="r_contrat", session_id="s1", dag_tasks=_dag_exemple(),
        description="", criteres=["le fichier X doit exister", "il doit contenir OK"],
    )
    d = hitl_store.lire_demande("r_contrat")
    assert d["criteres"] == ["le fichier X doit exister", "il doit contenir OK"]


def test_demande_sans_contrat_reste_lisible():
    hitl_store.enregistrer_demande(
        request_id="r_sans", session_id="s1", dag_tasks=_dag_exemple(), description="",
    )
    assert hitl_store.lire_demande("r_sans")["criteres"] == []


def test_demande_survit_a_la_session():
    """Une demande enregistrée est relisible, DAG compris."""
    hitl_store.enregistrer_demande(
        request_id="dag_s1_2", session_id="s1", dag_tasks=_dag_exemple(),
        objective="objectif", description="2 tâches à risque",
        risk_level="high", git_branch="agent/s1",
    )
    d = hitl_store.lire_demande("dag_s1_2")
    assert d is not None
    assert d["status"] == hitl_store.STATUT_EN_ATTENTE
    assert d["git_branch"] == "agent/s1"
    assert len(d["dag_tasks"]) == 2
    assert d["dag_tasks"][1]["depends_on"] == ["t1"]


def test_double_approbation_refusee():
    """La transition n'est acceptée que depuis `pending` : pas de double exécution."""
    hitl_store.enregistrer_demande(
        request_id="r1", session_id="s1", dag_tasks=_dag_exemple(), description="x",
    )
    assert hitl_store.marquer_decision("r1", approuve=True) is True
    # Second clic sur « Approuver » : refusé, sinon le plan à risque partirait 2 fois.
    assert hitl_store.marquer_decision("r1", approuve=True) is False
    assert hitl_store.lire_demande("r1")["status"] == hitl_store.STATUT_APPROUVE


def test_liste_en_attente_ignore_les_demandes_decidees():
    hitl_store.enregistrer_demande(request_id="a", session_id="s", dag_tasks=[{"task_objective": "x"}], description="")
    hitl_store.enregistrer_demande(request_id="b", session_id="s", dag_tasks=[{"task_objective": "y"}], description="")
    hitl_store.marquer_decision("a", approuve=False)
    en_attente = [d["request_id"] for d in hitl_store.lister_en_attente()]
    assert en_attente == ["b"]


def test_decision_sur_demande_inconnue():
    assert hitl_store.marquer_decision("jamais_vue", approuve=True) is False


# ── Point d'approbation de l'Engine ──────────────────────────────────────────

def _engine_minimal(session_id="chat_t267"):
    """Engine réel mais sans agents : seul le point d'approbation est exercé."""
    from core.engine import Engine
    return Engine(session_id=session_id)


@pytest.mark.asyncio
async def test_plan_a_risque_met_la_session_en_attente():
    """Le plan à risque n'est ni approuvé ni rejeté : il est MIS EN ATTENTE."""
    eng = _engine_minimal()
    taches = [TaskPayload(task_objective="Modifier la config", available_tools=["write_file"])]

    decision = await eng._check_hitl_before_dag(taches, "objectif de test")

    assert decision == DECISION_EN_ATTENTE
    assert eng.est_suspendue() is True
    # La demande est persistée AVEC le DAG à rejouer.
    d = hitl_store.lire_demande(eng._suspension_request_id)
    assert d is not None
    assert len(d["dag_tasks"]) == 1
    assert d["dag_tasks"][0]["available_tools"] == ["write_file"]


@pytest.mark.asyncio
async def test_plan_sans_risque_passe_sans_demande():
    """Un plan en lecture seule ne crée aucune demande (bypass low risk)."""
    eng = _engine_minimal()
    taches = [TaskPayload(task_objective="Lire un fichier", available_tools=["read_file"])]

    decision = await eng._check_hitl_before_dag(taches, "lecture")

    assert decision == DECISION_APPROUVE
    assert eng.est_suspendue() is False
    assert hitl_store.lister_en_attente() == []


@pytest.mark.asyncio
async def test_mode_bloquant_reste_accessible(monkeypatch):
    """`MOTEUR_HITL_NON_BLOQUANT=0` restaure l'attente d'avant #T267."""
    monkeypatch.setenv("MOTEUR_HITL_NON_BLOQUANT", "0")
    eng = _engine_minimal()

    async def _approuve_tout(**kwargs):
        from core.hitl import ApprovalRequest
        return ApprovalRequest(request_id=kwargs["request_id"], description="", approved=True)

    monkeypatch.setattr(eng.hitl, "request_approval", _approuve_tout)
    taches = [TaskPayload(task_objective="Modifier", available_tools=["write_file"])]

    decision = await eng._check_hitl_before_dag(taches, "obj")

    assert decision == DECISION_APPROUVE
    assert eng.est_suspendue() is False
    assert hitl_store.lister_en_attente() == []


@pytest.mark.asyncio
async def test_attente_n_est_pas_une_erreur():
    """`_handle_dag_execution` remonte (None, has_error=False) quand on attend."""
    eng = _engine_minimal()
    taches = [TaskPayload(task_objective="Supprimer le dossier", available_tools=["run_terminal_command"])]
    payload = TaskPayload(task_objective="objectif")

    tasks_status, has_error = await eng._handle_dag_execution(taches, payload, 10_000, None)

    assert tasks_status is None
    assert has_error is False  # suspendue ≠ échouée
    assert eng.est_suspendue() is True


# ── ⚠️ Le point dangereux : la branche Git d'une session suspendue ───────────

@pytest.mark.asyncio
async def test_session_suspendue_ne_finalise_pas_git(monkeypatch):
    """
    Une session suspendue laisse sa branche éphémère EN PLACE.

    Sans la garde posée dans le `finally` de `run()`, la branche portant le
    travail à approuver serait fusionnée (sans approbation) ou détruite.
    """
    eng = _engine_minimal()
    appels: list = []
    monkeypatch.setattr(eng, "_prepare_git_branch", lambda: "agent/session_t267")
    monkeypatch.setattr(eng, "_run_doc_hook", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_init_langfuse", lambda: None)
    monkeypatch.setattr(eng, "_finalize_git", lambda *a, **k: appels.append(a))

    async def _sequence_suspendue(*a, **k):
        # Simule une boucle d'agents qui a mis le plan en attente.
        eng._suspension_request_id = "dag_test_1"
        return False, {}

    monkeypatch.setattr(eng, "_run_sequential_agents", _sequence_suspendue)

    await eng.run(TaskPayload(task_objective="objectif"), "planner")

    assert appels == [], "La branche d'une session suspendue ne doit pas être finalisée"


@pytest.mark.asyncio
async def test_session_normale_finalise_git(monkeypatch):
    """Contre-épreuve : sans suspension, la finalisation Git a bien lieu."""
    eng = _engine_minimal()
    appels: list = []
    monkeypatch.setattr(eng, "_prepare_git_branch", lambda: "agent/session_ok")
    monkeypatch.setattr(eng, "_run_doc_hook", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_init_langfuse", lambda: None)
    monkeypatch.setattr(eng, "_finalize_git", lambda *a, **k: appels.append(a))
    # Une session NON suspendue passe par la mémoire épisodique et la
    # consolidation (RAG/embeddings) : hors périmètre ici, et bien trop lourd
    # pour un test unitaire — c'est ce qui distingue ce chemin du précédent.
    monkeypatch.setattr(eng, "_save_episode", lambda *a, **k: None)

    async def _pas_de_consolidation(*a, **k):
        return None

    monkeypatch.setattr(eng, "_consolidate_memory", _pas_de_consolidation)

    async def _sequence_normale(*a, **k):
        return False, {"t1": "success"}

    monkeypatch.setattr(eng, "_run_sequential_agents", _sequence_normale)

    await eng.run(TaskPayload(task_objective="objectif"), "planner")

    assert len(appels) == 1
    assert appels[0][0] == "agent/session_ok"


@pytest.mark.asyncio
async def test_session_suspendue_garde_la_phase_waiting_approval(monkeypatch):
    """La phase finale reste `waiting_approval` (ni completed, ni failed)."""
    from core.state import ExecutionPhase

    eng = _engine_minimal()
    monkeypatch.setattr(eng, "_prepare_git_branch", lambda: None)
    monkeypatch.setattr(eng, "_run_doc_hook", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_init_langfuse", lambda: None)

    async def _sequence_suspendue(*a, **k):
        eng._suspension_request_id = "dag_test_2"
        eng.state.current_phase = ExecutionPhase.WAITING_APPROVAL
        return False, {}

    monkeypatch.setattr(eng, "_run_sequential_agents", _sequence_suspendue)

    etat = await eng.run(TaskPayload(task_objective="objectif"), "planner")

    assert etat.current_phase == ExecutionPhase.WAITING_APPROVAL


# ── Reprise ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reprise_refuse_une_demande_non_approuvee():
    from services.approval_resume_service import reprendre_apres_approbation

    hitl_store.enregistrer_demande(
        request_id="r_pending", session_id="s", dag_tasks=_dag_exemple(), description="",
    )
    res = await reprendre_apres_approbation("r_pending")
    assert res["status"] == "non_approuve"


@pytest.mark.asyncio
async def test_reprise_refuse_une_demande_inconnue():
    from services.approval_resume_service import reprendre_apres_approbation

    res = await reprendre_apres_approbation("inexistante")
    assert res["status"] == "introuvable"


@pytest.mark.asyncio
async def test_reprise_sans_dag_ne_pretend_pas_avoir_reussi():
    """Un DAG vide/illisible ne doit surtout pas ressortir en succès."""
    from services.approval_resume_service import reprendre_apres_approbation

    hitl_store.enregistrer_demande(
        request_id="r_vide", session_id="s", dag_tasks=[], description="",
    )
    hitl_store.marquer_decision("r_vide", approuve=True)

    res = await reprendre_apres_approbation("r_vide")

    assert res["status"] == "vide"
    assert hitl_store.lire_demande("r_vide")["status"] == hitl_store.STATUT_ECHEC


def test_dag_stocke_est_rejouable_a_l_identique():
    """
    Le DAG relu reconstruit des TaskPayload identiques à ceux approuvés.

    C'est l'exigence de sécurité de la reprise : ce qui est approuvé est ce qui
    s'exécute — jamais un plan re-généré.
    """
    from core.engine import Engine

    origine = [
        TaskPayload(task_objective="Écrire A", task_id="t1", available_tools=["write_file"]),
        TaskPayload(task_objective="Vérifier A", task_id="t2", depends_on=["t1"]),
    ]
    serialise = Engine._serialiser_dag(origine)
    hitl_store.enregistrer_demande(
        request_id="r_fidele", session_id="s", dag_tasks=serialise, description="",
    )

    relu = [TaskPayload(**t) for t in hitl_store.lire_demande("r_fidele")["dag_tasks"]]

    assert [t.task_objective for t in relu] == ["Écrire A", "Vérifier A"]
    assert relu[0].available_tools == ["write_file"]
    assert relu[1].depends_on == ["t1"]
    assert relu[0].status == origine[0].status


@pytest.mark.asyncio
async def test_notification_sse_emise_sans_blocage():
    """La demande non bloquante notifie l'IHM et rend la main immédiatement."""
    evenements: list = []

    async def _on_event(nom, data):
        evenements.append((nom, data))

    hitl = HITLManager()
    await hitl.demander_sans_bloquer(
        request_id="r_sse", session_id="s", dag_tasks=_dag_exemple(),
        description="2 tâches à risque", risk_level="high", on_event=_on_event,
    )

    assert [n for n, _ in evenements] == ["approval_required"]
    assert evenements[0][1]["non_bloquant"] is True
    assert hitl_store.lire_demande("r_sse") is not None
