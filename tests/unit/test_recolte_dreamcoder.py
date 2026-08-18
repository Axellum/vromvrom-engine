"""
tests/unit/test_recolte_dreamcoder.py — Récolte des branches de nuit.

DreamCoder produit des branches `task/*` sur le clone du Deck, qui ne peut pas
les publier (clé de déploiement en lecture seule). `scripts/recolte_dreamcoder.py`
fait la dernière marche depuis le PC. Ces tests figent les deux propriétés qui
comptent : on ne confond pas « rien produit » avec « je n'ai pas pu regarder »,
et on ne ramasse que des branches de tâche.
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

# Le script vit dans scripts/ (pas un paquet importable) : chargement par chemin.
_CHEMIN = Path(__file__).resolve().parents[2] / "scripts" / "recolte_dreamcoder.py"
_spec = importlib.util.spec_from_file_location("recolte_dreamcoder", _CHEMIN)
recolte = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recolte)


# ── Reconnaissance des branches ──────────────────────────────────────────────

@pytest.mark.parametrize("nom", [
    "task/42_1787000000",
    "task/1_1786519469",
])
def test_branche_de_tache_reconnue(nom):
    assert recolte._RE_BRANCHE.match(nom)


@pytest.mark.parametrize("nom", [
    "agent/run_chat_0ab605f939_1786564679",  # branche de session vocale du Deck
    "master",
    "task/abc_1787000000",                   # id non numérique
    "task/42",                               # horodatage manquant
    "feat/task/42_1787000000",               # préfixé : pas une branche de tâche
])
def test_branche_hors_perimetre_ignoree(nom):
    assert recolte._RE_BRANCHE.match(nom) is None


def test_liste_ne_garde_que_les_branches_de_tache():
    """Le clone du Deck porte aussi des branches de session : elles ne se récoltent pas."""
    sortie = "master\nagent/run_chat_0ab605f939_1786564679\ntask/7_1787000123\ntask/9_1787000456"

    with patch.object(recolte, "_ssh_deck", return_value=(0, sortie, "")):
        branches = recolte.lister_branches_taches("h", "c", "/clone", "master")

    assert [b["branche"] for b in branches] == ["task/7_1787000123", "task/9_1787000456"]
    assert [b["task_id"] for b in branches] == [7, 9]


# ── La panne ne se déguise pas en absence de travail ─────────────────────────

def test_deck_injoignable_leve_au_lieu_de_rendre_une_liste_vide():
    """
    Retourner [] ici ferait dire au script « rien produit cette nuit » avec un
    code de sortie 0 — un Deck éteint passerait pour une nuit calme.
    """
    with patch.object(recolte, "_ssh_deck", return_value=(255, "", "Connection refused")):
        with pytest.raises(RuntimeError, match="lecture des branches impossible"):
            recolte.lister_branches_taches("h", "c", "/clone", "master")


def test_code_de_sortie_non_nul_quand_le_deck_ne_repond_pas():
    """Bout du fil : l'échec doit être visible depuis un shell ou un cron."""
    class _Args:
        hote, cle, clone, prod, base = "h", __file__, "/clone", "/prod", "master"
        depot, remote, dry_run = ".", "origin", True

    with patch.object(recolte, "_ssh_deck", return_value=(255, "", "Connection refused")):
        assert recolte.recolter(_Args()) == 1


def test_aucune_branche_est_un_succes():
    """Backlog drainé sans production : ce n'est pas une erreur."""
    class _Args:
        hote, cle, clone, prod, base = "h", __file__, "/clone", "/prod", "master"
        depot, remote, dry_run = ".", "origin", True

    with patch.object(recolte, "_ssh_deck", return_value=(0, "master\n", "")):
        assert recolte.recolter(_Args()) == 0


# ── Corps de PR ──────────────────────────────────────────────────────────────

def test_corps_de_pr_porte_le_contexte_d_execution():
    rapport = {
        "title": "Standardiser query_deepseek sur generate_async",
        "summary": "Deux appels migrés, 4 tests ajoutés.",
        "cost_usd": 0.0123,
        "tokens_used": 8421,
    }
    corps = recolte.corps_de_pr(7, rapport)

    assert "Tâche #7" in corps
    assert "Standardiser query_deepseek" in corps
    assert "$0.0123" in corps
    assert "8421" in corps
    # La CI GitHub est morte (#T382) : la relecture humaine est la seule barrière.
    assert "pytest tests/unit" in corps


def test_corps_de_pr_survit_a_un_rapport_absent():
    """Le rapport vit dans le runtime de prod : il peut manquer sans bloquer la PR."""
    corps = recolte.corps_de_pr(11, {})

    assert "Tâche #11" in corps
    assert "inconnu" in corps
    assert "(pas de résumé)" in corps


# ── Rejouabilité ─────────────────────────────────────────────────────────────

def test_pr_existante_detectee():
    """Relancer la récolte ne doit pas ouvrir une seconde PR sur la même branche."""
    with patch.object(recolte, "_run",
                      return_value=(0, '[{"url":"https://github.com/x/y/pull/1"}]', "")):
        assert recolte.pr_existante(".", "task/7_1787000123") == "https://github.com/x/y/pull/1"


def test_pr_existante_absente():
    with patch.object(recolte, "_run", return_value=(0, "[]", "")):
        assert recolte.pr_existante(".", "task/7_1787000123") is None


def test_pr_existante_tolere_une_sortie_illisible():
    """`gh` absent ou non authentifié ne doit pas casser la récolte."""
    with patch.object(recolte, "_run", return_value=(1, "pas du json", "erreur gh")):
        assert recolte.pr_existante(".", "task/7_1787000123") is None
