"""Tests unitaires — vocal_host + spécialistes Sprint C."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.vocal_host import VocalIntent, classify_vocal_intent, handle_discussion


def test_classify_chat_default():
    intent, score = classify_vocal_intent("raconte une blague courte")
    assert intent == VocalIntent.CHAT
    assert score == 0.0


def test_classify_web_meteo():
    intent, score = classify_vocal_intent("Quelle météo demain à Paris ?")
    assert intent == VocalIntent.WEB
    assert score > 0


def test_classify_web_dernier_modele():
    intent, score = classify_vocal_intent("Quel est le dernier modèle sorti de Gemini ?")
    assert intent == VocalIntent.WEB
    assert score > 0


def test_classify_calendar():
    intent, _ = classify_vocal_intent("Qu'est-ce que j'ai au calendrier demain matin ?")
    assert intent == VocalIntent.CALENDAR


def test_classify_files():
    intent, _ = classify_vocal_intent("Cherche le document pdf sur le drive")
    assert intent == VocalIntent.FILES


def test_classify_deep():
    intent, _ = classify_vocal_intent("Fais une analyse détaillée de mon installation domotique")
    assert intent == VocalIntent.DEEP


# ─────────────────────────────────────────────────────────────
# [#T351] Reconnaissance des demandes météo / agenda formulées
# naturellement (sans le mot-clé attendu), mesurées en prod via
# vocal_audit_log, + non-régression sur bavardage et commandes HA.
# ─────────────────────────────────────────────────────────────

# Phrases réelles du vocal_audit_log : aucune ne contient « météo »
# ni « agenda », pourtant l'outil (web / calendrier) existe.
_REAL_METEO_AGENDA_PHRASES = [
    "Quand est-ce que je travaille",
    "Quel temps il fait demain ?",
    "C'est quand la prochaine fois que je travaille",
]


@pytest.mark.parametrize(
    "phrase, attendu",
    [
        ("Quand est-ce que je travaille", VocalIntent.CALENDAR),
        ("Quel temps il fait demain ?", VocalIntent.WEB),
        ("C'est quand la prochaine fois que je travaille", VocalIntent.CALENDAR),
        # Variantes du corpus #T351
        ("est-ce qu'il va pleuvoir", VocalIntent.WEB),
        ("il fait combien dehors", VocalIntent.WEB),
        ("je bosse quand", VocalIntent.CALENDAR),
    ],
)
def test_classify_formulations_naturelles(phrase, attendu):
    """#T351 — les demandes naturelles vont vers l'intention qui possède l'outil."""
    intent, _ = classify_vocal_intent(phrase)
    assert intent == attendu


@pytest.mark.parametrize(
    "phrase",
    [
        "raconte-moi une blague",
        "comment tu vas",
    ],
)
def test_classify_bavardage_reste_chat(phrase):
    """#T351 — le bavardage ordinaire ne doit pas être détourné vers web/calendrier."""
    assert classify_vocal_intent(phrase)[0] == VocalIntent.CHAT


@pytest.mark.parametrize(
    "phrase",
    [
        "allume la lumière du salon",
        "allume la lumière ce soir",
    ],
)
def test_classify_commande_ha_ne_part_pas_calendrier_ni_web(phrase):
    """#T351 — une commande domotique ne part NI au calendrier NI au web.

    « ce soir » a été retiré de _CALENDAR_MARKERS : trop court, il détournait
    « allume la lumière ce soir » vers le calendrier.
    """
    intent, _ = classify_vocal_intent(phrase)
    assert intent == VocalIntent.CHAT


def test_classify_est_purement_local():
    """#T351 — la classification d'intention ne fait AUCUN appel de gateway.

    C'est une fonction déterministe à base de mots-clés : aucun aller-retour
    LLM avant la classification (88 % des réponses vocales sortent en < 1 s).
    """
    with patch("core.vocal_host._run_sync_discussion") as mock_discussion, \
         patch("core.vocal_host._try_zero_llm_ha_command") as mock_ha, \
         patch("core.vocal_host._try_zero_llm_ha_state") as mock_state:
        for phrase in _REAL_METEO_AGENDA_PHRASES + [
            "raconte-moi une blague",
            "allume la lumière du salon",
        ]:
            classify_vocal_intent(phrase)
    mock_discussion.assert_not_called()
    mock_ha.assert_not_called()
    mock_state.assert_not_called()


