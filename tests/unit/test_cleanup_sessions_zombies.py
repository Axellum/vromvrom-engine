"""
tests/unit/test_cleanup_sessions_zombies.py — Nettoyage périodique des zombies (#T298).

Vérifie que :
- une session 'running' ancienne ET sans signe de vie (last_activity) est bien
  marquée 'error' avec le message de zombie ;
- une session 'running' ancienne mais VIVANTE n'est jamais touchée — le
  heartbeat est écrit par le chemin d'exécution réel (BaseAgent.invoke →
  record_session_activity), jamais simulé en SQL à la main dans le test ;
- le nettoyage périodique tourne PENDANT la vie du serveur, sans redémarrage,
  avec une horloge simulée (aucune attente réelle d'une heure) ;
- la boucle survit à une erreur ponctuelle du nettoyage ;
- la migration additive last_activity s'applique aux bases créées avant le
  heartbeat ;
- le comportement historique est préservé : une session récente n'est pas
  touchée, et l'intervalle de balayage est configurable via l'environnement.

Aucune écriture dans la base partagée du poste : moteur_runtime.db est isolée
dans tmp_path (fixture standard du repo, cf. test_quota_collector).
"""

import asyncio
import sqlite3
import time

import pytest

import core.runtime_db as runtime_db
import core.session_history as sh
from agents.base_agent import BaseAgent
from core.state import StateUpdate, TaskPayload

# ──────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _base_isolee(tmp_path, monkeypatch):
    """Isolation stricte : moteur_runtime.db dans tmp_path, jamais la base réelle."""
    monkeypatch.setattr(runtime_db, "_DB_PATH", str(tmp_path / "moteur_runtime.db"))


def _creer_session(session_id: str, objectif: str = "Objectif de test") -> None:
    """Crée une session 'running' via le chemin réel (record_session_start)."""
    sh.record_session_start(session_id, objectif, "planner")


def _vieillir(session_id: str, age_secondes: float) -> None:
    """Vieillit artificiellement une session (started_at ET last_activity).

    Simule l'écoulement du temps sans aucune attente réelle : les timestamps
    sont décalés dans le passé, comme le ferait le temps qui passe.
    """
    conn = runtime_db.get_connection()
    conn.execute(
        "UPDATE sessions SET started_at = ?, last_activity = ? WHERE session_id = ?",
        (time.time() - age_secondes, time.time() - age_secondes, session_id),
    )
    conn.commit()
    conn.close()


