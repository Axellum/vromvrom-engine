"""
tests/unit/test_outil_comptes.py — l'outil d'état des comptes (#T320).

Mesuré le 12/08/2026, après #T319 : « Il me reste combien de crédit chez Anthropic et
chez Gemini ? » traversait tout le moteur — routage `accounts`, raccourci vers
l'Executor, boucle ReAct, **99 secondes** — pour finir sur un
`mcp_filesystem_list_directory` de la racine du dépôt.

L'agent listait des fichiers parce qu'**aucun des 23 outils enregistrés ne savait
répondre**. La donnée existait pourtant déjà côté serveur (`get_quota_summary()` et la
table `token_usage`), mais n'était exposée à aucun agent.

Après correction, même requête en réel : **26,7 s**, catégorie `accounts`, outil
`get_account_status` appelé, et une réponse chiffrée (Anthropic ok, 6 clés Gemini
gratuites + 1 payante, DeepSeek 14,26 $, dépense 30 j à 0 $).

Ce module de test vérifie le contrat de l'outil sans toucher ni à la base de
production, ni au réseau : les deux sources sont injectées.
"""

from unittest.mock import patch

import pytest

import tools.comptes as comptes

RESUME_TYPE = {
    "global_status": "ok",
    "keys_count": 3,
    "keys": [
        {"api_key_id": "GEMINI_API_KEY", "provider_id": "gemini_free",
         "project_name": "moteur-ia-free", "saturation_pct": 0.2,
         "external_status": "ok", "external_balance_usd": None},
        {"api_key_id": "ANTHROPIC_API_KEY", "provider_id": "anthropic",
         "project_name": "", "saturation_pct": 91.5,
         "external_status": "ok", "external_balance_usd": 12.34},
        {"api_key_id": "DEEPSEEK_API_KEY", "provider_id": "deepseek",
         "project_name": "", "saturation_pct": 5.0,
         "external_status": "ok", "external_balance_usd": 3.21},
    ],
}


class _FausseConnexion:
    """Connexion SQLite minimale : renvoie des dépenses fixes."""

    def __init__(self, lignes, total):
        self._lignes, self._total = lignes, total

    def execute(self, sql, params=()):
        return self

    def fetchall(self):
        return self._lignes

    def fetchone(self):
        return (self._total,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _appeler(provider="", lignes=None, total=0.0, resume=RESUME_TYPE):
    lignes = lignes if lignes is not None else []
    with patch("core.models_db.get_quota_summary", return_value=resume), \
         patch("core.runtime_db.get_connection",
               return_value=_FausseConnexion(lignes, total)):
        return comptes.get_account_status(provider)


# ── Le cas de production ─────────────────────────────────────────────────────

def test_repond_a_la_question_du_credit_restant():
    """La demande exacte du 12/08 : le solde doit apparaître, chiffré."""
    res = _appeler()
    assert "ANTHROPIC_API_KEY" in res
    assert "12.34 $" in res
    assert "GEMINI_API_KEY" in res


def test_filtre_par_fournisseur():
    """« chez Anthropic » ne doit pas noyer la réponse sous 21 clés."""
    res = _appeler("anthropic")
    assert "ANTHROPIC_API_KEY" in res
    assert "DEEPSEEK_API_KEY" not in res
    assert "1 clé(s) affichée(s) sur 3" in res


def test_filtre_insensible_a_la_casse():
    assert "ANTHROPIC_API_KEY" in _appeler("AnThRoPiC")


def test_filtre_sans_correspondance_guide_l_agent():
    """Un filtre vide ne doit pas rendre une réponse muette : il oriente."""
    res = _appeler("fournisseur-inexistant")
    assert "Aucune clé d'API ne correspond" in res
    assert "sans argument" in res


# ── Les clés sous tension remontent en tête ─────────────────────────────────

def test_cle_saturee_signalee_en_tete():
    """91,5 % > seuil : la clé doit être signalée AVANT le détail complet."""
    res = _appeler()
    assert "⚠️" in res
    assert res.index("⚠️") < res.index("Détail par clé")
    assert f"{comptes.SEUIL_SATURATION_ALERTE:.0f} %" in res


def test_aucune_alerte_si_tout_est_calme():
    calme = {**RESUME_TYPE, "keys": [RESUME_TYPE["keys"][0], RESUME_TYPE["keys"][2]]}
    assert "⚠️" not in _appeler(resume=calme)


# ── La dépense réelle ───────────────────────────────────────────────────────

def test_depense_reelle_par_modele():
    res = _appeler(lignes=[("claude-opus-4-8", 0.6665, 1)], total=0.6665)
    assert "0.6665 $" in res
    assert "claude-opus-4-8" in res
    assert "1 appel(s)" in res


def test_aucun_appel_payant_est_une_reponse_pas_un_vide():
    """Un total à zéro est un résultat légitime — il doit se lire comme tel."""
    res = _appeler(lignes=[], total=0.0)
    assert "0.0000 $" in res
    assert "tiers gratuits" in res


# ── Robustesse : l'outil ne doit jamais faire tomber la boucle ReAct ────────

def test_quotas_indisponibles_ne_leve_pas():
    with patch("core.models_db.get_quota_summary", side_effect=RuntimeError("bdd HS")), \
         patch("core.runtime_db.get_connection", return_value=_FausseConnexion([], 0.0)):
        res = comptes.get_account_status()
    assert "Aucune donnée de quota" in res
    assert "Dépense réelle" in res, "la dépense doit rester lisible malgré l'échec des quotas"


def test_depense_indisponible_ne_leve_pas():
    with patch("core.models_db.get_quota_summary", return_value=RESUME_TYPE), \
         patch("core.runtime_db.get_connection", side_effect=RuntimeError("verrou")):
        res = comptes.get_account_status()
    assert "ANTHROPIC_API_KEY" in res, "les quotas doivent rester lisibles malgré l'échec de la dépense"
    assert "Dépense indisponible" in res


def test_retourne_toujours_une_chaine():
    """Le ToolRegistry attend une chaîne : jamais None, jamais d'exception."""
    assert isinstance(_appeler(), str)


# ── L'outil est réellement exposé aux agents ───────────────────────────────

def test_outil_enregistre_dans_le_registry():
    """Un outil non enregistré est un outil qui n'existe pas (cf. #T320)."""
    from tools.registry_setup import register_base_tools
    from tools.tool_registry import ToolRegistry

    registry = ToolRegistry()
    register_base_tools(registry, git_safety=False)
    schemas = registry.get_all_schemas("combien de crédit", agent_name="executor")
    noms = {
        s.get("function", {}).get("name") or s.get("name")
        for s in schemas
    }
    assert "get_account_status" in noms
