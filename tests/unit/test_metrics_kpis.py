"""Tests des KPI d'observabilité de l'IHM (#T294).

Historique : la vue « Observabilité » affichait COÛT TOTAL $0.0000 et SESSIONS 0
au-dessus d'un tableau « Usage par modèle » à plusieurs dizaines de centimes.
Cause racine : cinq requêtes de api/routes/metrics.py interrogeaient des colonnes
inexistantes dans moteur_runtime.db (schéma hérité de l'ancien session_history.db).
_safe_query attrapait l'OperationalError, journalisait un simple warning et
retournait [] — que l'appelant convertissait en 0 : une exception déguisée en
mesure, jamais signalée.

Garde-fou : ces tests tournent contre une base créée par le VRAI schéma du dépôt
(runtime_db._init_schema via override_db_path + get_connection), jamais par un
CREATE TABLE écrit à la main dans le test — un test qui écrirait son propre
schéma validerait une hypothèse au lieu du schéma réel, c'est le mécanisme qui a
laissé ces cinq requêtes pourrir (modèle : test_billing_history_schema.py).
"""
import logging
import sqlite3
import time

import pytest

from core import runtime_db


def _base_au_vrai_schema(tmp_path, name: str) -> str:
    """Crée une base temporaire avec le schéma réel du dépôt."""
    db = tmp_path / name
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema
    return str(db)


def _insert_usage(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insère des lignes de consommation (colonnes réelles de token_usage)."""
    conn.executemany(
        "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
        "completion_tokens, total_tokens, cost_usd, channel, agent_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


async def test_kpis_egalent_la_somme_du_tableau_et_les_sessions(tmp_path):
    """KPI coût == somme des cost_usd de model_stats ; KPI sessions == sessions insérées.

    Échoue sur master : _get_kpis interroge quota_snapshots.estimated_cost_usd
    (colonne inexistante) et sessions.start_time → _safe_query rend [] → 0, au
    -dessus d'un tableau « Usage par modèle » qui, lui, lit token_usage correctement.
    """
    db = _base_au_vrai_schema(tmp_path, "metrics_central.db")
    now = time.time()

    conn = sqlite3.connect(db)
    try:
        # token_usage : 2 lignes dans la fenêtre (0,10 + 0,20 = 0,30 $), 1 hors fenêtre.
        _insert_usage(conn, [
            ("s1", now - 3600, "modele-a", 100, 50, 150, 0.10, "api", "agent-1"),
            ("s2", now - 7200, "modele-b", 200, 100, 300, 0.20, "api", None),
            ("s3", now - 3 * 86400, "modele-c", 50, 10, 60, 0.05, "api", "agent-2"),
        ])
        # sessions : 2 dans la fenêtre, 1 hors.
        conn.executemany(
            "INSERT INTO sessions (session_id, objective, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            [
                ("s1", "objectif-1", "success", now - 3600),
                ("s2", "objectif-2", "running", now - 7200),
                ("s3", "objectif-3", "success", now - 3 * 86400),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    # Import après override : _safe_query passe par get_connection() (base temporaire).
    from api.routes.metrics import get_telemetry

    telemetry = await get_telemetry(period="24h")

    # Le KPI coût coïncide avec le tableau « Usage par modèle » (seule source : token_usage).
    total_tableau = sum(m["cost_usd"] for m in telemetry["model_stats"].values())
    assert telemetry["kpis"]["total_cost_usd"] == pytest.approx(total_tableau, abs=1e-6)
    # KPI sessions == sessions réellement insérées dans la fenêtre (started_at, comparaison numérique).
    assert telemetry["kpis"]["total_sessions"] == 2
    # KPI tokens : somme des total_tokens de la fenêtre (150 + 300) — déjà correct, non régressé.
    assert telemetry["kpis"]["total_tokens"] == 450
    # Contrat : les 3 clés gardent leur nom et leur type numérique (consommateurs `?? 0` / `|| 0`).
    assert list(telemetry["kpis"].keys()) == ["total_cost_usd", "total_sessions", "total_tokens"]
    for valeur in telemetry["kpis"].values():
        assert isinstance(valeur, (int, float))
    # Aucune requête signalée cassée : le zéro menteur a disparu.
    assert "query_errors" not in telemetry


async def test_requete_invalide_remonte_une_erreur_pas_un_zero(tmp_path, caplog):
    """Colonne inexistante = défaut de code : logger.error + null dans la réponse, jamais 0."""
    db = _base_au_vrai_schema(tmp_path, "metrics_menteur.db")

    # On part du vrai schéma, puis on le casse finement : retrait de cost_usd
    # (colonne « argent ») de token_usage. Les index de _init_schema restent
    # valides (timestamp/session_id/model intacts) : seules les requêtes de
    # coût échouent, sessions et tokens restent des mesures nulles réelles.
    conn = sqlite3.connect(db)
    try:
        conn.execute("ALTER TABLE token_usage DROP COLUMN cost_usd")
        conn.commit()
    finally:
        conn.close()

    from api.routes.metrics import get_telemetry

    with caplog.at_level(logging.ERROR, logger="api.routes.metrics"):
        telemetry = await get_telemetry(period="24h")

    # Défaut de code journalisé en ERROR (plus un simple warning étouffé).
    assert any("METRICS" in record.getMessage() for record in caplog.records)

    # La mesure cassée ne rend PAS un 0 indiscernable d'une vraie mesure nulle.
    assert telemetry["kpis"]["total_cost_usd"] is None
    # Zéro ligne et requête cassée ne produisent plus le même affichage :
    # sessions et tokens (intacts) rendent de vraies mesures nulles, 0.
    assert telemetry["kpis"]["total_sessions"] == 0
    assert telemetry["kpis"]["total_tokens"] == 0
    # L'information remonte jusqu'à la réponse (champ additif, requête nommée).
    assert any("token_usage" in e for e in telemetry["query_errors"])


async def test_agent_name_null_est_signale_pas_un_tableau_vide(tmp_path):
    """agent_name NULL côté écrivains ≠ tableau vide : compteur additif exposé."""
    db = _base_au_vrai_schema(tmp_path, "metrics_agents.db")
    now = time.time()

    conn = sqlite3.connect(db)
    try:
        _insert_usage(conn, [
            ("s1", now - 3600, "modele-a", 100, 50, 150, 0.10, "api", None),
            ("s2", now - 7200, "modele-b", 200, 100, 300, 0.20, "api", None),
        ])
        conn.commit()
    finally:
        conn.close()

    from api.routes.metrics import get_telemetry

    telemetry = await get_telemetry(period="24h")

    # « Aucun agent renseigné » est distingué d'un tableau vide : la liste reste
    # vide (pas de ligne « null » trompeuse) mais le compteur des appels sans
    # agent est exposé.
    assert telemetry["agent_stats"] == []
    assert telemetry["agent_stats_unassigned_count"] == 2
