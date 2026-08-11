"""
tests/unit/test_budget_baskets.py — Paniers budgétaires du catalogue dynamique (#T246).

Le point verrouillé : le panier "free" doit filtrer sur la colonne `tier`
(économie : local/free), PAS sur `routing_tier` (capacité : leger/moyen/fort).
Aucun modèle du catalogue n'a routing_tier 'free'/'local' — l'ancien filtrage
vidait structurellement le panier, et les outils MCP de recommandation/délégation
répondaient « catalogue free vide » alors qu'il est plein.

Aucun accès base : les deux lecteurs du catalogue sont monkeypatchés.

NB : shim pour forcer le package local `tools` (namespace) face au paquet
site-packages homonyme qui le masque sous Windows (divergence CI/local connue).
"""
import os
import sys
import types

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

import core.mcp_tools.orchestrator as mcp_orchestrator  # noqa: E402
import core.models_db as models_db  # noqa: E402


def _modele(mid, tier, routing_tier, prio):
    return {
        "id": mid, "tier": tier, "routing_tier": routing_tier,
        "cascade_priority": prio, "speciality": "", "recommended_use": "",
    }


# Catalogue factice réaliste : les tiers catalogue et les routing_tiers
# ne coïncident jamais sur 'free'/'local' (c'est le cœur du bug).
_CATALOGUE = [
    _modele("qwen3-coder-next", "local", "leger", 1),
    _modele("gemini-3.1-flash-lite", "free", "leger", 2),
    _modele("gemini-3.5-flash", "free", "moyen", 3),
    _modele("mistral-large-latest", "free", "moyen", 4),
    _modele("claude-sonnet-4-6", "subscription", "fort", 5),
    _modele("deepseek-chat", "paid", "leger", 6),
]


def _faux_get_models_for_tier(routing_tier):
    return [m for m in _CATALOGUE if m["routing_tier"] == routing_tier]


def _faux_get_models_for_catalog_tiers(catalog_tiers):
    return [m for m in _CATALOGUE if m["tier"] in catalog_tiers]


def _patch_lecteurs(monkeypatch):
    monkeypatch.setattr(models_db, "get_models_for_tier", _faux_get_models_for_tier)
    monkeypatch.setattr(models_db, "get_models_for_catalog_tiers", _faux_get_models_for_catalog_tiers)
    monkeypatch.setattr(models_db, "get_model_cost", lambda mid: None)


# ── Le panier free lit la colonne tier, pas routing_tier ─────────────────────


def test_panier_free_renvoie_les_modeles_local_et_free(monkeypatch):
    """Régression #T246 : avant le fix, ce panier revenait toujours vide."""
    _patch_lecteurs(monkeypatch)
    picked = mcp_orchestrator._catalog_models_for_budget("free")
    ids = [m["id"] for m in picked]
    assert ids == ["qwen3-coder-next", "gemini-3.1-flash-lite", "gemini-3.5-flash", "mistral-large-latest"]
    assert all(m["is_free"] for m in picked)


def test_panier_free_ne_passe_pas_par_le_filtre_routing_tier(monkeypatch):
    """Le panier free ne doit JAMAIS interroger get_models_for_tier."""
    appels = []

    def _espion(routing_tier):
        appels.append(routing_tier)
        return _faux_get_models_for_tier(routing_tier)

    monkeypatch.setattr(models_db, "get_models_for_tier", _espion)
    monkeypatch.setattr(models_db, "get_models_for_catalog_tiers", _faux_get_models_for_catalog_tiers)
    monkeypatch.setattr(models_db, "get_model_cost", lambda mid: None)
    mcp_orchestrator._catalog_models_for_budget("free")
    assert appels == []


# ── cheap/best restent sur l'axe capacité (routing_tier) ─────────────────────


def test_panier_cheap_filtre_sur_routing_tier(monkeypatch):
    _patch_lecteurs(monkeypatch)
    picked = mcp_orchestrator._catalog_models_for_budget("cheap")
    ids = [m["id"] for m in picked]
    assert ids == ["qwen3-coder-next", "gemini-3.1-flash-lite", "gemini-3.5-flash", "mistral-large-latest", "deepseek-chat"]


def test_panier_best_filtre_sur_routing_tier_fort(monkeypatch):
    _patch_lecteurs(monkeypatch)
    picked = mcp_orchestrator._catalog_models_for_budget("best")
    assert [m["id"] for m in picked] == ["claude-sonnet-4-6"]


def test_budget_inconnu_replie_sur_cheap(monkeypatch):
    _patch_lecteurs(monkeypatch)
    assert (mcp_orchestrator._catalog_models_for_budget("nimporte_quoi")
            == mcp_orchestrator._catalog_models_for_budget("cheap"))


# ── Ordre, dédoublonnage, budget effectif ────────────────────────────────────


def test_concatenation_de_tiers_reordonnee_par_cascade_priority(monkeypatch):
    """cheap = leger + moyen : l'ordre final suit cascade_priority, pas l'ordre des tiers."""
    _patch_lecteurs(monkeypatch)
    picked = mcp_orchestrator._catalog_models_for_budget("cheap")
    prios = [m for m in picked]
    assert [m["id"] for m in prios] == sorted(
        [m["id"] for m in prios],
        key=lambda mid: next(x["cascade_priority"] for x in _CATALOGUE if x["id"] == mid),
    )


def test_dedoublonnage_si_un_modele_apparait_deux_fois(monkeypatch):
    doublon = _modele("gemini-3.5-flash", "free", "moyen", 3)

    def _lecteur_avec_doublon(routing_tier):
        return [m for m in _CATALOGUE + [doublon] if m["routing_tier"] == routing_tier]

    monkeypatch.setattr(models_db, "get_models_for_tier", _lecteur_avec_doublon)
    monkeypatch.setattr(models_db, "get_models_for_catalog_tiers", _faux_get_models_for_catalog_tiers)
    monkeypatch.setattr(models_db, "get_model_cost", lambda mid: None)
    picked = mcp_orchestrator._catalog_models_for_budget("cheap")
    assert [m["id"] for m in picked].count("gemini-3.5-flash") == 1


def test_effectif_budget_inchange():
    """Le mapping type de tâche → panier n'est pas modifié par #T246."""
    assert mcp_orchestrator._effective_budget("auto", "casual_chat") == "free"
    assert mcp_orchestrator._effective_budget("auto", "architecture") == "best"
    assert mcp_orchestrator._effective_budget("auto", "standard") == "cheap"
    assert mcp_orchestrator._effective_budget("best", "casual_chat") == "best"


def test_describe_basket_distingue_les_deux_axes():
    assert "tier catalogue" in mcp_orchestrator._describe_basket("free")
    assert "routing_tier" in mcp_orchestrator._describe_basket("cheap")
    assert "routing_tier" in mcp_orchestrator._describe_basket("best")
