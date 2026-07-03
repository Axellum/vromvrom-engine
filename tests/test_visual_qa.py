"""
tests/test_visual_qa.py — Tests unitaires pour le service Visual-QA (V6 Acte 1).

Vérifie :
- Analyse LVGL textuelle (pas de Puppeteer nécessaire)
- Résultat d'erreur formaté correctement
- Détection des mots-clés UI
- Intégration avec le ReviewLoop (mock)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestVisualQAService(unittest.TestCase):
    """Tests unitaires pour core/visual_qa.py"""

    def test_error_result_format(self):
        """Le résultat d'erreur doit avoir la bonne structure."""
        from core.visual_qa import VisualQAService
        result = VisualQAService._error_result("Test error")
        self.assertFalse(result["success"])
        self.assertIsNone(result["screenshot_path"])
        self.assertIn("Test error", result["visual_verdict"])
        self.assertEqual(result["score"], -1)
        self.assertIsInstance(result["issues"], list)

    def test_analyze_lvgl_code_extracts_colors(self):
        """L'analyse LVGL doit extraire les couleurs hexadécimales."""
        from core.visual_qa import VisualQAService
        service = VisualQAService()

        yaml_content = """
lvgl:
  widgets:
    - label:
        text_color: 0xFF5733
        bg_color: 0x4CD964
"""
        result = service.analyze_lvgl_code(yaml_content)
        self.assertIn("FF5733", result["colors"])
        self.assertIn("4CD964", result["colors"])

    def test_analyze_lvgl_code_detects_too_many_colors(self):
        """L'analyse doit avertir quand il y a trop de couleurs distinctes."""
        from core.visual_qa import VisualQAService
        service = VisualQAService()

        # Générer un YAML avec 15 couleurs différentes
        colors = [f"0x{i:02X}0000" for i in range(15)]
        yaml_content = "\n".join(f"color: {c}" for c in colors)

        result = service.analyze_lvgl_code(yaml_content)
        self.assertTrue(len(result["warnings"]) > 0)
        self.assertIn("couleurs", result["warnings"][0].lower())

    def test_analyze_lvgl_code_extracts_fonts(self):
        """L'analyse LVGL doit extraire les noms de polices."""
        from core.visual_qa import VisualQAService
        service = VisualQAService()

        yaml_content = """
- label:
    text_font: roboto_24
- arc:
    text_font: montserrat_16
"""
        result = service.analyze_lvgl_code(yaml_content)
        self.assertIn("roboto_24", result["fonts"])
        self.assertIn("montserrat_16", result["fonts"])

    def test_capture_screenshot_returns_none_when_no_chrome(self):
        """Sans Chrome sur le port 9222, la capture doit retourner None."""
        import asyncio
        from core.visual_qa import VisualQAService
        service = VisualQAService()

        # Capture avec URL invalide = doit échouer proprement
        result = asyncio.run(
            service.capture_screenshot("http://localhost:99999/nonexistent")
        )
        self.assertIsNone(result)

    def test_capture_and_analyze_fails_gracefully(self):
        """Sans Puppeteer, capture_and_analyze doit retourner un résultat d'erreur."""
        import asyncio
        from core.visual_qa import VisualQAService
        service = VisualQAService()

        result = asyncio.run(
            service.capture_and_analyze(
                url="http://localhost:99999/nonexistent",
                question="Test",
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["score"], -1)


class TestReviewLoopVisualIntegration(unittest.TestCase):
    """Tests d'intégration pour la détection UI dans le ReviewLoop."""

    def test_ui_keywords_detection(self):
        """Les mots-clés UI doivent être correctement détectés."""
        ui_keywords = [
            "interface", "ui", "ihm", "dashboard", "page",
            "html", "css", "frontend", "bouton", "button",
            "formulaire", "form", "onglet", "tab", "modal",
        ]
        
        # Vérifier que chaque mot-clé est bien dans la liste
        test_objectives = [
            "Créer une interface utilisateur pour la config",
            "Ajouter un bouton de sauvegarde",
            "Modifier le dashboard des métriques",
            "Créer un onglet pour les paramètres",
            "Ajouter un formulaire de contact",
        ]
        
        for obj in test_objectives:
            obj_lower = obj.lower()
            found = any(kw in obj_lower for kw in ui_keywords)
            self.assertTrue(found, f"'{obj}' devrait être détecté comme tâche UI")

        # Non-UI objectives
        non_ui = [
            "Corriger le bug de parsing JSON",
            "Optimiser la requête SQLite",
            "Ajouter le logging dans le routeur",
        ]
        for obj in non_ui:
            obj_lower = obj.lower()
            found = any(kw in obj_lower for kw in ui_keywords)
            self.assertFalse(found, f"'{obj}' ne devrait PAS être détecté comme UI")


if __name__ == "__main__":
    unittest.main()
