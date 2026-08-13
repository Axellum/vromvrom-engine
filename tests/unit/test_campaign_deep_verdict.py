"""tests/unit/test_campaign_deep_verdict.py — Verdict honnête de la campagne (#T286).

La campagne du 11/08 rejouée avec les clés réellement chargées annonçait
6 succès sur 7. Trois de ces « succès » ne répondaient pas à la question :
une réponse vide (#T270) et deux `tool_calls` bruts (#T278, #T280), parce que
`success` était posé à True dès que l'appel ne levait pas d'exception.

Ces tests figent le verdict : seule une vraie réponse textuelle est un succès.
"""

from scripts.test_campaign_deep import _qualifier_reponse


def test_texte_reel_est_un_succes():
    nature, succes = _qualifier_reponse("L'analyse de la tâche montre que...")
    assert (nature, succes) == ("texte", True)


def test_reponse_vide_est_un_echec():
    """Cas #T270 : le modèle n'a rien renvoyé, la campagne comptait un succès."""
    assert _qualifier_reponse("") == ("vide", False)
    assert _qualifier_reponse("   \n  ") == ("vide", False)
    assert _qualifier_reponse(None) == ("vide", False)


def test_tool_call_brut_est_un_echec():
    """Cas #T278/#T280 : le modèle demande un outil au lieu de répondre.

    Aucun outil n'est déclaré par la campagne : cette réponse est un échec, même
    si la cascade la juge « adéquate » — `FallbackProvider._is_response_adequate()`
    (core/llm/providers/deepseek.py) rend True pour tout objet non-`str`.
    """
    reponse = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_gemini_0_1786446231",
                "type": "function",
                "function": {"name": "mcp-tab5-engine:get_engine_status", "arguments": "{}"},
            }
        ],
    }
    assert _qualifier_reponse(reponse) == ("tool_call", False)


def test_un_zero_ou_un_faux_ne_passe_pas_pour_du_texte():
    """Garde-fou : `bool(reponse)` était le test d'origine — 0 et False sont
    des valeurs falsy mais ne sont pas davantage des réponses."""
    assert _qualifier_reponse(0)[1] is False
    assert _qualifier_reponse(False)[1] is False
