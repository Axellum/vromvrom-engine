"""
tests/unit/test_seed_routing_rules_dashscope.py — Règles de routage DashScope (#T247).

La table `routing_rules` est consultative : elle alimente uniquement les outils de
recommandation `get_routing_recommendation` / `get_routing_matrix` de
`core/mcp_tools/orchestrator.py`, pas le routage runtime (qui passe par les tiers
leger/moyen/fort de `core/llm_gateway.py`).

Ces tests vérifient que `seed_routing_rules()` expose bien des recommandations vers
le forfait DashScope Coding Plan (~¥40/mois amorti, quota 4 req/min et ~600/jour),
et qu'aucune règle ne pointe vers un modèle absent du catalogue (pas de règle
orpheline, `PRAGMA foreign_key_check` propre).
"""

import os
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.models_db as models_db
import seed_models_db


@pytest.fixture
def base_peuplee(tmp_path, monkeypatch):
    """Catalogue neuf et isolé, peuplé comme le seed (jamais la base du poste).

    Les connexions SQLite de core.models_db sont mises en cache par thread
    (_thread_local.conn) : remplacer _thread_local par une nouvelle instance force
    tous les threads à rouvrir une connexion contre le nouveau chemin patché.
    """
    chemin = str(tmp_path / "models_registry.db")
    monkeypatch.setattr(models_db, "_DB_PATH", chemin)
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    seed_models_db.seed_providers()
    seed_models_db.seed_models()
    seed_models_db.seed_routing_rules()
    return chemin


def _regles_dashscope(chemin):
    """Retourne les règles de routage pointant vers un modèle dashscope/*."""
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    lignes = conn.execute(
        "SELECT * FROM routing_rules WHERE recommended_model LIKE 'dashscope/%'"
    ).fetchall()
    conn.close()
    return [dict(ligne) for ligne in lignes]


class TestReglesDashScope:
    """Le seed expose des recommandations vers le forfait DashScope Coding Plan."""

    def test_au_moins_une_regle_dashscope(self, base_peuplee):
        """Au moins une règle pointe vers un modèle du forfait DashScope."""
        regles = _regles_dashscope(base_peuplee)
        assert len(regles) >= 1, "aucune règle de routage vers dashscope/*"

    def test_couverture_code_et_agentique(self, base_peuplee):
        """Les types de tâche code et agentique sont couverts."""
        types = {r["task_type"] for r in _regles_dashscope(base_peuplee)}
        for attendu in ("code_generation", "code_complexe", "code_revision", "refactoring", "agentique"):
            assert attendu in types, f"règle {attendu} manquante pour dashscope/*"

    def test_regles_dashscope_non_orphelines(self, base_peuplee):
        """Chaque modèle recommandé dashscope/* existe dans la table models."""
        conn = sqlite3.connect(base_peuplee)
        for r in _regles_dashscope(base_peuplee):
            modele = conn.execute(
                "SELECT id FROM models WHERE id = ?", (r["recommended_model"],)
            ).fetchone()
            assert modele is not None, (
                f"règle {r['task_type']} → {r['recommended_model']} orpheline (modèle absent du catalogue)"
            )
            assert r["provider_id"] == "dashscope", (
                f"règle {r['task_type']} : provider_id {r['provider_id']} incohérent"
            )
        conn.close()

    def test_foreign_key_check_propre(self, base_peuplee):
        """Aucune violation de clé étrangère après le seed complet."""
        conn = sqlite3.connect(base_peuplee)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert violations == [], f"violations de clé étrangère : {violations}"

    def test_regles_existantes_inchangees(self, base_peuplee):
        """Les règles préexistantes ne sont pas écrasées par les ajouts DashScope."""
        conn = sqlite3.connect(base_peuplee)
        conn.row_factory = sqlite3.Row
        architecture = dict(conn.execute(
            "SELECT * FROM routing_rules WHERE task_type = 'architecture'"
        ).fetchone())
        conn.close()
        assert architecture["recommended_model"] == "claude-opus-4-8"
