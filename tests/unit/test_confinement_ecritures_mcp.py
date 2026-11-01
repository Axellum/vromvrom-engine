"""
[#T439] Un garde-fou posé sur un seul des deux chemins d'écriture ne garde rien.

`tools/system.py::write_file` refuse d'écrire hors du dépôt de travail d'une
tâche autonome (#T433). Mais les agents disposent AUSSI des outils du serveur MCP
`filesystem`, enraciné sur tout l'espace de travail et sur un partage réseau —
et ceux-là ne passaient par aucun contrôle.

Mesuré le 21/08 sur la tâche 62 : trois agents ont tourné,
`mcp_filesystem_edit_file` a été appelé, et la branche est restée vide. Le
fichier avait bien été écrit — ailleurs.
"""

import os

import pytest

from core.mcp_bridge import _refus_ecriture_mcp_hors_depot
from tools import system as systeme


@pytest.fixture
def racine(tmp_path):
    clone = tmp_path / "moteur_agents_dc"
    clone.mkdir()
    jeton = systeme.definir_racine_travail(str(clone))
    yield clone
    systeme.reinitialiser_racine_travail(jeton)


def test_une_ecriture_dans_le_depot_passe(racine):
    cible = str(racine / "docs" / "analyses" / "rapport.md")
    assert _refus_ecriture_mcp_hors_depot("write_file", {"path": cible}) is None


def test_une_ecriture_hors_du_depot_est_refusee(racine):
    dehors = str(racine.parent / "analyse_egaree.md")
    refus = _refus_ecriture_mcp_hors_depot("write_file", {"path": dehors})
    assert refus is not None
    assert "refusée hors du dépôt de travail" in refus
    assert str(racine) in refus, "le message doit dire OÙ écrire"


def test_edit_file_est_couvert(racine):
    """Le cas RÉEL de la tâche 62 : c'est `edit_file` qui a servi."""
    dehors = str(racine.parent / "00ProjetTab" / "Tab5" / "tab5-hardware.yaml")
    assert _refus_ecriture_mcp_hors_depot("edit_file", {"path": dehors}) is not None


@pytest.mark.parametrize("outil", ["create_directory", "move_file", "copy_file"])
def test_les_autres_outils_d_ecriture_sont_couverts(racine, outil):
    dehors = str(racine.parent / "ailleurs")
    assert _refus_ecriture_mcp_hors_depot(outil, {"path": dehors}) is not None


@pytest.mark.parametrize("outil", ["read_file", "read_text_file", "list_directory", "search_files"])
def test_la_lecture_n_est_jamais_bloquee(racine, outil):
    """Une analyse doit pouvoir LIRE les autres projets (#T425)."""
    dehors = str(racine.parent / "00ProjetTab")
    assert _refus_ecriture_mcp_hors_depot(outil, {"path": dehors}) is None


def test_hors_tache_autonome_rien_n_est_bloque(tmp_path):
    """Sans racine de travail posée (chat interactif), le comportement est intact."""
    assert _refus_ecriture_mcp_hors_depot("write_file", {"path": str(tmp_path / "x.md")}) is None


def test_l_argument_destination_est_aussi_inspecte(racine):
    """`move_file` nomme sa cible `destination`, pas `path`."""
    refus = _refus_ecriture_mcp_hors_depot(
        "move_file",
        {"source": str(racine / "a.md"), "destination": str(racine.parent / "b.md")},
    )
    assert refus is not None


def test_un_outil_inconnu_n_est_pas_bloque(racine):
    """On ne casse pas un serveur MCP tiers par excès de zèle."""
    assert _refus_ecriture_mcp_hors_depot("ha_call_service", {"path": "/ailleurs"}) is None
