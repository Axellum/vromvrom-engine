"""
tests/unit/test_billing_balance_t349.py — historisation du solde réel des fournisseurs.

Le collecteur récupère déjà le solde DeepSeek à chaque rafraîchissement mais le
jette après usage immédiat : rien ne part en historique, alors que la table
`billing_history` existe. Ce lot branche l'écriture d'un relevé de solde
(`metric='balance_usd'`, `currency='USD'`, `sync_source='api'`) avec une
politique d'écriture dédupliquante, et une lecture par fournisseur/fenêtre.

Garde-fous exercés ici :
  - un solde `None` (fournisseur injoignable) n'écrit AUCUNE ligne, et le
    rafraîchissement se termine normalement sans exception ;
  - deux relevés successifs à solde différent → deux lignes, lisibles par
    `get_balance_history`, dans l'ordre chronologique, avec la bonne valeur ;
  - deux relevés rapprochés à solde INCHANGÉ → une seule ligne ; un relevé
    simulé plus d'une heure après → une ligne de plus (temps piloté par
    monkeypatch du timestamp, jamais par `sleep`) ;
  - une erreur d'écriture en base n'interrompt pas `refresh_all_quotas` : elle
    est journalisée et remontée dans `result["errors"]`.

Aucun réseau : `_collect_deepseek_balance` est mocké. Aucune écriture dans les
bases partagées du poste : `runtime_db.override_db_path()` + `tmp_path`.
"""
import logging
import sqlite3
import threading
import time

import pytest

import core.models_db as models_db
import core.quota_collector as qc
import core.runtime_db as runtime_db
from core import session_history


