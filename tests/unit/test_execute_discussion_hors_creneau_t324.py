"""
tests/unit/test_execute_discussion_hors_creneau_t324.py — le mode Discussion
sort du registre d'exécution (suite de #T324).

#T324 a remplacé le verrou global par un verrou PAR SESSION avec plafond de
concurrence (`MOTEUR_EXECUTION_MAX_CONCURRENCY`, défaut 3). Le portillon reste
posé AVANT le choix du chemin, or la branche Discussion (`mode=chat`) ne touche
pas l'Engine partagé que ce portillon protège. Elle payait donc deux fois :

  - un créneau du plafond pendant toute sa durée — trois tours vocaux en vol
    suffisaient à refuser un DAG de l'IHM ;
  - un 409 « doublon de session » sur un second tour vocal, la clé d'exécution
    du Tab5 retombant sur son `device_id` faute de `session_id`.

Et `router.analyze_request()` était appelé puis JETÉ (le `routing_type` retenu
vient de `host_result`).

Les tests pilotent la VRAIE route `execute_chat` ; seuls le travail LLM
(`handle_discussion`) et la passerelle sont doublés. Aucun réseau, aucun LLM.
"""
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
    """Registre d'exécution et vue agrégée remis au repos autour du test."""
    state = get_app_state()
    registre_avant = dict(state.execution_registry)
    vue_avant = dict(state.execution_state)
    state.execution_registry.clear()
    state.execution_state.update({
        "status": "idle", "objective": "", "engine_state": None,
        "error_message": None,
    })
    yield state
    state.execution_registry.clear()
    state.execution_registry.update(registre_avant)
    state.execution_state.clear()
    state.execution_state.update(vue_avant)


@pytest.fixture
def discussion(monkeypatch):
    """Double de `handle_discussion` : résultat plausible, appels comptés."""
    host_result = SimpleNamespace(
        response_text="Il fait 21 degrés dans le salon.",
        async_job_id=None,
        agents_used=["vocal_host"],
        routing_type="discussion_chat",
        metadata={"modele": "faux-modele"},
    )
    faux = AsyncMock(return_value=host_result)
    monkeypatch.setattr("core.vocal_host.handle_discussion", faux)
    monkeypatch.setattr(route_agents, "LLMGateway", MagicMock())
    return faux


def _corps(prompt="il fait combien dans le salon ?", **source_extra):
    source = {"type": "tab5", "mode": "chat", "tts_enabled": True}
    source.update(source_extra)
    return route_agents.ExecuteRequestBody(user_prompt=prompt, source=source)


def _occuper(state, cle: str, objectif="un DAG en cours") -> None:
    """Simule une exécution en vol pour la clé donnée."""
    state.execution_registry[cle] = {
        "status": "running", "objective": objectif,
        "engine_state": None, "error_message": None,
    }
    state.execution_state.update({"status": "running", "objective": objectif})


# ── Le plafond de concurrence ne doit pas museler le vocal ───────────────────

@pytest.mark.asyncio
async def test_discussion_repond_meme_avec_le_plafond_sature(
    temp_runtime_db, etat_moteur, discussion
):
    """Plafond atteint par des exécutions lourdes : un tour vocal en mode
    Discussion répond quand même — il ne demande aucun créneau."""
    for i in range(etat_moteur.max_concurrency):
        _occuper(etat_moteur, f"session_lourde_{i}")

    result = await route_agents.execute_chat(_corps())

    assert result["status"] == "completed", result
    assert result["response"] == "Il fait 21 degrés dans le salon."
    assert discussion.await_count == 1


@pytest.mark.asyncio
async def test_second_tour_vocal_du_meme_device_n_est_plus_un_doublon(
    temp_runtime_db, etat_moteur, discussion
):
    """La clé d'exécution du Tab5 retombe sur son `device_id` : un second tour
    vocal pendant qu'un premier est en vol se prenait un 409 « doublon »."""
    corps = _corps(device_id="tab5_salon")
    cle_du_device = _cle_execution(None, parse_source(corps.source))
    _occuper(etat_moteur, cle_du_device, objectif="premier tour vocal")

    result = await route_agents.execute_chat(corps)

    assert result["status"] == "completed", result
    assert discussion.await_count == 1


@pytest.mark.asyncio
async def test_discussion_ne_prend_aucun_creneau(
    temp_runtime_db, etat_moteur, discussion
):
    """Ni entrée ajoutée au registre, ni vue agrégée touchée : le tour vocal
    passe sans laisser de trace de concurrence."""
    _occuper(etat_moteur, "session_lourde")

    await route_agents.execute_chat(_corps(device_id="tab5_salon"))

    assert list(etat_moteur.execution_registry) == ["session_lourde"], (
        "le tour Discussion a pris un créneau ou laissé une entrée orpheline"
    )
    assert etat_moteur.execution_state["status"] == "running", (
        "le tour Discussion a écrasé la vue agrégée d'une exécution en cours"
    )
    assert etat_moteur.execution_state["objective"] == "un DAG en cours"


@pytest.mark.asyncio
async def test_discussion_au_repos_ne_declare_rien(
    temp_runtime_db, etat_moteur, discussion
):
    """Sans exécution concurrente : le mode Discussion n'a pas de raison
    d'annoncer `running` à l'IHM pour un tour de 2 s."""
    await route_agents.execute_chat(_corps())

    assert etat_moteur.execution_registry == {}
    assert etat_moteur.execution_state["status"] == "idle"


