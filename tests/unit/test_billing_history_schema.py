"""Tests du schéma unique de billing_history (#T64).

Le schéma (11 colonnes, dont model/tokens_used/cost_usd/window_type) est désormais
centralisé dans runtime_db._init_schema. budget_guard ne définit plus son propre CREATE.
On vérifie :
1. qu'une base neuve créée par runtime_db possède toutes les colonnes ;
2. qu'une base legacy (ancien schéma 7 colonnes) est migrée à chaud (colonnes ajoutées) ;
3. que budget_guard.initialize() s'appuie sur ce schéma et permet l'INSERT complet.
"""
import asyncio
import sqlite3

import pytest

from core import runtime_db


_FULL_COLUMNS = {
    "id", "timestamp", "provider", "metric", "value", "currency",
    "sync_source", "model", "tokens_used", "cost_usd", "window_type",
}


def _columns(db_path: str) -> set:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(billing_history)")}
    finally:
        conn.close()


def test_schema_neuf_contient_toutes_les_colonnes(tmp_path):
    """Une base créée from scratch par runtime_db a le schéma canonique complet."""
    db = tmp_path / "neuf.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema
    assert _columns(str(db)) == _FULL_COLUMNS


def test_base_legacy_7_colonnes_est_migree(tmp_path):
    """Une base avec l'ancien schéma 7 colonnes reçoit les 4 colonnes manquantes."""
    db = tmp_path / "legacy.db"
    # Simule l'ancien schéma 7 colonnes (avant centralisation).
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE billing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            provider TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            sync_source TEXT
        )
    """)
    conn.commit()
    conn.close()

    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche la migration additive

    assert _FULL_COLUMNS.issubset(_columns(str(db)))


def test_budget_guard_initialize_permet_insert_complet(tmp_path):
    """budget_guard.initialize() prépare le schéma → INSERT 11 colonnes OK."""
    db = tmp_path / "bg.db"
    runtime_db.override_db_path(str(db))

    from core.budget_guard import BudgetGuard
    bg = BudgetGuard()
    asyncio.run(bg.initialize())

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("""
            INSERT INTO billing_history (
                timestamp, provider, metric, value, currency, sync_source,
                model, tokens_used, cost_usd, window_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (0.0, "gemini", "tokens_and_cost", 0.01, "USD", "test",
              "domotique-qwen7b:q4", 123, 0.01, "daily"))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM billing_history").fetchone()[0] == 1
    finally:
        conn.close()
