"""
tests/unit/test_cost_per_success_t325.py — la métrique coût/succès ne croise
plus deux tables qui n'ont rien en commun (#T325).

Mesuré en base de prod le 12/08 : `model_elo_scores.model_name` porte des
TIERS et des noms d'agents ('leger', 'reviewer', 'ha_agent'…),
`token_usage.model` porte des ids de MODÈLES ('deepseek-chat',
'gemini-3.5-flash'…). L'intersection est VIDE : la jointure wins × coûts
produisait un résultat absurde (88 tâches réussies pour $0, $0,0854 pour
0 succès).

La voie de remplacement, vérifiée avant d'écrire : 821 lignes de
`token_usage` sur 1145 portent un `session_id`, et `sessions` porte le statut
réel (406 success / 84 error / 2 waiting_approval) ; 314 sessions joignent
déjà les deux tables. Le coût par tâche réussie se calcule donc par
`token_usage.session_id` × `sessions.status`, sans l'Elo.

Les tests utilisent le VRAI schéma (`runtime_db._init_schema` via
`override_db_path`), jamais un CREATE TABLE écrit à la main — un test qui
fabriquerait son propre schéma validerait son hypothèse au lieu du schéma réel.
"""
import sqlite3
import time

import pytest

from core import runtime_db


@pytest.fixture
def base_vide(tmp_path):
    """Base SQLite temporaire au vrai schéma du moteur."""
    db = tmp_path / "cout_par_succes.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema (schéma réel)
    return db


def _inserer(base, sql: str, params):
    conn = sqlite3.connect(str(base))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


# ── Le critère d'acceptation central ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_cout_du_succes_exclut_celui_de_l_echec(base_vide):
    """Une session réussie à $0,10, une en échec à $0,05 → $0,10 par succès."""
    from core.elo_scorer import get_cost_per_successful_task

    maintenant = time.time()
    _inserer(base_vide,
             "INSERT INTO sessions (session_id, objective, status, started_at) "
             "VALUES (?, ?, ?, ?)",
             ("s_reussie", "question", "success", maintenant))
    _inserer(base_vide,
             "INSERT INTO sessions (session_id, objective, status, started_at) "
             "VALUES (?, ?, ?, ?)",
             ("s_echouee", "question", "error", maintenant))
    _inserer(base_vide,
             "INSERT INTO token_usage (session_id, timestamp, model, cost_usd) "
             "VALUES (?, ?, ?, ?)",
             ("s_reussie", maintenant, "deepseek-chat", 0.10))
    _inserer(base_vide,
             "INSERT INTO token_usage (session_id, timestamp, model, cost_usd) "
             "VALUES (?, ?, ?, ?)",
             ("s_echouee", maintenant, "deepseek-chat", 0.05))

    resultat = get_cost_per_successful_task()

    # La métrique rend bien $0,10 par succès.
    assert resultat["successful_tasks"] == 1
    assert resultat["total_cost_usd"] == 0.10
    assert resultat["cost_per_success_usd"] == 0.10
    # Le coût de la session en échec n'est PAS attribué au succès : il est
    # exposé à part, ni disparu, ni compté gratuit.
    assert resultat["cost_failed_or_other_usd"] == 0.05
    assert resultat["sessions_par_statut"] == {"success": 1, "error": 1}
    # La ventilation par modèle ne porte que le coût des sessions réussies.
    assert resultat["cost_by_model_usd"] == {"deepseek-chat": 0.10}


# ── « 0 $ mesuré » vs « coût inconnu » ───────────────────────────────────────

@pytest.mark.asyncio
async def test_le_cout_sans_session_n_est_ni_disparu_ni_gratuit(base_vide):
    """Les lignes token_usage sans session_id sont exposées explicitement."""
    from core.elo_scorer import get_cost_per_successful_task

    maintenant = time.time()
    _inserer(base_vide,
             "INSERT INTO sessions (session_id, objective, status, started_at) "
             "VALUES (?, ?, ?, ?)",
             ("s_reussie", "question", "success", maintenant))
    _inserer(base_vide,
             "INSERT INTO token_usage (session_id, timestamp, model, cost_usd) "
             "VALUES (?, ?, ?, ?)",
             ("s_reussie", maintenant, "deepseek-chat", 0.10))
    # 324 lignes de prod n'ont pas de session_id : ni disparues, ni gratuites.
    _inserer(base_vide,
             "INSERT INTO token_usage (timestamp, model, cost_usd) "
             "VALUES (?, ?, ?)",
             (maintenant, "gemini-3.5-flash", 0.07))

    resultat = get_cost_per_successful_task()

    assert resultat["successful_tasks"] == 1
    assert resultat["total_cost_usd"] == 0.10
    assert resultat["cost_per_success_usd"] == 0.10
    assert resultat["cost_without_session_usd"] == 0.07


# ── Piège historique #T328 : se borner dans le temps ─────────────────────────

@pytest.mark.asyncio
async def test_le_seuil_de_fiabilite_exclut_les_succes_anterieurs(base_vide):
    """`seuil_fiabilite_status` permet de ne compter que les succès récents.

    Avant le correctif #T328 (déployé le 12/08 ~22h52), `sessions.status` était
    écrit en dur sur « success » : les succès antérieurs peuvent être
    surdéclarés. Le bornage temporel est la voie « se borner dans le temps »
    offerte par la métrique ; la voie « le signaler » est le champ
    `fiabilite_status_sessions` présent dans toute réponse.
    """
    from core.elo_scorer import get_cost_per_successful_task

    _inserer(base_vide,
             "INSERT INTO sessions (session_id, objective, status, started_at) "
             "VALUES (?, ?, ?, ?)",
             ("s_ancienne", "question", "success", 1000.0))
    _inserer(base_vide,
             "INSERT INTO sessions (session_id, objective, status, started_at) "
             "VALUES (?, ?, ?, ?)",
             ("s_recente", "question", "success", 2000.0))
    _inserer(base_vide,
             "INSERT INTO token_usage (session_id, timestamp, model, cost_usd) "
             "VALUES (?, ?, ?, ?)",
             ("s_ancienne", 1000.0, "deepseek-chat", 0.90))
    _inserer(base_vide,
             "INSERT INTO token_usage (session_id, timestamp, model, cost_usd) "
             "VALUES (?, ?, ?, ?)",
             ("s_recente", 2000.0, "deepseek-chat", 0.10))

    # Sans borne : les deux succès comptent.
    sans_borne = get_cost_per_successful_task()
    assert sans_borne["successful_tasks"] == 2
    assert sans_borne["total_cost_usd"] == 1.00
    assert "note" in sans_borne["fiabilite_status_sessions"]

    # Bornée au 12/08 ~22h52 (epoch 1783 904 000) : seul le succès récent compte.
    bornee = get_cost_per_successful_task(seuil_fiabilite_status=1500.0)
    assert bornee["successful_tasks"] == 1
    assert bornee["total_cost_usd"] == 0.10
    assert bornee["cost_per_success_usd"] == 0.10
