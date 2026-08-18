"""Tests vocal_tts_cache."""

from core.vocal_tts_cache import (
    _TOOL_DUMP_FALLBACK,
    canonical_text_for_ha_action,
    load_phrase_catalog,
    looks_like_tool_or_code_dump,
    normalize_tts_text,
    resolve_phrase_id,
    sanitize_discussion_tts,
)


def test_normalize_tts_text():
    assert normalize_tts_text("  Lumière du salon allumée. ") == "lumière du salon allumée"


def test_resolve_phrase_id_from_catalog():
    catalog = load_phrase_catalog()
    assert "lum_salon_on" in catalog
    pid = resolve_phrase_id(catalog["lum_salon_on"])
    assert pid == "lum_salon_on"


def test_canonical_text_for_ha_action():
    text = canonical_text_for_ha_action("light.living_room", "light.turn_on")
    assert text == "Lumière du salon allumée."


def test_looks_like_tool_dump_json():
    dump = (
        '{"tool":"tab5-engine","arguments":{"action":"get_models_catalog"}}'
        '{ "status": "success", "models": [] }'
    )
    assert looks_like_tool_or_code_dump(dump) is True
    assert sanitize_discussion_tts(dump) == _TOOL_DUMP_FALLBACK


def test_looks_like_tool_dump_mcp_prose():
    dump = 'We will call the MCP tool search_ha_entities with query "chimini".'
    assert looks_like_tool_or_code_dump(dump) is True
    assert "outils" in sanitize_discussion_tts(dump).lower() or "web" in sanitize_discussion_tts(dump).lower()


def test_sanitize_keeps_french_sentence():
    ok = "Gemini est une famille de modèles Google, mais je n'ai pas la date exacte de la dernière sortie."
    assert looks_like_tool_or_code_dump(ok) is False
    assert "Gemini" in sanitize_discussion_tts(ok)


# ── [#T366] Le LaTeX ne doit pas partir au TTS ──

def test_latex_inline_neutralise():
    """
    Phrase exacte mesurée en prod le 17/08 : « donne-moi la formule de la
    relativité générale » rendait la formule brute, ânonnée symbole par symbole
    par la synthèse vocale.
    """
    brut = (
        "L'equation s'ecrit "
        r"\(G_{\mu\nu} + \Lambda\,g_{\mu\nu} = \frac{8\pi G}{c^{4}}\,T_{\mu\nu}\)"
        " ou G est le tenseur."
    )
    sortie = sanitize_discussion_tts(brut)
    assert "\\" not in sortie and "{" not in sortie
    assert "la formule" in sortie
    # La phrase autour est conservée : on remplace, on ne tronque pas.
    assert "tenseur" in sortie


def test_latex_display_neutralise():
    sortie = sanitize_discussion_tts("Voici : $$E = mc^2$$ tout simplement.")
    assert "$" not in sortie and "la formule" in sortie


def test_prix_en_dollars_non_avale():
    """Garde-fou : « 5 $ » n'est pas du LaTeX et doit survivre intact."""
    sortie = sanitize_discussion_tts("Ce cafe coute 5 $ et le the 3 $ seulement.")
    assert "5 $" in sortie and "3 $" in sortie
    assert "la formule" not in sortie


def test_phrase_normale_inchangee():
    texte = "La clim est allumee en mode froid."
    assert sanitize_discussion_tts(texte) == texte
