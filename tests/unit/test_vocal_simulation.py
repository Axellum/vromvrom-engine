"""
POST /api/vocal/simulate — « que ferait le moteur de cette phrase ? » sans rien exécuter.

Ce que ces tests défendent, dans l'ordre d'importance :
  1. PURETÉ : la simulation n'appelle ni Home Assistant, ni un LLM, ni une base SQLite, ni le
     réseau, et n'alimente pas le journal vocal. Chaque porte d'effet de bord est piégée
     (elle lève si on la franchit) avant d'appeler la route ;
  2. VÉRITÉ : quand le chemin réel dépend de l'état (volet en mouvement, routeur LLM), la
     réponse est « indetermine » avec des booléens à `null` — jamais une devinette ;
  3. les chemins reconnus sont ceux que la cascade du moteur décide (domotique et discussion) ;
  4. le contrat HTTP (corps validé, 401 sans jeton).
"""

import socket
import sqlite3

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.routes import vocal_simulation
from core.auth import require_auth
from services import vocal_simulation as service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(vocal_simulation.router, dependencies=[Depends(require_auth)])
    return TestClient(app)


@pytest.fixture
def effets_de_bord_pieges(monkeypatch):
    """Toute porte vers Home Assistant, un LLM, une base ou le réseau lève si on la franchit."""
    def piege(nom):
        def _leve(*_a, **_k):
            raise AssertionError(f"effet de bord interdit dans la simulation : {nom}")
        return _leve

    import aiohttp

    import core.llm_gateway as gateway
    import core.vocal_audit as audit
    import services.execute_service as execute

    for cible, attribut in (
        (execute, "execute_ha_service"), (execute, "read_ha_state"), (execute, "is_volet_moving"),
        (execute, "_get_ha_session"), (audit, "log_vocal_request"), (audit, "log_vocal_response"),
    ):
        monkeypatch.setattr(cible, attribut, piege(f"{cible.__name__}.{attribut}"))
    monkeypatch.setattr(gateway, "LLMGateway", piege("LLMGateway"))
    monkeypatch.setattr(aiohttp, "ClientSession", piege("aiohttp.ClientSession"))
    monkeypatch.setattr(sqlite3, "connect", piege("sqlite3.connect"))

    connect_reel = socket.socket.connect

    def connect_filtre(self, adresse, *a, **k):
        hote = adresse[0] if isinstance(adresse, tuple) else adresse
        if hote not in ("127.0.0.1", "::1", "localhost"):   # la boucle de TestClient est locale
            raise AssertionError(f"réseau interdit dans la simulation : {hote}")
        return connect_reel(self, adresse, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect_filtre)
    monkeypatch.setattr(socket, "getaddrinfo", piege("socket.getaddrinfo"))


