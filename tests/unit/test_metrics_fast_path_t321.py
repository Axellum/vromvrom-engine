"""
Répartition fast-path / slow-path de la vue Observabilité (#T321).

Défaut mesuré en PRODUCTION le 12/08 : `/api/metrics/routing` renvoyait
`{"fast_path": 0, "slow_path": 59, "total": 59}` — soit 100 % de slow-path —
alors que la base contenait, sur la même fenêtre de 7 jours, **27 décisions
fast-path sur 61** (et 259 sur 501 au total).

Cause : la requête comptait `routing_type = 'fast_path'`. Cette colonne existe,
mais ne prend JAMAIS cette valeur ; ses valeurs réelles sont 'casual_chat',
'ha_direct', 'ha_deterministic', 'default', 'executor_direct',
'sysadmin_direct', 'planner_pour_approbation'. L'information vit dans
`fast_path_used` (0/1).

C'est un cousin de #T294 hors de sa portée : la requête est syntaxiquement
VALIDE, donc `_safe_query` ne peut rien signaler. Un 0 produit par une valeur
qui n'existe pas est indiscernable d'un vrai 0 — sauf en regardant la donnée.

Enjeu concret : c'est la métrique qui sert à juger l'intérêt du fast-path (un
appel LLM économisé par demande, #T319). Affichée à 0 en permanence, elle
conduit à croire que l'optimisation ne sert jamais.

Comme test_metrics_kpis.py : base créée par le VRAI schéma du dépôt
(`runtime_db._init_schema`), jamais par un CREATE TABLE écrit à la main.
"""
import sqlite3
import time

from api.routes.metrics import _get_routing_stats
from core import runtime_db


def _base_au_vrai_schema(tmp_path, nom: str) -> str:
    db = tmp_path / nom
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema
    return str(db)


def _inserer_decisions(conn: sqlite3.Connection, lignes: list[tuple]) -> None:
    """(timestamp, dominant_category, routing_type, fast_path_used) — colonnes réelles."""
    conn.executemany(
        "INSERT INTO routing_decisions (timestamp, user_prompt_hash, dominant_category, "
        "routing_type, target_agent, fast_path_used, success) "
        "VALUES (?, 'h', ?, ?, 'executor', ?, 1)",
        lignes,
    )


def test_fast_path_compte_la_bonne_colonne(tmp_path):
    """
    Échoue sur master : la requête cherche routing_type = 'fast_path', valeur
    qui n'existe pas → fast_path = 0 alors que 3 décisions sur 5 l'ont emprunté.
    """
    db = _base_au_vrai_schema(tmp_path, "metrics_routing.db")
    maintenant = time.time()

    conn = sqlite3.connect(db)
    try:
        _inserer_decisions(conn, [
            # Les routing_type sont ceux relevés en prod — aucun ne vaut 'fast_path'
            (maintenant - 100, "casual_chat", "casual_chat", 1),
            (maintenant - 200, "home_assistant", "ha_direct", 1),
            (maintenant - 300, "home_assistant", "ha_deterministic", 1),
            (maintenant - 400, "analysis", "default", 0),
            (maintenant - 500, "files", "executor_direct", 0),
            # Hors fenêtre : ne doit pas être comptée
            (maintenant - 30 * 86400, "casual_chat", "casual_chat", 1),
        ])
        conn.commit()
    finally:
        conn.close()

    stats = _get_routing_stats(maintenant - 7 * 86400)
    repartition = stats["path_distribution"]

    assert repartition["fast_path"] == 3, (
        f"3 décisions fast-path attendues, {repartition['fast_path']} comptées — "
        "la requête interroge probablement routing_type au lieu de fast_path_used"
    )
    assert repartition["slow_path"] == 2
    assert repartition["total"] == 5
    # Les deux compteurs doivent toujours boucler sur le total
    assert repartition["fast_path"] + repartition["slow_path"] == repartition["total"]


def test_fast_path_null_compte_comme_slow_path(tmp_path):
    """Une décision sans `fast_path_used` ne doit disparaître d'aucun compteur."""
    db = _base_au_vrai_schema(tmp_path, "metrics_routing_null.db")
    maintenant = time.time()

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO routing_decisions (timestamp, user_prompt_hash, dominant_category, "
            "routing_type, target_agent, fast_path_used, success) "
            "VALUES (?, 'h', 'analysis', 'default', 'executor', NULL, 1)",
            (maintenant - 100,),
        )
        _inserer_decisions(conn, [(maintenant - 200, "casual_chat", "casual_chat", 1)])
        conn.commit()
    finally:
        conn.close()

    repartition = _get_routing_stats(maintenant - 7 * 86400)["path_distribution"]

    assert repartition["total"] == 2
    assert repartition["fast_path"] == 1
    assert repartition["slow_path"] == 1, (
        "la ligne à fast_path_used NULL a été perdue : en SQL `NULL != 1` rend "
        "NULL, donc elle ne serait comptée dans aucun des deux compteurs"
    )
    assert repartition["fast_path"] + repartition["slow_path"] == repartition["total"]
