"""Tests de la réconciliation des coûts Anthropic : réel (CSV export console) vs estimé (token_usage).

#T182 — On vérifie :
1. l'écart calculé est EXACT (valeur précise, pas seulement le type) ;
2. aucun CSV → réponse explicite « no_exports », jamais un zéro trompeur ;
3. un CSV malformé ne fait pas planter l'appel (ligne ignorée + journalisation) ;
4. le câblage de l'endpoint GET /api/billing/anthropic/reconciliation.
"""
import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import billing as billing_routes
from api.services import billing_service
from core import runtime_db


def _ecrire_csv(tmp_path, nom: str, contenu: str):
    """Écrit un export CSV fabriqué dans un dossier dédié, retourne le dossier."""
    dossier = tmp_path / "exports"
    dossier.mkdir(exist_ok=True)
    (dossier / nom).write_text(contenu, encoding="utf-8")
    return dossier


def _inserer_token_usage(db_path, lignes):
    """Insère des lignes token_usage fabriquées : (model, jour 'YYYY-MM-DD', cost_usd).

    Le timestamp est posé à 12:00 UTC du jour : l'agrégation BDD par jour UTC
    (`date(timestamp, 'unixepoch')`) doit retomber exactement sur ce jour.
    """
    runtime_db.override_db_path(str(db_path))
    runtime_db.get_connection().close()  # déclenche _init_schema
    conn = sqlite3.connect(str(db_path))
    try:
        for model, jour, cout in lignes:
            ts = datetime(int(jour[:4]), int(jour[5:7]), int(jour[8:10]), 12, 0, tzinfo=UTC).timestamp()
            conn.execute(
                "INSERT INTO token_usage (timestamp, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
                "VALUES (?, ?, 100, 50, 150, ?)",
                (ts, model, cout),
            )
        conn.commit()
    finally:
        conn.close()


def _client() -> TestClient:
    """Mini-application FastAPI ne montant que le router billing (pas de dépendance auth ici)."""
    app = FastAPI()
    app.include_router(billing_routes.router)
    return TestClient(app)


def test_ecart_exact_entre_csv_reel_et_estime(tmp_path, monkeypatch):
    """Avec un CSV fabriqué et des token_usage fabriqués, l'écart par jour et le total sont exacts.

    Jeu choisi avec des montants exacts en binaire (0.25, 0.75, 1.50…) pour
    comparer des valeurs, pas des arrondis flottants :
    - 2026-08-01 : réel 2.25 (1.50 + 0.75) vs estimé 2.00 (1.25 + 0.75) ;
    - 2026-08-02 : réel 3.00 vs estimé 2.50 ;
    - 2026-08-03 : réel absent (0) vs estimé 0.50 → écart -0.50, relatif None ;
    - deepseek (99.0) hors périmètre Anthropic : exclu de l'estimé.
    """
    dossier = _ecrire_csv(tmp_path, "anthropic_usage_20260810_120000.csv", (
        "Date (UTC),Model,Prompt tokens,Completion tokens,Cost (USD)\n"
        "2026-08-01,claude-sonnet-4-6,100000,50000,1.50\n"
        "2026-08-01,claude-opus-5,25000,10000,0.75\n"
        "2026-08-02,claude-haiku-4-5-20251001,300000,200000,3.00\n"
    ))
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [
        ("claude-sonnet-4-6", "2026-08-01", 1.25),
        ("claude-opus-5", "2026-08-01", 0.75),
        ("claude-haiku-4-5-20251001", "2026-08-02", 2.50),
        ("claude-sonnet-4-6", "2026-08-03", 0.50),   # jour sans CSV : révélé par l'union
        ("deepseek-chat", "2026-08-01", 99.0),        # hors périmètre Anthropic : exclu
    ])

    resultat = billing_service.reconcile_anthropic_costs()

    assert resultat["status"] == "ok"
    assert resultat["period"] == "day"

    par_jour = {p["date"]: p for p in resultat["periods"]}
    assert set(par_jour) == {"2026-08-01", "2026-08-02", "2026-08-03"}

    j1 = par_jour["2026-08-01"]
    assert j1["real_cost_usd"] == 2.25
    assert j1["estimated_cost_usd"] == 2.00
    assert j1["abs_diff_usd"] == 0.25
    assert j1["rel_diff_pct"] == 11.11  # 0.25 / 2.25 * 100

    j2 = par_jour["2026-08-02"]
    assert j2["real_cost_usd"] == 3.00
    assert j2["estimated_cost_usd"] == 2.50
    assert j2["abs_diff_usd"] == 0.50
    assert j2["rel_diff_pct"] == 16.67  # 0.50 / 3.00 * 100

    j3 = par_jour["2026-08-03"]
    assert j3["real_cost_usd"] == 0.0
    assert j3["estimated_cost_usd"] == 0.50
    assert j3["abs_diff_usd"] == -0.50
    assert j3["rel_diff_pct"] is None  # réel nul, estimé non nul : relatif indéfini

    total = resultat["totals"]
    assert total["real_cost_usd"] == 5.25
    assert total["estimated_cost_usd"] == 5.00
    assert total["abs_diff_usd"] == 0.25
    assert total["rel_diff_pct"] == 4.76  # 0.25 / 5.25 * 100

    assert resultat["db_rows_used"] == 4  # 5 lignes insérées, deepseek exclu
    assert resultat["export_files"][0]["rows_parsed"] == 3


