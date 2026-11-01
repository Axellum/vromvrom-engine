"""
test_async_db_serializer.py — le worker SQLite ne doit pas figer la boucle.

Le worker est une `asyncio.Task` : y appeler `func()` directement bloque la
boucle d'événements pendant toute la durée de l'opération SQLite. Sur la prod,
FastAPI sert le vocal et le SSE depuis cette même boucle — une écriture lente
gelait donc tout le serveur. Ces tests figent les deux propriétés qui doivent
tenir ensemble : la boucle reste vivante, ET l'ordre FIFO est conservé (c'est
la raison d'être du sérialiseur, on ne l'échange pas contre du parallélisme).
"""

import asyncio
import time

import pytest

from core.async_db_serializer import AsyncDBSerializer


@pytest.fixture
async def serialiseur():
    s = AsyncDBSerializer()
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_une_operation_bloquante_ne_gele_pas_la_boucle(serialiseur):
    """Pendant une opération synchrone de 250 ms, la boucle doit continuer à tourner."""
    battements = 0

    async def horloge():
        nonlocal battements
        while True:
            await asyncio.sleep(0.02)
            battements += 1

    tic = asyncio.create_task(horloge())
    try:
        resultat = await serialiseur.execute(lambda: (time.sleep(0.25), "ok")[1])
    finally:
        tic.cancel()

    assert resultat == "ok"
    # Sans `to_thread`, la boucle est figée : 0 battement. Avec, on en attend
    # une dizaine ; on exige 3 pour rester robuste sur une machine chargée.
    assert battements >= 3, f"boucle gelée pendant l'opération SQLite ({battements} battement(s))"


@pytest.mark.asyncio
async def test_ordre_fifo_conserve(serialiseur):
    """Le passage en thread ne doit pas rendre les opérations concurrentes."""
    ordre: list[int] = []

    def travail(n: int):
        time.sleep(0.02)
        ordre.append(n)
        return n

    resultats = await asyncio.gather(*(serialiseur.execute(lambda n=i: travail(n)) for i in range(5)))

    assert resultats == [0, 1, 2, 3, 4]
    assert ordre == [0, 1, 2, 3, 4], "les opérations SQLite se sont chevauchées"


@pytest.mark.asyncio
async def test_exception_remontee_a_l_appelant(serialiseur):
    """Une exception dans la fonction soumise revient à l'appelant, pas au worker."""
    def casse():
        raise ValueError("échec volontaire")

    with pytest.raises(ValueError, match="échec volontaire"):
        await serialiseur.execute(casse)

    # Le worker survit et sert l'opération suivante.
    assert await serialiseur.execute(lambda: 42) == 42
