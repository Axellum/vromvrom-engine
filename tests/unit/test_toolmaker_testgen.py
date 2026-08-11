"""
tests/unit/test_toolmaker_testgen.py — Test minimal des outils auto-générés (#T190).

Vérifie le cycle complet « tout outil généré est testé avant d'être enregistré » :
- la déduction des arguments d'appel depuis la signature (**kwargs) ;
- le contenu du test généré (import du module + appel + retour ni None ni exception) ;
- que le test généré s'exécute réellement et distingue un outil valide d'un
  outil qui lève à l'import ou qui retourne None ;
- le gate d'enregistrement : test échoué → outil JAMAIS persisté, raison
  journalisée ; test réussi → outil + test + plugin.json persistés ensemble.

Note sécurité : les exécutions locales du test généré (runpy) portent sur des
outils construits par le TEMPLATE du moteur avec une execution_logic littérale
contrôlée — pas sur du code LLM non maîtrisé (interdit par #T207). Le vrai
runner GitHub Actions reste le seul exécuteur des candidats LLM.
"""

import asyncio
import os
import runpy

from agents.tool_maker_agent import ToolMakerAgent
from core.state import TaskPayload

# ─────────────────────────────────────────────────────────────
# Déduction des arguments d'appel depuis la signature
# ─────────────────────────────────────────────────────────────

def _build_agent_code(execution_logic: str, tool_name="test_gen_args", class_name="TestGenArgs"):
    """Construit le code d'un outil template avec une logique contrôlée.

    Le TOOL_TEMPLATE indente la PREMIÈRE ligne de la logique de 12 espaces ;
    les lignes suivantes doivent porter leur propre indentation (12 espaces),
    comme un LLM correctement éduqué les produit.
    """
    agent = ToolMakerAgent()
    lines = execution_logic.strip("\n").split("\n")
    indented = "\n".join(
        line if i == 0 else (f"            {line}" if line.strip() else line)
        for i, line in enumerate(lines)
    ) + "\n"
    spec = {
        "tool_name": tool_name,
        "class_name": class_name,
        "description": "Outil de test unitaire",
        "execution_logic": indented,
    }
    return agent._build_tool_code(spec, ["tool_a", "tool_b"])


def test_deduce_args_kwargs_get():
    """Les clés lues via kwargs.get() deviennent des arguments de test."""
    agent = ToolMakerAgent()
    code = _build_agent_code(
        "param = kwargs.get('param', 'defaut')\n"
        "results.append(f'ok {param}')\n"
    )
    assert agent._deduce_execute_args(code) == {"param": "test_value"}


def test_deduce_args_kwargs_subscript():
    """Les clés lues via kwargs['cle'] sont aussi déduites."""
    agent = ToolMakerAgent()
    code = _build_agent_code("results.append(kwargs['value'])\n")
    assert agent._deduce_execute_args(code) == {"value": "test_value"}


def test_deduce_args_dedoublonne_et_ignore_autres_gets():
    """Dédoublonnage des clés ; kwargs.get sur autre chose que kwargs ignoré."""
    agent = ToolMakerAgent()
    code = _build_agent_code(
        "a = kwargs.get('a', 1)\n"
        "b = kwargs.get('a', 2)\n"
        "c = {'x': 1}.get('x')\n"
        "results.append(f'{a}{b}{c}')\n"
    )
    assert agent._deduce_execute_args(code) == {"a": "test_value"}


def test_deduce_args_vide_sans_lecture_kwargs():
    """Sans lecture de kwargs : appel sans argument (valide pour **kwargs)."""
    agent = ToolMakerAgent()
    code = _build_agent_code("results.append('rien')\n")
    assert agent._deduce_execute_args(code) == {}


def test_deduce_args_code_invalide_vide():
    """Code syntaxiquement invalide : déduction vide, pas d'exception."""
    agent = ToolMakerAgent()
    assert agent._deduce_execute_args("def broken(\n") == {}


# ─────────────────────────────────────────────────────────────
# Contenu du test généré
# ─────────────────────────────────────────────────────────────

