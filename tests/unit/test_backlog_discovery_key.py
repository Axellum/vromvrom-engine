"""
tests/unit/test_backlog_discovery_key.py — #T397 : la base refuse elle-même le doublon.

Le planificateur de découverte déduplique déjà en amont, mais une logique
applicative peut se tromper (elle s'est trompée : `get_all_tasks(limit=50)`
oubliait tout au-delà de cinquante lignes). L'index unique partiel sur
`discovery_key` est le garde-fou qui ne dépend d'aucun appelant : deux passages
concurrents du scan, ou deux processus, ne peuvent pas produire deux tâches pour
la même source.

Les tâches créées à la main (clé nulle) restent libres de porter le même titre.
"""

import pytest

from core.backlog_db import (
    add_task,
    get_discovery_keys,
    get_task_stats,
    init_backlog_db,
    update_task_status,
)


@pytest.fixture
async def backlog_initialise():
    """La base runtime est déjà isolée par test (conftest `_isoler_base_runtime`)."""
    await init_backlog_db()


@pytest.mark.asyncio
async def test_meme_cle_deux_fois_une_seule_tache(backlog_initialise):
    premier = await add_task(
        title="TODO dans core/x.py — purger le cache",
        description="prompt",
        discovery_key="todo:core/x.py#abc1234567",
        complexity="low",
    )
    second = await add_task(
        title="Titre reformulé entre-temps",
        description="autre prompt",
        discovery_key="todo:core/x.py#abc1234567",
        complexity="low",
    )

    assert second == premier, "la seconde insertion doit rendre la tâche existante"
    stats = await get_task_stats()
    assert stats["pending"] == 1


@pytest.mark.asyncio
async def test_une_tache_close_bloque_toujours_sa_cle(backlog_initialise):
    """
    Le cœur du rejeu du 18/08 : une tâche terminée doit rester connue. Si sa clé
    se libérait à la clôture, le scan suivant la redécouvrirait et la relancerait.
    """
    task_id = await add_task(
        title="Tâche board", description="prompt", discovery_key="board:T900"
    )
    await update_task_status(task_id, "completed")

    rejeu = await add_task(
        title="Tâche board", description="prompt", discovery_key="board:T900"
    )

    assert rejeu == task_id
    stats = await get_task_stats()
    assert stats["completed"] == 1
    assert stats["pending"] == 0


@pytest.mark.asyncio
async def test_une_tache_failed_ne_duplique_pas_sa_cle(backlog_initialise):
    """
    [#T432] Le rejeu d'un échec passe par le drainage de la MÊME ligne,
    pas par une seconde insertion qui casserait l'index unique.
    """
    task_id = await add_task(
        title="Tâche board", description="prompt", discovery_key="board:T901"
    )
    await update_task_status(task_id, "failed")

    rejeu = await add_task(
        title="Tâche board", description="prompt", discovery_key="board:T901"
    )

    assert rejeu == task_id
    stats = await get_task_stats()
    assert stats["failed"] == 1
    assert stats["pending"] == 0


@pytest.mark.asyncio
async def test_les_taches_manuelles_ne_sont_pas_contraintes(backlog_initialise):
    """L'index est partiel : deux tâches sans clé coexistent, même titre compris."""
    a = await add_task(title="Vérifier la prod", description="d1")
    b = await add_task(title="Vérifier la prod", description="d2")

    assert a != b
    stats = await get_task_stats()
    assert stats["pending"] == 2


@pytest.mark.asyncio
async def test_get_discovery_keys_ignore_le_statut_et_la_limite(backlog_initialise):
    """
    Régression directe de `get_all_tasks(limit=50)` : au-delà de cinquante
    tâches, la mémoire s'effaçait et le scan ré-injectait les plus anciennes.
    """
    for i in range(60):
        task_id = await add_task(
            title=f"Tâche {i}", description="d", discovery_key=f"board:T{i}"
        )
        if i % 2 == 0:
            await update_task_status(task_id, "completed")

    cles = await get_discovery_keys()

    assert len(cles) == 60
    assert "board:T0" in cles      # la plus ancienne, et close
    assert "board:T59" in cles
