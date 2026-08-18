"""
tests/unit/test_cleanup_zombie_dag.py — Réconciliation des tâches DAG des
sessions zombies (#T362).

Vérifie que cleanup_zombie_sessions, en plus de marquer les sessions zombies
'error', réconcilie DANS LA MÊME TRANSACTION les tâches non terminales
(pending/running) rattachées à ces sessions : elles passent 'error' avec un
ended_at renseigné. Les garde-fous sont prouvés :
- une session running RÉCENTE et ses tâches ne sont pas touchées ;
- une session waiting_approval ANCIENNE et ses tâches ne sont pas touchées
  (HITL en attente légitime) ;
- les tâches déjà success d'une session zombifiée gardent leur statut ;
- la valeur de retour reste le nombre de SESSIONS nettoyées.

Aucune écriture dans la base partagée du poste : moteur_runtime.db est isolée
dans tmp_path (même motif que test_cleanup_sessions_zombies).
"""

import time

import pytest

import core.runtime_db as runtime_db
import core.session_history as sh

# ──────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    """Isolation stricte : moteur_runtime.db dans tmp_path, jamais la base réelle."""
    monkeypatch.setattr(runtime_db, "_DB_PATH", str(tmp_path / "moteur_runtime.db"))


def _creer_session(session_id: str, statut: str = "running") -> None:
    """Crée une session via le chemin réel, puis force son statut."""
    sh.record_session_start(session_id, "Objectif de test", "planner")
    conn = runtime_db.get_connection()
    conn.execute("UPDATE sessions SET status = ? WHERE session_id = ?", (statut, session_id))
    conn.commit()
    conn.close()


def _vieillir(session_id: str, age_secondes: float) -> None:
    """Vieillit une session (started_at ET last_activity) sans attente réelle."""
    conn = runtime_db.get_connection()
    conn.execute(
        "UPDATE sessions SET started_at = ?, last_activity = ? WHERE session_id = ?",
        (time.time() - age_secondes, time.time() - age_secondes, session_id),
    )
    conn.commit()
    conn.close()


def _creer_tache(session_id: str, task_id: str, statut: str) -> None:
    """Crée une tâche DAG pour une session."""
    conn = runtime_db.get_connection()
    conn.execute(
        """
        INSERT INTO dag_tasks (session_id, task_id, status, worker_id)
        VALUES (?, ?, ?, 'local')
        """,
        (session_id, task_id, statut),
    )
    conn.commit()
    conn.close()


def _tache(session_id: str, task_id: str) -> tuple | None:
    """Retourne (status, ended_at) d'une tâche, None si absente."""
    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT status, ended_at FROM dag_tasks WHERE session_id = ? AND task_id = ?",
        (session_id, task_id),
    ).fetchone()
    conn.close()
    return row


# ──────────────────────────────────────────────────────────────────
# Test central : réconciliation des tâches d'une session zombie
# ──────────────────────────────────────────────────────────────────

def test_session_zombie_reconcilie_ses_taches_non_terminales():
    """[Central] Une session running ancienne portant pending + running :
    après nettoyage, la session est 'error' ET les deux tâches sont terminales
    avec un ended_at renseigné."""
    _creer_session("dag_zombie")
    _vieillir("dag_zombie", 2 * 3600)
    _creer_tache("dag_zombie", "t1", "pending")
    _creer_tache("dag_zombie", "t2", "running")

    nettoyees = sh.cleanup_zombie_sessions()

    assert nettoyees == 1
    # La session est bien zombifiée.
    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT status FROM sessions WHERE session_id = ?", ("dag_zombie",)
    ).fetchone()
    conn.close()
    assert row[0] == "error"

    # Les deux tâches sont terminales, avec ended_at renseigné.
    for task_id in ("t1", "t2"):
        status, ended_at = _tache("dag_zombie", task_id)
        assert status == "error"
        assert ended_at is not None


# ──────────────────────────────────────────────────────────────────
# Garde-fous
# ──────────────────────────────────────────────────────────────────

def test_session_recente_et_ses_taches_ne_sont_pas_touchees():
    """Une session running RÉCENTE et ses tâches ne sont pas touchées."""
    _creer_session("dag_recente")
    _vieillir("dag_recente", 600)  # 10 minutes seulement
    _creer_tache("dag_recente", "t1", "pending")
    _creer_tache("dag_recente", "t2", "running")

    assert sh.cleanup_zombie_sessions() == 0
    assert _tache("dag_recente", "t1")[0] == "pending"
    assert _tache("dag_recente", "t2")[0] == "running"


def test_session_waiting_approval_ancienne_et_ses_taches_non_touchees():
    """[Garde-fou HITL] Une session waiting_approval ANCIENNE et ses tâches ne
    sont pas touchées : le prédicat ne doit pas être élargi."""
    _creer_session("dag_hitl", statut="waiting_approval")
    _vieillir("dag_hitl", 5 * 3600)
    _creer_tache("dag_hitl", "t1", "pending")
    _creer_tache("dag_hitl", "t2", "running")

    assert sh.cleanup_zombie_sessions() == 0
    assert _tache("dag_hitl", "t1")[0] == "pending"
    assert _tache("dag_hitl", "t2")[0] == "running"


def test_taches_deja_success_de_session_zombifiee_gardent_leur_statut():
    """Les tâches déjà terminales (success) d'une session zombifiée ne sont pas
    écrasées ; seules les tâches non terminales sont réconciliées."""
    _creer_session("dag_mixte")
    _vieillir("dag_mixte", 2 * 3600)
    _creer_tache("dag_mixte", "terminee", "success")
    _creer_tache("dag_mixte", "en_cours", "running")

    assert sh.cleanup_zombie_sessions() == 1

    status_terminee, ended_at_terminee = _tache("dag_mixte", "terminee")
    assert status_terminee == "success"
    assert ended_at_terminee is None  # inchangée

    status_en_cours, ended_at_en_cours = _tache("dag_mixte", "en_cours")
    assert status_en_cours == "error"
    assert ended_at_en_cours is not None


# ──────────────────────────────────────────────────────────────────
# Contrat de retour & erreur_message
# ──────────────────────────────────────────────────────────────────

def test_valeur_retour_reste_le_nombre_de_sessions():
    """La valeur de retour reste le nombre de SESSIONS nettoyées (contrat
    existant, appelé et journalisé par gui_server), pas le nombre de tâches."""
    _creer_session("dag_a")
    _vieillir("dag_a", 2 * 3600)
    _creer_tache("dag_a", "t1", "pending")
    _creer_tache("dag_a", "t2", "running")
    _creer_session("dag_b")
    _vieillir("dag_b", 2 * 3600)
    _creer_tache("dag_b", "t1", "running")

    # 2 sessions zombies, 3 tâches non terminales au total.
    assert sh.cleanup_zombie_sessions() == 2


def test_tache_reconciliee_a_un_message_derreur_explicite():
    """Les tâches réconciliées portent un error_message explicite."""
    _creer_session("dag_message")
    _vieillir("dag_message", 2 * 3600)
    _creer_tache("dag_message", "t1", "pending")

    sh.cleanup_zombie_sessions()

    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT error_message FROM dag_tasks WHERE session_id = ? AND task_id = ?",
        ("dag_message", "t1"),
    ).fetchone()
    conn.close()
    assert row[0] and "réconciliée" in row[0]