# ── L'analyse du routeur n'est plus payée pour rien ──────────────────────────

@pytest.mark.asyncio
async def test_discussion_n_appelle_pas_le_routeur(
    temp_runtime_db, etat_moteur, discussion, monkeypatch
):
    """`analyze_request()` était appelé puis jeté : le `routing_type` retenu
    vient de `host_result`, et ni `initial_payload` ni `starting_agent` n'y
    servent. Le routeur ne doit plus être sollicité du tout."""
    faux_routeur = MagicMock()
    monkeypatch.setattr(etat_moteur, "get_shared_router", faux_routeur)

    result = await route_agents.execute_chat(_corps())

    assert result["status"] == "completed"
    faux_routeur.assert_not_called()


# ── Non-régression : contrat rendu au Tab5 et historique de conversation ────

@pytest.mark.asyncio
async def test_forme_de_reponse_inchangee(
    temp_runtime_db, etat_moteur, discussion
):
    """Agent, routing_type, métadonnées du host et `agents_used` viennent tous
    de `host_result`, comme avant."""
    result = await route_agents.execute_chat(_corps())

    entree = result["history"][0]
    assert entree["agent_name"] == "vocal_host"
    assert entree["metadata"]["routing_type"] == "discussion_chat"
    assert entree["metadata"]["model_tier"] == "leger"
    assert entree["metadata"]["modele"] == "faux-modele", "métadonnées du host perdues"
    assert result["agents_used"] == ["vocal_host"]


@pytest.mark.asyncio
async def test_les_deux_tours_de_conversation_sont_enregistres(
    temp_runtime_db, etat_moteur, discussion, monkeypatch
):
    """Avec un `conversation_id`, le tour utilisateur ET le tour assistant sont
    enregistrés — une perte silencieuse casserait le multi-tour."""
    tours = []
    monkeypatch.setattr(
        "core.vocal_session.record_vocal_turn",
        lambda conv_id, role, texte, **kw: tours.append((conv_id, role, texte)),
    )

    await route_agents.execute_chat(_corps(conversation_id="conv_42"))

    assert [(c, r) for c, r, _ in tours] == [
        ("conv_42", "user"), ("conv_42", "assistant"),
    ], tours


@pytest.mark.asyncio
async def test_echec_du_host_rend_le_repli_sans_toucher_au_registre(
    temp_runtime_db, etat_moteur, discussion
):
    """Si `handle_discussion` échoue, le repli part quand même et le registre
    d'une exécution concurrente reste intact."""
    discussion.side_effect = RuntimeError("passerelle indisponible")
    _occuper(etat_moteur, "session_lourde")

    result = await route_agents.execute_chat(_corps())

    assert result["response"], result
    assert list(etat_moteur.execution_registry) == ["session_lourde"]


# ── Garde-fou : le portillon protège TOUJOURS les chemins lourds ────────────

@pytest.mark.asyncio
async def test_le_portillon_refuse_toujours_le_doublon_hors_discussion(
    temp_runtime_db, etat_moteur, discussion
):
    """Sortir Discussion du registre ne doit pas ouvrir le portillon pour les
    autres : un appel hors mode Discussion, sur une clé déjà en vol, reçoit
    toujours 409 — l'Engine partagé n'accepte pas deux fois la même session."""
    corps = route_agents.ExecuteRequestBody(
        user_prompt="refactore le module de routage",
        source={"type": "ide", "mode": "default", "tts_enabled": False},
        session_id="session_ide",
    )
    _occuper(etat_moteur, "session_ide")

    with pytest.raises(HTTPException) as exc:
        await route_agents.execute_chat(corps)

    assert exc.value.status_code == 409
    assert discussion.await_count == 0, "le chemin Discussion n'a rien à faire ici"


@pytest.mark.asyncio
async def test_le_plafond_refuse_toujours_un_chemin_lourd(
    temp_runtime_db, etat_moteur, discussion, monkeypatch
):
    """Et le plafond de concurrence tient toujours pour les chemins lourds.

    [#T344] Le portillon est désormais posé après `analyze_request` (juste avant
    le pipeline) : ce test mocke le routeur pour router vers le pipeline sans
    appeler le LLM, puis vérifie que le plafond refuse toujours la requête."""
    # Route vers le pipeline complet (chemin lourd) sans appeler le LLM.
    payload = SimpleNamespace(metadata={"routing_type": "default"})
    faux_routeur = MagicMock()
    faux_routeur.analyze_request = AsyncMock(return_value=(payload, "planner"))
    monkeypatch.setattr(etat_moteur, "get_shared_router",
                        MagicMock(return_value=faux_routeur))

    for i in range(etat_moteur.max_concurrency):
        _occuper(etat_moteur, f"session_lourde_{i}")
    corps = route_agents.ExecuteRequestBody(
        user_prompt="refactore le module de routage",
        source={"type": "ide", "mode": "default", "tts_enabled": False},
        session_id="une_autre_session",
    )

    with pytest.raises(HTTPException) as exc:
        await route_agents.execute_chat(corps)

    assert exc.value.status_code == 409
