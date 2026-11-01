"""
tests/unit/test_routage_dreamcoder_domaines.py — Routage DreamCoder par
historique de réussite (type de tâche × provider).

Vérifie :
- l'extraction du domaine d'une tâche du backlog ;
- le classement par taux de réussite historique (pas par mots-clés) ;
- l'exclusion dure : un modèle à 3 échecs sans aucun succès sur un domaine
  n'y reçoit plus rien, quel que soit son prix ;
- la sélection dans le drainage : historique d'abord, cascade de coût en
  secours, libération de la tâche si personne ne peut la jouer.
"""

import pytest

from agents.dreamer_agent import _choisir_provider_tache, _domaine_de_tache
from core import provider_scorer

# ── Domaine d'une tâche ─────────────────────────────────────────────────

def test_domaine_tache_board():
    assert _domaine_de_tache({"title": "[T381] archi/ihm-v2 - Plomber le canal SSE"}) == "archi/ihm-v2"
    assert _domaine_de_tache({"title": "[T383] vocal - Réparer les marqueurs"}) == "vocal"


def test_domaine_tache_todo_concentre_sur_code_generation():
    # Les TODO sont regroupés sur un domaine unique : l'historique ne doit
    # pas s'émietter par fichier.
    assert _domaine_de_tache({"title": "TODO dans core/x.py — Brancher le scorer"}) == "code_generation"


def test_domaine_tache_generale_par_defaut():
    assert _domaine_de_tache({"title": "Nettoyer les rapports"}) == "general"
    assert _domaine_de_tache({"title": None}) == "general"


# ── Classement par historique ───────────────────────────────────────────

def _stats(modele, wins, losses, elo):
    matches = wins + losses
    return {"model": modele, "elo": elo, "matches": matches,
            "wins": wins, "losses": losses,
            "win_rate": wins / max(1, matches) * 100.0}


def test_exclusion_dure_apres_trois_echecs_sans_succes(monkeypatch):
    # cerebras-free → gpt-oss-120b : 3 échecs sur le vocal, aucun succès.
    # gemini-free   → gemini-3.5-flash-free : 3 succès sur 4.
    monkeypatch.setattr("core.elo_scorer.get_domain_stats", lambda d: [
        _stats("gpt-oss-120b", 0, 3, 1400.0),
        _stats("gemini-3.5-flash-free", 3, 1, 1600.0),
    ])

    ordonnes, exclusions = provider_scorer.classer_providers_pour_tache(
        ["cerebras-free", "gemini-free"], "vocal")

    assert ordonnes == ["gemini-free"]
    assert "cerebras-free" in exclusions
    assert "3 échecs" in exclusions["cerebras-free"]


def test_classement_par_taux_de_reussite_dabord(monkeypatch):
    monkeypatch.setattr("core.elo_scorer.get_domain_stats", lambda d: [
        _stats("open-mistral-nemo", 1, 1, 1700.0),   # 50 %, meilleur Elo
        _stats("deepseek-chat", 2, 0, 1490.0),       # 100 %, Elo plus bas
    ])

    ordonnes, exclusions = provider_scorer.classer_providers_pour_tache(
        ["mistral-free", "deepseek-free"], "domotique")

    # Le taux de réussite prime sur l'Elo et sur le prix.
    assert ordonnes == ["deepseek-free", "mistral-free"]
    assert exclusions == {}


def test_modeles_sans_historique_departages_par_cout(monkeypatch):
    monkeypatch.setattr("core.elo_scorer.get_domain_stats", lambda d: [])

    class _ScorerFactice:
        def __init__(self, noms):
            pass

        def score(self, modele):
            return {"deepseek-chat": 4.0, "open-mistral-nemo": 5.0}.get(modele, 6.0)

    monkeypatch.setattr(provider_scorer, "ProviderScorer", _ScorerFactice)

    ordonnes, exclusions = provider_scorer.classer_providers_pour_tache(
        ["mistral-free", "deepseek-free"], "general")

    assert ordonnes == ["deepseek-free", "mistral-free"]
    assert exclusions == {}


