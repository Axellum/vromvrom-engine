"""
tests/unit/test_regles_chemins_hors_moteur.py — #T479.

La règle 4 du prompt système affirmait toujours « le code Python du moteur est
dans <racine>, donc `core/` est dans <racine>/core ». Vrai tant que le moteur
ne travaillait que sur lui-même ; faux depuis le registre multi-projet.

Une tâche Tab5 s'exécute dans `00ProjetTab` — un dépôt ESPHome, sans aucun
`core/` Python. On affirmait donc à l'agent l'existence d'un dossier qui n'est
pas là.

Mesuré le 25/08 sur la tâche 122 : l'agent a écrit son rapport sous
`00ProjetTab/Tab5/docs/analyses/`, une racine inventée, le reviewer a refusé,
l'agent a fait `rm -rf` de son propre travail, et la branche est repartie
identique à `main`. **355 242 jetons pour zéro fichier.**

Ces tests figent la propriété : on décrit la racine, jamais une arborescence
qu'on n'a pas vérifiée.
"""

from unittest.mock import patch

import pytest

from agents.executor import _regles_os


def _avec_racine(chemin: str, is_windows: bool = False) -> str:
    with patch("agents.executor._racine_de_travail", return_value=str(chemin)):
        return _regles_os(is_windows)


@pytest.fixture
def depot_moteur(tmp_path):
    """Un dépôt qui a bien un `core/` — le moteur lui-même."""
    racine = tmp_path / "moteur_agents"
    (racine / "core").mkdir(parents=True)
    return racine


@pytest.fixture
def depot_firmware(tmp_path):
    """Un dépôt sans `core/` — le Tab5."""
    racine = tmp_path / "00ProjetTab"
    (racine / "esphome").mkdir(parents=True)
    return racine


def test_le_depot_moteur_garde_sa_description(depot_moteur):
    """Quand `core/` existe vraiment, on peut le nommer : rien ne régresse."""
    regles = _avec_racine(depot_moteur)
    assert str(depot_moteur) in regles
    assert "core" in regles


def test_un_depot_sans_core_ne_se_voit_pas_inventer_un_core(depot_firmware):
    """
    LE test de non-régression de la tâche 122.

    Si la règle réaffirme un `core/` sur un dépôt qui n'en a pas, il tombe.
    """
    regles = _avec_racine(depot_firmware)
    coeur_invente = str(depot_firmware / "core")
    assert coeur_invente not in regles, (
        "la règle nomme un dossier core/ qui n'existe pas dans ce dépôt"
    )


def test_un_depot_sans_core_est_annonce_comme_non_moteur(depot_firmware):
    """
    L'agent doit savoir qu'il est ailleurs, et explorer avant d'écrire.

    Sans cette phrase, il applique ses réflexes de dépôt moteur et invente des
    niveaux intermédiaires — c'est ainsi qu'est né `00ProjetTab/Tab5/docs/`.
    """
    regles = _avec_racine(depot_firmware)
    assert "n'est PAS le moteur" in regles
    assert "Explore" in regles


def test_la_racine_est_toujours_donnee(depot_firmware, depot_moteur):
    """La racine est la seule chose dont on soit sûr : elle ne disparaît jamais."""
    for racine in (depot_firmware, depot_moteur):
        for is_windows in (True, False):
            assert str(racine) in _avec_racine(racine, is_windows)


def test_le_confinement_d_ecriture_reste_annonce(depot_firmware):
    """La frontière d'écriture ne doit pas être perdue au passage."""
    regles = _avec_racine(depot_firmware)
    assert "doit l'etre uniquement sous" in regles


# --- La porte de livrable et la convention du dépôt visé -------------------


class TestPorteLivrableHorsMoteur:
    """
    #T479 — la porte exigeait `docs/analyses/` À LA RACINE.

    C'est la convention du dépôt moteur. `00ProjetTab` n'a pas ce dossier,
    mais il a un `Tab5/` et un `docs/` : l'agent y a rangé son rapport sous
    `Tab5/docs/analyses/` — un chemin cohérent avec ce qu'il voyait, pas une
    invention. La porte l'a refusé pour sa POSITION, pas pour son contenu.

    Mesuré deux fois le 25/08, sur deux hôtes et deux modèles : tâche 122
    (Deck) et tâche 78 (PC), ~1,94 M de jetons pour deux refus.

    Ce qui compte est la convention `docs/analyses/….md`, pas la profondeur
    à laquelle le dépôt la place.
    """

    TEXTE = "[TAB5] À ANALYSER — Inventaire des entités exposées par le firmware"

    @pytest.mark.parametrize("chemin", [
        "Tab5/docs/analyses/2026-08-25_inventaire-entites-tab5.md",  # le cas réel
        "docs/analyses/2026-08-25_rapport.md",                       # convention moteur
        "un/niveau/profond/docs/analyses/r.md",
    ])
    def test_un_rapport_est_accepte_quelle_que_soit_sa_profondeur(self, chemin):
        from agents.dreamer_agent import _livrable_insuffisant
        assert _livrable_insuffisant(self.TEXTE, [chemin]) is None

    @pytest.mark.parametrize("chemin", [
        "docs/rapport.md",        # docs/ mais pas analyses/
        "analyses/rapport.md",    # analyses/ mais pas docs/
        "rapport.md",             # à la racine, hors convention
    ])
    def test_la_porte_refuse_toujours_hors_convention(self, chemin):
        """L'assouplissement porte sur la PROFONDEUR, pas sur la convention."""
        from agents.dreamer_agent import _livrable_insuffisant
        assert _livrable_insuffisant(self.TEXTE, [chemin]) is not None
