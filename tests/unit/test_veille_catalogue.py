"""
tests/unit/test_veille_catalogue.py — Veille modèles & tarifs (out of the box).

Couvre les fonctions pures de `tools/veille_catalogue.py` : normalisation des
noms, parsing des deux sources externes, fusion, détection d'écarts et de
nouveautés. AUCUN appel réseau : l'isolation réseau des tests unitaires est
stricte, les réponses API sont injectées en fixtures.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.veille_catalogue import (
    PREFIXES_VENDORS,
    _barème_depuis_data,
    appliquer_maj_tarifs,
    cles_directes_litellm,
    comparer_tarifs,
    fusionner_sources,
    normaliser_nom,
    nouveaux_openrouter,
    parser_litellm,
    parser_openrouter,
)

# ── Fixtures : formes réelles des deux sources externes ──────────────────────

OPENROUTER_BRUT = {
    "data": [
        {
            "id": "deepseek/deepseek-chat",
            "pricing": {"prompt": "0.00000014", "completion": "0.00000028",
                        "input_cache_read": "0.000000003"},
        },
        {
            "id": "z-ai/glm-4.6",
            "pricing": {"prompt": "0.0000006", "completion": "0.0000022"},
        },
        {
            # Tarifs vides (modèle :free) → écarté du parsing
            "id": "vendor/modele-free",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            # Hors de nos vendors → ignoré par la détection de nouveautés
            "id": "openai/gpt-5",
            "pricing": {"prompt": "0.000005", "completion": "0.000015"},
        },
        {"id": "casse/rien", "pricing": "pas-un-dict"},
    ]
}

LITELLM_BRUT = {
    "deepseek/deepseek-chat": {
        "input_cost_per_token": 1e-7,
        "output_cost_per_token": 2e-7,
    },
    "gemini-2.5-flash": {
        "input_cost_per_token": 3e-7,
        "output_cost_per_token": 2.5e-6,
        "cache_read_input_cost_per_token": 3e-8,
    },
    # Pseudo-entrée sans coût → filtrée
    "sample_spec": {"mode": "chat"},
}


def test_normaliser_nom_retire_prefixe_et_suffixe():
    assert normaliser_nom("deepseek/deepseek-chat") == "deepseek-chat"
    assert normaliser_nom("Gemini-2.5-Flash-Paid") == "gemini-2.5-flash"
    assert normaliser_nom("gemini-cli") == "gemini"
    assert normaliser_nom("  x-ai/grok-4-Free  ") == "grok-4"
    assert normaliser_nom("") == ""


def test_parser_openrouter_convertit_en_usd_par_token():
    parsé = parser_openrouter(OPENROUTER_BRUT)
    chat = parsé["deepseek-chat"]
    assert abs(chat["input"] - 0.14 / 1_000_000) < 1e-15
    assert abs(chat["output"] - 0.28 / 1_000_000) < 1e-15
    assert abs(chat["cache"] - 0.003 / 1_000_000) < 1e-18
    # Cache absent tolerated → None (le barème local retombera sur input)
    assert parsé["glm-4.6"]["cache"] is None
    # Tarifs 0/0 et entrées malformées écartés
    assert "modele-free" not in parsé
    assert "rien" not in parsé


def test_parser_openrouter_tolerant_aux_entrees_invalides():
    assert parser_openrouter({"data": [{"id": None}, "pas-un-dict", None]}) == {}
    assert parser_openrouter("pas-un-dict") == {}


def test_parser_litellm_filtre_les_pseudo_entrees():
    parsé = parser_litellm(LITELLM_BRUT)
    assert "sample_spec" not in parsé
    gemini = parsé["gemini-2.5-flash"]
    assert abs(gemini["input"] - 3e-7) < 1e-18
    assert abs(gemini["cache"] - 3e-8) < 1e-19


def test_fusion_openrouter_gagne_a_modele_egal():
    fusion = fusionner_sources(parser_openrouter(OPENROUTER_BRUT),
                               parser_litellm(LITELLM_BRUT))
    assert fusion["deepseek-chat"]["source"] == "openrouter"
    assert fusion["gemini-2.5-flash"]["source"] == "litellm"


def test_comparer_tarifs_signale_au_dela_du_seuil():
    local = {
        # Écart de +100 % sur l'entrée par rapport à la source externe
        "deepseek-chat": {"input": 0.28 / 1_000_000, "output": 0.28 / 1_000_000,
                          "cache": 0.0},
        # Aligné → aucun signalement
        "glm-4.6": {"input": 0.60 / 1_000_000, "output": 2.20 / 1_000_000,
                    "cache": None},
        # Payant sans référence externe
        "modele-mysterieux": {"input": 1e-6, "output": 2e-6, "cache": None},
        # Gratuit par décision (free tier) → jamais comparé
        "gemini-2.5-flash": {"input": 0.0, "output": 0.0, "cache": 0.0},
    }
    externe = fusionner_sources(parser_openrouter(OPENROUTER_BRUT),
                                parser_litellm(LITELLM_BRUT))
    bilan = comparer_tarifs(local, externe, seuil_pct=5.0)

    assert bilan["sans_reference"] == ["modele-mysterieux"]
    assert len(bilan["ecarts_tarifs"]) == 1
    fiche = bilan["ecarts_tarifs"][0]
    assert fiche["modele"] == "deepseek-chat"
    assert fiche["input"]["variation_pct"] == 100.0
    # Sortie alignée → pas d'entrée output dans la fiche
    assert "output" not in fiche


def test_comparer_tarifs_local_payant_externe_gratuit_non_chiffrable():
    local = {"modele-x": {"input": 1e-6, "output": 0.0, "cache": None}}
    externe = {"modele-x": {"input": 0.0, "output": 0.0, "source": "openrouter"}}
    bilan = comparer_tarifs(local, externe, seuil_pct=5.0)
    assert bilan["ecarts_tarifs"][0]["input"]["variation_pct"] is None


def test_nouveaux_openrouter_limite_aux_vendors_cables():
    deja = {"deepseek-chat"}  # déjà catalogué → pas « nouveau »
    nouveaux = nouveaux_openrouter(OPENROUTER_BRUT, deja)
    assert "deepseek" not in nouveaux
    assert nouveaux["zhipu"] == ["glm-4.6"]
    # openai/gpt-5 : vendor non câblé → jamais signalé
    assert all("gpt-5" not in noms for noms in nouveaux.values())


def test_exclusions_permanentes_deepseek():
    # Décision du 18/08/2026 : les alias deepseek-chat / deepseek-r1 servent
    # les modèles récents facturés au tarif V4-Flash ; la référence externe
    # (legacy) est écartée de l'application mais reste affichée.
    from tools.veille_catalogue import EXCLUSIONS_PERMANENTES, exclusions_effectives

    assert {"deepseek-chat", "deepseek-r1"} <= set(exclusions_effectives())
    avec_cli = exclusions_effectives(("glm-5",))
    assert "glm-5" in avec_cli
    assert avec_cli["glm-5"] == "exclu en ligne de commande"
    assert EXCLUSIONS_PERMANENTES["deepseek-chat"]  # justification documentée


def test_prefixes_vendors_couvrent_des_vendors_cables():
    # Garde-fou : la table de préfixes ne référence que des vendors réels
    from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS

    câblés = set(OPENAI_COMPAT_PROVIDERS) | {
        "gemini_free", "gemini_paid", "anthropic_native",
    }
    assert set(PREFIXES_VENDORS) <= câblés


# ── Phase 2 : priorité direct-vendor, application, vérification ────────────────

LITELLM_DIRECT = {
    # Clé préfixée « deepseek/ » : tarif DIRECT du vendor, prioritaire sur
    # le prix revendeur OpenRouter à la fusion.
    "deepseek/deepseek-chat": {
        "input_cost_per_token": 1.4e-7,
        "output_cost_per_token": 2.8e-7,
    },
}


def test_cles_directes_litellm_repere_les_prefixes_vendors():
    directs = cles_directes_litellm({
        **LITELLM_DIRECT,
        "gemini-2.5-flash": {"input_cost_per_token": 3e-7},
        "sample_spec": {"mode": "chat"},
    })
    assert directs == {"deepseek-chat"}
    assert cles_directes_litellm("pas-un-dict") == set()


def test_fusion_priorite_au_tarif_direct_vendor():
    openrouter = {"deepseek-chat": {"input": 9e-7, "output": 9e-7, "cache": None}}
    litellm = parser_litellm(LITELLM_DIRECT)
    directs = cles_directes_litellm(LITELLM_DIRECT)
    fusion = fusionner_sources(openrouter, litellm, directs)
    assert fusion["deepseek-chat"]["source"] == "litellm"
    assert abs(fusion["deepseek-chat"]["input"] - 1.4e-7) < 1e-18
    # Sans l'information de préfixe, OpenRouter garde la priorité (repli)
    fusion_repli = fusionner_sources(openrouter, litellm)
    assert fusion_repli["deepseek-chat"]["source"] == "openrouter"


DATA_FIXTURE = {
    "subscriptions": [{"name": "Claude Pro", "models": ["claude-opus-4-8"]}],
    "apis": [
        {
            "name": "Gemini Free Tier",
            "rates": {"gemini-2.5-flash": {"input_cost_per_m": 0.0,
                                            "output_cost_per_m": 0.0}},
        },
        {
            "name": "GCP Payant",
            "rates": {
                # Entrée libellée en EUR : l'application doit rester en EUR
                "gemini-2.5-flash-paid": {
                    "input_cost_per_m_eur": 0.28,
                    "output_cost_per_m_eur": 2.70,
                    "cached_input_per_m_eur": 0.07,
                },
            },
        },
        {
            "name": "DeepSeek",
            "rates": {
                "deepseek-chat": {
                    "input_cost_per_m": 0.14,
                    "output_cost_per_m": 0.28,
                    "input_cache_hit_cost_per_m": 0.003,
                    "note": "à conserver tel quel",
                },
            },
        },
        {
            # Collision de casse : deux clés qui normalisent en « minimax-m3 ».
            # `_build_table` ignore la casse (dernier écrivain gagne) : c'est
            # l'entrée « minimax-m3 » (placée après) qui survit au barème.
            "name": "MiniMax",
            "rates": {
                "MiniMax-M3": {"input_cost_per_m": 0.9, "output_cost_per_m": 3.0},
                "minimax-m3": {"input_cost_per_m": 1.2, "output_cost_per_m": 4.0},
            },
        },
    ],
}


def test_barème_depdata_la_collision_préfère_l_entrée_payante():
    barème = _barème_depuis_data(DATA_FIXTURE)
    # Collision « gemini-2.5-flash » (free) / « gemini-2.5-flash-paid » :
    # c'est le tarif payant (converti EUR→USD) qui doit être comparé.
    assert barème["gemini-2.5-flash"]["input"] > 0.0


def test_appliquer_maj_tarifs_usd_et_eur():
    import copy

    from core.pricing import EUR_USD

    data = copy.deepcopy(DATA_FIXTURE)
    fiches = [
        {"modele": "deepseek-chat", "source": "litellm",
         "input": {"local": 0.14e-6, "externe": 0.20e-6, "variation_pct": -30.0}},
        {"modele": "gemini-2.5-flash", "source": "openrouter",
         "input": {"local": 0.30e-6, "externe": 0.25e-6, "variation_pct": 20.0},
         "output": {"local": 3.0e-6, "externe": 2.5e-6, "variation_pct": 20.0}},
        # Non chiffrable : jamais appliqué automatiquement
        {"modele": "deepseek-chat", "source": "openrouter",
         "output": {"local": 0.28e-6, "externe": 0.0, "variation_pct": None}},
        # Absent du JSON : signalé, rien n'est créé
        {"modele": "modele-inexistant", "source": "openrouter",
         "input": {"local": 1e-6, "externe": 2e-6, "variation_pct": -50.0}},
    ]
    journal = appliquer_maj_tarifs(data, fiches)

    statuts = {j["modele"]: j["statut"] for j in journal}
    assert statuts["modele-inexistant"] == "absent_du_json"
    assert statuts["gemini-2.5-flash"] == "applique"

    # deepseek-chat : la fiche 1 (input chiffrable) est appliquée ; la fiche 3
    # (output non chiffrable) n'ajoute rien.
    deepseek = data["apis"][2]["rates"]["deepseek-chat"]
    assert abs(deepseek["input_cost_per_m"] - 0.2) < 1e-12
    assert deepseek["output_cost_per_m"] == 0.28          # axe non signalé, intact
    assert deepseek["input_cache_hit_cost_per_m"] == 0.003  # cache jamais touché
    assert deepseek["note"] == "à conserver tel quel"      # champs tiers intacts

    # Collision de normalisation : c'est l'entrée « -paid » (EUR) qui bouge…
    gcp = data["apis"][1]["rates"]["gemini-2.5-flash-paid"]
    assert abs(gcp["input_cost_per_m_eur"] - 0.25 / EUR_USD) < 2e-6
    assert gcp["cached_input_per_m_eur"] == 0.07            # cache intact
    # … et l'entrée free tier reste strictement à zéro.
    gratuit = data["apis"][0]["rates"]["gemini-2.5-flash"]
    assert gratuit == {"input_cost_per_m": 0.0, "output_cost_per_m": 0.0}
    # Abonnements jamais touchés
    assert data["subscriptions"][0]["models"] == ["claude-opus-4-8"]
    # Aucune clé de modèle créée
    assert all("modele-inexistant" not in api.get("rates", {})
               for api in data["apis"])


def test_appliquer_puis_recomparer_resout_l_ecart():
    import copy

    data = copy.deepcopy(DATA_FIXTURE)
    externe = {"deepseek-chat": {"input": 0.20e-6, "output": 0.28e-6,
                                 "cache": None, "source": "litellm"}}
    avant = comparer_tarifs(_barème_depuis_data(data), externe, seuil_pct=5.0)
    assert any(f["modele"] == "deepseek-chat" for f in avant["ecarts_tarifs"])

    appliquer_maj_tarifs(data, avant["ecarts_tarifs"])
    après = comparer_tarifs(_barème_depuis_data(data), externe, seuil_pct=5.0)
    assert après["ecarts_tarifs"] == []


def test_appliquer_cible_la_cle_survivante_des_collisions_de_casse():
    import copy

    data = copy.deepcopy(DATA_FIXTURE)
    externe = {"minimax-m3": {"input": 0.30e-6, "output": 1.0e-6,
                              "cache": None, "source": "litellm"}}
    avant = comparer_tarifs(_barème_depuis_data(data), externe, seuil_pct=5.0)
    fiche = next(f for f in avant["ecarts_tarifs"] if f["modele"] == "minimax-m3")
    # La comparaison porte sur l'entrée survivante (1.2) et transmet sa clé…
    assert fiche["_cle_json"] == "minimax-m3"

    appliquer_maj_tarifs(data, avant["ecarts_tarifs"])
    bloc = data["apis"][3]["rates"]
    # … donc la mise à jour tombe sur la bonne fiche…
    assert bloc["minimax-m3"]["input_cost_per_m"] == 0.3
    assert bloc["minimax-m3"]["output_cost_per_m"] == 1.0
    # … et l'homonyme en casse différente reste intact.
    assert bloc["MiniMax-M3"] == {"input_cost_per_m": 0.9, "output_cost_per_m": 3.0}
