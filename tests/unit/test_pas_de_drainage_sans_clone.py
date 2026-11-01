"""
tests/unit/test_pas_de_drainage_sans_clone.py — pas de clone dédié, pas de drainage (#T419).

Les 18–19/08, le cycle autonome a tourné sur DEUX machines en parallèle. Sur le
PC, `persistent_agents.dreamcoder_repo_path` était vide — la valeur livrée dans
`config.json` — donc `_dreamcoder_repo_root` retombait sur `_ENGINE_ROOT` : le
moteur a travaillé **directement dans le dépôt de développement d'Axel**,
pendant qu'il y codait.

Bilan mesuré : 34,2 M de tokens (64 % de la dépense des deux jours), 12 branches
poussées sur `origin` que la récolte — câblée sur le Deck — ne pouvait pas voir,
donc jamais relues. Sur ces 12, deux étaient destructrices (`pricing_strategy.json`
812 → 125 nœuds ; banc de régression 11 tests → 4) et une portait une backdoor
d'authentification.

Le danger ne commence pas au commit : `_preparer_slot` ouvre par un
`git checkout master`. Sur un dépôt de travail, ce seul geste arrache la branche
courante de celui qui code. D'où un refus AVANT toute opération Git.

Le chemin reste ouvert — il se déclare, c'est tout.
"""

import os

import pytest

from agents import dreamer_agent as da

# ── Détection du slot en place ──────────────────────────────────────────────

def test_le_depot_du_moteur_est_reconnu():
    """Le repli historique sur `_ENGINE_ROOT` est exactement le cas à fermer."""
    slots = da._slots_dreamcoder({"dreamcoder_repo_path": ""})

    assert da._slots_dans_le_depot_courant(slots) == [os.path.abspath(da._ENGINE_ROOT)]


def test_un_chemin_ecrit_autrement_est_quand_meme_reconnu(tmp_path):
    """
    `.../moteur_agents/./` et `.../moteur_agents` désignent le même dépôt : la
    comparaison se fait sur le chemin absolu normalisé, pas sur la chaîne.
    """
    detourne = os.path.join(da._ENGINE_ROOT, ".", "")

    assert da._slots_dans_le_depot_courant([detourne]) != []


def test_un_clone_dedie_n_est_pas_signale(tmp_path):
    clone = str(tmp_path / "moteur_agents_dc3")

    assert da._slots_dans_le_depot_courant([clone]) == []


def test_seuls_les_slots_en_place_sont_remontes(tmp_path):
    """Une liste mixte ne remonte que le slot fautif."""
    clone = str(tmp_path / "moteur_agents_dc3")

    en_place = da._slots_dans_le_depot_courant([clone, da._ENGINE_ROOT])

    assert en_place == [os.path.abspath(da._ENGINE_ROOT)]


# ── Refus du cycle ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_le_cycle_est_annule_sans_clone_dedie(monkeypatch):
    """
    Le refus doit tomber AVANT `_preparer_slot` : c'est lui qui fait le
    `git checkout master` destructeur.
    """
    appels: list[str] = []

    async def _interdit(*a, **k):
        appels.append("preparer_slot")
        return "master"

    monkeypatch.setattr(da, "_preparer_slot", _interdit)

    resultats = await da._run_dreamcoder_drain_loop({"dreamcoder_repo_path": ""})

    assert resultats["stopped_reason"] == "pas_de_clone_dedie"
    assert resultats["processed"] == 0
    assert appels == [], "aucune opération Git ne doit avoir eu lieu"


@pytest.mark.asyncio
async def test_le_message_dit_quoi_faire(monkeypatch, caplog):
    """Un refus sans mode d'emploi est un refus qu'on contourne au hasard."""
    monkeypatch.setattr(da, "_preparer_slot", lambda *a, **k: None)

    with caplog.at_level("ERROR", logger="dreamer_agent"):
        await da._run_dreamcoder_drain_loop({"dreamcoder_repo_path": ""})

    message = " ".join(r.message for r in caplog.records)
    assert "dreamcoder_repo_paths" in message
    assert da.CLE_DEPOT_COURANT in message


@pytest.mark.asyncio
async def test_la_cle_d_echappement_laisse_passer(monkeypatch):
    """
    Assumer le risque reste possible — explicitement. Le cycle repart alors sur
    son chemin normal (ici : il s'arrête plus loin, faute de provider sain,
    ce qui suffit à prouver qu'il a dépassé la porte).
    """
    async def _slot(*a, **k):
        return "master"

    async def _aucun_provider(*a, **k):
        return {}

    monkeypatch.setattr(da, "_preparer_slot", _slot)
    import core.sante_providers as sp
    monkeypatch.setattr(sp, "sonder_providers", _aucun_provider)
    monkeypatch.setattr(sp, "providers_sains", lambda _d: set())

    resultats = await da._run_dreamcoder_drain_loop({
        "dreamcoder_repo_path": "",
        da.CLE_DEPOT_COURANT: True,
    })

    assert resultats["stopped_reason"] != "pas_de_clone_dedie"


def test_la_config_livree_ne_declare_pas_le_risque():
    """
    `config.json` du dépôt porte `dreamcoder_repo_path: ""` — c'est l'état qui a
    causé l'incident. La clé d'échappement doit y figurer à `false` : un défaut
    explicite se lit, un défaut implicite se subit.
    """
    import json
    from pathlib import Path

    config = json.loads(
        (Path(da._ENGINE_ROOT) / "config.json").read_text(encoding="utf-8"))
    pa = config["persistent_agents"]

    assert pa.get(da.CLE_DEPOT_COURANT) is False
