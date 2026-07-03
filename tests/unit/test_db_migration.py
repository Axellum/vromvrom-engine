"""
tests/unit/test_db_migration.py — Tests du moteur de consolidation SQLite (Phase 1, M1/#7).

Vérifie sur des bases temporaires : fusion par clé naturelle (dédup), exclusion du
surrogate id, suppression de la table source, idempotence, et DROP des doublons vides.
"""

import os
import sqlite3

from tools import db_migration as mig


def _make_dbs(db_dir):
    """Crée un runtime + routing_metrics minimal avec model_elo_scores en collision."""
    rt = sqlite3.connect(os.path.join(db_dir, "moteur_runtime.db"))
    rt.execute("""CREATE TABLE model_elo_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, domain TEXT,
        elo_score REAL, UNIQUE(model_name, domain))""")
    # runtime = canonique : possède (gemini, ha) avec un score "courant"
    rt.execute("INSERT INTO model_elo_scores (model_name, domain, elo_score) VALUES ('gemini','ha',1500)")
    rt.execute("CREATE TABLE quota_snapshots (id INTEGER PRIMARY KEY, ts TEXT)")  # vide
    rt.commit(); rt.close()

    rm = sqlite3.connect(os.path.join(db_dir, "routing_metrics.db"))
    rm.execute("""CREATE TABLE model_elo_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT, domain TEXT,
        elo_score REAL, UNIQUE(model_name, domain))""")
    # un doublon (gemini, ha) + une nouvelle entrée (deepseek, code)
    rm.execute("INSERT INTO model_elo_scores (model_name, domain, elo_score) VALUES ('gemini','ha',999)")
    rm.execute("INSERT INTO model_elo_scores (model_name, domain, elo_score) VALUES ('deepseek','code',1600)")
    rm.commit(); rm.close()


def _scores(db_dir):
    c = sqlite3.connect(os.path.join(db_dir, "moteur_runtime.db"))
    rows = dict(c.execute("SELECT model_name||':'||domain, elo_score FROM model_elo_scores").fetchall())
    c.close()
    return rows


def test_merge_dedup_and_drop(tmp_path, monkeypatch):
    db_dir = str(tmp_path)
    _make_dbs(db_dir)
    # Restreindre les plans au cas testé pour l'isolation.
    monkeypatch.setattr(mig, "MERGE_PLANS", [
        mig.MergePlan("model_elo_scores", "routing_metrics.db", "moteur_runtime.db",
                      ["model_name", "domain"]),
    ])
    monkeypatch.setattr(mig, "DROP_PLANS", [
        mig.DropPlan("quota_snapshots", "moteur_runtime.db", "vide"),
    ])

    mig.run(db_dir, apply=True, do_backup=False)

    scores = _scores(db_dir)
    # La nouvelle entrée est importée…
    assert scores["deepseek:code"] == 1600
    # …mais le doublon (gemini, ha) garde la valeur CANONIQUE du runtime (1500), pas 999.
    assert scores["gemini:ha"] == 1500
    # Table source supprimée.
    rm = sqlite3.connect(os.path.join(db_dir, "routing_metrics.db"))
    assert rm.execute("SELECT 1 FROM sqlite_master WHERE name='model_elo_scores'").fetchone() is None
    rm.close()
    # quota_snapshots vide supprimée.
    rt = sqlite3.connect(os.path.join(db_dir, "moteur_runtime.db"))
    assert rt.execute("SELECT 1 FROM sqlite_master WHERE name='quota_snapshots'").fetchone() is None
    rt.close()


def test_idempotent_rerun(tmp_path, monkeypatch):
    db_dir = str(tmp_path)
    _make_dbs(db_dir)
    monkeypatch.setattr(mig, "MERGE_PLANS", [
        mig.MergePlan("model_elo_scores", "routing_metrics.db", "moteur_runtime.db",
                      ["model_name", "domain"]),
    ])
    monkeypatch.setattr(mig, "DROP_PLANS", [])

    mig.run(db_dir, apply=True, do_backup=False)
    first = _scores(db_dir)
    # Re-run : source déjà supprimée → aucun changement, pas de doublon.
    mig.run(db_dir, apply=True, do_backup=False)
    assert _scores(db_dir) == first
    assert len(first) == 2  # gemini:ha + deepseek:code


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    db_dir = str(tmp_path)
    _make_dbs(db_dir)
    monkeypatch.setattr(mig, "MERGE_PLANS", [
        mig.MergePlan("model_elo_scores", "routing_metrics.db", "moteur_runtime.db",
                      ["model_name", "domain"]),
    ])
    monkeypatch.setattr(mig, "DROP_PLANS", [])

    mig.run(db_dir, apply=False)  # dry-run
    # Le runtime ne contient toujours qu'une seule ligne, la source intacte.
    assert len(_scores(db_dir)) == 1
    rm = sqlite3.connect(os.path.join(db_dir, "routing_metrics.db"))
    assert rm.execute("SELECT COUNT(*) FROM model_elo_scores").fetchone()[0] == 2
    rm.close()