def _statut(session_id: str) -> str | None:
    """Retourne le statut d'une session, None si absente."""
    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT status FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _erreur(session_id: str) -> str | None:
    """Retourne le message d'erreur d'une session."""
    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT error_message FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _last_activity(session_id: str) -> float | None:
    """Retourne le heartbeat last_activity d'une session."""
    conn = runtime_db.get_connection()
    row = conn.execute(
        "SELECT last_activity FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


class _AgentHeartbeat(BaseAgent):
    """Agent concret minimal : prouve que l'enveloppe BaseAgent.invoke écrit
    le heartbeat sur le chemin d'exécution réel (aucun mock de la BDD)."""

    def __init__(self) -> None:
        super().__init__("agent_heartbeat", "prompt système de test")

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        return StateUpdate(
            agent_name="agent_heartbeat",
            status="success",
            result_data="ok",
        )


# ──────────────────────────────────────────────────────────────────
# Critère de vivacité (garde-fou n°1)
# ──────────────────────────────────────────────────────────────────

def test_session_ancienne_sans_signes_de_vie_est_marquee_error():
    """Une session running ancienne et sans heartbeat devient 'error'."""
    # Session antérieure au déploiement du heartbeat (last_activity NULL) :
    # cas exact des zombies restés en base (ex. campagne du 11/08).
    _creer_session("bg_zombie_pre_heartbeat")
    conn = runtime_db.get_connection()
    conn.execute(
        "UPDATE sessions SET started_at = ?, last_activity = NULL WHERE session_id = ?",
        (time.time() - 2 * 3600, "bg_zombie_pre_heartbeat"),
    )
    conn.commit()
    conn.close()

    # Session dont le dernier heartbeat date de 2h : le moteur ne travaille plus.
    _creer_session("bg_zombie_morte")
    _vieillir("bg_zombie_morte", 2 * 3600)

    nettoyees = sh.cleanup_zombie_sessions()

    assert nettoyees == 2
    assert _statut("bg_zombie_pre_heartbeat") == "error"
    assert _statut("bg_zombie_morte") == "error"
    # On marque, on ne supprime pas : la trace de l'incident est conservée.
    assert "Session zombie" in (_erreur("bg_zombie_pre_heartbeat") or "")
    assert "Session zombie" in (_erreur("bg_zombie_morte") or "")


def test_session_ancienne_mais_vivante_n_est_pas_touchee():
    """[Garde-fou n°1] Une session running ancienne mais VIVANTE reste 'running'.

    La vivacité est prouvée par le heartbeat réel (record_session_activity) :
    le test n'injecte aucune valeur de liveness en SQL à la main.
    """
    _creer_session("bg_longue_mais_vivante")
    _vieillir("bg_longue_mais_vivante", 2 * 3600)

    # Dernière étape d'agent il y a 30 minutes : la session travaille encore.
    sh.record_session_activity("bg_longue_mais_vivante", time.time() - 1800)

    assert sh.cleanup_zombie_sessions() == 0
    assert _statut("bg_longue_mais_vivante") == "running"


def test_heartbeat_ecrit_par_le_chemin_reel_des_agents():
    """Un vrai agent (BaseAgent.invoke) écrit le heartbeat sur la session.

    C'est le chemin d'exécution complet : pas de mock du heartbeat ni de la BDD.
    """
    _creer_session("bg_agent_reel")
    _vieillir("bg_agent_reel", 2 * 3600)
    ancien_heartbeat = _last_activity("bg_agent_reel")

    asyncio.run(
        _AgentHeartbeat().invoke(
            TaskPayload(task_objective="tâche de test", metadata={"session_id": "bg_agent_reel"})
        )
    )

    # L'étape d'agent a rafraîchi last_activity (heartbeat réel, temps réel).
    nouveau_heartbeat = _last_activity("bg_agent_reel")
    assert nouveau_heartbeat is not None
    assert nouveau_heartbeat > ancien_heartbeat

    # Grâce à ce heartbeat, le nettoyage n'ose pas toucher la session.
    assert sh.cleanup_zombie_sessions() == 0
    assert _statut("bg_agent_reel") == "running"


def test_agent_sans_session_id_ne_casse_pas_linvocation():
    """Un payload sans session_id est inoffensif : l'invoke réussit quand même."""
    update = asyncio.run(
        _AgentHeartbeat().invoke(TaskPayload(task_objective="sans session"))
    )
    assert update.status == "success"


def test_session_recente_running_n_est_pas_touchee():
    """Comportement historique préservé : une session récente reste 'running'."""
    _creer_session("bg_recente")
    _vieillir("bg_recente", 600)  # 10 minutes seulement

    assert sh.cleanup_zombie_sessions() == 0
    assert _statut("bg_recente") == "running"


# ──────────────────────────────────────────────────────────────────
# Boucle périodique (temps simulé, aucune attente réelle)
# ──────────────────────────────────────────────────────────────────

class _ArretBoucle(Exception):
    """Levée par l'horloge simulée pour clore la boucle sans attendre."""


class _HorlogeSimulee:
    """Remplace asyncio.sleep : enregistre les durées puis stoppe la boucle."""

    def __init__(self, arrets_apres: int = 3):
        self.durees: list[float] = []
        self._restants = arrets_apres

    async def sleep(self, duree: float) -> None:
        self.durees.append(duree)
        self._restants -= 1
        if self._restants <= 0:
            raise _ArretBoucle()


def test_boucle_periodique_nettoie_sans_redemarrage(monkeypatch):
    """[Critère] Le nettoyage est déclenché périodiquement, sans redémarrage.

    Horloge simulée : la boucle dort 600 s (virtuelles) puis nettoie — la
    session zombie est passée en 'error' pendant la VIE de la boucle.
    """
    _creer_session("bg_zombie_periodique")
    _vieillir("bg_zombie_periodique", 2 * 3600)

    horloge = _HorlogeSimulee(arrets_apres=2)
    monkeypatch.setattr(sh.asyncio, "sleep", horloge.sleep)

    with pytest.raises(_ArretBoucle):
        asyncio.run(sh.zombie_sweep_loop(interval_seconds=600))

    assert horloge.durees == [600, 600]
    assert _statut("bg_zombie_periodique") == "error"


def test_boucle_periodique_epargne_la_session_vivante(monkeypatch):
    """[Critère] Le balayage périodique respecte le garde-fou n°1."""
    _creer_session("bg_vivante_periodique")
    _vieillir("bg_vivante_periodique", 2 * 3600)
    sh.record_session_activity("bg_vivante_periodique", time.time() - 600)

    horloge = _HorlogeSimulee(arrets_apres=2)
    monkeypatch.setattr(sh.asyncio, "sleep", horloge.sleep)

    with pytest.raises(_ArretBoucle):
        asyncio.run(sh.zombie_sweep_loop(interval_seconds=600))

    assert _statut("bg_vivante_periodique") == "running"


def test_boucle_survit_a_exception_du_nettoyage(monkeypatch, caplog):
    """Une erreur ponctuelle du nettoyage est journalisée, la boucle continue."""
    appels = []

    def _nettoyage_instable() -> int:
        appels.append(1)
        if len(appels) == 1:
            raise RuntimeError("BDD temporairement verrouillée")
        return 0

    monkeypatch.setattr(sh, "cleanup_zombie_sessions", _nettoyage_instable)
    horloge = _HorlogeSimulee(arrets_apres=3)
    monkeypatch.setattr(sh.asyncio, "sleep", horloge.sleep)
    caplog.set_level("WARNING")

    with pytest.raises(_ArretBoucle):
        asyncio.run(sh.zombie_sweep_loop(interval_seconds=600))

    assert len(appels) == 2  # l'échec n'a pas interrompu l'itération suivante
    assert any("la boucle continue" in r.getMessage() for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Intervalle configurable
# ──────────────────────────────────────────────────────────────────

def test_intervalle_defaut_sans_env(monkeypatch):
    """Sans variable d'environnement, l'intervalle par défaut est 600 s."""
    monkeypatch.delenv("ZOMBIE_SWEEP_INTERVAL_SECONDS", raising=False)
    assert sh.get_zombie_sweep_interval() == 600


def test_intervalle_env_valide(monkeypatch):
    """ZOMBIE_SWEEP_INTERVAL_SECONDS valide est respecté."""
    monkeypatch.setenv("ZOMBIE_SWEEP_INTERVAL_SECONDS", "123")
    assert sh.get_zombie_sweep_interval() == 123


def test_intervalle_env_invalide_retombe_defaut(monkeypatch, caplog):
    """Valeur invalide → défaut, avec un warning explicite."""
    monkeypatch.setenv("ZOMBIE_SWEEP_INTERVAL_SECONDS", "abc")
    caplog.set_level("WARNING")
    assert sh.get_zombie_sweep_interval() == 600
    assert any("invalide" in r.getMessage() for r in caplog.records)


def test_intervalle_env_trop_bas_borne_minimum(monkeypatch):
    """Une valeur trop basse est bornée à ZOMBIE_SWEEP_INTERVAL_MIN (60 s)."""
    monkeypatch.setenv("ZOMBIE_SWEEP_INTERVAL_SECONDS", "5")
    assert sh.get_zombie_sweep_interval() == 60


# ──────────────────────────────────────────────────────────────────
# Migration additive du schéma
# ──────────────────────────────────────────────────────────────────

def test_migration_colonne_last_activity_sur_base_ancienne(tmp_path, monkeypatch):
    """Une base créée avant le heartbeat reçoit last_activity à l'ouverture.

    L'ancien schéma (celui qui a produit la session du 11/08) n'a pas la
    colonne : _init_schema doit l'ajouter sans casser les données existantes.
    """
    ancien_schema = """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            objective TEXT NOT NULL,
            status TEXT DEFAULT 'running',
            started_at REAL NOT NULL,
            ended_at REAL,
            duration_ms REAL,
            starting_agent TEXT,
            agents_invoked TEXT,
            task_count INTEGER DEFAULT 0,
            error_message TEXT,
            result_summary TEXT,
            metadata TEXT
        )
    """
    db = tmp_path / "ancienne_moteur_runtime.db"
    conn = sqlite3.connect(db)
    conn.execute(ancien_schema)
    conn.execute(
        "INSERT INTO sessions (session_id, objective, started_at) VALUES ('bg_ancienne', 'ancienne', ?)",
        (time.time() - 2 * 3600,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(runtime_db, "_DB_PATH", str(db))
    conn = runtime_db.get_connection()
    colonnes = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()

    assert "last_activity" in colonnes
    # La ligne préexistante est conservée, last_activity NULL → nettoyable.
    assert _statut("bg_ancienne") == "running"
    assert sh.cleanup_zombie_sessions() == 1
    assert _statut("bg_ancienne") == "error"
