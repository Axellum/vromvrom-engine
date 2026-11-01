"""
tests/unit/test_quota_gemini_requetes.py — Le vrai plafond Gemini est en requetes/jour (#T404).

Releve dans le corps du 403 rendu par Google en production le 18/08 a 22h20 :

    "status": "RESOURCE_EXHAUSTED"
    "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    "quotaValue": "20"

Vingt REQUETES par jour et par modele. Le moteur, lui, plafonnait `gemini-free`
a 1 000 000 de TOKENS par HEURE : mauvaise unite, mauvaise fenetre. Le compteur
restait donc a zero pendant que le quota reel etait deja epuise, et la rotation
continuait d'elire un provider mort -- deux cycles autonomes perdus dans la
soiree.
"""

import sqlite3
import time

import pytest

from core.budget_guard import BudgetGuard


def _base_avec_requetes(chemin, nombre, provider="gemini-free", il_y_a_s=60.0):
    """Fabrique une billing_history portant `nombre` requetes recentes."""
    conn = sqlite3.connect(chemin)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS billing_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, provider TEXT, "
        "model TEXT, tokens_used INTEGER, cost_usd REAL, window_type TEXT)"
    )
    maintenant = time.time()
    for _ in range(nombre):
        conn.execute(
            "INSERT INTO billing_history (timestamp, provider, model, tokens_used, cost_usd, window_type) "
            "VALUES (?,?,?,?,?,?)",
            (maintenant - il_y_a_s, provider, "gemini-3.5-flash", 10, 0.0, "daily"),
        )
    conn.commit()
    conn.close()


def test_le_plafond_par_defaut_est_celui_de_google():
    """20 requetes/jour : la valeur que Google renvoie dans son 403."""
    assert BudgetGuard().config["gemini_free_requests_per_day"] == 20


@pytest.mark.asyncio
async def test_gemini_est_ecarte_au_dela_de_20_requetes(tmp_path):
    """
    Le piege exact du 18/08 : peu de tokens (donc le compteur horaire dort),
    mais le quota de requetes est creve.
    """
    base = str(tmp_path / "runtime.db")
    _base_avec_requetes(base, 25)

    bg = BudgetGuard()
    bg.db_path = base
    bg._initialized = True

    provider = await bg.get_available_provider(sains={"gemini-free"})
    assert provider != "gemini-free"


@pytest.mark.asyncio
async def test_gemini_reste_eligible_en_dessous_du_plafond(tmp_path):
    base = str(tmp_path / "runtime.db")
    _base_avec_requetes(base, 3)

    bg = BudgetGuard()
    bg.db_path = base
    bg._initialized = True

    provider = await bg.get_available_provider(sains={"gemini-free"})
    assert provider == "gemini-free"


@pytest.mark.asyncio
async def test_le_rapport_de_quota_annonce_la_metrique_qui_bloque(tmp_path):
    base = str(tmp_path / "runtime.db")
    _base_avec_requetes(base, 21)

    bg = BudgetGuard()
    bg.db_path = base
    bg._initialized = True

    rapport = await bg.get_quota_summary()
    gemini = rapport["providers"]["gemini-free"]
    assert gemini["metric"] == "requêtes_24h"
    assert gemini["limit"] == 20
    assert gemini["used"] == 21
    assert gemini["available"] is False
