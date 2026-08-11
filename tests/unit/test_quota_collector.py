"""
tests/unit/test_quota_collector.py — Boucle périodique et fenêtres glissantes (#T269).

Vérifie que :
- quota_refresh_loop() appelle refresh_all_quotas() à l'intervalle configuré
  (horloge simulée, aucune attente réelle dans les tests) ;
- une exception du collecteur est journalisée et ne casse pas la boucle
  (l'itération suivante a lieu) ;
- après un rafraîchissement avec des lignes token_usage couvrant plusieurs
  fenêtres, used_rpd, used_tph ET used_monthly sont non nuls et cohérents
  avec les données injectées ;
- GEMINI_API_KEY_6 (6e clé free seedée dans api_keys) passe en source
  'calculated' — la liste des clés est dérivée de la SSOT, pas codée en dur ;
- un instantané est écrit dans quota_snapshots à chaque rafraîchissement.

Aucune écriture dans les bases partagées du poste : models_registry.db et
moteur_runtime.db sont isolées dans tmp_path (fixtures standard du repo).
"""

import asyncio
import logging
import sqlite3
import threading
import time

import pytest

import core.models_db as models_db
import core.quota_collector as qc
import core.runtime_db as runtime_db

# ──────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _bases_iso(tmp_path, monkeypatch):
    """Isolation stricte : catalogue + runtime dans tmp_path, pas de DeepSeek."""
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    monkeypatch.setattr(runtime_db, "_DB_PATH", str(tmp_path / "moteur_runtime.db"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _seed_catalogue():
    """Catalogue minimal : clés Gemini free (6) + paid + Anthropic + abonnements."""
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
    # Clés touchées par le refresh mais hors périmètre du test : présentes pour
    # respecter la FK de quota_realtime (même catalogue que la prod seedée)
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


def _injecter_token_usage():
    """Lignes token_usage couvrant plusieurs fenêtres (1 h / 24 h / 30 j).

    - gemini-free-flash : 12 appels dans les 24 h → RPD agrégé 12 ;
    - claude-cli-abo     : 3 appels dans l'heure (1000 tokens) + 7 plus anciens
      (< 30 j, 1000 tokens) → TPH 3000, mensuel 10000 ;
    - gemini-cli-abo     : 2 appels dans l'heure (500 tokens) + 8 plus anciens
      (< 30 j, 500 tokens) → TPH 1000, mensuel 5000.
    """
    now = time.time()
    lignes = [("gemini-free-flash", now - 3600, 100) for _ in range(12)]
    lignes += [("claude-cli-abo", now - 600, 1000) for _ in range(3)]
    lignes += [("claude-cli-abo", now - 7 * 86400, 1000) for _ in range(7)]
    lignes += [("gemini-cli-abo", now - 300, 500) for _ in range(2)]
    lignes += [("gemini-cli-abo", now - 10 * 86400, 500) for _ in range(8)]

    conn = runtime_db.get_connection()
    for channel, ts, tokens in lignes:
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, total_tokens, channel) "
            "VALUES (?, ?, ?, ?, ?)",
            ("session-test", ts, f"modele-{channel}", tokens, channel),
        )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────────
# Boucle périodique (horloge simulée)
# ──────────────────────────────────────────────────────────────────

class _ArretBoucle(Exception):
    """Levée par l'horloge simulée pour clore la boucle sans attendre."""


class _HorlogeSimulee:
    """Remplace asyncio.sleep : enregistre les durées puis stoppe la boucle."""

    def __init__(self, arrets_apres: int = 3):
        self.durees: list[float] = []
        self._restants = arrets_apres

    async def sleep(self, duree: float) -> None:
        self.durees.append(duree)
        self._restants -= 1
        if self._restants <= 0:
            raise _ArretBoucle()


def _lancer_boucle(monkeypatch, intervalle, collecteur, iterations: int = 3) -> _HorlogeSimulee:
    """Exécute quota_refresh_loop avec horloge simulée ; retourne l'horloge.

    L'horloge laisse passer `iterations` cycles complets (sommeil + collecte)
    puis lève _ArretBoucle lors du sommeil suivant.
    """
    horloge = _HorlogeSimulee(arrets_apres=iterations + 1)
    monkeypatch.setattr(qc.asyncio, "sleep", horloge.sleep)
    monkeypatch.setattr(qc, "refresh_all_quotas", collecteur)
    with pytest.raises(_ArretBoucle):
        asyncio.run(qc.quota_refresh_loop(interval_seconds=intervalle))
    return horloge


def test_boucle_appelle_collecteur_a_intervalle_configurable(monkeypatch):
    """[#T269] La boucle dort `intervalle` s puis appelle le collecteur sans Claude."""
    appels = []

    def _collecteur(include_claude=False, force_claude=False):
        appels.append({"include_claude": include_claude, "force_claude": force_claude})
        return {"updated_keys": 1, "snapshot_rows": 2}

    horloge = _lancer_boucle(monkeypatch, intervalle=42, collecteur=_collecteur)

    assert horloge.durees == [42, 42, 42, 42]
    assert len(appels) == 3
    assert all(a["include_claude"] is False for a in appels)


def test_boucle_intervalle_lu_depuis_env(monkeypatch):
    """[#T269] Sans intervalle explicite, la boucle lit QUOTA_REFRESH_INTERVAL_SECONDS."""
    monkeypatch.setenv("QUOTA_REFRESH_INTERVAL_SECONDS", "123")

    def _collecteur(**kwargs):
        return {}

    horloge = _lancer_boucle(monkeypatch, intervalle=None, collecteur=_collecteur)
    assert horloge.durees == [123, 123, 123, 123]


