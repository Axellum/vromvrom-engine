"""
tests/test_elo_scorer.py — Tests unitaires pour le système de scoring Elo (V6 Acte 3).

Vérifie :
- Initialisation des scores (1500 par défaut)
- Montée du score après succès
- Descente du score après échec
- Classement correct par domaine
- Profil modèle (forces/faiblesses)
- Persistance SQLite
"""

import os
import sys
import tempfile
import unittest

# Ajouter le répertoire parent au path pour importer les modules du moteur
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestEloScorer(unittest.TestCase):
    """Tests unitaires pour core/elo_scorer.py"""

    def setUp(self):
        """Créer une BDD temporaire pour chaque test (isolation complète)."""
        import core.runtime_db as db_mod
        # Rediriger la BDD vers un fichier temporaire
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)

    def tearDown(self):
        """Supprimer la BDD temporaire."""
        import core.runtime_db as db_mod
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_initial_score_is_default(self):
        """Un modèle inconnu doit avoir le score par défaut (1500)."""
        from core.elo_scorer import get_ranked_models, DEFAULT_ELO
        ranked = get_ranked_models("code_generation", ["model-a", "model-b"])
        self.assertEqual(len(ranked), 2)
        for _, score in ranked:
            self.assertEqual(score, DEFAULT_ELO)

    def test_score_increases_after_success(self):
        """Le score Elo doit augmenter après un succès."""
        from core.elo_scorer import update_elo, DEFAULT_ELO
        new_elo = update_elo("test-model", "code_gen", success=True)
        self.assertGreater(new_elo, DEFAULT_ELO)

    def test_score_decreases_after_failure(self):
        """Le score Elo doit diminuer après un échec."""
        from core.elo_scorer import update_elo, DEFAULT_ELO
        new_elo = update_elo("test-model", "analysis", success=False)
        self.assertLess(new_elo, DEFAULT_ELO)

    def test_multiple_successes_increase_score(self):
        """Plusieurs succès consécutifs doivent augmenter le score significativement."""
        from core.elo_scorer import update_elo, DEFAULT_ELO
        last_elo = DEFAULT_ELO
        for _ in range(5):
            last_elo = update_elo("strong-model", "home_assistant", success=True)
        self.assertGreater(last_elo, DEFAULT_ELO + 50)  # Au moins +50 après 5 victoires

    def test_ranking_reflects_performance(self):
        """Le modèle le plus performant doit être classé en premier."""
        from core.elo_scorer import update_elo, get_ranked_models
        # Modèle A : 5 succès
        for _ in range(5):
            update_elo("model-a", "test_domain", success=True)
        # Modèle B : 5 échecs
        for _ in range(5):
            update_elo("model-b", "test_domain", success=False)
        # Modèle C : pas d'historique (score par défaut)

        ranked = get_ranked_models("test_domain", ["model-a", "model-b", "model-c"])
        self.assertEqual(ranked[0][0], "model-a")  # A doit être premier (meilleur)
        self.assertEqual(ranked[-1][0], "model-b")  # B doit être dernier (pire)
        self.assertGreater(ranked[0][1], ranked[1][1])  # Score A > Score C (défaut)
        self.assertLess(ranked[-1][1], 1500)  # Score B < 1500

    def test_domain_independence(self):
        """Les scores Elo sont indépendants par domaine."""
        from core.elo_scorer import update_elo, get_ranked_models
        # Modèle X : excellent en code, mauvais en HA
        for _ in range(3):
            update_elo("model-x", "code_gen", success=True)
            update_elo("model-x", "home_assistant", success=False)

        ranked_code = get_ranked_models("code_gen", ["model-x"])
        ranked_ha = get_ranked_models("home_assistant", ["model-x"])

        self.assertGreater(ranked_code[0][1], 1500)   # Bon en code
        self.assertLess(ranked_ha[0][1], 1500)         # Mauvais en HA

    def test_get_all_scores(self):
        """get_all_scores doit retourner la structure correcte."""
        from core.elo_scorer import update_elo, get_all_scores
        update_elo("model-test", "domain-a", success=True)
        update_elo("model-test", "domain-b", success=False)

        all_scores = get_all_scores()
        self.assertIn("model-test", all_scores)
        self.assertIn("domain-a", all_scores["model-test"])
        self.assertIn("domain-b", all_scores["model-test"])
        self.assertIn("elo", all_scores["model-test"]["domain-a"])
        self.assertIn("wins", all_scores["model-test"]["domain-a"])
        self.assertEqual(all_scores["model-test"]["domain-a"]["wins"], 1)
        self.assertEqual(all_scores["model-test"]["domain-b"]["losses"], 1)

    def test_get_model_profile(self):
        """get_model_profile doit retourner les forces et faiblesses."""
        from core.elo_scorer import update_elo, get_model_profile
        # Créer un profil diversifié
        for _ in range(10):
            update_elo("profiled-model", "code", success=True)
        for _ in range(10):
            update_elo("profiled-model", "analysis", success=False)

        profile = get_model_profile("profiled-model")
        self.assertIn("code", profile)
        self.assertIn("analysis", profile)
        self.assertEqual(profile["code"]["rank"], "expert")  # Score élevé
        self.assertEqual(profile["analysis"]["rank"], "unreliable")  # Score bas

    def test_get_domain_leaderboard(self):
        """Le leaderboard doit être trié par Elo décroissant."""
        from core.elo_scorer import update_elo, get_domain_leaderboard
        for _ in range(3):
            update_elo("leader-1", "lb_test", success=True)
            update_elo("leader-2", "lb_test", success=False)

        lb = get_domain_leaderboard("lb_test", top_n=5)
        self.assertEqual(len(lb), 2)
        self.assertEqual(lb[0]["model"], "leader-1")
        self.assertGreater(lb[0]["elo"], lb[1]["elo"])

    def test_empty_inputs(self):
        """Les entrées vides ne doivent pas planter."""
        from core.elo_scorer import update_elo, get_ranked_models, DEFAULT_ELO
        result = update_elo("", "", success=True)
        self.assertEqual(result, DEFAULT_ELO)
        ranked = get_ranked_models("", [])
        self.assertEqual(ranked, [])

    def test_cost_per_successful_task(self):
        """[#T116] Le coût par tâche réussie doit être correctement agrégé."""
        from core.elo_scorer import update_elo, get_cost_per_successful_task
        import core.runtime_db as db_mod

        # 2 succès + 1 échec pour "cost-model" (seuls les succès comptent)
        update_elo("cost-model", "code_gen", success=True)
        update_elo("cost-model", "analysis", success=True)
        update_elo("cost-model", "home_assistant", success=False)

        # Coût cumulé simulé pour ce modèle (table token_usage)
        conn = db_mod.get_connection()
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-session", 0.0, "cost-model", 100, 50, 150, 0.06),
        )
        conn.commit()
        conn.close()

        result = get_cost_per_successful_task()
        # "cost-model" n'existe pas dans models_registry.db → regroupé sous "unknown"
        self.assertIn("unknown", result)
        entry = result["unknown"]
        self.assertEqual(entry["successful_tasks"], 2)
        self.assertAlmostEqual(entry["total_cost_usd"], 0.06, places=6)
        self.assertAlmostEqual(entry["cost_per_success_usd"], 0.03, places=6)

    def test_cost_per_successful_task_no_wins(self):
        """[#T116] Un modèle sans succès ne doit pas provoquer de division par zéro."""
        from core.elo_scorer import update_elo, get_cost_per_successful_task
        update_elo("failing-model", "code_gen", success=False)

        result = get_cost_per_successful_task()
        self.assertIn("unknown", result)
        self.assertEqual(result["unknown"]["successful_tasks"], 0)
        self.assertIsNone(result["unknown"]["cost_per_success_usd"])

    def test_k_factor_decreases_over_time(self):
        """Après 30+ matchs, le K-factor diminue → convergence plus lente."""
        from core.elo_scorer import update_elo
        # Créer 30 matchs pour dépasser le seuil K
        for _ in range(30):
            update_elo("k-test", "k_domain", success=True)
        # Le prochain succès doit avoir un delta plus faible (K=16 vs K=32)
        score_30 = update_elo("k-test", "k_domain", success=True)
        score_31 = update_elo("k-test", "k_domain", success=True)
        delta = score_31 - score_30
        # Avec K=16 et un Elo élevé, le delta doit être très faible (< 5)
        self.assertLess(delta, 5.0)


if __name__ == "__main__":
    unittest.main()
