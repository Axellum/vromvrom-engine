"""
[#T420] Les chemins de slot DreamCoder se déclarent dans `.env`, pas seulement
dans `config.json`.

Pourquoi ça compte : `config.json` est suivi par git alors que ces chemins sont
machine-locaux. Chaque machine gardait donc un diff local sur un fichier suivi,
qu'un `git pull` ou un `reset --hard` efface — et le garde-fou #T419 rebascule
alors en refus silencieux, cycle autonome à l'arrêt sans que rien ne l'annonce.
"""

import os

import pytest

from agents import dreamer_agent
from agents.dreamer_agent import (
    CLE_ENV_SLOTS,
    _slots_configures,
    _slots_dans_le_depot_courant,
    _slots_dreamcoder,
)


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch):
    """Aucun test ne doit dépendre de l'environnement réel de la machine."""
    monkeypatch.delenv(CLE_ENV_SLOTS, raising=False)


def test_l_env_prime_sur_config_json(monkeypatch):
    monkeypatch.setenv(CLE_ENV_SLOTS, os.pathsep.join(["/tmp/slot_a", "/tmp/slot_b"]))
    config = {"dreamcoder_repo_paths": ["/config/ignore"], "dreamcoder_repo_path": "/config/ignore2"}
    assert _slots_configures(config) == ["/tmp/slot_a", "/tmp/slot_b"]


def test_l_env_accepte_un_seul_chemin(monkeypatch):
    monkeypatch.setenv(CLE_ENV_SLOTS, "/tmp/slot_unique")
    assert _slots_configures({}) == ["/tmp/slot_unique"]


def test_les_espaces_autour_des_chemins_sont_ignores(monkeypatch):
    monkeypatch.setenv(CLE_ENV_SLOTS, f"  /tmp/a {os.pathsep} /tmp/b  {os.pathsep}")
    assert _slots_configures({}) == ["/tmp/a", "/tmp/b"]


def test_env_vide_ou_blanc_retombe_sur_config(monkeypatch):
    """Une variable présente mais vide ne doit pas écraser la config."""
    monkeypatch.setenv(CLE_ENV_SLOTS, "   ")
    assert _slots_configures({"dreamcoder_repo_paths": ["/config/slot"]}) == ["/config/slot"]


def test_la_cle_historique_reste_lue():
    assert _slots_configures({"dreamcoder_repo_path": "/vieux/clone"}) == ["/vieux/clone"]


def test_rien_de_declare_ne_fabrique_pas_de_slot():
    """
    Liste vide = rien de déclaré. C'est ce que le garde-fou #T419 interroge :
    fabriquer un repli ici lui ferait croire à un clone dédié inexistant.
    """
    assert _slots_configures({}) == []
    assert _slots_configures(None) == []


def test_sans_declaration_le_slot_est_le_depot_du_moteur():
    """Comportement historique conservé — et c'est exactement ce que #T419 refuse."""
    slots = _slots_dreamcoder({})
    assert slots == [dreamer_agent._ENGINE_ROOT]
    assert _slots_dans_le_depot_courant(slots) == slots


def test_un_slot_declare_dans_l_env_passe_le_garde_fou(monkeypatch, tmp_path):
    clone = tmp_path / "moteur_agents_dc"
    clone.mkdir()
    monkeypatch.setenv(CLE_ENV_SLOTS, str(clone))
    slots = _slots_dreamcoder({})
    assert slots == [os.path.abspath(str(clone))]
    assert _slots_dans_le_depot_courant(slots) == []


def test_un_meme_clone_deux_fois_ne_donne_qu_un_slot(monkeypatch):
    """Deux slots sur le même dépôt = deux tâches qui s'écrasent au checkout."""
    monkeypatch.setenv(CLE_ENV_SLOTS, os.pathsep.join(["/tmp/slot", "/tmp/slot"]))
    assert _slots_dreamcoder({}) == [os.path.abspath("/tmp/slot")]
