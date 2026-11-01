"""
tests/unit/test_task_discovery_scheduler.py — Test unitaire pour TaskDiscoveryScheduler et routes API
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.task_discovery import router as task_discovery_router
from core.task_discovery_scheduler import TaskDiscoveryScheduler


@pytest.fixture(autouse=True)
def _clefs_de_decouverte_en_base(monkeypatch):
    """
    [#T397] `sync_discovered_tasks` lit désormais les clés de découverte
    directement en base, et plus seulement par expression régulière sur les
    descriptions. Ces tests simulent le backlog (`get_all_tasks`, `add_task`) :
    sans ce garde, ils tomberaient sur une vraie base sans table. Base vide par
    défaut ; un test qui veut une clé déjà connue surcharge ce patch.
    """
    monkeypatch.setattr(
        "core.task_discovery_scheduler.get_discovery_keys",
        AsyncMock(return_value=set()),
    )


@pytest.mark.asyncio
async def test_scheduler_sync_deduplication():
    scheduler = TaskDiscoveryScheduler()

    mock_discovered = [
        {
            "task_id": "T101",
            "title": "Tache Découverte 1",
            "prompt": "Description prompt 1",
            "prio": "P1",
            "complexity": "high"
        },
        {
            "task_id": "T102",
            "title": "Tache Déjà Présente",
            "prompt": "Description existante",
            "prio": "P2",
            "complexity": "low"
        }
    ]

    mock_existing = [
        {"id": 1, "title": "Tache Déjà Présente", "description": "Description existante", "priority": 2}
    ]

    with patch.object(scheduler.agent, "run_discovery", return_value=mock_discovered):
        with patch("core.task_discovery_scheduler.get_all_tasks", new=AsyncMock(return_value=mock_existing)):
            with patch("core.task_discovery_scheduler.add_task", new=AsyncMock(return_value=42)) as mock_add:
                result = await scheduler.sync_discovered_tasks()

                assert result["discovered_count"] == 2
                assert result["enqueued_count"] == 1
                assert len(result["enqueued_tasks"]) == 1
                assert result["enqueued_tasks"][0]["task_id"] == "T101"
                # [#T397] La complexité était calculée à la découverte puis jetée :
                # elle descend maintenant en base avec la clé de découverte.
                mock_add.assert_awaited_once_with(
                    title="Tache Découverte 1",
                    description="Description prompt 1",
                    priority=1,
                    discovery_key=None,
                    complexity="high",
                    project_id=None,
                )


@pytest.mark.asyncio
async def test_la_cle_de_decouverte_descend_en_base(monkeypatch):
    """
    [#T397] Une tâche porteuse d'une clé stable doit l'écrire dans la colonne
    `discovery_key`, pas seulement dans le marqueur `[dedup:…]` de son prompt.

    C'est ce qui permet à l'index unique de refuser un doublon même quand le
    filtrage applicatif se trompe — le rejeu mesuré le 18/08 (mêmes tâches
    rejouées sur sept cycles) passait par des descriptions reformulées.
    """
    scheduler = TaskDiscoveryScheduler()
    monkeypatch.setattr(scheduler, "load_state", lambda: {})
    monkeypatch.setattr(scheduler, "save_state", lambda state: None)

    decouvertes = [{
        "task_id": "T404",
        "title": "Tache avec cle stable",
        "prompt": "Corriger le quota [dedup:board:T404]",
        "prio": "P2",
        "complexity": "medium",
        "dedup_key": "board:T404",
        "project_id": "tab5",
    }]

    with patch.object(scheduler.agent, "run_discovery", return_value=decouvertes):
        with patch("core.task_discovery_scheduler.get_all_tasks", new=AsyncMock(return_value=[])):
            with patch("core.task_discovery_scheduler.add_task",
                       new=AsyncMock(return_value=7)) as mock_add:
                await scheduler.sync_discovered_tasks()

    assert mock_add.await_args.kwargs["discovery_key"] == "board:t404"
    assert mock_add.await_args.kwargs["complexity"] == "medium"
    assert mock_add.await_args.kwargs["project_id"] == "tab5"


@pytest.mark.asyncio
async def test_une_cle_deja_en_base_n_est_pas_reinjectee(monkeypatch):
    """
    [#T397] La mémoire des clés vient de la BASE, pas des descriptions.

    Le backlog rendu est vide (aucun texte à regexer) : seule la colonne
    `discovery_key` sait que cette source a déjà été injectée un jour. Sans
    cette source, la tâche repartirait — c'est exactement le rejeu qui a coûté
    ~15 M de tokens le 18/08 pour une tâche déjà close.
    """
    scheduler = TaskDiscoveryScheduler()
    monkeypatch.setattr(scheduler, "load_state", lambda: {})
    monkeypatch.setattr(scheduler, "save_state", lambda state: None)
    monkeypatch.setattr(
        "core.task_discovery_scheduler.get_discovery_keys",
        AsyncMock(return_value={"board:t404"}),
    )

    decouvertes = [{
        "task_id": "T404",
        "title": "Tache avec cle stable",
        "prompt": "Corriger le quota [dedup:board:T404]",
        "prio": "P2",
        "complexity": "medium",
        "dedup_key": "board:T404",
    }]

    with patch.object(scheduler.agent, "run_discovery", return_value=decouvertes):
        with patch("core.task_discovery_scheduler.get_all_tasks", new=AsyncMock(return_value=[])):
            with patch("core.task_discovery_scheduler.add_task",
                       new=AsyncMock(return_value=7)) as mock_add:
                resultat = await scheduler.sync_discovered_tasks()

    mock_add.assert_not_awaited()
    assert resultat["enqueued_count"] == 0


def test_api_task_discovery_routes():
    app = FastAPI()
    app.include_router(task_discovery_router)
    client = TestClient(app)

    response = client.get("/api/tasks/discovery/status")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.asyncio
async def test_scheduler_dedup_par_marqueur_stable(monkeypatch):
    """Une tâche déjà injectée est reconnue via son marqueur [dedup:…], même
    si son titre/description a bougé (ex. numéro de ligne changé)."""
    scheduler = TaskDiscoveryScheduler()
    monkeypatch.setattr(scheduler, "load_state", lambda: {})
    monkeypatch.setattr(scheduler, "save_state", lambda state: None)

    decouvertes = [{
        "task_id": "TODO_budget_guard_293",
        "title": "TODO dans core/budget_guard.py — Brancher ProviderScorer",
        "prompt": "Contrat… [dedup:todo:core/budget_guard.py:brancher providerscorer]",
        "prio": "P3",
    }]
    # Le backlog porte le MÊME travail, mais l'ancienne ligne a bougé : la
    # description ne correspond plus texte pour texte — seule la clé compte.
    existantes = [{
        "id": 1,
        "title": "TODO dans core/budget_guard.py:200",
        "description": "Fichier core/budget_guard.py ligne 200: brancher ProviderScorer\n"
                       "[dedup:todo:core/budget_guard.py:brancher providerscorer]",
    }]

    with patch.object(scheduler.agent, "run_discovery", return_value=decouvertes):
        with patch("core.task_discovery_scheduler.get_all_tasks",
                   new=AsyncMock(return_value=existantes)):
            with patch("core.task_discovery_scheduler.add_task",
                       new=AsyncMock(return_value=42)) as mock_add:
                result = await scheduler.sync_discovered_tasks()

                assert result["discovered_count"] == 1
                assert result["enqueued_count"] == 0
                mock_add.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_plafond_dur_dinjection(monkeypatch):
    """Au-delà du plafond, les tâches restantes attendent le prochain cycle ;
    les plus prioritaires passent en premier."""
    import core.task_discovery_scheduler as module_scheduler

    scheduler = TaskDiscoveryScheduler()
    monkeypatch.setattr(scheduler, "load_state", lambda: {})
    monkeypatch.setattr(scheduler, "save_state", lambda state: None)
    monkeypatch.setattr(module_scheduler, "MAX_INJECTIONS_PAR_RUN", 1)

    decouvertes = [
        {"task_id": "T1", "title": "Tâche P3",
         "prompt": "Contrat P3 [dedup:todo:a.py:troisieme]", "prio": "P3"},
        {"task_id": "T2", "title": "Tâche P2",
         "prompt": "Contrat P2 [dedup:todo:b.py:deuxieme]", "prio": "P2"},
    ]

    with patch.object(scheduler.agent, "run_discovery", return_value=decouvertes):
        with patch("core.task_discovery_scheduler.get_all_tasks",
                   new=AsyncMock(return_value=[])):
            with patch("core.task_discovery_scheduler.add_task",
                       new=AsyncMock(return_value=42)) as mock_add:
                result = await scheduler.sync_discovered_tasks()

                assert result["candidates_count"] == 2
                assert result["enqueued_count"] == 1
                # La P2 est passée avant la P3.
                mock_add.assert_awaited_once()
                assert mock_add.await_args.kwargs["title"] == "Tâche P2"
                assert result["cap"] == 1


@pytest.mark.asyncio
async def test_scheduler_start_stop_propre():
    """Le câblage lifespan s'appuie sur start()/stop() : ils doivent être
    réversibles sans laisser de tâche asyncio pendante."""
    scheduler = TaskDiscoveryScheduler(interval_s=3600)

    with patch.object(scheduler, "sync_discovered_tasks",
                      new=AsyncMock(return_value={})):
        scheduler.start()
        assert scheduler._running is True
        assert scheduler._task is not None
        await asyncio.sleep(0.05)
        scheduler.stop()
        assert scheduler._running is False
        # Un second stop ne doit pas exploser (arrêt déjà fait).
        scheduler.stop()
