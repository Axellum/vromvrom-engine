"""
tests/unit/test_model_inventory.py — Croisement catalogue ↔ inventaire live (#T243).

Le point sensible n'est pas la lecture du fichier, c'est la **distinction entre
« absent » et « on ne sait pas »**, et le détourage des identifiants : le catalogue
désambiguïse ses ids (`gemini-3.5-flash-paid`, `deepinfra/deepseek-r1`) là où l'API
annonce le nom nu. Sans détourage, une trentaine de modèles seraient signalés absents
à tort — exactement le genre de faux positif qui ferait désactiver un modèle qui marche.

Aucun appel réseau : les instantanés sont construits à la main.
"""

import pytest

from core.model_inventory import _variantes, offert_par_api, resume


def _instantane(**providers):
    return {"genere_le": "2026-08-10T09:00:00+00:00", "providers": providers}


# ── Ignorance vs absence ──────────────────────────────────────────────────────


def test_aucun_instantane_donne_none():
    """Jamais False par défaut : sans inventaire, on ne sait pas."""
    assert offert_par_api("mistral", "mistral-large-latest", None) is None


def test_provider_absent_de_l_instantane_donne_none():
    inst = _instantane(mistral={"statut": "OK", "offerts": ["mistral-large-latest"]})
    assert offert_par_api("cohere", "command-r-latest", inst) is None


@pytest.mark.parametrize("statut", ["SANS_CLE", "HTTP_404", "INJOIGNABLE", "REPONSE_ILLISIBLE"])
def test_provider_muet_donne_none(statut):
    """Un provider qui n'a pas répondu ne dit RIEN sur ses modèles."""
    inst = _instantane(github={"statut": statut, "offerts": []})
    assert offert_par_api("github", "gpt-4o", inst) is None


# ── Cas nominaux ──────────────────────────────────────────────────────────────


def test_modele_offert():
    inst = _instantane(mistral={"statut": "OK", "offerts": ["mistral-large-latest", "codestral-latest"]})
    assert offert_par_api("mistral", "mistral-large-latest", inst) is True


def test_modele_absent_du_listing():
    inst = _instantane(mistral={"statut": "OK", "offerts": ["mistral-large-latest"]})
    assert offert_par_api("mistral", "open-mistral-nemo", inst) is False


# ── Détourage des identifiants de catalogue ───────────────────────────────────


@pytest.mark.parametrize(
    "provider, catalogue_id, offert_api",
    [
        ("gemini_paid", "gemini-3.5-flash-paid", "gemini-3.5-flash"),
        ("gemini_free", "gemini-3.1-flash-lite-free", "gemini-3.1-flash-lite"),
        ("deepinfra", "deepinfra/deepseek-r1", "deepseek-r1"),
        ("dashscope", "dashscope/qwen3-coder-next", "qwen3-coder-next"),
        ("mistral", "Mistral-Large-Latest", "mistral-large-latest"),
    ],
)
def test_alias_de_catalogue_reconnus(provider, catalogue_id, offert_api):
    """Un id désambiguïsé côté catalogue ne doit pas passer pour absent."""
    inst = _instantane(**{provider: {"statut": "OK", "offerts": [offert_api]}})
    assert offert_par_api(provider, catalogue_id, inst) is True


def test_variantes_ne_produit_pas_de_chaine_vide():
    assert "" not in _variantes("deepinfra/", "deepinfra")


def test_detourage_ne_rend_pas_tout_positif():
    """Le détourage ne doit pas devenir un « ça correspond à peu près »."""
    inst = _instantane(mistral={"statut": "OK", "offerts": ["mistral-large-latest"]})
    assert offert_par_api("mistral", "magistral-medium-latest", inst) is False


# ── Résumé ────────────────────────────────────────────────────────────────────


def test_resume_sans_instantane():
    r = resume(None)
    assert r["genere_le"] is None
    assert r["providers_ok"] == 0


def test_resume_compte_les_providers_ok():
    inst = _instantane(
        mistral={"statut": "OK", "offerts": ["a", "b"]},
        github={"statut": "HTTP_404", "offerts": []},
    )
    r = resume(inst)
    assert r["providers_ok"] == 1
    assert r["providers_total"] == 2
    assert r["modeles_recenses"] == 2
    assert r["par_provider"]["github"]["statut"] == "HTTP_404"
