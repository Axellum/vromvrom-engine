"""
test_upsert_model_non_destructif.py — `upsert_model` ne détruit plus (#T254).

Le catalogue est enrichi par plusieurs sources qui ne connaissent pas les mêmes
colonnes : le seed (25), les descripteurs de plugins providers (16, #T231),
l'IHM, le ping. Tant que l'écriture était un `INSERT OR REPLACE` portant toutes
les colonnes, la source la plus pauvre effaçait le travail des autres — et
l'enregistrement des plugins rejouant à chaque démarrage, la perte était
silencieuse et permanente (mesurée en prod le 10/08 : `cost_cached_per_m` sur
0 modèle des 106, tous les modèles EUR repassés en USD, `supports_search_grounding`
à 0 partout).

Ces tests fixent le contrat : à la création on applique les défauts, à la mise
à jour on n'écrit QUE ce qui est fourni.
"""

import os
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.models_db as models_db


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Catalogue neuf et isolé (jamais la base du poste)."""
    chemin = str(tmp_path / "models_registry.db")
    monkeypatch.setattr(models_db, "_DB_PATH", chemin)
    # La connexion est mémorisée dans un thread-local : sans le remettre à neuf,
    # les tests suivants écriraient dans la base temporaire du PREMIER test tout
    # en lisant la leur (piège rencontré en écrivant ces tests).
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    conn = sqlite3.connect(chemin)
    models_db._ensure_schema(conn)
    # `models.provider_id` référence `providers(id)` et la contrainte est active :
    # sans provider, tout upsert échouerait sur la clé étrangère.
    for provider in ("p", "p1", "gemini_paid"):
        conn.execute(
            "insert into providers (id, name, type) values (?, ?, 'api')",
            (provider, provider),
        )
    conn.commit()
    conn.close()
    return chemin


def _lire(chemin, model_id):
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    ligne = conn.execute("select * from models where id = ?", (model_id,)).fetchone()
    conn.close()
    return dict(ligne) if ligne else None


class TestCreation:
    def test_defauts_a_la_creation(self, base):
        assert models_db.upsert_model("m1", provider_id="p1") is True

        ligne = _lire(base, "m1")
        assert ligne["provider_id"] == "p1"
        assert ligne["display_name"] == "m1"      # défaut = l'id
        assert ligne["status"] == "active"        # défaut SQL
        assert ligne["tier"] == "free"
        assert ligne["currency"] == "USD"

    def test_valeurs_completes_conservees(self, base):
        models_db.upsert_model(
            "m2", provider_id="gemini_paid", display_name="Gemini payant",
            cost_input_per_m=1.28, cost_cached_per_m=0.128, currency="EUR",
            supports_search_grounding=1, supports_thinking=1, ttft_ms=622,
            throughput_tps=100.0, last_tested="2026-05-26",
        )

        ligne = _lire(base, "m2")
        assert ligne["currency"] == "EUR"
        assert ligne["cost_cached_per_m"] == 0.128
        assert ligne["supports_search_grounding"] == 1
        assert ligne["ttft_ms"] == 622


class TestMiseAJourNonDestructive:
    """Le cœur de #T254 : une source pauvre n'efface plus une source riche."""

    def test_les_colonnes_absentes_survivent(self, base):
        # Le seed pose un modèle complet…
        models_db.upsert_model(
            "gemini-3.5-flash-paid", provider_id="gemini_paid",
            cost_input_per_m=1.282575, cost_cached_per_m=0.128257, currency="EUR",
            supports_search_grounding=1, supports_thinking=1,
            ttft_ms=622, throughput_tps=100.0, last_tested="2026-05-26",
        )
        # …puis un descripteur de plugin, qui ne connaît que 16 colonnes.
        models_db.upsert_model(
            "gemini-3.5-flash-paid", provider_id="gemini_paid",
            display_name="Gemini 3.5 Flash (GCP)", tier="paid", routing_tier="moyen",
            cost_input_per_m=1.282575, cost_output_per_m=7.69545,
            supports_tools=1, supports_json_mode=1, supports_streaming=1,
        )

        ligne = _lire(base, "gemini-3.5-flash-paid")
        assert ligne["cost_cached_per_m"] == 0.128257, "remise cache effacée"
        assert ligne["currency"] == "EUR", "devise repassée en USD"
        assert ligne["supports_search_grounding"] == 1, "capacité grounding perdue"
        assert ligne["supports_thinking"] == 1
        assert ligne["ttft_ms"] == 622
        assert ligne["last_tested"] == "2026-05-26"
        # …et ce que le plugin apporte est bien appliqué
        assert ligne["display_name"] == "Gemini 3.5 Flash (GCP)"
        assert ligne["routing_tier"] == "moyen"
        assert ligne["supports_tools"] == 1

    def test_idempotence_sur_plusieurs_demarrages(self, base):
        models_db.upsert_model("m3", provider_id="p", cost_cached_per_m=0.003, currency="EUR")
        for _ in range(5):   # 5 démarrages du moteur
            models_db.upsert_model("m3", provider_id="p", tier="paid", supports_tools=1)

        ligne = _lire(base, "m3")
        assert ligne["cost_cached_per_m"] == 0.003
        assert ligne["currency"] == "EUR"

    def test_desactivation_manuelle_non_ecrasee(self, base):
        """Un modèle désactivé depuis l'IHM (#T243) doit le rester au redémarrage."""
        models_db.upsert_model("m4", provider_id="p")
        models_db.set_model_status("m4", "inactive")

        models_db.upsert_model("m4", provider_id="p", tier="paid")   # ré-enregistrement plugin

        assert _lire(base, "m4")["status"] == "inactive"

    def test_statut_explicite_toujours_applique(self, base):
        models_db.upsert_model("m5", provider_id="p")
        models_db.set_model_status("m5", "inactive")

        models_db.upsert_model("m5", provider_id="p", status="active")

        assert _lire(base, "m5")["status"] == "active"

    def test_colonne_inconnue_ignoree_et_journalisee(self, base, caplog):
        assert models_db.upsert_model("m6", provider_id="p", colonne_qui_nexiste_pas=1) is True
        assert _lire(base, "m6") is not None
        assert "colonne_qui_nexiste_pas" in caplog.text


class TestDescripteurDePlugin:
    """Le descripteur ne doit plus réactiver ce que l'utilisateur a désactivé."""

    def test_en_colonnes_ne_porte_pas_de_statut(self):
        from core.llm.provider_plugin import ModelSpec

        colonnes = ModelSpec(id="x").en_colonnes("p")
        assert "status" not in colonnes, "le descripteur force encore le statut"
        assert colonnes["provider_id"] == "p"