def test_build_tool_test_contenu():
    """Le test généré importe le module, appelle execute() avec les arguments
    déduits et vérifie que le retour n'est ni None ni une exception."""
    agent = ToolMakerAgent()
    code = _build_agent_code(
        "param = kwargs.get('param', 'defaut')\nresults.append(f'ok {param}')\n",
        tool_name="test_gen_contenu",
        class_name="TestGenContenu",
    )
    test_code = agent._build_tool_test(code, "test_gen_contenu", "TestGenContenu")

    # Le test généré est du Python valide
    import ast
    ast.parse(test_code)

    assert "from test_gen_contenu import TestGenContenu" in test_code
    assert "asyncio.run(tool.execute(param='test_value'))" in test_code
    assert "assert result is not None" in test_code
    assert "SANDBOX_OK" in test_code
    assert "SANDBOX_FAIL" in test_code
    assert "ne pas modifier manuellement" in test_code


# ─────────────────────────────────────────────────────────────
# Exécution réelle du test généré (code template maîtrisé)
# ─────────────────────────────────────────────────────────────

def _run_generated_test(tmp_path, tool_name, class_name, tool_code):
    """Écrit l'outil + son test généré sur disque et exécute le test (runpy).

    Returns:
        (stdout, exc) — exc est l'exception SystemExit si le test a échoué.
    """
    tool_dir = tmp_path / tool_name
    tool_dir.mkdir()
    (tool_dir / f"{tool_name}.py").write_text(tool_code, encoding="utf-8")

    agent = ToolMakerAgent()
    test_code = agent._build_tool_test(tool_code, tool_name, class_name)
    test_path = tool_dir / f"test_{tool_name}.py"
    test_path.write_text(test_code, encoding="utf-8")

    exc = None
    try:
        runpy.run_path(str(test_path), run_name="__main__")
    except SystemExit as e:
        exc = e
    return test_path, exc


def test_test_genere_passe_sur_outil_valide(tmp_path, capsys):
    """Outil valide : le test généré s'exécute et imprime SANDBOX_OK."""
    code = _build_agent_code(
        "param = kwargs.get('param', 'defaut')\n"
        "results.append(f'ok {param}')\n",
        tool_name="test_gen_ok",
        class_name="TestGenOk",
    )
    _test_path, exc = _run_generated_test(
        tmp_path, "test_gen_ok", "TestGenOk", code
    )
    assert exc is None
    assert "SANDBOX_OK" in capsys.readouterr().out


def test_test_genere_echoue_sur_import_casse(tmp_path, capsys):
    """Outil qui lève à l'import (syntaxe invalide) : SANDBOX_FAIL + code 1."""
    _test_path, exc = _run_generated_test(
        tmp_path, "test_gen_badimport", "TestGenBadimport", "def broken(\n"
    )
    assert exc is not None and exc.code == 1
    assert "SANDBOX_FAIL" in capsys.readouterr().out


def test_test_genere_echoue_sur_retour_none(tmp_path, capsys):
    """Outil dont execute() retourne None : le test échoue (ni None ni exception)."""
    code = _build_agent_code(
        "return None\n",  # court-circuite le return du template
        tool_name="test_gen_none",
        class_name="TestGenNone",
    )
    _test_path, exc = _run_generated_test(
        tmp_path, "test_gen_none", "TestGenNone", code
    )
    assert exc is not None and exc.code == 1
    assert "SANDBOX_FAIL" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────
# Gate d'enregistrement : un outil dont le test échoue n'est pas enregistré
# ─────────────────────────────────────────────────────────────

class _FakeRemoteRunner:
    """Faux runner GitHub Actions : capture les fichiers poussés et rend le
    verdict demandé (pas de réseau — le conftest unit bloque le réseau)."""

    def __init__(self, passed: bool, error: str | None = None):
        self.passed = passed
        self.error = error
        self.pushed_files = {}

    async def __call__(self, files, **kwargs):
        self.pushed_files = dict(files)
        if not self.passed:
            return {"passed": False, "error": self.error, "run_url": None}
        tool_rel = [k for k in files if not k.startswith("test_")][0]
        test_rel = f"test_{tool_rel}"
        return {
            "passed": True,
            "error": None,
            "run_url": "https://example.invalid/run/1",
            "tool_name": tool_rel[:-3],
            "class_name": "X",
            "code": files[tool_rel],
            "test_code": files[test_rel],
        }


