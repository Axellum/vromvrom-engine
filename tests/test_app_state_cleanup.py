"""
tests/test_app_state_cleanup.py — Tests du mécanisme de nettoyage des entrées
obsolètes dans execution_registry de core.app_state.

Contexte : une exécution crashée sans passage par end_execution() laissait une
entrée orpheline dans le registre, bloquant la session en doublon 409 pour
toujours. La correction ajoute :
  1. un timestamp `started_at` à chaque entrée créée par begin_execution() ;
  2. une méthode _cleanup_stale_entries_locked() qui purge les entrées dont le
     timestamp dépasse le seuil (stale_ttl_seconds) ;
  3. l'appel de ce nettoyage au début de begin_execution().

Ces tests échouent AVANT la correction (pas de started_at, pas de cleanup) et
passent APRÈS.
"""

import asyncio
import time

import pytest

from core.app_state import AppState


@pytest.fixture
def app_state():
    """Instance AppState fraîche (singleton réinitialisé pour le test)."""
    # Réinitialise le singleton pour isoler chaque test.
    AppState._instance = None
    state = AppState()
    state.initialize()
    return state


@pytest.mark.asyncio
async def test_begin_execution_ajoute_timestamp(app_state):
    """Chaque entrée du registre doit porter un timestamp `started_at`."""
    resultat = await app_state.begin_execution("session-test-1", "Objectif test")
    assert resultat is None, f"begin_execution a refusé : {resultat}"

    entree = app_state.execution_registry["session-test-1"]
    assert "started_at" in entree, (
        "L'entrée du registre doit contenir un timestamp `started_at` "
        "(absent avant la correction)."
    )
    assert isinstance(entree["started_at"], float), (
        "`started_at` doit être un float (epoch secondes)."
    )
    # Le timestamp doit être proche de l'heure actuelle.
    assert abs(entree["started_at"] - time.time()) < 5, (
        "`started_at` doit être proche de l'heure actuelle."
    )


@pytest.mark.asyncio
async def test_cleanup_purge_entrees_orphelines(app_state):
    """Les entrées obsolètes (dépassant le TTL) doivent être purgées."""
    # Crée une entrée normale.
    await app_state.begin_execution("session-active", "Objectif actif")
    assert "session-active" in app_state.execution_registry

    # Injecte une entrée orpheline avec un timestamp très ancien.
    app_state.execution_registry["session-orpheline"] = {
        "status": "running",
        "objective": "Orpheline",
        "engine_state": None,
        "error_message": None,
        "started_at": time.time() - app_state.stale_ttl_seconds - 60,  # dépassée
    }

    # Appelle begin_execution sur une NOUVELLE session : le cleanup doit
    # purger l'entrée orpheline avant de vérifier le doublon.
    resultat = await app_state.begin_execution("session-nouvelle", "Objectif nouveau")
    assert resultat is None, f"begin_execution a refusé : {resultat}"

    # L'entrée orpheline doit avoir été purgée.
    assert "session-orpheline" not in app_state.execution_registry, (
        "L'entrée orpheline doit être purgée par le nettoyage."
    )
    # Les entrées actives et nouvelles doivent rester.
    assert "session-active" in app_state.execution_registry
    assert "session-nouvelle" in app_state.execution_registry


@pytest.mark.asyncio
async def test_cleanup_ne_purge_pas_entrees_recentes(app_state):
    """Les entrées récentes (dans le TTL) ne doivent PAS être purgées."""
    await app_state.begin_execution("session-recente", "Objectif récent")
    assert "session-recente" in app_state.execution_registry

    # Appelle begin_execution sur une autre session : le cleanup ne doit pas
    # toucher l'entrée récente.
    await app_state.begin_execution("session-2", "Objectif 2")
    assert "session-recente" in app_state.execution_registry
    assert "session-2" in app_state.execution_registry


@pytest.mark.asyncio
async def test_cleanup_liberes_session_bloquee(app_state):
    """Une session bloquée par une entrée orpheline doit être débloquée."""
    # Simule une entrée orpheline pour la session "session-bloquee".
    app_state.execution_registry["session-bloquee"] = {
        "status": "running",
        "objective": "Orpheline",
        "engine_state": None,
        "error_message": None,
        "started_at": time.time() - app_state.stale_ttl_seconds - 120,  # très dépassée
    }

    # AVANT la correction : begin_execution renverrait 409 (doublon).
    # APRÈS la correction : le cleanup purge l'orpheline et accepte.
    resultat = await app_state.begin_execution("session-bloquee", "Nouvel objectif")
    assert resultat is None, (
        "La session bloquée par une entrée orpheline doit être débloquée "
        "par le nettoyage (résultat attendu : None, pas un 409)."
    )
    # L'entrée doit être réenregistrée avec un nouveau timestamp.
    entree = app_state.execution_registry["session-bloquee"]
    assert entree["objective"] == "Nouvel objectif"
    assert abs(entree["started_at"] - time.time()) < 5


@pytest.mark.asyncio
async def test_une_entree_sans_horodatage_n_est_jamais_purgee(app_state):
    """
    Sans `started_at` exploitable, on ne peut pas prouver que l'entrée est morte.

    Le repli d'origine (`get("started_at", 0)`) rendait `0 < seuil` toujours
    vrai : **toute** entrée sans horodatage était purgée au premier passage,
    y compris une exécution bien vivante posée par un appelant qui ne
    renseigne pas le champ. C'est ce qui a cassé le plafond de concurrence
    de #T324 — les entrées lourdes disparaissaient et le refus n'arrivait plus.

    Même règle que `_branche_distante_existe` : quand on ne peut pas prouver
    que c'est mort, on ne détruit pas.
    """
    for cle, debut in (
        ("sans-champ", None),
        ("champ-vide", ""),
        ("champ-non-numerique", "hier"),
    ):
        entree = {"status": "running", "objective": "Vivante"}
        if debut is not None:
            entree["started_at"] = debut
        app_state.execution_registry[cle] = entree

    async with app_state.execution_lock:
        app_state._cleanup_stale_entries_locked()

    assert set(app_state.execution_registry) == {
        "sans-champ", "champ-vide", "champ-non-numerique"
    }, "aucune entrée sans horodatage exploitable ne doit être purgée"
