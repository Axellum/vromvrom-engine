"""
tests/unit/test_execute_chemins_legers_hors_creneau_t344.py — les chemins
légers d'`/api/execute` sortent du créneau d'exécution (#T344).

#T324 a remplacé le verrou global par un registre PAR SESSION avec plafond de
concurrence (`MOTEUR_EXECUTION_MAX_CONCURRENCY`, défaut 3). #T304 avait déjà
sorti le mode Discussion du portillon. Mais le portillon restait posé AVANT le
choix du chemin : les cinq chemins légers restants (casual_chat, HA déterministe,
fuzzy HA, repli LLM, small-talk) tenaient un créneau du plafond pendant toute
leur durée, alors qu'ils ne touchent JAMAIS l'Engine partagé que ce portillon
protège. Conséquence : trois requêtes légères en vol suffisaient à refuser un
vrai DAG de l'IHM.

#T344 descend la prise du créneau (`begin_execution`) juste avant
`run_full_pipeline`, son seul consommateur réel, et introduit un COMPTEUR DE
REQUÊTES EN VOL (`active_requests`) distinct du registre de concurrence pour
maintenir la contre-pression du dreamer : tant qu'une requête tourne — chemin
léger compris — `execution_state["status"]` reste "running" et le dreamer se
tait (il lit cette vue pour savoir si l'utilisateur travaille).

Les tests pilotent la VRAIE route `execute_chat` ; seuls le routeur (analyse) et
le travail LLM (fast path) sont doublés. Aucun réseau, aucun LLM.
"""
import asyncio
import gc
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes import agents as route_agents
from core.app_state import _cle_execution, get_app_state
from core.runtime_db import get_connection, override_db_path
from core.source_router import parse_source