def test_files_prefers_gmail():
    from core.vocal_jobs import _files_prefers_gmail

    assert _files_prefers_gmail("Cherche dans mes emails récents") is True
    assert _files_prefers_gmail("Liste les fichiers pdf sur le drive") is False


def test_build_gmail_query_extracts_keywords():
    from core.vocal_jobs import _build_gmail_query

    q = _build_gmail_query("Cherche le mail de Google concernant la facture")
    assert "google" in q or "facture" in q


@pytest.mark.asyncio
async def test_web_specialist_uses_direct_grounding():
    from core.vocal_jobs import run_web_specialist

    with patch(
        "core.vocal_jobs._call_gemini_search_grounding",
        new_callable=AsyncMock,
        return_value="Demain à Paris, 24 degrés et ensoleillé.",
    ):
        text = await run_web_specialist(
            "Quelle météo demain à Paris ?",
            session_id="test_web_direct",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert "24" in text or "Paris" in text


@pytest.mark.asyncio
async def test_web_specialist_uses_grounding():
    from core.vocal_jobs import run_web_specialist

    with patch(
        "core.vocal_jobs._call_gemini_search_grounding",
        new_callable=AsyncMock,
        return_value=None,
    ):
        mock_provider = MagicMock()
        mock_provider.search_grounding_available = True
        mock_provider.generate.return_value = "Demain à Paris, il fera 22 degrés avec un ciel dégagé."

        mock_gateway = MagicMock()
        mock_gateway._get_raw_provider.return_value = mock_provider

        text = await run_web_specialist(
            "Quelle météo demain à Paris ?",
            session_id="test_web",
            gateway=mock_gateway,
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert "22" in text or "Paris" in text
    mock_provider.generate.assert_called_once()
    _, kwargs = mock_provider.generate.call_args
    assert kwargs.get("use_search_grounding") is True


@pytest.mark.asyncio
async def test_calendar_specialist_oauth_error():
    from core.vocal_jobs import run_calendar_specialist

    with patch("tools.google_workspace.get_calendar_events", return_value="Erreur: OAuth2 Google non configuré."):
        text = await run_calendar_specialist(
            "Qu'est-ce que j'ai demain ?",
            session_id="test_cal",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert "calendrier" in text.lower() or "oauth" in text.lower()


@pytest.mark.asyncio
async def test_calendar_specialist_summarizes_events():
    from core.vocal_jobs import run_calendar_specialist

    events = "📅 1 événement(s) à venir :\n  1. Réunion équipe | 2026-07-16 → 2026-07-16"
    with patch("tools.google_workspace.get_calendar_events", return_value=events):
        with patch(
            "core.vocal_jobs._summarize_data_for_tts",
            new_callable=AsyncMock,
            return_value="Demain tu as une réunion équipe.",
        ) as mock_sum:
            text = await run_calendar_specialist(
                "Qu'est-ce que j'ai demain ?",
                session_id="test_cal2",
                gateway=MagicMock(),
                token_tracker=MagicMock(),
                fast_path_cache={},
            )
    assert "réunion" in text.lower()
    mock_sum.assert_awaited_once()


@pytest.mark.asyncio
async def test_files_specialist_drive():
    from core.vocal_jobs import run_files_specialist

    drive_data = "📁 1 fichier(s) Drive récent(s) :\n  • rapport.pdf [pdf] (120.0 KB)"
    with patch("tools.google_workspace.list_drive_files", return_value=drive_data):
        with patch(
            "core.vocal_jobs._summarize_data_for_tts",
            new_callable=AsyncMock,
            return_value="Tu as un fichier rapport pdf récent sur Drive.",
        ):
            text = await run_files_specialist(
                "Cherche le document pdf sur le drive",
                session_id="test_files",
                gateway=MagicMock(),
                token_tracker=MagicMock(),
                fast_path_cache={},
            )
    assert "pdf" in text.lower() or "rapport" in text.lower()


@pytest.mark.asyncio
async def test_handle_discussion_web_sync():
    """WEB repasse par le spécialiste grounding (sync).

    NB : on utilise une demande web NON météo (« dernier modèle ») : les
    demandes météo sont désormais interceptées en Zero-LLM par
    `services/ha_weather_query` (voir test_ha_weather_query).
    """
    with patch(
        "core.vocal_jobs.run_vocal_specialist",
        new_callable=AsyncMock,
        return_value="Le dernier modèle de Gemini est disponible."
    ):
        result = await handle_discussion(
            user_prompt="Quel est le dernier modèle sorti de Gemini ?",
            session_id="sess_web",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert result.async_job_id is None
    assert result.routing_type == "vocal_host_web"
    assert "Gemini" in result.response_text or "modèle" in result.response_text


@pytest.mark.asyncio
async def test_handle_discussion_ha_command_zero_llm():
    with patch(
        "core.vocal_host._try_zero_llm_ha_command",
        new_callable=AsyncMock,
        return_value="Lumière chambre allumée.",
    ):
        result = await handle_discussion(
            user_prompt="Allume la lumière de la chambre",
            session_id="sess_ha",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert result.routing_type == "discussion_ha_command"
    assert "allumée" in result.response_text.lower()
    assert "ha_command" in result.agents_used


@pytest.mark.asyncio
async def test_handle_discussion_async_returns_ack():
    mock_gateway = MagicMock()
    with patch("core.vocal_jobs.create_vocal_job", return_value="vjob_test123"):
        with patch("core.vocal_host.asyncio.create_task") as mock_task:
            result = await handle_discussion(
                user_prompt="Cherche le document pdf sur le drive",
                session_id="sess_async",
                gateway=mock_gateway,
                token_tracker=MagicMock(),
                fast_path_cache={},
            )
    assert result.async_job_id == "vjob_test123"
    assert "instant" in result.response_text.lower()
    assert result.routing_type == "vocal_host_files_async"
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_handle_discussion_chat_sync():
    with patch(
        "core.vocal_host._run_sync_discussion",
        new_callable=AsyncMock,
        return_value="Salut, ça va bien !",
    ):
        result = await handle_discussion(
            user_prompt="Bonjour comment vas-tu ?",
            session_id="sess_chat",
            gateway=MagicMock(),
            token_tracker=MagicMock(),
            fast_path_cache={},
        )
    assert result.routing_type == "discussion_chat"
    assert result.async_job_id is None
    assert "Salut" in result.response_text


# ── [#T365] Le champ horaire du travail doit atteindre le calendrier ──

@pytest.mark.parametrize("phrase", [
    # Formulations mesurées en prod le 17/08, toutes routées `chat` (score 0) et
    # dont 4 recevaient « Je n'ai pas accès à ton agenda » alors que l'accès marche.
    "A quelle heure je commence a travailler demain ?",
    "C'est quand mon prochain jour de repos ?",
    "je finis a quelle heure demain ?",
    "j'ai quoi de prevu ce week-end ?",
    "Quel est mon planning de la semaine ?",
])
def test_formulations_horaires_routees_calendrier(phrase):
    from core.vocal_host import VocalIntent, classify_vocal_intent
    intent, score = classify_vocal_intent(phrase)
    assert intent == VocalIntent.CALENDAR, f"{phrase!r} doit aller au calendrier"
    assert score > 0


@pytest.mark.parametrize("phrase", [
    # Garde-fou : les marqueurs sont volontairement longs pour éviter ceci.
    "je commence a comprendre la relativite",
    "raconte-moi une blague tres courte",
    "c'est quoi un volet roulant ?",
])
def test_pas_de_faux_positif_calendrier(phrase):
    from core.vocal_host import VocalIntent, classify_vocal_intent
    intent, _ = classify_vocal_intent(phrase)
    assert intent != VocalIntent.CALENDAR


def test_consigne_chat_interdit_de_nier_agenda_et_domotique():
    """
    Le chat ne voit pas les autres chemins et niait des capacités réelles :
    « je n'ai pas accès à ton agenda », « je n'ai pas la capacité de contrôler
    le Tab 5 » — alors que 29 commandes HA ont abouti en 30 jours.
    """
    from core.vocal_host import _INTENT_SUFFIX, VocalIntent
    consigne = _INTENT_SUFFIX[VocalIntent.CHAT]
    assert "JAMAIS" in consigne
    assert "agenda" in consigne and "domotique" in consigne
