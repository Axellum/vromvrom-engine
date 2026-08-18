"""
Les marqueurs vocaux doivent matcher le texte RÉEL du STT, qui est accentué.

Défaut mesuré le 17/08 au soir, après le merge de #T365 (PR #328) : les six
marqueurs du champ horaire avaient été écrits sans accent, alors que
`_normalize_prompt` conserve les accents (`core/vocal_host.py`, la classe de
caractères gardés contient explicitement `àâäéèêëïîôùûüç`). Résultat :
« je commence à travailler » ne matchait AUCUN marqueur et repartait en chat.

Le défaut a survécu au merge parce que les tests de #T365 écrivent leurs
phrases sans accent (`tests/unit/test_vocal_host.py:331,334`) — ils validaient
donc une entrée que le STT ne produit pas.

Ce banc ferme le trou dans les deux sens : les formulations accentuées
atteignent le calendrier, et le garde-fou anti-faux-positif de #T365
(« je commence à comprendre » reste du chat) tient toujours.
"""

import unicodedata

import pytest

from core.vocal_host import VocalIntent, _normalize_prompt, classify_vocal_intent

# Formulations réelles, telles qu'un STT français les rend : avec accents.
PHRASES_ACCENTUEES_CALENDRIER = [
    "je commence à travailler demain",
    "à quelle heure je commence à travailler ?",
    "je finis à quelle heure aujourd'hui ?",
    "je termine à quelle heure ?",
    "est-ce que je suis en congé demain ?",
    "j'ai quoi de prévu ce week-end ?",
    "quoi de prévu demain ?",
]


@pytest.mark.parametrize("phrase", PHRASES_ACCENTUEES_CALENDRIER)
def test_les_formulations_accentuees_atteignent_le_calendrier(phrase):
    """Le cas qui échouait : accentué, donc jamais reconnu avant #T383."""
    intent, score = classify_vocal_intent(phrase)
    assert intent is VocalIntent.CALENDAR, (
        f"{phrase!r} doit être routée vers le calendrier, obtenu {intent} "
        f"(score {score})"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        # Les mêmes sans accent : le STT peut aussi produire du texte plat.
        "je commence a travailler demain",
        "je finis a quelle heure aujourd'hui ?",
        "j'ai quoi de prevu ce week-end ?",
    ],
)
def test_les_formulations_sans_accent_marchent_toujours(phrase):
    """#T365 ne doit pas régresser : les deux orthographes sont couvertes."""
    intent, _ = classify_vocal_intent(phrase)
    assert intent is VocalIntent.CALENDAR, f"{phrase!r} doit rester calendrier"


@pytest.mark.parametrize(
    "phrase",
    [
        # Garde-fou de #T365 : « je commence » seul ne doit pas capter le chat.
        "je commence à comprendre la relativité",
        "je commence a comprendre la relativite",
    ],
)
def test_le_garde_fou_anti_faux_positif_tient(phrase):
    """Une phrase de chat qui contient « je commence » reste du chat."""
    intent, _ = classify_vocal_intent(phrase)
    assert intent is not VocalIntent.CALENDAR, (
        f"{phrase!r} ne doit PAS partir au calendrier"
    )


def test_normalize_prompt_conserve_bien_les_accents():
    """
    Fige la cause racine. Si un jour `_normalize_prompt` se met à dépouiller
    les accents, ce test tombe et signale que les doublons de marqueurs
    accentués sont devenus inutiles — au lieu de les laisser pourrir.
    """
    norm = _normalize_prompt("Je commence à travailler")
    assert "à" in norm, "la normalisation ne doit pas dépouiller les accents"
    assert norm == unicodedata.normalize("NFKC", norm), "sortie attendue en NFKC"