@pytest.fixture(autouse=True)
def _bases_iso(tmp_path, monkeypatch):
    """Isolation stricte : catalogue + runtime dans tmp_path, pas de DeepSeek."""
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    monkeypatch.setattr(runtime_db, "_DB_PATH", str(tmp_path / "moteur_runtime.db"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # Initialise le schéma canonique réel (dont billing_history) dans la base tmp.
    runtime_db.get_connection().close()


def _seed_catalogue():
    """Catalogue minimal (même seed que test_quota_collector.py) pour que
    refresh_all_quotas puisse écrire sans violer les FK du registre."""
    models_db.upsert_provider("gemini_free", name="Gemini Free", type="free_tier")
    models_db.upsert_provider("gemini_paid", name="Gemini Paid", type="pay_as_you_go")
    models_db.upsert_provider("anthropic_native", name="Anthropic", type="pay_as_you_go")
    for i in (1, 2, 3, 4, 5, 6):
        key_id = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
        models_db.upsert_api_key(
            key_id, provider_id="gemini_free", env_var=key_id,
            project_name=f"projet-{i}", key_type="free",
            quota_rpm=15, quota_rpd=500, quota_tpm=250000,
        )
    models_db.upsert_api_key(
        "GEMINI_PAYANT_API_KEY", provider_id="gemini_paid", env_var="GEMINI_PAYANT_API_KEY",
        project_name="payant", key_type="paid",
        quota_rpm=500, quota_rpd=10000, quota_tpm=4000000,
    )
    models_db.upsert_api_key(
        "ANTHROPIC_API_KEY", provider_id="anthropic_native", env_var="ANTHROPIC_API_KEY",
        project_name="anthropic", key_type="paid",
    )
    models_db.upsert_provider("deepseek", name="DeepSeek", type="pay_as_you_go")
    models_db.upsert_provider("cloud_apis", name="Cloud APIs", type="unknown")
    models_db.upsert_provider("xai", name="xAI", type="pay_as_you_go")
    models_db.upsert_api_key(
        "DEEPSEEK_API_KEY", provider_id="deepseek", env_var="DEEPSEEK_API_KEY",
        project_name="deepseek", key_type="paid", quota_rpm=60, quota_tpm=1000000,
    )
    models_db.upsert_api_key(
        "CLOUD_API_KEY", provider_id="cloud_apis", env_var="CLOUD_API_KEY",
        project_name="cloud", key_type="paid",
    )
    models_db.upsert_api_key(
        "XAI_API_KEY", provider_id="xai", env_var="XAI_API_KEY",
        project_name="grok", key_type="paid", quota_rpm=60, quota_tpm=1000000,
    )
    models_db.upsert_subscription(
        "claude_pro", name="Claude Pro", rolling_window_hours=5,
        hourly_token_limit=1500000, monthly_token_limit=35000000,
    )
    models_db.upsert_subscription(
        "gemini_advanced", name="Gemini Advanced", rolling_window_hours=5,
        hourly_token_limit=4000000, monthly_token_limit=100000000,
    )


def _lignes_balance(provider: str = "deepseek") -> list[tuple]:
    """Lit les lignes balance_usd de billing_history (timestamp, value) triées asc."""
    conn = sqlite3.connect(runtime_db.get_db_path())
    try:
        return conn.execute(
            "SELECT timestamp, value FROM billing_history "
            "WHERE provider = ? AND metric = 'balance_usd' ORDER BY timestamp ASC",
            (provider,),
        ).fetchall()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────
# Critère 1 : un solde None n'écrit AUCUNE ligne
# ──────────────────────────────────────────────────────────────────

def test_solde_none_n_ecrit_rien():
    """[#T349] Un solde None (fournisseur injoignable) n'écrit aucune ligne."""
    assert session_history.insert_balance_snapshot("deepseek", None) == 0
    assert _lignes_balance() == []


def test_refresh_solde_none_se_termine_sans_exception_ni_ligne(monkeypatch):
    """[#T349] refresh_all_quotas avec solde None se termine normalement, sans
    exception, et n'écrit aucune ligne dans billing_history."""
    _seed_catalogue()
    monkeypatch.setattr(qc, "_collect_deepseek_balance", lambda: None)

    resultat = qc.refresh_all_quotas(include_claude=False)

    assert resultat["errors"] == []  # pas d'erreur, le refresh s'est terminé
    assert _lignes_balance() == []   # aucun relevé écrit pour un solde None


# ──────────────────────────────────────────────────────────────────
# Critère 2 : deux solde différents → deux lignes lisibles et ordonnées
# ──────────────────────────────────────────────────────────────────

def test_deux_releves_solde_different_deux_lignes():
    """[#T349] Deux relevés successifs à solde différent → deux lignes, lisibles
    par get_balance_history, dans l'ordre chronologique, avec la bonne valeur."""
    assert session_history.insert_balance_snapshot("deepseek", 11.89) == 1
    assert session_history.insert_balance_snapshot("deepseek", 8.0) == 1

    releves = session_history.get_balance_history(hours=24, provider="deepseek")

    assert len(releves) == 2
    assert [r["value"] for r in releves] == [11.89, 8.0]
    assert releves[0]["timestamp"] <= releves[1]["timestamp"]  # ordre chronologique
    assert all(r["metric"] == "balance_usd" for r in releves)
    assert all(r["currency"] == "USD" for r in releves)
    assert all(r["sync_source"] == "api" for r in releves)


# ──────────────────────────────────────────────────────────────────
# Critère 3 : solde inchangé → pas de doublon ; > 1 h → une ligne de plus
# ──────────────────────────────────────────────────────────────────

def test_solde_inchange_pas_de_doublon_puis_une_ligne_apres_une_heure():
    """[#T349] Deux relevés rapprochés à solde inchangé → une seule ligne ; un
    troisième relevé simulé plus d'une heure après → une ligne de plus.
    Le temps est piloté en vieillissant le dernier relevé en base (jamais sleep)."""
    assert session_history.insert_balance_snapshot("deepseek", 10.0) == 1
    # Relevé immédiat à solde inchangé : pas de doublon (< 1 h, même valeur)
    assert session_history.insert_balance_snapshot("deepseek", 10.0) == 0
    assert len(_lignes_balance()) == 1

    # Simule un relevé plus d'une heure après : on vieillit la ligne existante.
    now = time.time()
    conn = sqlite3.connect(runtime_db.get_db_path())
    conn.execute(
        "UPDATE billing_history SET timestamp = ? WHERE provider = 'deepseek' "
        "AND metric = 'balance_usd'",
        (now - 3601,),
    )
    conn.commit()
    conn.close()

    # Solde inchangé MAIS dernier relevé > 1 h → on écrit une nouvelle ligne.
    assert session_history.insert_balance_snapshot("deepseek", 10.0) == 1
    assert len(_lignes_balance()) == 2


# ──────────────────────────────────────────────────────────────────
# Critère 4 : une erreur d'écriture n'interrompt pas refresh_all_quotas
# ──────────────────────────────────────────────────────────────────

def test_erreur_ecriture_n_interrompt_pas_refresh(monkeypatch, caplog):
    """[#T349] Une erreur d'écriture en base ne fait pas tomber refresh_all_quotas :
    elle est journalisée et remontée dans result['errors'].

    On fait échouer `_get_connection` (pas un monkeypatch de toute la fonction) :
    ainsi le try/except de quota_collector voit une vraie exception SQLite, pas
    un return 0 silencieux (Bugbot #T349).
    """
    _seed_catalogue()
    monkeypatch.setattr(qc, "_collect_deepseek_balance", lambda: 11.89)

    def _panne():
        raise sqlite3.OperationalError("base verrouillée (simulé)")

    monkeypatch.setattr("core.session_history._get_connection", _panne)
    caplog.set_level(logging.WARNING)

    resultat = qc.refresh_all_quotas(include_claude=False)

    # Le refresh s'est terminé normalement : d'autres clés ont été mises à jour.
    assert resultat["updated_keys"] >= 1
    # L'erreur d'écriture est remontée dans errors et journalisée.
    assert any("balance_history" in e for e in resultat["errors"])
    assert any("Erreur relevé solde" in r.getMessage() for r in caplog.records)
    # Dedup skip ≠ panne : rows reste à 0 après échec (pas de faux positif).
    assert resultat.get("balance_snapshot_rows", 0) == 0