def test_un_seul_succes_ne_condamne_pas(monkeypatch):
    # 1 victoire et 3 défaites ≠ exclusion : la règle exige ZÉRO succès.
    monkeypatch.setattr("core.elo_scorer.get_domain_stats", lambda d: [
        _stats("gpt-oss-120b", 1, 3, 1400.0),
    ])

    ordonnes, exclusions = provider_scorer.classer_providers_pour_tache(
        ["cerebras-free"], "vocal")

    assert ordonnes == ["cerebras-free"]
    assert exclusions == {}


# ── Sélection dans le drainage ──────────────────────────────────────────

class _BudgetGuardFactice:
    def __init__(self, epuises=()):
        self.appels = []
        # [#T409] Providers dont le quota gratuit est crevé. Le routage doit les
        # écarter AVANT de consulter l'historique — la cascade de coût, qui
        # portait seule ce contrôle, n'est plus que le secours.
        self.epuises = set(epuises)

    async def get_available_provider(self, sains=None, exclus=None):
        self.appels.append((sains, exclus))
        return "mistral-free"

    async def quota_gratuit_epuise(self, provider):
        return provider in self.epuises


@pytest.mark.asyncio
async def test_choisir_provider_prefere_lhistorique(monkeypatch):
    monkeypatch.setattr(provider_scorer, "classer_providers_pour_tache",
                        lambda p, d: (["gemini-free"], {}))
    bg = _BudgetGuardFactice()

    provider, raison, exclusions = await _choisir_provider_tache(
        "vocal", bg, {"gemini-free", "mistral-free"}, set())

    assert provider == "gemini-free"
    assert raison == "historique_reussite"
    assert bg.appels == []  # la cascade n'a pas été jouée


@pytest.mark.asyncio
async def test_choisir_provider_replie_sur_la_cascade(monkeypatch):
    monkeypatch.setattr(provider_scorer, "classer_providers_pour_tache",
                        lambda p, d: ([], {}))
    bg = _BudgetGuardFactice()

    provider, raison, _ = await _choisir_provider_tache(
        "general", bg, {"mistral-free"}, set())

    assert provider == "mistral-free"
    assert raison == "cascade_cout"
    assert len(bg.appels) == 1


@pytest.mark.asyncio
async def test_choisir_provider_aucun_candidat_par_historique(monkeypatch):
    # Tous les candidats écartés PAR L'HISTORIQUE : la cascade ne doit pas
    # être rejouée (elle ignorerait le motif), la tâche sera libérée.
    monkeypatch.setattr(
        provider_scorer, "classer_providers_pour_tache",
        lambda p, d: ([], {"mistral-free": "3 échecs sur 'vocal', aucun succès"}))
    bg = _BudgetGuardFactice()

    provider, raison, exclusions = await _choisir_provider_tache(
        "vocal", bg, {"mistral-free"}, set())

    assert provider is None
    assert raison.startswith("historique")
    assert bg.appels == []
    assert "mistral-free" in exclusions


@pytest.mark.asyncio
async def test_un_provider_au_quota_creve_nest_pas_elu(monkeypatch):
    """
    [#T409] Mesuré le 19/08 à 22h18 : les deux tâches du cycle reçoivent
    `gemini-free`, quota du jour déjà consommé. Le plafond corrigé par #T404
    vivait dans la cascade de coût ; l'élection par historique l'ignorait.
    """
    monkeypatch.setattr(
        provider_scorer, "classer_providers_pour_tache",
        lambda candidats, d: ([c for c in candidats], {}))
    bg = _BudgetGuardFactice(epuises={"gemini-free"})

    provider, _raison, exclusions = await _choisir_provider_tache(
        "vocal", bg, {"gemini-free", "mistral-free"}, set())

    assert provider != "gemini-free"
    assert exclusions.get("gemini-free") == "quota gratuit epuise"


@pytest.mark.asyncio
async def test_un_quota_intact_ne_change_rien(monkeypatch):
    monkeypatch.setattr(
        provider_scorer, "classer_providers_pour_tache",
        lambda candidats, d: (["gemini-free"], {}))
    bg = _BudgetGuardFactice()

    provider, raison, _exclusions = await _choisir_provider_tache(
        "vocal", bg, {"gemini-free", "mistral-free"}, set())

    assert provider == "gemini-free"
    assert raison == "historique_reussite"
