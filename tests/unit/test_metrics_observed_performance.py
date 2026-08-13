"""
tests/unit/test_metrics_observed_performance.py — La performance « observée » ne
remonte que des mesures réelles (#T300).

`_observed_performance()` croisait une troisième source, `routing_decisions`, pour
la latence et le taux de succès des modèles. Elle était fausse sur les trois plans :
groupée sur `resolved_model` (vide par conception, #T296), alimentée par la latence
du ROUTAGE et non de l'appel, et par une colonne `success` déclarée `DEFAULT 1` que
personne n'écrit jamais.

Elle ne se voyait pas parce que la colonne de groupement était vide. Ces tests
verrouillent le cas dangereux : quelqu'un remplit `resolved_model` — ce qu'un
contributeur ferait naturellement en voyant une colonne vide — et la vue se met à
afficher des latences de routage comme latences de modèle, avec 100 % de succès.

Base créée par le VRAI schéma (`runtime_db._init_schema` via `override_db_path`).
"""
import time

import pytest


@pytest.fixture
def base_avec_routage(tmp_path):
    """Base neuve : un appel LLM réel + une décision de routage au resolved_model REMPLI."""
    from core import runtime_db

    db = tmp_path / "observed.db"
    runtime_db.override_db_path(str(db))
    conn = runtime_db.get_connection()  # déclenche _init_schema

    maintenant = time.time()
    conn.execute(
        "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
        "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s_t300", maintenant, "gemma-4-31b", 100, 50, 150, 0.25),
    )
    # Le cas dangereux : resolved_model rempli, latence de routage énorme (mesurée
    # jusqu'à 187 659 ms en prod le 11/08), et `success` laissé à son DEFAULT 1.
    conn.execute(
        "INSERT INTO routing_decisions (timestamp, user_prompt_hash, prompt_length, "
        "dominant_category, routing_type, target_agent, model_tier, resolved_model, "
        "latency_ms, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (maintenant, "abc123", 42, "code_generation", "default", "executor",
         "moyen", "gemma-4-31b", 187_659.0, "s_t300"),
    )
    conn.commit()
    conn.close()
    return str(db)


def _observed(depuis_secondes: int = 3600) -> list:
    from api.routes.metrics import _observed_performance

    return _observed_performance(time.time() - depuis_secondes)


class TestPasDeLatenceDeRoutage:
    """Le temps passé à CHOISIR un modèle n'est pas le temps mis par ce modèle."""

    def test_la_latence_de_routage_n_est_pas_attribuee_au_modele(self, base_avec_routage):
        entrees = {e["model"]: e for e in _observed()}
        assert "gemma-4-31b" in entrees, entrees

        latence = entrees["gemma-4-31b"].get("avg_latency_ms")
        assert latence != 187_659.0, (
            "la latence du routage a été présentée comme celle du modèle — "
            "c'est exactement la régression que #T300 verrouille"
        )
        assert latence is None, (
            f"aucune source honnête de latence par modèle n'existe : attendu None, reçu {latence}"
        )

    def test_le_taux_de_succes_n_est_pas_invente(self, base_avec_routage):
        """`routing_decisions.success` vaut 1 par DEFAUT, jamais par mesure."""
        entrees = {e["model"]: e for e in _observed()}
        taux = entrees["gemma-4-31b"].get("success_rate")
        assert taux != 100.0, "100 % de succès calculés sur une colonne jamais écrite"
        assert taux is None, f"attendu None, reçu {taux}"


class TestLesVraiesMesuresRestent:
    """Retirer une source fausse ne doit rien retirer de ce qui était juste."""

    def test_consommation_reelle_intacte(self, base_avec_routage):
        entrees = {e["model"]: e for e in _observed()}
        entree = entrees["gemma-4-31b"]

        assert entree["calls"] == 1, entree
        assert entree["total_tokens"] == 150, entree
        assert entree["cost_usd"] == 0.25, entree
        assert entree["avg_cost_per_call_usd"] == 0.25, entree
