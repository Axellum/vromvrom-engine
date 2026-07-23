"""Tests clim avancée : nombres en lettres, split mode/température, relatif."""

import aiohttp
import pytest

import services.execute_service as es
from services.execute_service import (
    match_ha_climate_command,
    parse_climate_temperature,
)


# ── Item 1 : nombres en lettres ──

def test_parse_temperature_digits_and_words():
    assert parse_climate_temperature("clim a 24 degres") == 24
    assert parse_climate_temperature("mets la clim a vingt deux degres") == 22
    assert parse_climate_temperature("clim vingt-deux") == 22
    assert parse_climate_temperature("clim vingt et un") == 21
    assert parse_climate_temperature("clim trente et un") == 31
    assert parse_climate_temperature("clim dix huit") == 18
    assert parse_climate_temperature("clim quinze") == 15
    assert parse_climate_temperature("clim trente") == 30


def test_parse_temperature_none_out_of_range():
    assert parse_climate_temperature("clim de 2 degres") is None  # 2 hors plage
    assert parse_climate_temperature("la clim") is None


def test_climate_command_accepts_word_number():
    m = match_ha_climate_command("mets la clim a vingt deux degres")
    assert m is not None
    assert m.service == "climate.set_temperature"
    assert m.service_data["temperature"] == 22


# ── Item 2 : split set_hvac_mode PUIS set_temperature ──

class _FakeResp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""

    async def json(self):
        return {}


class _FakeSession:
    """Enregistre l'ordre des POST HA (url, payload)."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json))
        return _FakeResp(200)


@pytest.mark.asyncio
async def test_set_temperature_with_mode_splits_in_two_calls(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))

    ok, text = await es.execute_ha_service(
        "climate.set_temperature",
        "climate.salon_daikinap71273_clim",
        service_data={"temperature": 22, "hvac_mode": "cool"},
    )
    assert ok
    # 1er appel = set_hvac_mode, 2e = set_temperature SANS hvac_mode.
    assert fake.calls[0][0].endswith("/climate/set_hvac_mode")
    assert fake.calls[0][1]["hvac_mode"] == "cool"
    assert fake.calls[1][0].endswith("/climate/set_temperature")
    assert "hvac_mode" not in fake.calls[1][1]
    assert fake.calls[1][1]["temperature"] == 22
    # La phrase TTS garde le mode (service_data d'origine intact).
    assert "froid" in text and "22" in text


@pytest.mark.asyncio
async def test_set_temperature_without_mode_single_call(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))

    ok, _ = await es.execute_ha_service(
        "climate.set_temperature",
        "climate.salon_daikinap71273_clim",
        service_data={"temperature": 20},
    )
    assert ok
    assert len(fake.calls) == 1
    assert fake.calls[0][0].endswith("/climate/set_temperature")


# ── Item 3 : réglages relatifs ──

import services.ha_climate_control as cc
from services.ha_climate_control import match_climate_relative


def test_match_relative_directions():
    assert match_climate_relative("monte la clim de 2 degres").delta == 2
    assert match_climate_relative("augmente la clim").delta == 1
    assert match_climate_relative("un peu plus chaud la clim").delta == 1
    assert match_climate_relative("baisse la clim de 3").delta == -3
    assert match_climate_relative("la clim un peu plus froid").delta == -1


def test_match_relative_ignores_absolute_and_non_clim():
    assert match_climate_relative("mets la clim a 22 degres") is None
    assert match_climate_relative("monte le volet") is None
    assert match_climate_relative("allume la clim") is None


@pytest.mark.asyncio
async def test_resolve_relative_applies_delta(monkeypatch):
    captured = {}

    async def _fake_state(_entity):
        return {"state": "cool", "attributes": {"temperature": 21}}

    async def _fake_exec(service, entity, service_data=None, **_kw):
        captured["service"] = service
        captured["data"] = service_data
        return True, f"Climatisation réglée sur {service_data['temperature']} degrés."

    monkeypatch.setattr(cc, "read_ha_state", _fake_state)
    monkeypatch.setattr(cc, "execute_ha_service", _fake_exec)

    reply = await cc.resolve_climate_relative("monte la clim de 2 degres")
    assert captured["service"] == "climate.set_temperature"
    assert captured["data"]["temperature"] == 23  # 21 + 2
    assert "23" in reply


@pytest.mark.asyncio
async def test_resolve_relative_clamps_to_max(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "heat", "attributes": {"temperature": 30}}

    async def _fake_exec(service, entity, service_data=None, **_kw):
        return True, f"Climatisation réglée sur {service_data['temperature']} degrés."

    monkeypatch.setattr(cc, "read_ha_state", _fake_state)
    monkeypatch.setattr(cc, "execute_ha_service", _fake_exec)

    reply = await cc.resolve_climate_relative("monte la clim de 5 degres")
    assert "31" in reply  # clamp à 31, pas 35


@pytest.mark.asyncio
async def test_resolve_relative_honest_without_setpoint(monkeypatch):
    async def _fake_state(_entity):
        return {"state": "off", "attributes": {}}  # pas de consigne

    monkeypatch.setattr(cc, "read_ha_state", _fake_state)
    reply = await cc.resolve_climate_relative("monte la clim de 2 degres")
    assert "consigne actuelle" in reply