def test_aucun_csv_repond_no_exports_sans_chiffre(tmp_path, monkeypatch):
    """Dossier d'exports vide : status « no_exports » et AUCUN champ numérique.

    Un montant absent ne doit jamais se lire comme un écart nul : la réponse
    ne contient ni « periods », ni « totals », ni valeur d'écart à 0.
    """
    dossier = tmp_path / "exports_vide"
    dossier.mkdir(exist_ok=True)
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [("claude-sonnet-4-6", "2026-08-01", 1.25)])

    resultat = billing_service.reconcile_anthropic_costs()

    assert resultat["status"] == "no_exports"
    assert "aucun export" in resultat["message"].lower()
    assert "periods" not in resultat
    assert "totals" not in resultat
    assert "abs_diff_usd" not in resultat
    assert "db_rows_used" not in resultat


def test_dossier_exports_absent_repond_no_exports(tmp_path, monkeypatch):
    """Dossier data/anthropic_usage_exports/ absent (cas réel actuel) : même contrat « no_exports »."""
    dossier = tmp_path / "exports_absents"  # jamais créé
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    resultat = billing_service.reconcile_anthropic_costs()

    assert resultat["status"] == "no_exports"
    assert "aucun export" in resultat["message"].lower()
    assert "periods" not in resultat
    assert "totals" not in resultat


def test_csv_malforme_ignore_les_lignes_et_journalise(tmp_path, monkeypatch, caplog):
    """Un CSV malformé ne fait pas planter l'appel : lignes invalides ignorées + warning journalisé."""
    dossier = _ecrire_csv(tmp_path, "anthropic_usage_20260810_120000.csv", (
        "Date (UTC),Model,Prompt tokens,Completion tokens,Cost (USD)\n"
        "2026-08-01,claude-sonnet-4-6,100000,50000,1.50\n"
        "2026-08-02,claude-opus-5\n"           # champs manquants
        "2026-13-99,claude-opus-5,0,0,2.00\n"  # date invalide
        "2026-08-02,claude-opus-5,0,0,abc\n"   # coût illisible
        "\n"                                   # ligne vide : sans objet
    ))
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [("claude-sonnet-4-6", "2026-08-01", 1.25)])

    with caplog.at_level(logging.WARNING, logger="api.services.billing_service"):
        resultat = billing_service.reconcile_anthropic_costs()

    assert resultat["status"] == "ok"
    assert resultat["periods"][0]["date"] == "2026-08-01"
    assert resultat["periods"][0]["real_cost_usd"] == 1.50  # seule ligne valide

    detail = resultat["export_files"][0]
    assert detail["rows_parsed"] == 1
    assert detail["rows_skipped"] == 3
    assert any("anthropic_usage_20260810_120000.csv" in r.message for r in caplog.records)
    assert any("ignor" in r.message for r in caplog.records)


def test_fichier_sans_en_tete_reconnu_repond_no_data(tmp_path, monkeypatch):
    """Des CSV présents mais 100 % illisibles : status « no_data » explicite, jamais un zéro."""
    dossier = _ecrire_csv(tmp_path, "anthropic_usage_20260810_120000.csv", (
        "Rapport de consommation\n"
        "Organisation: Test\n"
        "2026-08-01;claude-sonnet-4-6;1.50\n"
    ))
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [("claude-sonnet-4-6", "2026-08-01", 1.25)])

    resultat = billing_service.reconcile_anthropic_costs()

    assert resultat["status"] == "no_data"
    assert "aucun contenu exploitable" in resultat["message"]
    assert "periods" not in resultat
    assert "totals" not in resultat


def test_endpoint_http_aucun_export_repond_no_exports(tmp_path, monkeypatch):
    """L'endpoint GET répond « no_exports » sans CSV — jamais un zéro (critère d'acceptation #4)."""
    dossier = tmp_path / "exports"
    dossier.mkdir(exist_ok=True)
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [("claude-sonnet-4-6", "2026-08-01", 1.25)])

    reponse = _client().get("/api/billing/anthropic/reconciliation")

    assert reponse.status_code == 200
    payload = reponse.json()
    assert payload["status"] == "no_exports"
    assert "aucun export" in payload["message"].lower()
    assert "periods" not in payload
    assert "totals" not in payload


def test_endpoint_http_retourne_les_ecarts(tmp_path, monkeypatch):
    """L'endpoint expose bien les écarts réels vs estimés (valeur exacte, pas seulement le type)."""
    dossier = _ecrire_csv(tmp_path, "anthropic_usage_20260810_120000.csv", (
        "Date (UTC),Model,Prompt tokens,Completion tokens,Cost (USD)\n"
        "2026-08-01,claude-sonnet-4-6,100000,50000,2.25\n"
    ))
    monkeypatch.setattr(billing_service, "_ANTHROPIC_EXPORT_DIR", str(dossier))

    db = tmp_path / "moteur.db"
    _inserer_token_usage(db, [("claude-sonnet-4-6", "2026-08-01", 2.00)])

    reponse = _client().get("/api/billing/anthropic/reconciliation")

    assert reponse.status_code == 200
    payload = reponse.json()
    assert payload["status"] == "ok"
    assert payload["periods"][0]["abs_diff_usd"] == 0.25
    assert payload["totals"]["rel_diff_pct"] == 11.11
