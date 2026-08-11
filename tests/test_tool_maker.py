"""
tests/test_tool_maker.py — Tests unitaires pour le ToolMakerAgent (V6 Acte 2).

Vérifie :
- Génération de code Python valide (ast.parse)
- Sauvegarde dans plugins/auto_generated/
- Détection de skill candidat dans le SkillStore
- Cycle complet : record_skill → detection → generation → mark_as_generated
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def _fake_remote_ok(files, **kwargs):
    """[#T207/#T190] Sandbox distante mockée : verdict vert sans appel réseau.
    Les fichiers candidats (outil + test) sont ignorés, le verdict est vert."""
    return {"passed": True, "error": None, "run_url": "https://example.invalid/run/1"}


class TestToolMakerAgent(unittest.TestCase):
    """Tests unitaires pour agents/tool_maker_agent.py"""

    def test_validate_code_valid_python(self):
        """Un code Python valide doit être accepté."""
        from agents.tool_maker_agent import ToolMakerAgent
        agent = ToolMakerAgent()
        result = agent._validate_code("x = 1\nprint(x)")
        self.assertTrue(result["valid"])
        self.assertIsNone(result["error"])

    def test_validate_code_invalid_python(self):
        """Un code Python invalide doit être rejeté avec une erreur."""
        from agents.tool_maker_agent import ToolMakerAgent
        agent = ToolMakerAgent()
        result = agent._validate_code("def foo(\n  broken")
        self.assertFalse(result["valid"])
        self.assertIn("SyntaxError", result["error"])

    def test_build_tool_code_produces_valid_python(self):
        """Le code généré à partir du template doit être du Python valide."""
        from agents.tool_maker_agent import ToolMakerAgent
        agent = ToolMakerAgent()
        spec = {
            "tool_name": "test_tool",
            "class_name": "TestTool",
            "description": "Outil de test",
            "execution_logic": 'results.append("ok")',
        }
        code = agent._build_tool_code(spec, ["read_file", "write_file"])
        validation = agent._validate_code(code)
        self.assertTrue(validation["valid"], f"Code invalide : {validation.get('error')}")

    def test_save_tool_creates_files(self):
        """La sauvegarde doit créer le fichier Python, le test minimal (#T190)
        et le plugin.json."""
        import agents.tool_maker_agent as tm_mod
        # Rediriger vers un répertoire temporaire
        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = tmp_dir

        try:
            agent = tm_mod.ToolMakerAgent()
            code = "# Test tool\nclass TestTool:\n    pass\n"
            test_code = (
                "import sys, os\n"
                "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                "from test_save_tool import TestTool\n"
            )
            path = agent._save_tool("test_save_tool", code, test_code)

            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith(".py"))

            # [#T190] Le test minimal est écrit à côté de l'outil, à un
            # emplacement déterministe : test_<tool_name>.py
            test_path = os.path.join(os.path.dirname(path), "test_test_save_tool.py")
            self.assertTrue(os.path.exists(test_path))
            with open(test_path, encoding='utf-8') as f:
                self.assertEqual(f.read(), test_code)

            # Vérifier le plugin.json
            meta_path = os.path.join(os.path.dirname(path), "plugin.json")
            self.assertTrue(os.path.exists(meta_path))

            with open(meta_path, encoding='utf-8') as f:
                meta = json.load(f)
            self.assertEqual(meta["name"], "test_save_tool")
            self.assertTrue(meta["auto_generated"])
            self.assertTrue(meta["test_passed"])
            self.assertEqual(meta["test_entry_point"], "test_test_save_tool.py")
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_invoke_without_sequence_returns_error(self):
        """Sans séquence d'outils, l'invocation doit retourner une erreur."""
        from agents.tool_maker_agent import ToolMakerAgent
        from core.state import TaskPayload

        agent = ToolMakerAgent()
        payload = TaskPayload(
            task_objective="Test",
            metadata={"tools_sequence": []},
        )
        result = asyncio.run(agent.invoke(payload))
        self.assertEqual(result.status, "error")
        self.assertIn("Aucune séquence", result.error_message)

    def test_invoke_generates_tool_with_fallback(self):
        """Sans LLM, le fallback doit générer un template fonctionnel, exécuter
        son test minimal (#T190) et persister outil + test quand
        MOTEUR_ENABLE_TOOL_MAKER est actif (P0-1.4)."""
        import agents.tool_maker_agent as tm_mod
        from core.state import TaskPayload

        # Rediriger vers un répertoire temporaire
        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = tmp_dir
        os.environ["MOTEUR_ENABLE_TOOL_MAKER"] = "1"  # [P0-1.4] autoriser la persistance

        try:
            agent = tm_mod.ToolMakerAgent(llm_gateway=None)
            payload = TaskPayload(
                task_objective="Générer un outil",
                metadata={
                    "skill_pattern": "lire et modifier yaml",
                    "tools_sequence": ["read_file", "write_file", "validate_yaml"],
                    "objective": "Modifier un fichier YAML",
                },
            )
            # [#T207] La validation d'exécution passe désormais par le runner
            # GitHub Actions distant — mocké ici (pas de réseau dans les tests).
            with patch("core.toolmaker_sandbox.validate_candidate_remote", _fake_remote_ok):
                result = asyncio.run(agent.invoke(payload))
            self.assertEqual(result.status, "success")
            self.assertIn("généré avec succès", result.result_data)
            self.assertTrue(result.metadata.get("auto_generated"))
            self.assertTrue(result.metadata.get("persisted"))
            self.assertTrue(result.metadata.get("test_passed"))
            self.assertIn("Test :", result.result_data)

            # [#T190] Outil + test + plugin.json sont persistés ensemble
            saved = os.listdir(tmp_dir)
            self.assertEqual(len(saved), 1)
            tool_dir = os.path.join(tmp_dir, saved[0])
            files = sorted(os.listdir(tool_dir))
            self.assertEqual(
                files,
                sorted([f"{saved[0]}.py", f"test_{saved[0]}.py", "plugin.json"]),
            )
            # Le test persisté est exécutable seul : import + appel + retour
            test_path = os.path.join(tool_dir, f"test_{saved[0]}.py")
            with open(test_path, encoding='utf-8') as f:
                test_content = f.read()
            self.assertIn(f"from {saved[0]} import", test_content)
            self.assertIn("asyncio.run(tool.execute(", test_content)
            self.assertIn("SANDBOX_OK", test_content)
            self.assertIn("assert result is not None", test_content)
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            os.environ.pop("MOTEUR_ENABLE_TOOL_MAKER", None)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_invoke_flag_off_does_not_persist(self):
        """[P0-1.4] Flag désactivé : l'outil est validé mais JAMAIS écrit sur disque."""
        import agents.tool_maker_agent as tm_mod
        from core.state import TaskPayload

        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = tmp_dir
        os.environ.pop("MOTEUR_ENABLE_TOOL_MAKER", None)  # explicitement off

        try:
            agent = tm_mod.ToolMakerAgent(llm_gateway=None)
            payload = TaskPayload(
                task_objective="Générer un outil",
                metadata={
                    "skill_pattern": "lire et modifier yaml",
                    "tools_sequence": ["read_file", "write_file"],
                    "objective": "Modifier un fichier YAML",
                },
            )
            # [#T207] Sandbox distante mockée (verdict vert, pas de réseau).
            with patch("core.toolmaker_sandbox.validate_candidate_remote", _fake_remote_ok):
                result = asyncio.run(agent.invoke(payload))
            self.assertEqual(result.status, "success")
            self.assertFalse(result.metadata.get("persisted"))
            # Rien ne doit avoir été écrit
            self.assertEqual(os.listdir(tmp_dir), [])
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_invoke_sans_pat_rejette_fail_closed(self):
        """[#T207] Sans MOTEUR_TOOLMAKER_PAT, la validation distante échoue et
        l'outil est rejeté — il n'existe AUCUN repli vers une exécution locale."""
        import agents.tool_maker_agent as tm_mod
        from core.state import TaskPayload

        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = tmp_dir
        os.environ["MOTEUR_ENABLE_TOOL_MAKER"] = "1"
        os.environ.pop("MOTEUR_TOOLMAKER_PAT", None)

        try:
            agent = tm_mod.ToolMakerAgent(llm_gateway=None)
            payload = TaskPayload(
                task_objective="Générer un outil",
                metadata={
                    "skill_pattern": "lire et modifier yaml",
                    "tools_sequence": ["read_file", "write_file"],
                    "objective": "Modifier un fichier YAML",
                },
            )
            result = asyncio.run(agent.invoke(payload))
            self.assertEqual(result.status, "error")
            self.assertIn("sandbox", result.error_message.lower())
            # Rien ne doit avoir été écrit sur disque
            self.assertEqual(os.listdir(tmp_dir), [])
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            os.environ.pop("MOTEUR_ENABLE_TOOL_MAKER", None)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_invoke_rejects_path_traversal_tool_name(self):
        """[P0-1.4] Un tool_name malveillant (traversée de chemin) est rejeté,
        rien n'est écrit hors du répertoire prévu."""
        import agents.tool_maker_agent as tm_mod
        from core.state import TaskPayload

        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = os.path.join(tmp_dir, "auto")
        os.environ["MOTEUR_ENABLE_TOOL_MAKER"] = "1"

        try:
            agent = tm_mod.ToolMakerAgent(llm_gateway=None)

            async def _fake_spec(*a, **k):
                return {
                    "tool_name": "../../evil",
                    "class_name": "Evil",
                    "description": "x",
                    "execution_logic": 'results.append("x")',
                }
            agent._generate_tool_spec = _fake_spec

            payload = TaskPayload(
                task_objective="x",
                metadata={"skill_pattern": "x", "tools_sequence": ["a"], "objective": "x"},
            )
            result = asyncio.run(agent.invoke(payload))
            self.assertEqual(result.status, "error")
            self.assertIn("invalides", result.error_message)
            # Aucun fichier 'evil' écrit où que ce soit sous tmp_dir
            written = []
            for root, _dirs, files in os.walk(tmp_dir):
                written.extend(files)
            self.assertEqual(written, [])
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            os.environ.pop("MOTEUR_ENABLE_TOOL_MAKER", None)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_invoke_rejects_class_name_injection(self):
        """[P0-1.4] Un class_name contenant du code (injection) est rejeté."""
        import agents.tool_maker_agent as tm_mod
        from core.state import TaskPayload

        original_dir = tm_mod.AUTO_TOOLS_DIR
        tmp_dir = tempfile.mkdtemp()
        tm_mod.AUTO_TOOLS_DIR = tmp_dir
        os.environ["MOTEUR_ENABLE_TOOL_MAKER"] = "1"

        try:
            agent = tm_mod.ToolMakerAgent(llm_gateway=None)

            async def _fake_spec(*a, **k):
                return {
                    "tool_name": "ok_tool",
                    "class_name": "Evil(); import os; os.system('x')",
                    "description": "x",
                    "execution_logic": 'results.append("x")',
                }
            agent._generate_tool_spec = _fake_spec

            payload = TaskPayload(
                task_objective="x",
                metadata={"skill_pattern": "x", "tools_sequence": ["a"], "objective": "x"},
            )
            result = asyncio.run(agent.invoke(payload))
            self.assertEqual(result.status, "error")
            self.assertEqual(os.listdir(tmp_dir), [])
        finally:
            tm_mod.AUTO_TOOLS_DIR = original_dir
            os.environ.pop("MOTEUR_ENABLE_TOOL_MAKER", None)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_save_tool_rejects_bad_name(self):
        """[P0-1.4] _save_tool refuse un nom non conforme (défense en profondeur)."""
        from agents.tool_maker_agent import ToolMakerAgent
        agent = ToolMakerAgent()
        with self.assertRaises(ValueError):
            agent._save_tool("../escape", "x = 1\n", "test\n")


class TestSkillStoreToolmaking(unittest.TestCase):
    """Tests pour les méthodes ToolMaker du SkillStore."""

    def setUp(self):
        """Créer un fichier skills temporaire."""
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode='w')
        self._tmp.write("[]")
        self._tmp.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_no_candidates_initially(self):
        """Pas de candidats au départ."""
        from memory.skills import SkillStore
        store = SkillStore(skills_file=self._tmp.name)
        self.assertEqual(len(store.get_candidates_for_toolmaking()), 0)

    def test_candidate_after_threshold(self):
        """Un skill doit devenir candidat après le seuil de succès."""
        from memory.skills import SkillStore
        store = SkillStore(skills_file=self._tmp.name)

        # Enregistrer le même skill 3 fois (seuil par défaut)
        for _ in range(3):
            store.record_skill(
                pattern="lire_modifier_yaml",
                tools_sequence=["read_file", "write_file"],
                tags=["yaml"],
            )

        candidates = store.get_candidates_for_toolmaking()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["pattern"], "lire_modifier_yaml")
        self.assertEqual(candidates[0]["success_count"], 3)

    def test_mark_as_generated_removes_candidate(self):
        """Marquer un skill comme généré doit empêcher la ré-candidature."""
        from memory.skills import SkillStore
        store = SkillStore(skills_file=self._tmp.name)

        for _ in range(3):
            store.record_skill(
                pattern="test_pattern",
                tools_sequence=["tool_a", "tool_b"],
            )

        self.assertEqual(len(store.get_candidates_for_toolmaking()), 1)

        # Marquer comme généré
        result = store.mark_as_generated("test_pattern", "/path/to/tool.py")
        self.assertTrue(result)

        # Plus de candidats
        self.assertEqual(len(store.get_candidates_for_toolmaking()), 0)

    def test_mark_nonexistent_pattern_returns_false(self):
        """Marquer un pattern inexistant doit retourner False."""
        from memory.skills import SkillStore
        store = SkillStore(skills_file=self._tmp.name)
        self.assertFalse(store.mark_as_generated("inexistant"))


if __name__ == "__main__":
    unittest.main()