def _simuler(client, auth_headers, texte, mode="ha"):
    r = client.post("/api/vocal/simulate", json={"texte": texte, "mode": mode}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pureté
# ═══════════════════════════════════════════════════════════════════════════

PHRASES_TOUS_CHEMINS = [
    ("ha", "allume la lumière du salon"), ("ha", "ferme le volet"), ("ha", "stop"),
    ("ha", "la clim est allumée ?"), ("ha", "monte la clim de 2 degrés"), ("ha", "mets la clim à 22"),
    ("ha", "quelle heure est-il"), ("ha", "quel temps fait-il demain"), ("ha", "raconte moi une blague"),
    ("chat", "allume le salon"), ("chat", "il fait combien dans la chambre"),
    ("chat", "quelle heure est-il"), ("chat", "cherche sur le web le prix du café"),
    ("chat", "qu'est ce que j'ai dans mon agenda demain"), ("chat", "raconte moi une blague"),
]


@pytest.mark.parametrize("mode,texte", PHRASES_TOUS_CHEMINS)
def test_aucun_effet_de_bord_sur_aucun_chemin(client, auth_headers, effets_de_bord_pieges, mode, texte):
    reponse = _simuler(client, auth_headers, texte, mode)
    assert reponse["simulation"] is True
    assert "rien n'a été exécuté" in reponse["explication"]


def test_le_journal_vocal_n_est_pas_alimente(client, auth_headers):
    from core import runtime_db

    runtime_db.get_connection().close()                       # crée le schéma
    def compter():
        conn = sqlite3.connect(runtime_db.get_db_path())
        try:
            return conn.execute("SELECT COUNT(*) FROM vocal_audit_log").fetchone()[0]
        finally:
            conn.close()
    avant = compter()
    for mode, texte in PHRASES_TOUS_CHEMINS:
        _simuler(client, auth_headers, texte, mode)
    assert compter() == avant


# ═══════════════════════════════════════════════════════════════════════════
# 2. Chemins reconnus (mode domotique)
# ═══════════════════════════════════════════════════════════════════════════

CAS_DOMOTIQUE = [
    # texte, chemin, service attendu (ou None), exécuterait une action, LLM requis
    ("allume la lumière du salon", "ha_command", "light.turn_on", True, False),
    ("éteins la lumière de la chambre", "ha_command", "light.turn_off", True, False),
    ("ferme le volet", "ha_command", "script.blind_action", True, False),
    ("mets la clim à 22", "ha_command", "climate.set_temperature", True, False),
    ("monte la clim de 2 degrés", "ha_climate_relative", "climate.set_temperature", True, False),
    ("la clim est allumée ?", "ha_state_query", None, False, False),
    ("quelle heure est-il", "ha_datetime", None, False, False),
    ("quel temps fait-il demain", "ha_weather", None, False, False),
]


@pytest.mark.parametrize("texte,chemin,service,execute,llm", CAS_DOMOTIQUE)
def test_chemins_du_mode_domotique(client, auth_headers, texte, chemin, service, execute, llm):
    r = _simuler(client, auth_headers, texte, "ha")
    assert r["chemin"] == chemin
    assert r["executerait_une_action"] is execute
    assert r["llm_requis"] is llm
    if service is None:
        assert r["action_prevue"] is None
    else:
        assert r["action_prevue"]["service"] == service
        assert set(r["action_prevue"]) >= {"service", "entite"}
    assert r["mode"] == "ha"


def test_une_question_d_etat_passe_avant_l_action(client, auth_headers):
    """« allumée » est un marqueur d'action : la question d'état doit l'emporter, comme dans le moteur."""
    r = _simuler(client, auth_headers, "la lumière du salon est allumée ?")
    assert r["chemin"] == "ha_state_query"
    assert r["action_prevue"] is None and r["executerait_une_action"] is False


def test_la_cible_de_la_clim_suit_la_piece_citee(client, auth_headers):
    from services.execute_service import normalize_ha_command_prompt, resolve_climate_entity

    attendu = resolve_climate_entity(normalize_ha_command_prompt("mets la clim à 22"))
    assert _simuler(client, auth_headers, "mets la clim à 22")["action_prevue"]["entite"] == attendu


def test_un_script_porte_sa_cible_dans_le_service(client, auth_headers):
    r = _simuler(client, auth_headers, "ferme le volet")
    assert r["action_prevue"]["entite"] is None
    assert r["action_prevue"]["donnees"] == {"action": "close"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. Vérité : l'indéterminé reste indéterminé
# ═══════════════════════════════════════════════════════════════════════════

def test_stop_sans_le_mot_volet_depend_de_l_etat(client, auth_headers):
    r = _simuler(client, auth_headers, "stop")
    assert r["chemin"] == "indetermine"
    assert r["executerait_une_action"] is None              # ni vrai ni faux : dépend du volet
    assert r["llm_requis"] is False
    assert r["action_prevue"]["service"] == "script.blind_action"
    assert "en mouvement" in r["explication"]


def test_stop_volet_explicite_est_determine(client, auth_headers):
    r = _simuler(client, auth_headers, "arrête le volet")
    assert r["chemin"] == "ha_command" and r["executerait_une_action"] is True


def test_phrase_inconnue_en_mode_domotique_est_indeterminee(client, auth_headers):
    r = _simuler(client, auth_headers, "raconte moi une blague")
    assert r["chemin"] == "indetermine"
    assert r["action_prevue"] is None
    assert r["llm_requis"] is None and r["executerait_une_action"] is None
    assert "routeur" in r["explication"]


def test_verbe_d_action_empeche_l_etape_du_quotidien(client, auth_headers):
    """« allume … quelle heure » : un verbe d'action domotique suit la cascade d'avant, pas la réponse d'heure."""
    r = _simuler(client, auth_headers, "allume quelle heure est-il maintenant")
    assert r["chemin"] != "ha_datetime"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Mode discussion
# ═══════════════════════════════════════════════════════════════════════════

CAS_DISCUSSION = [
    ("allume le salon", "discussion_ha_command", True, False),
    ("il fait combien dans la chambre", "discussion_ha_state", False, False),
    ("quelle heure est-il", "discussion_datetime", False, False),
    ("cherche sur le web le prix du café", "vocal_host_web", False, True),
    ("qu'est ce que j'ai dans mon agenda demain", "vocal_host_calendar", False, True),
    ("raconte moi une blague", "discussion_chat", False, True),
]


@pytest.mark.parametrize("texte,chemin,execute,llm", CAS_DISCUSSION)
def test_chemins_du_mode_discussion(client, auth_headers, texte, chemin, execute, llm):
    r = _simuler(client, auth_headers, texte, "chat")
    assert r["chemin"] == chemin
    assert r["executerait_une_action"] is execute
    assert r["llm_requis"] is llm
    assert r["mode"] == "chat"


def test_le_chat_libre_ne_peut_commander_aucun_appareil(client, auth_headers):
    r = _simuler(client, auth_headers, "raconte moi une blague", "chat")
    assert r["executerait_une_action"] is False and r["action_prevue"] is None
    assert "ne peut commander aucun appareil" in r["explication"]


@pytest.mark.parametrize("texte,chemin", [
    ("retrouve le pdf de mon contrat de location", "vocal_host_files_async"),
    ("fais moi un rapport complet sur mon installation", "vocal_host_deep_async"),
])
def test_les_intentions_longues_sont_annoncees_en_arriere_plan(texte, chemin):
    """Fichiers / réponse approfondie : le moteur accuse réception puis travaille en tâche de fond."""
    r = service.simuler(texte, "chat")
    assert r["chemin"] == chemin
    assert r["llm_requis"] is True and r["executerait_une_action"] is False
    assert "en arrière-plan" in r["explication"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. Contrat HTTP
# ═══════════════════════════════════════════════════════════════════════════

def test_champs_du_contrat(client, auth_headers):
    r = _simuler(client, auth_headers, "allume la lumière du salon")
    assert set(r) >= {"chemin", "explication", "action_prevue", "llm_requis", "executerait_une_action"}
    assert isinstance(r["chemin"], str) and isinstance(r["explication"], str)


def test_mode_par_defaut_est_domotique(client, auth_headers):
    r = client.post("/api/vocal/simulate", json={"texte": "allume la lumière du salon"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["mode"] == "ha"


@pytest.mark.parametrize("corps", [
    {"texte": "", "mode": "ha"}, {"texte": "   ", "mode": "ha"}, {"texte": "x" * 501, "mode": "ha"},
    {"texte": "allume", "mode": "inconnu"}, {"mode": "ha"}, {},
])
def test_corps_invalide_donne_422(client, auth_headers, corps):
    assert client.post("/api/vocal/simulate", json=corps, headers=auth_headers).status_code == 422


def test_exige_l_authentification(client):
    corps = {"texte": "allume la lumière du salon", "mode": "ha"}
    assert client.post("/api/vocal/simulate", json=corps).status_code == 401
    assert client.post("/api/vocal/simulate", json=corps,
                       headers={"Authorization": "Bearer mauvais"}).status_code == 401


def test_mode_inconnu_leve_dans_le_service():
    with pytest.raises(ValueError):
        service.simuler("allume", "inconnu")
