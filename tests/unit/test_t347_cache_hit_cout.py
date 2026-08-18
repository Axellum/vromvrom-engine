"""
tests/unit/test_t347_cache_hit_cout.py — Les tokens cache-hit DeepSeek sont facturés au bon tarif (#T347).

Avant cette PR, la chaîne de comptage jetait les compteurs de cache renvoyés par
l'API : `record_usage()` facturait TOUT le prompt au tarif d'entrée plein, et le
barème (`pricing.py`) ignorait `input_cache_hit_cost_per_m`. Résultat : l'unique
indicateur de dépense du moteur surestimait le coût d'un facteur qui se compte en
unités (97,5 % de cache-hit mesuré sur un appel réel le 17/08).

Sonde réelle (api.deepseek.com, second appel identique, HTTP 200) :
    {"prompt_tokens": 3413, "completion_tokens": 1, "total_tokens": 3414,
     "prompt_tokens_details": {"cached_tokens": 3328},
     "prompt_cache_hit_tokens": 3328, "prompt_cache_miss_tokens": 85}

Ces tests écrivent dans une base créée par le VRAI schéma (`override_db_path` +
`tmp_path`, motif de #T299) et n'appellent AUCUN LLM réel (garde-fou #T261).
"""
import sqlite3

import pytest

# Sonde réelle DeepSeek (second appel, cache-hit massif).
SONDE_CACHE = {
    "prompt_tokens": 3413,
    "completion_tokens": 1,
    "total_tokens": 3414,
    "prompt_tokens_details": {"cached_tokens": 3328},
    "prompt_cache_hit_tokens": 3328,
    "prompt_cache_miss_tokens": 85,
}
# Objet `usage` OpenAI ordinaire : aucun champ de cache.
USAGE_OPENAI_ORDINAIRE = {"prompt_tokens": 100, "completion_tokens": 50}

# Tarifs deepseek-chat (pricing_strategy.json) en $/token.
_INPUT = 0.14 / 1_000_000
_OUTPUT = 0.28 / 1_000_000
_CACHE = 0.003 / 1_000_000


@pytest.fixture
def base_temporaire(tmp_path):
    """Base neuve au schéma canonique, isolée de la base de développement."""
    from core import runtime_db

    db = tmp_path / "t347.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema (schéma réel)
    return str(db)


@pytest.fixture
def hors_pic(monkeypatch):
    """Neutralise le doublement en heures de pic DeepSeek (CI indépendante de l'heure)."""
    import core.pricing as pricing

    monkeypatch.setattr(pricing, "is_deepseek_peak_hours", lambda now: False)


def _ligne_usage(db: str) -> dict:
    """Retourne la (seule) ligne token_usage sous forme de dict."""
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT model, prompt_tokens, completion_tokens, cost_usd, cache_hit_tokens "
            "FROM token_usage"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "aucune ligne enregistrée dans token_usage"
    cols = ("model", "prompt_tokens", "completion_tokens", "cost_usd", "cache_hit_tokens")
    return dict(zip(cols, row, strict=True))


class TestRecordUsageTarifCache:
    """Le test central : le compteur cache change le coût, PAS le calcul de master."""

    def test_cache_hit_facture_au_tarif_cache(self, base_temporaire, hors_pic):
        """90 000 tokens cache-hit sur 100 000 → coût = 10k*input + 90k*cache + 1k*output."""
        from core.token_tracker import record_usage

        record_usage(
            "deepseek-chat", 100_000, 1_000,
            cache_hit_tokens=90_000, session_id="s_t347_central",
        )

        ligne = _ligne_usage(base_temporaire)
        attendu = 10_000 * _INPUT + 90_000 * _CACHE + 1_000 * _OUTPUT
        # PAS 100_000 * _INPUT + 1_000 * _OUTPUT (= 0.01428) : c'est le coût de master.
        assert ligne["cost_usd"] == pytest.approx(attendu, rel=1e-9), ligne
        assert ligne["cache_hit_tokens"] == 90_000

    def test_sans_compteur_cache_coût_identique_a_master(self, base_temporaire, hors_pic):
        """Le même appel SANS compteur cache coûte exactement ce que coûtait master."""
        from core.token_tracker import record_usage

        record_usage("deepseek-chat", 100_000, 1_000, session_id="s_t347_jumeau")

        ligne = _ligne_usage(base_temporaire)
        # Coût d'avant la PR : tout le prompt au tarif plein.
        attendu_master = 100_000 * _INPUT + 1_000 * _OUTPUT
        assert ligne["cost_usd"] == pytest.approx(attendu_master, rel=1e-9), ligne
        assert ligne["cache_hit_tokens"] == 0

    def test_cache_hit_borne_a_prompt_tokens(self, base_temporaire, hors_pic):
        """Un compteur cache > prompt ne peut pas produire un coût négatif."""
        from core.token_tracker import record_usage

        record_usage(
            "deepseek-chat", 100, 10,
            cache_hit_tokens=500, session_id="s_t347_borne",
        )

        ligne = _ligne_usage(base_temporaire)
        attendu = 0 * _INPUT + 100 * _CACHE + 10 * _OUTPUT  # cache borné à 100
        assert ligne["cost_usd"] == pytest.approx(attendu, rel=1e-9), ligne