def _invoke_with(tmp_path, runner, monkeypatch):
    """Invocation complète du ToolMakerAgent avec le runner injecté."""
    import agents.tool_maker_agent as tm_mod

    monkeypatch.setenv("MOTEUR_ENABLE_TOOL_MAKER", "1")
    tmp_dir = str(tmp_path / "auto")
    monkeypatch.setattr(tm_mod, "AUTO_TOOLS_DIR", tmp_dir)
    monkeypatch.setattr("core.toolmaker_sandbox.validate_candidate_remote", runner)

    agent = tm_mod.ToolMakerAgent(llm_gateway=None)
    payload = TaskPayload(
        task_objective="Générer un outil",
        metadata={
            "skill_pattern": "lire et modifier yaml",
            "tools_sequence": ["read_file", "write_file"],
            "objective": "Modifier un fichier YAML",
        },
    )
    return asyncio.run(agent.invoke(payload)), tmp_dir


def test_invoke_accepte_et_persiste_avec_test(tmp_path, monkeypatch):
    """Outil valide : test généré + exécuté (verdict runner), outil enregistré,
    et le test persisté est exactement celui exécuté."""
    runner = _FakeRemoteRunner(passed=True)
    result, tmp_dir = _invoke_with(tmp_path, runner, monkeypatch)

    assert result.status == "success"
    assert result.metadata["test_passed"] is True
    assert result.metadata["persisted"] is True
    assert os.path.basename(result.metadata["test_path"]).startswith("test_")

    # Le candidat poussé au runner contient outil + test
    assert len(runner.pushed_files) == 2
    tool_rel = [k for k in runner.pushed_files if not k.startswith("test_")][0]
    assert runner.pushed_files[f"test_{tool_rel}"] != ""

    # Le test persisté est celui exécuté par le runner
    saved_test = os.path.join(
        os.path.dirname(result.metadata["tool_path"]), f"test_{tool_rel}"
    )
    with open(saved_test, encoding="utf-8") as f:
        assert f.read() == runner.pushed_files[f"test_{tool_rel}"]

    # Outil + test + plugin.json sont bien les 3 fichiers du dossier
    files = sorted(os.listdir(os.path.dirname(result.metadata["tool_path"])))
    assert files == sorted([tool_rel, f"test_{tool_rel}", "plugin.json"])


def test_invoke_rejette_import_echoue_non_enregistre(tmp_path, monkeypatch, caplog):
    """Outil qui lève à l'import : verdict runner rouge → jamais enregistré,
    raison journalisée."""
    runner = _FakeRemoteRunner(passed=False, error="SANDBOX_FAIL: import cassé")
    result, tmp_dir = _invoke_with(tmp_path, runner, monkeypatch)

    assert result.status == "error"
    assert result.metadata["sandbox_passed"] is False
    assert result.metadata["test_passed"] is False
    assert result.metadata["persisted"] is False
    assert "import cassé" in result.error_message
    # Rien sur disque
    assert not os.path.exists(tmp_dir) or os.listdir(tmp_dir) == []
    # Verdict journalisé (accepté / rejeté + raison)
    assert "rejeté" in caplog.text
    assert "import cassé" in caplog.text


def test_invoke_rejette_appel_echoue_non_enregistre(tmp_path, monkeypatch, caplog):
    """Outil dont l'appel échoue (retour None) : verdict runner rouge → jamais
    enregistré, raison journalisée."""
    runner = _FakeRemoteRunner(passed=False, error="SANDBOX_FAIL: execute() a retourné None")
    result, tmp_dir = _invoke_with(tmp_path, runner, monkeypatch)

    assert result.status == "error"
    assert result.metadata["persisted"] is False
    assert "retourné None" in result.error_message
    assert not os.path.exists(tmp_dir) or os.listdir(tmp_dir) == []
    assert "rejeté" in caplog.text
