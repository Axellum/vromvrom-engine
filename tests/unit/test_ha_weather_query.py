"""Tests météo locale vocale (services/ha_weather_query)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import ha_weather_query as hwq
from services.ha_weather_query import (
    WeatherHorizon,
    match_weather_query,
    resolve_weather_query,
)

# ── Détection : les phrases mesurées en prod sont de la météo ──

def test_prod_phrase_quel_temps_detecte():
    q = match_weather_query("quel temps fait-il aujourd'hui ?")
    assert q is not None
    assert q.horizon == WeatherHorizon.TODAY


def test_prod_phrase_pleuvoir_demain_detecte():
    q = match_weather_query("est-ce qu'il va pleuvoir demain ?")
    assert q is not None
    assert q.horizon == WeatherHorizon.TOMORROW


def test_variantes_formulations_detectees():
    assert match_weather_query("quel temps il fait") is not None
    assert match_weather_query("quel temps il fera") is not None
    assert match_weather_query("il va pleuvoir") is not None
    assert match_weather_query("il fait combien dehors") is not None
    assert match_weather_query("la météo") is not None


# ── Non-régression : « il fait combien dans le salon » reste intérieure ──

def test_question_temperature_interieure_nest_pas_meteo():
    """Une pièce nommée → question de température intérieure (ha_state_query)."""
    for phrase in (
        "il fait combien dans le salon ?",
        "il fait combien dans la chambre ?",
        "il fait combien dans la cuisine ?",
        "il fait combien dans le chevet ?",
    ):
        assert match_weather_query(phrase) is None, phrase


def test_phrase_non_meteo_renvoie_none():
    assert match_weather_query("raconte une blague") is None
    assert match_weather_query("quelle heure est il") is None
    assert match_weather_query("allume la lumière du salon") is None


# ── Résolution « maintenant » depuis les attributs simulés ──

@pytest.mark.asyncio
async def test_resolve_maintenant(monkeypatch):
    async def _fake_state(_entity):
        return {
            "state": "partlycloudy",
            "attributes": {
                "temperature": 26.2,
                "apparent_temperature": 32.6,
                "temperature_unit": "°C",
            },
        }
    monkeypatch.setattr(hwq, "read_ha_state", _fake_state)
    reply = await resolve_weather_query("quel temps fait-il")
    assert reply is not None
    assert "26 degrés" in reply          # la température lue est dans la phrase
    assert "partiellement nuageux" in reply
    # Écart > 3 °C → ressenti ajouté ; aucun mot inventé (pas de location, pas de %)
    assert "Ressenti 33 degrés" in reply
    assert "localisation" not in reply


@pytest.mark.asyncio
async def test_resolve_maintenant_sans_ressenti(monkeypatch):
    """Écart apparent ≤ 3 °C → pas de ressenti (phrase courte)."""
    async def _fake_state(_entity):
        return {
            "state": "sunny",
            "attributes": {"temperature": 24.0, "apparent_temperature": 25.0},
        }
    monkeypatch.setattr(hwq, "read_ha_state", _fake_state)
    reply = await resolve_weather_query("il fait combien dehors")
    assert reply is not None
    assert "24 degrés" in reply
    assert "ensoleillé" in reply
    assert "Ressenti" not in reply


# ── Résolution « demain » depuis un service_response simulé ──

class _FakeDate(date):
    """`date.today()` figé au 17/08/2026 pour rendre « demain » déterministe."""

    @classmethod
    def today(cls):
        return cls(2026, 8, 17)


def _async_forecast(value):
    """Retourne une fonction async renvoyant `value` (monkeypatch de lecture)."""
    async def _fake():
        return value
    return _fake


@pytest.mark.asyncio
async def test_resolve_demain(monkeypatch):
    # Prévision au format réel de `weather.get_forecasts?return_response`.
    forecast = [
        {
            "datetime": "2026-08-17T00:00:00+00:00",
            "condition": "cloudy",
            "temperature": 27.4,
            "templow": 22.4,
            "precipitation": 0.0,
            "humidity": 90,
        },
        {
            "datetime": "2026-08-18T00:00:00+00:00",
            "condition": "sunny",
            "temperature": 28.5,
            "templow": 20.2,
            "precipitation": 0.1,
            "humidity": 95,
        },
    ]
    monkeypatch.setattr(hwq, "date", _FakeDate)
    monkeypatch.setattr(hwq, "_get_daily_forecast", _async_forecast(forecast))
    reply = await resolve_weather_query("est-ce qu'il va pleuvoir demain ?")
    assert reply is not None
    assert "Demain" in reply
    assert "ensoleillé" in reply          # condition du 18/08 traduite
    assert "28 degrés" in reply           # température du 18/08 lue
    assert "10% de risques de précipitations" in reply  # 0.1 → 10 %


@pytest.mark.asyncio
async def test_resolve_demain_absente_renvoie_none(monkeypatch):
    """Prévision absente pour le jour demandé → None (cascade continue)."""
    monkeypatch.setattr(hwq, "date", _FakeDate)
    # Aucune entrée pour le 18/08.
    forecast = [
        {
            "datetime": "2026-08-17T00:00:00+00:00",
            "condition": "cloudy",
            "temperature": 27.4,
            "templow": 22.4,
        },
    ]
    monkeypatch.setattr(hwq, "_get_daily_forecast", _async_forecast(forecast))
    assert await resolve_weather_query("quel temps il fera demain ?") is None


# ── Lecture en échec → None (pas de valeur de repli) ──

@pytest.mark.asyncio
async def test_resolve_lecture_en_echec_renvoie_none(monkeypatch):
    async def _fake_state(_entity):
        return None  # HA muet / entité absente
    monkeypatch.setattr(hwq, "read_ha_state", _fake_state)
    assert await resolve_weather_query("quel temps fait-il ?") is None


@pytest.mark.asyncio
async def test_resolve_forecast_en_echec_renvoie_none(monkeypatch):
    monkeypatch.setattr(hwq, "date", _FakeDate)
    monkeypatch.setattr(hwq, "_get_daily_forecast", _async_forecast(None))
    assert await resolve_weather_query("est-ce qu'il va pleuvoir demain ?") is None


@pytest.mark.asyncio
async def test_resolve_non_meteo_renvoie_none(monkeypatch):
    async def _fake_state(_entity):
        raise AssertionError("read_ha_state ne doit pas être appelé hors météo")
    monkeypatch.setattr(hwq, "read_ha_state", _fake_state)
    assert await resolve_weather_query("il fait combien dans le salon ?") is None


# ── Branchement dans core/vocal_host.handle_discussion ──

@pytest.mark.asyncio
async def test_handle_discussion_route_meteo_vers_ha_weather():
    """Une demande météo est interceptée AVANT le spécialiste web."""
    from core.vocal_host import handle_discussion

    with patch(
        "core.vocal_host._try_zero_llm_ha_weather",
        new_callable=AsyncMock,
        return_value="Il fait 26 degrés, partiellement nuageux.",
    ):
        result = await handle_discussion(
            user_prompt="quel temps fait-il aujourd'hui ?",
            session_id="sess_meteo",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert result.routing_type == "discussion_ha_weather"
    assert result.agents_used == ["ha_weather", "vocal_host"]
    assert result.async_job_id is None
    assert "26 degrés" in result.response_text


@pytest.mark.asyncio
async def test_handle_discussion_meteo_en_echec_continue_vers_web():
    """Lecture HA en échec → None → la cascade repart vers le spécialiste web."""
    from core.vocal_host import handle_discussion

    with patch(
        "core.vocal_host._try_zero_llm_ha_weather",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "core.vocal_jobs.run_vocal_specialist",
        new_callable=AsyncMock,
        return_value="Réponse du spécialiste web.",
    ):
        result = await handle_discussion(
            user_prompt="quel temps fait-il aujourd'hui ?",
            session_id="sess_meteo_fail",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert result.routing_type == "vocal_host_web"