class TestChaineProvider:
    """La vraie chaîne : OpenAICompatibleProvider._record_usage transmet le compteur."""

    def _provider(self) -> "object":
        from core.openai_compat_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            provider_name="T347",
            base_url="http://provider-factice.invalid",
            api_key="cle-de-test",
            model="deepseek-chat",
        )

    def test_sonde_deepseek_enregistre_3328_tokens_cache(self, base_temporaire, hors_pic):
        """L'objet `usage` de la sonde réelle → 3328 tokens cache-hit enregistrés."""
        provider = self._provider()
        provider._record_usage(SONDE_CACHE, session_id="s_t347_sonde")

        ligne = _ligne_usage(base_temporaire)
        assert ligne["cache_hit_tokens"] == 3328
        assert ligne["prompt_tokens"] == 3413
        assert ligne["completion_tokens"] == 1

    def test_usage_openai_ordinaire_enregistre_zero_cache(self, base_temporaire, hors_pic):
        """Un `usage` OpenAI sans champ cache → 0 token cache (on n'invente rien)."""
        provider = self._provider()
        provider._record_usage(USAGE_OPENAI_ORDINAIRE, session_id="s_t347_ordinaire")

        ligne = _ligne_usage(base_temporaire)
        assert ligne["cache_hit_tokens"] == 0
        assert ligne["cost_usd"] == pytest.approx(
            100 * _INPUT + 50 * _OUTPUT, rel=1e-9
        )

    def test_repli_sur_prompt_tokens_details_cached_tokens(self, base_temporaire, hors_pic):
        """Forme normalisée OpenAI (`prompt_tokens_details.cached_tokens`) → lue."""
        provider = self._provider()
        usage_openai_cache = {
            "prompt_tokens": 200,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 150},
        }
        provider._record_usage(usage_openai_cache, session_id="s_t347_details")

        ligne = _ligne_usage(base_temporaire)
        assert ligne["cache_hit_tokens"] == 150


class TestMigrationIdempotente:
    """La colonne cache_hit_tokens s'ajoute aux bases préexistantes sans perte."""

    def test_migration_ajoute_la_colonne_sans_perdre_de_ligne(self, tmp_path):
        """Base à l'ancien schéma (sans cache_hit_tokens) → colonne ajoutée, ligne intacte."""
        from core import runtime_db

        db = tmp_path / "ancienne.db"
        runtime_db.override_db_path(str(db))

        # 1. Base préexistante à l'ANCIEN schéma (avant #T347), avec une ligne utile.
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp REAL NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                channel TEXT,
                agent_name TEXT
            )
        """)
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s_ancienne", 1234.0, "deepseek-chat", 100, 50, 150, 0.01428),
        )
        conn.commit()
        conn.close()

        # 2. Premier passage : get_connection() déclenche _init_schema → migration.
        runtime_db.get_connection().close()

        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(token_usage)")}
        assert "cache_hit_tokens" in cols, "colonne absente après migration"
        ligne = conn.execute(
            "SELECT session_id, model, prompt_tokens, cost_usd FROM token_usage"
        ).fetchone()
        conn.close()
        # La ligne préexistante n'a pas été perdue ni modifiée.
        assert ligne == ("s_ancienne", "deepseek-chat", 100, 0.01428), ligne

        # 3. Second passage : idempotent, ne casse rien.
        runtime_db.get_connection().close()
        conn = sqlite3.connect(db)
        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(token_usage)")}
        nb = conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
        conn.close()
        assert cols2 == cols
        assert nb == 1
