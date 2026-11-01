"""
tests/unit/test_gemini_cli_ipc.py — Gemini via `agy` (abo Google), pas l'IDE.

L'ancien IPC (fichier d'échange + `antigravity-ide.cmd chat -m chat`) n'a
jamais produit de stdout : 88 échecs, slug éteint. Le provider parle
maintenant à `agy -p` (CLI Antigravity headless, session Google).
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.llm.providers import gemini as gemini_module
from core.llm.providers.gemini import GeminiCLIProvider


class _ResultatCLI:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _agy_present(tmp_path, monkeypatch):
    binaire = tmp_path / "agy.exe"
    binaire.write_text("", encoding="utf-8")
    monkeypatch.setattr(gemini_module, "chemin_agy", lambda: str(binaire))
    monkeypatch.setattr(
        "core.token_tracker.record_usage",
        lambda *a, **kw: None,
    )
    return str(binaire)


@pytest.fixture
def provider():
    return GeminiCLIProvider()


def test_agy_en_print_sandbox_sans_cle_api(provider, monkeypatch, _agy_present):
    vu = {}

    def _faux_cli(cmd, **kwargs):
        vu["cmd"] = cmd
        vu["env"] = kwargs.get("env") or {}
        return _ResultatCLI(stdout="ok abo")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    monkeypatch.setenv("GEMINI_API_KEY", "ne-pas-utiliser")
    assert provider.generate("sys", "user") == "ok abo"
    assert vu["cmd"][0] == _agy_present
    assert vu["cmd"][1] == "--sandbox"
    assert "--model" in vu["cmd"]
    assert vu["cmd"][vu["cmd"].index("--model") + 1] == "gemini-3.7-flash-medium"
    assert "-p" in vu["cmd"]
    assert "chat" not in vu["cmd"]
    assert vu["env"].get("GEMINI_API_KEY") is None


def test_appels_concurrents_lisent_leur_stdout(provider, monkeypatch):
    def _faux_cli(cmd, **kwargs):
        prompt = cmd[-1]
        if "alpha" in prompt:
            return _ResultatCLI(stdout="réponse pour alpha")
        return _ResultatCLI(stdout="réponse pour beta")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futurs = [
            executor.submit(provider.generate, "sys", f"MARQUEUR-{m}")
            for m in ("alpha", "beta")
        ]
        resultats = [f.result(timeout=15) for f in futurs]
    assert sorted(resultats) == ["réponse pour alpha", "réponse pour beta"]


def test_prompt_long_passe_par_un_fichier(provider, monkeypatch):
    vu = {}

    def _faux_cli(cmd, **kwargs):
        vu["cmd"] = cmd
        return _ResultatCLI(stdout="ok")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    assert provider.generate("sys", "x" * 21000) == "ok"
    assert "--add-dir" in vu["cmd"]


def test_erreur_si_stdout_vide(provider, monkeypatch):
    def _faux_cli_muet(cmd, **kwargs):
        return _ResultatCLI(stdout="", stderr="boom", returncode=1)

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli_muet)
    with pytest.raises(RuntimeError, match="agy"):
        provider.generate("sys", "user")


def test_agy_high_passe_le_modele_high(monkeypatch, _agy_present):
    vu = {}

    def _faux_cli(cmd, **kwargs):
        vu["cmd"] = cmd
        return _ResultatCLI(stdout="ok")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    GeminiCLIProvider(model_name="gemini-3.5-flash-high-cli").generate("s", "u")
    assert vu["cmd"][vu["cmd"].index("--model") + 1] == "gemini-3.7-flash-high"


def test_modele_agy_mappe_le_slug_medium():
    from core.llm.providers.gemini import modele_agy
    assert modele_agy("gemini-3.5-flash-medium-cli") == "gemini-3.7-flash-medium"
    assert modele_agy("gemini-3.7-flash-medium-cli") == "gemini-3.7-flash-medium"
    assert modele_agy("inconnu") == "gemini-3.7-flash-medium"


def test_chemin_agy_absente_leve(monkeypatch):
    monkeypatch.setattr(gemini_module, "chemin_agy", lambda: None)
    with pytest.raises(RuntimeError, match="introuvable"):
        GeminiCLIProvider().generate("sys", "user")
