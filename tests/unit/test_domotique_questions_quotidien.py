"""
tests/unit/test_domotique_questions_quotidien.py — l'agent « Domotique » répond
aux questions du quotidien (heure, date, météo du moment).

Mesuré en prod le 19/10/2026 (`conversation/process` sur
`conversation.moteur_d_agents_v7_domotique_3`, audit vocal du Deck) : les dix
phrases ci-dessous recevaient « Je n'ai pas compris la commande domotique. »
(`routing_type = ha_mode_failure`, ~1 s de routeur), alors que l'agent
« Discussion » répondait juste. Les résolveurs Zero-LLM n'étaient branchés que
dans le mode Discussion.

Le même jour, le mode Discussion disait « Nous sommes le 19 October 2026. »
(locale anglaise du Deck) et « 190% de risques de précipitations » pour 1,9 mm.

Les tests pilotent la VRAIE route `execute_chat` en mode `ha` ; seules les
lectures HA et l'horloge sont doublées. Aucun réseau, aucun LLM.
"""

import gc
import os
import shutil
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.datetime_query as dtq
import services.ha_state_query as hsq
import services.ha_weather_query as hwq
from api.routes import agents
from core.runtime_db import get_connection, override_db_path
from core.source_router import parse_source
from services.datetime_query import match_datetime_query, resolve_datetime_query

# Lundi 19 octobre 2026, 19 h 22 — l'instant de l'essai en prod.
_INSTANT = datetime(2026, 10, 19, 19, 22)


class _HorlogeFigee(datetime):
    @classmethod
    def now(cls, tz=None):
        return _INSTANT


@pytest.fixture
def temp_runtime_db():
    """Base jetable : `log_vocal_request/response` écrivent réellement."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_runtime.db")
    override_db_path(db_path)
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()
    conn.close()
    yield db_path
    gc.collect()
    default_db = os.path.join(os.path.dirname(os.path.dirname(__file__)), "moteur_runtime.db")
    override_db_path(default_db)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def maison(monkeypatch):
    """HA doublé : météo de prod du 19/10 au soir, aucune autre entité lue,
    horloge figée, routeur interdit (un appel = la phrase est tombée au bout
    de la cascade, comme avant le correctif)."""

    async def _etat(entity_id):
        if entity_id.startswith("weather."):
            return {"state": "rainy", "attributes": {"temperature": 11.0, "apparent_temperature": 10.9}}
        return None

    monkeypatch.setattr(hwq, "read_ha_state", _etat)
    monkeypatch.setattr(hsq, "read_ha_state", _etat)
    monkeypatch.setattr(dtq, "datetime", _HorlogeFigee)
    routeur = MagicMock()
    routeur.analyze_request = AsyncMock(side_effect=AssertionError("routeur atteint"))
    monkeypatch.setattr("core.app_state.AppState.get_shared_router", MagicMock(return_value=routeur))
    executer = AsyncMock(return_value=(True, "C'est fait."))
    monkeypatch.setattr(agents, "execute_ha_service", executer)
    return executer


def _corps(phrase):
    return agents.ExecuteRequestBody(
        user_prompt=phrase,
        source={"type": "voice", "mode": "ha", "tts_enabled": True, "device_id": "tab5-main"},
    )


# ── Les dix phrases mesurées en prod, de bout en bout dans la route ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase, attendu, agent",
    [
        ("Quelle heure est-il ?", "Il est 19 heures 22.", "ha_datetime"),
        ("Il est quelle heure ?", "Il est 19 heures 22.", "ha_datetime"),
        ("Tu as l'heure ?", "Il est 19 heures 22.", "ha_datetime"),
        ("Quelle est la date ?", "Nous sommes le lundi 19 octobre 2026.", "ha_datetime"),
        ("On est quel jour ?", "Nous sommes le lundi 19 octobre 2026.", "ha_datetime"),
        ("Quel jour sommes-nous ?", "Nous sommes le lundi 19 octobre 2026.", "ha_datetime"),
        ("Quel temps fait-il ?", "Il fait 11 degrés, pluvieux.", "ha_weather"),
        ("Il fait combien dehors ?", "Il fait 11 degrés, pluvieux.", "ha_weather"),
        ("Quelle est la météo ?", "Il fait 11 degrés, pluvieux.", "ha_weather"),
    ],
)
async def test_domotique_repond_aux_questions_du_quotidien(temp_runtime_db, maison, phrase, attendu, agent):
    result = await agents.execute_chat(_corps(phrase), _auth=None)
    assert result["response"] == attendu
    assert result["agents_used"] == [agent]
    maison.assert_not_called()  # aucune action domotique déclenchée


@pytest.mark.asyncio
async def test_domotique_pleuvoir_lit_la_prevision_en_millimetres(temp_runtime_db, maison, monkeypatch):
    """« Est-ce qu'il va pleuvoir ? » : prévision de demain, hauteur en mm."""

    class _Aujourdhui(hwq.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 19)

    prevision = [
        {
            "datetime": "2026-10-19T00:00:00+00:00",
            "condition": "rainy",
            "temperature": 12.6,
            "templow": 7.0,
            "precipitation": 4.6,
        },
        {
            "datetime": "2026-10-20T00:00:00+00:00",
            "condition": "rainy",
            "temperature": 17.5,
            "templow": 8.1,
            "precipitation": 1.9,
        },
    ]
    monkeypatch.setattr(hwq, "date", _Aujourdhui)
    monkeypatch.setattr(hwq, "_get_daily_forecast", AsyncMock(return_value=prevision))
    result = await agents.execute_chat(_corps("Est-ce qu'il va pleuvoir ?"), _auth=None)
    assert result["response"] == "Demain, pluvieux, 18 degrés au plus bas 8. 1,9 millimètre de précipitations."
    assert result["agents_used"] == ["ha_weather"]


