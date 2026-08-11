"""
tests/unit/test_run_cli_command_windows.py — Quoting Windows de run_cli_command (#T292).

`run_cli_command()` enveloppe les `.cmd`/`.bat` Windows dans `cmd /c` (CreateProcess
ne sait pas lancer un `.cmd` directement). Mais `subprocess.list2cmdline` met des
guillemets autour de CHAQUE argument contenant un espace, et `cmd.exe` applique sa
règle documentée : quand la ligne qui suit `/c` commence ET finit par un guillemet,
il retire cette paire extérieure. Le chemin de l'exécutable perd donc ses guillemets
et se coupe à son premier espace.

Ces tests verrouillent la forme correcte : une paire de guillemets SUPPLÉMENTAIRE
autour de la ligne entière (`cmd /c ""C:\\chemin avec espace\\x.cmd" arg1 "arg 2""`),
sans jamais réintroduire `shell=True` (garde-fou P0-1.3).
"""

import subprocess
import sys

import pytest

from core.llm.providers import base as base_module
from core.llm.providers.base import run_cli_command


@pytest.mark.skipif(sys.platform != "win32", reason="spécifique à cmd.exe (Windows)")
def test_cmd_chemin_et_dernier_argument_avec_espaces(tmp_path):
    """Un .cmd dans un dossier à espace + dernier argument à espaces -> code 0.

    Reproduit le symptôme de campagne : le prompt passé à la CLI contient
    toujours des espaces, ce qui faisait disjoncter les circuit breakers.
    """
    bin_dir = tmp_path / "Dossier Avec Espace" / "bin"
    bin_dir.mkdir(parents=True)
    faux_cmd = bin_dir / "faux.cmd"
    faux_cmd.write_text(
        "@echo off\r\necho REPRO_OK: %*\r\nexit /b 0\r\n",
        encoding="utf-8",
    )

    resultat = run_cli_command(
        [str(faux_cmd), "un argument avec espaces"],
        capture_output=True,
        text=True,
    )
    assert resultat.returncode == 0, f"stderr: {resultat.stderr}"
    assert "REPRO_OK" in resultat.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="spécifique à cmd.exe (Windows)")
def test_cmd_chemin_avec_espaces_argument_sans_espace(tmp_path):
    """Sans espace dans le dernier argument : comportement historique conservé."""
    bin_dir = tmp_path / "Dossier Avec Espace" / "bin"
    bin_dir.mkdir(parents=True)
    faux_cmd = bin_dir / "faux.cmd"
    faux_cmd.write_text(
        "@echo off\r\necho REPRO_OK\r\nexit /b 0\r\n",
        encoding="utf-8",
    )

    resultat = run_cli_command(
        [str(faux_cmd), "argument"],
        capture_output=True,
        text=True,
    )
    assert resultat.returncode == 0, f"stderr: {resultat.stderr}"
    assert "REPRO_OK" in resultat.stdout


def test_forme_cmd_c_avec_paire_de_guillemets_externe(monkeypatch):
    """La ligne après /c = list2cmdline encadrée d'une paire de guillemets.

    Portable (CI Linux comprise) : subprocess.run est simulé, sys.platform forcé.
    `shutil.which` est neutralisé : sur POSIX il plante sur un chemin
    Windows-like (shutil._win_path_needs_curdir appelle _winapi, absent) — et
    ce test ne vérifie que la CONSTRUCTION de la ligne, pas la résolution.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(base_module.shutil, "which", lambda *a, **k: None)
    appels = []

    def faux_run(argv, **kwargs):
        appels.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(base_module.subprocess, "run", faux_run)

    chemin = r"C:\Program Files\Dossier Avec Espace\bin\faux.cmd"
    run_cli_command([chemin, "arg1", "un argument avec espaces"])

    assert appels, "subprocess.run n'a pas été appelé"
    argv, kwargs = appels[0]
    attendu = subprocess.list2cmdline([chemin, "arg1", "un argument avec espaces"])
    assert argv == f'cmd /c "{attendu}"', f"ligne mal encadrée : {argv!r}"
    assert kwargs.get("executable"), "cmd.exe doit être passé via executable (pas de shell)"
    assert kwargs.get("shell") is False, "shell=False (P0-1.3) doit être conservé"


def test_posix_ne_construit_pas_cmd_c(monkeypatch):
    """Sous POSIX, aucun cmd /c : argv transmis tel quel, shell=False."""
    monkeypatch.setattr(sys, "platform", "linux")
    appels = []

    def faux_run(argv, **kwargs):
        appels.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(base_module.subprocess, "run", faux_run)

    chemin = "/opt/dossier avec espace/bin/faux"
    run_cli_command([chemin, "un argument avec espaces"])

    assert appels, "subprocess.run n'a pas été appelé"
    argv, kwargs = appels[0]
    assert "cmd" not in argv and "/c" not in argv, f"cmd /c ne doit pas apparaître : {argv}"
    assert argv[0] == chemin
    assert argv[1] == "un argument avec espaces"
    assert kwargs.get("shell") is False, "shell=False (P0-1.3) doit être conservé"
