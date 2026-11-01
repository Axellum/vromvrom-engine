"""
test_gouverneur_vivant.py — l'escalade de gouverneur doit viser un modèle qui répond.

D-21 : cerveau PC = Gemini 3.7 Flash medium (`agy --model`). Deck = DeepSeek
V4 Pro (`MOTEUR_DISABLE_CLI_PROVIDERS`). Escalade Deck : glm-5 → reasoner
(V4 Pro est déjà le défaut). Escalade PC : glm-5 → v4-pro → reasoner.
"""

import pytest

from agents import dreamer_agent as da


@pytest.fixture
def eteints(monkeypatch):
    """Déclare l'ensemble des modèles que la sonde a éteints."""
    morts: set[str] = set()

    def faux_lire_sante(model_id: str) -> dict:
        return {"desactive_par_sonde": 1 if model_id in morts else 0}

    import core.model_health_probe as sonde
    monkeypatch.setattr(sonde, "lire_sante", faux_lire_sante)
    return morts


def test_escalade_prend_le_premier_choix_quand_il_repond(eteints):
    """Rien d'éteint : on garde la préférence d'escalade."""
    assert da._premier_gouverneur_vivant(
        da._GOUVERNEURS_ESCALADE, da._gouverneur_par_defaut()) == "glm-5"


def test_escalade_saute_le_modele_eteint(eteints):
    """glm-5 éteint → on passe au suivant (deepseek-reasoner sur la liste courte)."""
    eteints.add("glm-5")

    choisi = da._premier_gouverneur_vivant(
        da._GOUVERNEURS_ESCALADE, da._gouverneur_par_defaut())

    assert choisi != "glm-5"
    assert choisi == "deepseek-reasoner"


def test_escalade_descend_toute_la_liste(eteints):
    """Plusieurs morts d'affilée : on continue jusqu'à trouver un vivant."""
    eteints.update({"glm-5", "deepseek-reasoner"})

    assert da._premier_gouverneur_vivant(
        da._GOUVERNEURS_ESCALADE, da._gouverneur_par_defaut()) == da._gouverneur_par_defaut()


def test_aucun_candidat_vivant_retombe_sur_le_gouverneur_par_defaut(eteints):
    """Tout est éteint : gouverner moins haut vaut mieux que gouverner dans le vide."""
    eteints.update(da._GOUVERNEURS_ESCALADE)

    assert da._premier_gouverneur_vivant(
        da._GOUVERNEURS_ESCALADE, da._gouverneur_par_defaut()) == da._gouverneur_par_defaut()


def test_sante_illisible_ne_bloque_pas_la_tache(monkeypatch):
    """
    Un doute sur la santé ne doit jamais empêcher une tâche de partir : si la
    table est illisible, on considère le modèle comme disponible et on laisse
    la cascade trancher à l'exécution.
    """
    import core.model_health_probe as sonde

    def explose(_model_id: str) -> dict:
        raise RuntimeError("table model_health absente")

    monkeypatch.setattr(sonde, "lire_sante", explose)

    assert da._modele_eteint("glm-5") is False
    assert da._premier_gouverneur_vivant(
        da._GOUVERNEURS_ESCALADE, da._gouverneur_par_defaut()) == "glm-5"


def test_le_defaut_ne_figure_pas_dans_la_liste_d_escalade():
    """
    Garde-fou de cohérence : le repli doit rester un repli. S'il entrait dans
    la liste, un échec de tous les candidats ne serait plus distinguable d'un
    choix délibéré.
    """
    assert da._GOUVERNEUR_ANTIGRAVITY not in da._GOUVERNEURS_ESCALADE
    assert da._GOUVERNEUR_ANTIGRAVITY not in da._GOUVERNEURS_ESCALADE_PC
    assert da._GOUVERNEUR_SANS_CLI not in da._GOUVERNEURS_ESCALADE
    assert da._GOUVERNEUR_DEFAUT not in da._GOUVERNEURS_ESCALADE


def test_gouverneur_windows_est_gemini_medium(monkeypatch):
    monkeypatch.delenv("MOTEUR_DISABLE_CLI_PROVIDERS", raising=False)
    assert da._gouverneur_par_defaut() == "gemini-3.5-flash-medium-cli"
    assert da._GOUVERNEUR_ANTIGRAVITY.endswith("-cli")


def test_gouverneur_deck_est_deepseek_v4_pro(monkeypatch):
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", "1")
    assert da._gouverneur_par_defaut() == "deepseek-v4-pro"
    assert not da._GOUVERNEUR_SANS_CLI.endswith("-cli")


def test_reviewer_pc_est_gemini_medium(eteints, monkeypatch):
    monkeypatch.delenv("MOTEUR_DISABLE_CLI_PROVIDERS", raising=False)
    monkeypatch.setattr(da, "_agy_disponible", lambda: True)
    assert da._reviewer_par_defaut() == "gemini-3.5-flash-medium-cli"


def test_reviewer_deck_est_deepseek_v4_pro(eteints, monkeypatch):
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", "1")
    assert da._reviewer_par_defaut() == "deepseek-v4-pro"


def test_reviewer_deck_replie_si_v4_pro_eteint(eteints, monkeypatch):
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", "1")
    eteints.add("deepseek-v4-pro")
    assert da._reviewer_par_defaut() == "deepseek-reasoner"


def test_escalade_pc_peut_monter_vers_v4_pro(monkeypatch):
    monkeypatch.delenv("MOTEUR_DISABLE_CLI_PROVIDERS", raising=False)
    assert da._gouverneurs_escalade() == da._GOUVERNEURS_ESCALADE_PC
    assert "deepseek-v4-pro" in da._gouverneurs_escalade()


def test_escalade_deck_ne_rejoue_pas_v4_pro(monkeypatch):
    monkeypatch.setenv("MOTEUR_DISABLE_CLI_PROVIDERS", "1")
    assert da._gouverneurs_escalade() == da._GOUVERNEURS_ESCALADE
    assert "deepseek-v4-pro" not in da._gouverneurs_escalade()


def test_cli_gemini_pas_eteint_si_agy_present(eteints, monkeypatch):
    """L'IPC IDE a éteint le slug ; `agy` présent → on retente l'abo."""
    eteints.add(da._GOUVERNEUR_ANTIGRAVITY)
    monkeypatch.setattr(da, "_agy_disponible", lambda: True)
    assert da._modele_eteint(da._GOUVERNEUR_ANTIGRAVITY) is False
