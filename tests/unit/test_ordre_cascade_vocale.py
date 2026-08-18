import pytest
from unittest.mock import AsyncMock, patch
from core.vocal_host import handle_discussion

@pytest.mark.asyncio
async def test_cascade_order():
    """
    Vérifie que les court-circuits Zero-LLM sont consultés AVANT les spécialistes.
    On simule les résolveurs pour forcer une réponse à chaque étage.
    """
    
    # Phrases de test
    phrase_ha_cmd = "allume la lumière du salon"
    phrase_ha_state = "la lumière du salon est allumée ?"
    phrase_weather = "quel temps fait-il ?"
    phrase_datetime = "quelle heure est-il ?"
    phrase_calendar = "quand est-ce que je travaille ?"
    phrase_chat = "raconte une histoire"

    # Mock des résolveurs
    with patch("core.vocal_host._try_zero_llm_ha_command", new_callable=AsyncMock) as mock_ha_cmd, \
         patch("core.vocal_host._try_zero_llm_ha_state", new_callable=AsyncMock) as mock_ha_state, \
         patch("core.vocal_host._try_zero_llm_ha_weather", new_callable=AsyncMock) as mock_ha_weather, \
         patch("services.datetime_query.resolve_datetime_query", return_value="Il est 12h00") as mock_dt:

        # Test HA Commande
        mock_ha_cmd.return_value = "Commande exécutée"
        result = await handle_discussion(user_prompt=phrase_ha_cmd, session_id="test", gateway=None, token_tracker=None, fast_path_cache=None)
        assert result.routing_type == "discussion_ha_command"

        # Test HA État (si commande échoue)
        mock_ha_cmd.return_value = None
        mock_ha_state.return_value = "La lumière est allumée"
        result = await handle_discussion(user_prompt=phrase_ha_state, session_id="test", gateway=None, token_tracker=None, fast_path_cache=None)
        assert result.routing_type == "discussion_ha_state"

        # Test HA Météo (si état échoue)
        mock_ha_state.return_value = None
        mock_ha_weather.return_value = "Il fait beau"
        result = await handle_discussion(user_prompt=phrase_weather, session_id="test", gateway=None, token_tracker=None, fast_path_cache=None)
        assert result.routing_type == "discussion_ha_weather"

        # Test Date/Heure (si météo échoue)
        mock_ha_weather.return_value = None
        result = await handle_discussion(user_prompt=phrase_datetime, session_id="test", gateway=None, token_tracker=None, fast_path_cache=None)
        assert result.routing_type == "discussion_datetime"
