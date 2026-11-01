"""
tests/unit/test_agents.py — non-régression sur api/routes/agents.py.

Couvre l'endpoint `run_code_task` (/api/code) : la lecture des fichiers de
contexte doit être déléguée à un thread pool via `asyncio.to_thread` et non
faite de manière synchrone (`open().read()`) dans la coroutine asyncio, ce qui
gèlerait la boucle d'événements.

Le constat audité : `run_code_task` est défini en `async def` mais ouvrait et
lisait de manière synchrone jusqu'à 5 fichiers de contexte via `open(path).read()`.
Cette opération d'E/S bloquante sur le disque doit être déléguée à un thread pool
via `asyncio.to_thread`.
"""
import asyncio
import inspect
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import agents as route_agents


def _ecrire_fichier_tmp(contenu: str) -> str:
    """Crée un fichier temporaire et retourne son chemin absolu."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(contenu)
    return path


# ── TEST 1 : run_code_task est bien une coroutine asyncio ────────────────────

def test_run_code_task_est_une_coroutine():
    """`run_code_task` doit être `async def` (coroutine), pas une fonction
    synchrone — c'est un endpoint FastAPI asynchrone."""
    assert inspect.iscoroutinefunction(route_agents.run_code_task), (
        "run_code_task doit être une coroutine (async def)"
    )


# ── TEST 2 : la lecture des fichiers passe par asyncio.to_thread ─────────────

@pytest.mark.asyncio
async def test_run_code_task_delegue_la_lecture_a_asyncio_to_thread():
    """La lecture des fichiers de contexte doit être déléguée à un thread pool
    via `asyncio.to_thread`, et non faite de manière synchrone dans la coroutine.

    Échouait sur le code d'avant : `open(path).read()` était appelé directement
    dans la boucle de la coroutine, bloquant la boucle d'événements.
    """
    chemin1 = _ecrire_fichier_tmp("contenu du fichier 1")
    chemin2 = _ecrire_fichier_tmp("contenu du fichier 2")

    try:
        # On double `asyncio.to_thread` pour vérifier qu'il est bien appelé
        # pour chaque fichier de contexte, et on capture les arguments.
        appels = []

        async def _faux_to_thread(fn, *args, **kwargs):
            appels.append((fn, args, kwargs))
            return fn(*args, **kwargs)

        corps = route_agents.CodeRequestBody(
            prompt="refactore ce code",
            context_files=[chemin1, chemin2],
        )

        # On double `run_coding_task` pour éviter tout appel réseau/LLM.
        with patch.object(route_agents, "asyncio") as faux_asyncio, \
             patch("services.coding_front.run_coding_task",
                   new=AsyncMock(return_value={"status": "success"})) as faux_run:
            faux_asyncio.to_thread = _faux_to_thread

            result = await route_agents.run_code_task(corps)

        # `asyncio.to_thread` a été appelé pour chaque fichier de contexte.
        assert len(appels) == 2, f"attendu 2 appels à to_thread, obtenu {len(appels)}"
        for i, (fn, args, _kwargs) in enumerate(appels):
            assert fn is route_agents._read_context_file, (
                f"appel {i} : to_thread doit recevoir _read_context_file"
            )
            assert args[0] == corps.context_files[i], (
                f"appel {i} : mauvais chemin passé à to_thread"
            )

        # Le contenu lu est bien transmis à run_coding_task.
        _, kwargs = faux_run.call_args
        assert kwargs["file_contents"] == {
            chemin1: "contenu du fichier 1",
            chemin2: "contenu du fichier 2",
        }, kwargs["file_contents"]
        assert kwargs["force_tier"] is None
    finally:
        os.unlink(chemin1)
        os.unlink(chemin2)


# ── TEST 3 : un fichier inexistant est ignoré sans faire échouer la route ────

@pytest.mark.asyncio
async def test_run_code_task_ignore_un_fichier_inexistant():
    """Un chemin de fichier inexistant (OSError) doit être ignoré avec un
    warning, sans faire échouer l'endpoint."""
    chemin_valide = _ecrire_fichier_tmp("contenu valide")
    chemin_inexistant = os.path.join(tempfile.gettempdir(), "fichier_qui_n_existe_pas_xyz.txt")

    try:
        corps = route_agents.CodeRequestBody(
            prompt="refactore ce code",
            context_files=[chemin_inexistant, chemin_valide],
        )

        with patch("services.coding_front.run_coding_task",
                   new=AsyncMock(return_value={"status": "success"})) as faux_run:
            result = await route_agents.run_code_task(corps)

        # Le fichier valide est lu, l'inexistant est ignoré.
        _, kwargs = faux_run.call_args
        assert kwargs["file_contents"] == {chemin_valide: "contenu valide"}, (
            kwargs["file_contents"]
        )
        assert result["status"] == "success"
    finally:
        os.unlink(chemin_valide)


# ── TEST 4 : _read_context_file tronque à 40 000 caractères ─────────────────

def test_read_context_file_tronque_a_40000_caracteres():
    """`_read_context_file` lit le fichier et le tronque à 40 000 caractères
    (limite de contexte du coding front)."""
    contenu_long = "a" * 50_000
    chemin = _ecrire_fichier_tmp(contenu_long)

    try:
        lu = route_agents._read_context_file(chemin)
        assert len(lu) == 40_000, f"attendu 40 000, obtenu {len(lu)}"
        assert lu == "a" * 40_000
    finally:
        os.unlink(chemin)


# ── TEST 5 : au plus 5 fichiers de contexte sont lus ─────────────────────────

@pytest.mark.asyncio
async def test_run_code_task_limite_a_5_fichiers_de_contexte():
    """`run_code_task` ne lit jamais plus de 5 fichiers de contexte
    (`body.context_files[:5]`)."""
    chemins = [_ecrire_fichier_tmp(f"contenu {i}") for i in range(8)]

    try:
        corps = route_agents.CodeRequestBody(
            prompt="refactore ce code",
            context_files=chemins,
        )

        appels = []

        async def _faux_to_thread(fn, *args, **kwargs):
            appels.append(args[0])
            return fn(*args, **kwargs)

        with patch.object(route_agents, "asyncio") as faux_asyncio, \
             patch("services.coding_front.run_coding_task",
                   new=AsyncMock(return_value={"status": "success"})):
            faux_asyncio.to_thread = _faux_to_thread

            await route_agents.run_code_task(corps)

        # Seuls les 5 premiers fichiers sont lus.
        assert len(appels) == 5, f"attendu 5 lectures, obtenu {len(appels)}"
        assert appels == chemins[:5], appels
    finally:
        for chemin in chemins:
            os.unlink(chemin)