@pytest.fixture
def temp_runtime_db():
    """Base jetable : `log_vocal_request/response` écrivent réellement."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_runtime.db")
    override_db_path(db_path)
    conn = get_connection()
    # DELETE évite les fichiers WAL verrouillés sous Windows en teardown
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()
    conn.close()
    yield db_path
    gc.collect()
    default_db = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "moteur_runtime.db"
    )
    override_db_path(default_db)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def etat_moteur():
    """Registre, vue agrégée et compteur de requêtes remis au repos autour du test."""
    state = get_app_state()
    registre_avant = dict(state.execution_registry)
    vue_avant = dict(state.execution_state)
    compteur_avant = state.active_requests
    state.execution_registry.clear()
    state.active_requests = 0
    state.execution_state.update({
        "status": "idle", "objective": "", "engine_state": None,
        "error_message": None,
    })
    yield state
    state.execution_registry.clear()
    state.execution_registry.update(registre_avant)
    state.active_requests = compteur_avant
    state.execution_state.clear()
    state.execution_state.update(vue_avant)


def _corps(prompt="raconte une blague", **source_extra):
    """Corps de requête sur un chemin LÉGER (mode default, hors HA/CHAT)."""
    source = {"type": "ide", "mode": "default", "tts_enabled": False}
    source.update(source_extra)
    return route_agents.ExecuteRequestBody(user_prompt=prompt, source=source)


def _corps_lourd(prompt="refactore le module de routage", session_id="session_ide"):
    """Corps de requête qui va au pipeline complet (mode default)."""
    return route_agents.ExecuteRequestBody(
        user_prompt=prompt,
        source={"type": "ide", "mode": "default", "tts_enabled": False},
        session_id=session_id,
    )


def _occuper(state, cle: str, objectif="un DAG en cours") -> None:
    """Simule une exécution lourde en vol pour la clé donnée."""
    state.execution_registry[cle] = {
        "status": "running", "objective": objectif,
        "engine_state": None, "error_message": None,
    }
    state.execution_state.update({"status": "running", "objective": objectif})


def _routeur_casual_chat(monkeypatch):
    """Routeur doublé : analyse qui route vers le fast path casual_chat."""
    payload = SimpleNamespace(metadata={"routing_type": "casual_chat"})
    faux_routeur = MagicMock()
    faux_routeur.analyze_request = AsyncMock(return_value=(payload, "fast_path"))
    monkeypatch.setattr("core.app_state.AppState.get_shared_router",
                        MagicMock(return_value=faux_routeur))
    return faux_routeur


def _routeur_pipeline(monkeypatch):
    """Routeur doublé : analyse qui route vers le pipeline complet (lourd)."""
    payload = SimpleNamespace(metadata={"routing_type": "default"})
    faux_routeur = MagicMock()
    faux_routeur.analyze_request = AsyncMock(return_value=(payload, "planner"))
    monkeypatch.setattr("core.app_state.AppState.get_shared_router",
                        MagicMock(return_value=faux_routeur))
    return faux_routeur


# ── TEST CENTRAL : plafond saturé, un chemin léger répond quand même ─────────

@pytest.mark.asyncio
async def test_chemin_leger_repond_meme_avec_le_plafond_sature(
    temp_runtime_db, etat_moteur, monkeypatch
):
    """Le plafond de concurrence saturé par trois exécutions lourdes : une
    requête qui emprunte un chemin LÉGER répond quand même.

    Échouait sur master : le portillon était posé AVANT le choix du chemin, donc
    la requête légère prenait un créneau et se voyait refuser (409 plafond).
    """
    _routeur_casual_chat(monkeypatch)
    monkeypatch.setattr(route_agents, "run_fast_path",
                        AsyncMock(return_value="blague vite faite"))
    monkeypatch.setattr(route_agents, "LLMGateway", MagicMock())

    # Plafond saturé par des exécutions lourdes en vol.
    for i in range(etat_moteur.max_concurrency):
        _occuper(etat_moteur, f"session_lourde_{i}")

    result = await route_agents.execute_chat(_corps())

    assert result["status"] == "completed", result
    assert result["response"] == "blague vite faite"
    # Aucun créneau consommé : le registre n'a reçu aucune entrée nouvelle.
    assert list(etat_moteur.execution_registry) == [
        f"session_lourde_{i}" for i in range(etat_moteur.max_concurrency)
    ]


# ── TEST GARDE-FOU : le dreamer voit toujours l'activité utilisateur ─────────

@pytest.mark.asyncio
async def test_chemin_leger_maintient_le_signal_d_activite_pour_le_dreamer(
    temp_runtime_db, etat_moteur, monkeypatch
):
    """Pendant qu'une requête légère est en vol, le signal de contre-pression lu
    par le dreamer (`execution_state["status"] == "running"`) indique bien une
    activité utilisateur. Sans ce test, la PR est refusée même si le premier
    passe : si les chemins légers ne déclarent plus rien, le dreamer se réveille
    pendant qu'Axel parle à son Tab5.
    """
    _routeur_casual_chat(monkeypatch)
    monkeypatch.setattr(route_agents, "LLMGateway", MagicMock())

    # Le fast path reste bloqué tant que `porte` n'est pas posée : on observe la
    # requête légère en vol, exactement comme le dreamer la verrait. `entree`
    # signale que le fast path (donc le début du travail léger) est atteint.
    entree = asyncio.Event()
    porte = asyncio.Event()

    async def _fast_path_bloque(*args, **kwargs):
        entree.set()
        await porte.wait()
        return "blague vite faite"

    monkeypatch.setattr(route_agents, "run_fast_path", _fast_path_bloque)

    # Lance la requête légère en tâche de fond pour l'observer en vol.
    tache = asyncio.create_task(route_agents.execute_chat(_corps()))
    await asyncio.wait_for(entree.wait(), timeout=5)

    # Pendant qu'elle tourne : le dreamer doit voir l'utilisateur actif.
    assert etat_moteur.execution_state["status"] == "running", (
        "le dreamer ne verrait aucune activité pendant la requête légère"
    )
    assert etat_moteur.active_requests >= 1

    porte.set()
    await tache
    # Après la requête : plus rien en vol → la vue retombe au repos.
    assert etat_moteur.active_requests == 0
    assert etat_moteur.execution_state["status"] == "idle"


# ── TEST DOUBLON LOURD : le portillon protège toujours ce qu'il protège ──────

@pytest.mark.asyncio
async def test_deux_requetes_lourdes_de_la_meme_session_409_doublon(
    temp_runtime_db, etat_moteur, monkeypatch
):
    """Deux requêtes lourdes de la MÊME clé de session → toujours 409 doublon.
    Le portillon descendu juste avant le pipeline doit continuer à protéger
    l'Engine partagé : une même session ne s'exécute jamais deux fois."""
    _routeur_pipeline(monkeypatch)
    monkeypatch.setattr(route_agents, "run_full_pipeline",
                        AsyncMock(return_value={"status": "success", "response": "ok"}))
    monkeypatch.setattr(route_agents, "LLMGateway", MagicMock())

    corps = _corps_lourd(session_id="session_ide")
    cle_ide = _cle_execution("session_ide", parse_source(corps.source))
    _occuper(etat_moteur, cle_ide, objectif="premier DAG")

    with pytest.raises(HTTPException) as exc:
        await route_agents.execute_chat(corps)

    assert exc.value.status_code == 409
    assert "déjà en cours" in exc.value.detail or "Doublon" in exc.value.detail or "doublon" in exc.value.detail


# ── TEST ORPHELINE : aucune entrée laissée, même sur exception ───────────────

@pytest.mark.asyncio
async def test_pas_d_entree_orpheline_apres_un_chemin_leger(
    temp_runtime_db, etat_moteur, monkeypatch
):
    """Une requête légère qui se termine ne laisse AUCUNE entrée dans
    `execution_registry` (elle n'en a jamais créé) et remet le compteur à zéro."""
    _routeur_casual_chat(monkeypatch)
    monkeypatch.setattr(route_agents, "run_fast_path",
                        AsyncMock(return_value="blague vite faite"))
    monkeypatch.setattr(route_agents, "LLMGateway", MagicMock())

    await route_agents.execute_chat(_corps())

    assert etat_moteur.execution_registry == {}
    assert etat_moteur.active_requests == 0


@pytest.mark.asyncio
async def test_pas_d_entree_orpheline_apres_une_exception_dans_le_chemin_leger(
    temp_runtime_db, etat_moteur, monkeypatch
):
    """Une exception levée DANS un chemin léger (small-talk HA) ne laisse aucune
    entrée orpheline dans le registre et remet le compteur à zéro — le finally
    libère toujours le compteur, et ne libère jamais une entrée non créée."""
    # Route vers le chemin small-talk HA (routing "default", mode HA), en
    # neutralisant le fuzzy matcher pour que la requête atteigne bien ce palier.
    _routeur_pipeline(monkeypatch)
    monkeypatch.setattr("core.ha_fuzzy_matcher.get_fuzzy_matcher", lambda: None)
    monkeypatch.setattr(
        route_agents, "_ha_conversational_response",
        MagicMock(side_effect=RuntimeError("panne small-talk")),
    )

    corps = route_agents.ExecuteRequestBody(
        user_prompt="raconte une blague",
        source={"type": "tab5", "mode": "ha", "tts_enabled": True},
    )
    result = await route_agents.execute_chat(corps)

    assert result["status"] == "error", result  # repli d'erreur générique
    assert etat_moteur.execution_registry == {}
    assert etat_moteur.active_requests == 0
