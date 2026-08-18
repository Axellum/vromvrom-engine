"""Tests lecture d'état domotique vocale (services/ha_state_query)."""

import pytest

from services import ha_state_query as hsq
from services.ha_state_query import (
    match_ha_state_query,
    resolve_ha_state_query,
)

# ── Détection : questions d'état vs commandes ──

def test_command_imperative_is_not_state_query():
    """Un impératif reste une commande, jamais une lecture d'état."""
    assert match_ha_state_query("allume la lumiere du salon") is None
    assert match_ha_state_query("ferme le volet du salon") is None
    assert match_ha_state_query("eteins la clim du salon") is None


def test_light_state_question_detected():
    q = match_ha_state_query("la lumiere du salon est allumee ?")
    assert q is not None and q.kind == "light"
    assert q.entity_id == "light.living_room"


def test_climate_state_question_detected():
    q = match_ha_state_query("est-ce que la clim est allumee")
    assert q is not None and q.kind == "climate"
    assert q.entity_id == "climate.living_room"


def test_volet_state_question_detected():
    q = match_ha_state_query("le volet est ouvert ?")
    assert q is not None and q.kind == "volet"


def test_temperature_question_detected():
    q = match_ha_state_query("il fait combien dans le salon ?")
    assert q is not None and q.kind == "temperature"


def test_non_question_returns_none():
    assert match_ha_state_query("raconte une blague") is None
    assert match_ha_state_query("quelle heure est il") is None  # hors domaine domotique


# ── Formatage des réponses (état HA mocké) ──

@pytest.mark.asyncio
async def test_resolve_light_on(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "on", "attributes": {}}
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("la lumiere du salon est allumee ?")
    assert reply == "La lumière du salon est allumée."


@pytest.mark.asyncio
async def test_resolve_climate_on_with_mode_and_temp(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "cool", "attributes": {"temperature": 24, "current_temperature": 22}}
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("la clim est allumee ?")
    assert "allumée en mode froid" in reply
    assert "24 degrés" in reply
    assert "22 degrés" in reply


@pytest.mark.asyncio
async def test_resolve_climate_off(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "off", "attributes": {}}
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("est-ce que la clim tourne")
    assert reply == "La climatisation est éteinte."


@pytest.mark.asyncio
async def test_resolve_volet_closed(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "Ferme", "attributes": {}}
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("le volet est ouvert ?")
    assert reply == "Le volet est fermé."


@pytest.mark.asyncio
async def test_resolve_temperature(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "cool", "attributes": {"current_temperature": 21.4}}
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("il fait combien dans le salon ?")
    assert reply == "Il fait 21 degrés dans le salon."


@pytest.mark.asyncio
async def test_resolve_read_failure_is_honest(monkeypatch):
    """Lecture indisponible → on le dit, on n'invente pas de valeur."""
    async def _fake_state(_entity):
        return None
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    reply = await resolve_ha_state_query("la clim est allumee ?")
    assert "pas pu lire" in reply


@pytest.mark.asyncio
async def test_resolve_command_returns_none(monkeypatch):
    """Une commande d'action n'est pas une question d'état → None (cascade continue)."""
    async def _fake_state(_entity):  # ne devrait pas être appelé
        raise AssertionError("read_ha_state ne doit pas être appelé pour une commande")
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    assert await resolve_ha_state_query("allume la lumiere du salon") is None


# ── [#T358] Questions générales : le monde, pas l'installation ──

@pytest.mark.parametrize("phrase", [
    # Phrase exacte mesurée en prod le 17/08 (répondue « Le volet est
    # partiellement ouvert. » en 58 ms, deux fois de suite).
    "en une phrase, c'est quoi un volet roulant ?",
    "c'est quoi un volet roulant ?",
    "explique-moi en une phrase comment marche une ampoule LED",
    "comment fonctionne une climatisation reversible ?",
    "a quoi sert un thermostat ?",
    "quelle difference entre un store et un volet ?",
])
def test_question_generale_nest_pas_une_lecture_detat(phrase):
    """Une question de culture générale ne doit jamais lire un état HA."""
    assert match_ha_state_query(phrase) is None


@pytest.mark.parametrize("phrase,kind", [
    # Non-régression : les vraies questions d'état passent toujours.
    ("le volet est ouvert ?", "volet"),
    ("est-ce que la clim est allumee", "climate"),
    ("la lumiere du salon est allumee ?", "light"),
    ("il fait combien dans le salon ?", "temperature"),
    # Article indéfini MAIS adjectif d'état : reste une question d'état.
    ("est-ce qu'une lumiere est allumee dans le salon ?", "light"),
])
def test_vraies_questions_detat_inchangees(phrase, kind):
    q = match_ha_state_query(phrase)
    assert q is not None and q.kind == kind


@pytest.mark.asyncio
async def test_question_generale_ne_lit_aucun_etat(monkeypatch):
    """Garde-fou : aucune lecture HA ne part pour une question de définition."""
    async def _fake_state(_entity):
        raise AssertionError("read_ha_state ne doit pas être appelé pour une définition")
    monkeypatch.setattr(hsq, "read_ha_state", _fake_state)
    assert await resolve_ha_state_query("c'est quoi un volet roulant ?") is None


# ── [#T364] Ne pas deviner : « combien » seul, pièce sans capteur, mot de domaine ──

@pytest.mark.parametrize("phrase", [
    # Phrase exacte mesurée en prod le 17/08 (répondue « Il fait 22 degrés dans
    # le salon. » en 38 ms), et ses voisines vérifiées généralisables.
    "Combien fait 17 fois 24 ?",
    "Combien coute un billet de train pour Paris ?",
    "Combien d'habitants a la France ?",
    "Combien de temps pour cuire un gateau ?",
])
def test_combien_seul_nest_pas_une_question_de_temperature(phrase):
    """« combien » est trop courant en question libre pour valoir marqueur."""
    assert match_ha_state_query(phrase) is None


def test_piece_sans_capteur_ne_repond_pas_celle_d_a_cote():
    """
    Une question sur une pièce ne doit jamais rendre le capteur d'une autre.

    [#T371] L'assertion d'origine (#T364) exigeait `None` pour la chambre :
    c'était figer un comportement dégradé, car la chambre porte un thermomètre
    bien vivant (`sensor.bedroom_temperature`). Elle répond
    désormais — avec SON capteur. L'intention du test est conservée et
    renforcée : ce qui est interdit, c'est le capteur du salon.
    """
    q = match_ha_state_query("il fait combien dans la chambre ?")
    assert q is not None
    assert q.entity_id == "sensor.bedroom_temperature"

    # La pièce réellement sans capteur, elle, ne reçoit toujours rien.
    assert match_ha_state_query("il fait combien dans la buanderie ?") is None


def test_nom_de_piece_seul_ne_declenche_pas_la_lumiere():
    """
    Mesuré en prod : « Quel est l'attempérature du salon ? » (STT abîmé) tombait
    dans la branche lumière et répondait « La lumière du salon est allumée. ».
    """
    assert match_ha_state_query("Quel est l'attemperature du salon ?") is None


@pytest.mark.parametrize("phrase,kind", [
    ("il fait combien dans le salon ?", "temperature"),   # « combien » + pièce connue
    ("quelle temperature dans le salon ?", "temperature"),
    ("quelle est la temperature ?", "temperature"),        # sans pièce → défaut salon
    ("la lumiere du salon est allumee ?", "light"),
])
def test_non_regression_lectures_detat_legitimes(phrase, kind):
    q = match_ha_state_query(phrase)
    assert q is not None and q.kind == kind