def test_boucle_survit_a_exception_du_collecteur(monkeypatch, caplog):
    """[#T269] Une erreur de collecte est journalisée et la boucle continue."""
    appels = []

    def _collecteur_instable(**kwargs):
        appels.append(1)
        if len(appels) == 1:
            raise RuntimeError("Provider injoignable (normal)")
        return {"updated_keys": 0, "snapshot_rows": 0}

    caplog.set_level(logging.WARNING)
    _lancer_boucle(monkeypatch, intervalle=30, collecteur=_collecteur_instable)

    assert len(appels) == 3  # l'échec n'a pas interrompu les itérations suivantes
    assert any("la boucle continue" in r.getMessage() for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Intervalle configurable
# ──────────────────────────────────────────────────────────────────

def test_intervalle_env_valide(monkeypatch):
    """[#T269] QUOTA_REFRESH_INTERVAL_SECONDS valide est respecté."""
    monkeypatch.setenv("QUOTA_REFRESH_INTERVAL_SECONDS", "90")
    assert qc.get_quota_refresh_interval() == 90


def test_intervalle_env_invalide_retombe_defaut(monkeypatch, caplog):
    """[#T269] Valeur invalide → défaut, avec un warning explicite."""
    monkeypatch.setenv("QUOTA_REFRESH_INTERVAL_SECONDS", "abc")
    caplog.set_level(logging.WARNING)
    assert qc.get_quota_refresh_interval() == qc.QUOTA_REFRESH_INTERVAL_DEFAULT
    assert any("invalide" in r.getMessage() for r in caplog.records)


def test_intervalle_env_bornes_min(monkeypatch):
    """[#T269] Une valeur trop basse est bornée (ne jamais marteler les APIs)."""
    monkeypatch.setenv("QUOTA_REFRESH_INTERVAL_SECONDS", "5")
    assert qc.get_quota_refresh_interval() == qc.QUOTA_REFRESH_INTERVAL_MIN


# ──────────────────────────────────────────────────────────────────
# Fenêtres glissantes complètes (2a) + clés dérivées (2b) + snapshot (2c)
# ──────────────────────────────────────────────────────────────────

def test_refresh_propage_fenetres_glissantes_completes():
    """[#T269] used_rpd, used_tph ET used_monthly non nuls et cohérents."""
    _seed_catalogue()
    _injecter_token_usage()

    resultat = qc.refresh_all_quotas(include_claude=False)

    quotas = {q["api_key_id"]: q for q in models_db.get_all_quotas_realtime()}

    # (2b) les 6 clés free sont calculées : liste dérivée de api_keys (SSOT),
    # GEMINI_API_KEY_6 n'est plus oubliée
    for key_id in ("GEMINI_API_KEY", "GEMINI_API_KEY_6"):
        ligne = quotas[key_id]
        assert ligne["source"] == "calculated"
        assert ligne["used_rpd"] == 12 // 6  # 12 appels flash/24h répartis sur 6 clés

    # (2a) Claude Pro : fenêtre 1 h → used_tph, fenêtre 30 j → used_monthly
    claude = quotas["ANTHROPIC_API_KEY"]
    assert claude["used_tph"] == 3000
    assert claude["used_monthly"] == 10000
    assert claude["limit_tph"] == 1500000
    assert claude["limit_monthly"] == 35000000

    # (2a) la ligne Gemini agrège les TROIS fenêtres : RPD (API free,
    # approximation existante) + TPH/mensuel (CLI Advanced)
    gemini = quotas["GEMINI_PAYANT_API_KEY"]
    assert gemini["used_rpd"] == 12
    assert gemini["used_tph"] == 1000
    assert gemini["used_monthly"] == 5000
    assert gemini["limit_tph"] == 4000000

    # (2c) un instantané a été écrit à ce rafraîchissement
    assert resultat["snapshot_rows"] >= 3


def test_snapshot_ecrit_a_chaque_refresh():
    """[#T269] quota_snapshots reçoit les fenêtres calculées, pas un dict vide."""
    _seed_catalogue()
    _injecter_token_usage()

    qc.refresh_all_quotas(include_claude=False)

    conn = sqlite3.connect(runtime_db.get_db_path())
    lignes = conn.execute(
        "SELECT channel, metric, value FROM quota_snapshots"
    ).fetchall()
    conn.close()

    par_cle = {(c, m): v for c, m, v in lignes}
    assert par_cle.get(("gemini_free_flash", "rpd")) == 12
    assert par_cle.get(("claude_cli", "tph")) == 3000
    assert par_cle.get(("claude_cli", "tpm")) == 10000
    assert par_cle.get(("gemini_cli", "tph")) == 1000
    assert par_cle.get(("gemini_cli", "tpm")) == 5000


def test_snapshot_garantit_table_sur_base_heritee(tmp_path, monkeypatch):
    """[#T269] La table quota_snapshots est créée si absente (bases héritées)."""
    # Base brute sans aucune table (cas des bases héritées de session_history.db)
    chemin = str(tmp_path / "moteur_runtime.db")
    sqlite3.connect(chemin).close()

    monkeypatch.setattr(runtime_db, "_DB_PATH", chemin)

    from core.session_history import insert_quota_snapshot
    inseres = insert_quota_snapshot({"claude_cli_tph": 42})

    conn = sqlite3.connect(chemin)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ligne = conn.execute(
        "SELECT channel, metric, value FROM quota_snapshots"
    ).fetchone()
    conn.close()

    assert "quota_snapshots" in tables
    assert inseres == 1
    assert ligne == ("claude_cli", "tph", 42)
