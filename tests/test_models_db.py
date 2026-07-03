"""
tests/test_models_db.py — Tests d'intégration pour models_registry.db.

Vérifie le CRUD, le peuplement, l'export et le routing depuis la BDD.

Usage :
    python -X utf8 -m pytest tests/test_models_db.py -v
"""

import os
import sys

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestModelsDB:
    """Tests du module core/models_db.py."""

    def test_import(self):
        """Le module s'importe sans erreur."""
        from core.models_db import get_model, get_active_models, get_routing_score
        assert callable(get_model)
        assert callable(get_active_models)
        assert callable(get_routing_score)

    def test_db_exists(self):
        """La BDD models_registry.db existe après le seed."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "models_registry.db")
        assert os.path.exists(db_path), "models_registry.db introuvable — exécuter seed_models_db.py d'abord"

    def test_db_stats(self):
        """Les statistiques de la BDD sont cohérentes."""
        from core.models_db import get_db_stats
        stats = get_db_stats()
        assert stats.get("providers", 0) >= 6, f"Attendu >= 6 providers, reçu {stats.get('providers')}"
        assert stats.get("models", 0) >= 20, f"Attendu >= 20 modèles, reçu {stats.get('models')}"
        assert stats.get("api_keys", 0) >= 7, f"Attendu >= 7 clés API, reçu {stats.get('api_keys')}"
        assert stats.get("benchmarks", 0) >= 10, f"Attendu >= 10 benchmarks, reçu {stats.get('benchmarks')}"
        assert stats.get("subscriptions", 0) >= 2, f"Attendu >= 2 abonnements, reçu {stats.get('subscriptions')}"

    def test_get_model_existing(self):
        """Récupération d'un modèle existant."""
        from core.models_db import get_model
        model = get_model("gemini-3.5-flash")
        assert model is not None, "gemini-3.5-flash non trouvé"
        assert model["provider_id"] == "gemini_free"
        assert model["status"] == "active"
        assert model["tier"] == "free"

    def test_get_model_nonexistent(self):
        """Un modèle inexistant retourne None."""
        from core.models_db import get_model
        result = get_model("modele-inexistant-xyz")
        assert result is None

    def test_get_active_models(self):
        """Liste des modèles actifs non vide."""
        from core.models_db import get_active_models
        models = get_active_models()
        assert len(models) >= 20, f"Attendu >= 20 modèles actifs, reçu {len(models)}"
        # Vérifier que les modèles clés sont présents
        model_ids = {m["id"] for m in models}
        for expected in ["gemini-3.5-flash", "claude-opus-4-7", "deepseek-chat"]:
            assert expected in model_ids, f"{expected} manquant des modèles actifs"

    def test_get_active_models_by_provider(self):
        """Filtrage par provider fonctionne."""
        from core.models_db import get_active_models
        local_models = get_active_models(provider_id="local")
        assert len(local_models) >= 5, f"Attendu >= 5 modèles locaux, reçu {len(local_models)}"
        for m in local_models:
            assert m["provider_id"] == "local"

    def test_get_models_for_tier(self):
        """Récupération par tier retourne des modèles triés."""
        from core.models_db import get_models_for_tier
        free_models = get_models_for_tier("free")
        assert len(free_models) >= 3, f"Attendu >= 3 modèles free, reçu {len(free_models)}"

    def test_get_model_cost(self):
        """Les tarifs sont correctement stockés."""
        from core.models_db import get_model_cost
        cost = get_model_cost("deepseek-v4-flash")
        assert cost.get("cost_input_per_m") == 0.14
        assert cost.get("cost_output_per_m") == 0.28
        # Aligné sur le catalogue (seed_models_db) : cache-hit DeepSeek V4 Flash = 0.003.
        assert cost.get("cost_cached_per_m") == 0.003

    def test_routing_score_local(self):
        """Le score de routing pour un modèle local est 1.0."""
        from core.models_db import get_routing_score
        score = get_routing_score("qwen2.5-14b-instruct-1m")
        assert score == 1.0, f"Attendu 1.0 pour local, reçu {score}"

    def test_routing_score_gemini_free(self):
        """Le score de routing pour Gemini Free est 2.0."""
        from core.models_db import get_routing_score
        score = get_routing_score("gemini-3.5-flash")
        assert score == 2.0, f"Attendu 2.0 pour gemini_free, reçu {score}"

    def test_routing_score_claude(self):
        """Le score de routing pour Claude CLI est 3.5."""
        from core.models_db import get_routing_score
        score = get_routing_score("claude-opus-4-7")
        assert score == 3.5, f"Attendu 3.5 pour claude_cli, reçu {score}"

    def test_routing_score_deepseek(self):
        """Le score de routing pour DeepSeek est 4.0."""
        from core.models_db import get_routing_score
        score = get_routing_score("deepseek-chat")
        assert score == 4.0, f"Attendu 4.0 pour deepseek, reçu {score}"

    def test_routing_score_unknown(self):
        """Un modèle inconnu retourne 5.0 (défaut)."""
        from core.models_db import get_routing_score
        score = get_routing_score("modele-totalement-inconnu")
        assert score == 5.0, f"Attendu 5.0 par défaut, reçu {score}"

    def test_get_all_providers(self):
        """Liste des providers non vide et triée."""
        from core.models_db import get_all_providers
        providers = get_all_providers()
        assert len(providers) >= 6
        # Vérifie le tri par cascade_priority
        priorities = [p["cascade_priority"] for p in providers]
        assert priorities == sorted(priorities), "Providers non triés par cascade_priority"

    def test_get_benchmarks(self):
        """Les benchmarks de Claude Opus sont présents."""
        from core.models_db import get_benchmarks
        benchmarks = get_benchmarks("claude-opus-4-7")
        assert len(benchmarks) >= 3, f"Attendu >= 3 benchmarks pour opus, reçu {len(benchmarks)}"
        bench_names = {b["benchmark_name"] for b in benchmarks}
        assert "SWE-bench" in bench_names

    def test_get_subscriptions(self):
        """Les abonnements sont correctement stockés."""
        from core.models_db import get_subscriptions
        subs = get_subscriptions()
        assert len(subs) >= 2
        names = {s["name"] for s in subs}
        assert "Claude Pro" in names

    def test_export_pricing_json(self):
        """L'export pricing_json produit un dict non vide."""
        from core.models_db import export_to_pricing_json
        result = export_to_pricing_json()
        assert "subscriptions" in result
        assert len(result["subscriptions"]) >= 2
        # Vérifie que les abonnements ont les champs attendus
        sub = result["subscriptions"][0]
        assert "name" in sub
        assert "cost_monthly_usd" in sub

    def test_get_all_data(self):
        """Le dump complet contient toutes les sections."""
        from core.models_db import get_all_data
        data = get_all_data()
        assert "providers" in data
        assert "models" in data
        assert "api_keys" in data
        assert "subscriptions" in data
        assert data["db_exists"] is True

    def test_upsert_model_idempotent(self):
        """L'upsert d'un modèle existant ne crée pas de doublon."""
        from core.models_db import upsert_model, get_model, get_db_stats
        stats_before = get_db_stats()

        # upsert_model fait un INSERT OR REPLACE sur TOUTES les colonnes — tout champ non
        # passé en kwarg est réinitialisé à NULL. Ce test mute donc réellement la ligne
        # partagée de models_registry.db : on capture l'état complet avant modification et
        # on le restaure intégralement dans un finally (pas juste notes=None, qui laissait
        # context_input/cost_*/supports_*/recommended_use etc. effacés — bug constaté le
        # 03/07/2026 sur gemini-3.5-flash dans la base de dev).
        original = get_model("gemini-3.5-flash")
        assert original is not None, "gemini-3.5-flash doit exister avant ce test (seed manquant ?)"

        try:
            # Upsert un modèle existant avec une note modifiée
            ok = upsert_model(
                "gemini-3.5-flash",
                provider_id="gemini_free",
                display_name="Gemini 3.5 Flash",
                status="active",
                tier="free",
                notes="test_idempotent"
            )
            assert ok is True

            stats_after = get_db_stats()
            assert stats_after["models"] == stats_before["models"], "Le nombre de modèles a changé (doublon?)"

            # Vérifier que la note est mise à jour
            model = get_model("gemini-3.5-flash")
            assert model["notes"] == "test_idempotent"
        finally:
            # Restauration complète de la ligne originale (tous les champs, pas seulement notes)
            restore_fields = {k: v for k, v in original.items() if k != "id"}
            upsert_model("gemini-3.5-flash", **restore_fields)
