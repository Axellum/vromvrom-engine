"""Tests lecture d'état domotique vocale (services/ha_state_query)."""

import pytest

from services import ha_state_query as hsq
from services.ha_state_query import (
    HAStateQuery,
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
    assert q.entity_id == "light.salon"


def test_climate_state_question_detected():
    q = match_ha_state_query("est-ce que la clim est allumee")
    assert q is not None and q.kind == "climate"
    assert q.entity_id == "climate.salon_daikinap71273_clim"


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