# ── Les commandes gardent la priorité ───────────────────────────────────────


@pytest.mark.asyncio
async def test_une_commande_passe_toujours_avant(temp_runtime_db, maison):
    result = await agents.execute_chat(_corps("allume la lumière du salon"), _auth=None)
    maison.assert_awaited_once()
    assert maison.await_args.args[:2] == ("light.turn_on", "light.living_room")
    assert result["agents_used"] == ["ha_command"]


@pytest.mark.asyncio
async def test_une_question_d_etat_passe_toujours_avant(temp_runtime_db, monkeypatch):
    """« il fait combien dans le salon » reste une lecture de capteur, pas la météo."""

    async def _etat(entity_id):
        if entity_id.startswith("weather."):
            raise AssertionError("météo lue pour une question de pièce")
        return {"state": "25.0", "attributes": {"unit_of_measurement": "°C"}}

    monkeypatch.setattr(hsq, "read_ha_state", _etat)
    monkeypatch.setattr(hwq, "read_ha_state", _etat)
    result = await agents.execute_chat(_corps("Quelle est la température dans le salon ?"), _auth=None)
    assert result["agents_used"] == ["ha_state_query"]
    assert "salon" in result["response"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "mets la météo",  # verbe d'action : cascade d'avant
        "ferme quand il pleut",
        "éteins dans un quart d'heure",
        "raconte une histoire",  # pas une question du quotidien
        "Cette échelle là, à cet échalas ?",  # bruit de télévision transcrit en prod
        "159 jours, 01h46 et 40 secondes, passer d'un quart d'heure à 11 jours.",
    ],
)
async def test_l_etage_quotidien_laisse_passer_le_reste(maison, phrase):
    src = parse_source({"type": "voice", "mode": "ha"})
    assert await agents._try_ha_everyday_shortcut(_corps(phrase), src, "sess") is None


# ── Détection : ni trop, ni trop peu ────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase, attendu",
    [
        ("Quelle heure est-il ?", "time"),
        ("quel heure il est", "time"),  # « quel » sans e : transcription réelle
        ("C'est quelle heure ?", "time"),
        ("Donne-moi l'heure", "time"),
        ("Quelle date sommes-nous ?", "date"),
        ("On est le combien ?", "date"),
        ("Aujourd'hui on est quel jour ?", "date"),
        ("Quel jour on est aujourd'hui ?", "date"),
        ("C'est quoi la date ?", "date"),
        ("Quel jour de la semaine on est ?", "date"),
        # Ce qui n'est PAS l'heure ou la date du moment
        ("je suis heureux", None),
        ("passer d'un quart d'heure à 11 jours", None),
        ("À quelle heure ferme la pharmacie ?", None),
        ("Le train est à l'heure ?", None),
        ("C'est l'heure du goûter", None),
        ("C'est quel jour la fête des mères ?", None),
        ("Quel jour est-ce que le magasin ouvre ?", None),
        ("À quelle heure je commence demain ?", None),  # agenda
        ("raconte une blague", None),
    ],
)
def test_detection(phrase, attendu):
    assert match_datetime_query(phrase) == attendu


def test_heure_au_singulier_et_heure_pile():
    assert resolve_datetime_query("quelle heure est-il", now=datetime(2026, 10, 19, 1, 5)) == "Il est 1 heure 05."
    assert resolve_datetime_query("quelle heure est-il", now=datetime(2026, 10, 19, 0, 30)) == "Il est 0 heure 30."
    assert resolve_datetime_query("quelle heure est-il", now=datetime(2026, 10, 19, 15, 0)) == "Il est 15 heures."


def test_date_en_francais_quelle_que_soit_la_locale():
    """Noms écrits par le module, pas par `strftime` (locale anglaise du Deck)."""
    assert resolve_datetime_query("quelle est la date", now=datetime(2026, 11, 1, 9, 0)) == (
        "Nous sommes le dimanche 1er novembre 2026."
    )
    assert resolve_datetime_query("on est quel jour", now=datetime(2026, 8, 15, 9, 0)) == (
        "Nous sommes le samedi 15 août 2026."
    )
