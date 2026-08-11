"""
tests/unit/test_env_bootstrap.py — Tests du chargement centralisé du .env (T284).

Vérifie le contrat de core/env_bootstrap.py :
  - idempotence : deux appels = un seul chargement ;
  - override=False : une variable déjà dans os.environ n'est jamais écrasée ;
  - .env absent : aucune exception, message nommant le chemin cherché ;
  - diagnostic honnête (aucun .env trouvé ≠ .env chargé mais clé absente).
"""

import logging
import os
from pathlib import Path

import pytest

import core.env_bootstrap as env_bootstrap


@pytest.fixture(autouse=True)
def _etat_bootstrap_propre():
    """Réinitialise l'état module entre les tests (idempotence par processus)."""
    env_bootstrap._loaded = False
    env_bootstrap._env_file_found = None
    yield
    env_bootstrap._loaded = False
    env_bootstrap._env_file_found = None


def test_deux_appels_un_seul_chargement(tmp_path, monkeypatch):
    """Deux appels successifs ne chargent le .env qu'une seule fois."""
    fichier = tmp_path / ".env"
    fichier.write_text("CLE_TEST=du_fichier\n", encoding="utf-8")

    compteur = {"appels": 0}
    vrai_load = env_bootstrap.load_dotenv

    def load_compteur(*args, **kwargs):
        compteur["appels"] += 1
        return vrai_load(*args, **kwargs)

    monkeypatch.setattr(env_bootstrap, "load_dotenv", load_compteur)

    premier = env_bootstrap.bootstrap_env(fichier)
    second = env_bootstrap.bootstrap_env(fichier)

    assert premier is True
    assert second is True
    assert compteur["appels"] == 1


def test_variable_existante_non_ecrasee(tmp_path, monkeypatch):
    """Une variable déjà dans os.environ n'est pas écrasée par la valeur du .env."""
    monkeypatch.setenv("CLE_PRESENTE", "valeur_env")
    fichier = tmp_path / ".env"
    fichier.write_text(
        "CLE_PRESENTE=valeur_env_obsolete\nCLE_NOUVELLE=valeur_fichier\n",
        encoding="utf-8",
    )

    env_bootstrap.bootstrap_env(fichier)

    # override=False : la valeur d'origine de l'environnement reste prioritaire.
    assert os.environ["CLE_PRESENTE"] == "valeur_env"
    # Les clés absentes de l'environnement sont bien chargées depuis le .env.
    assert os.environ["CLE_NOUVELLE"] == "valeur_fichier"


def test_env_absent_aucune_exception_et_chemin_nomme(tmp_path, caplog):
    """.env absent → aucune exception, et un message nomme le chemin cherché."""
    chemin_absent = tmp_path / "absent.env"

    with caplog.at_level(logging.WARNING, logger="core.env_bootstrap"):
        resultat = env_bootstrap.bootstrap_env(chemin_absent)

    assert resultat is False
    assert any(
        "Aucun fichier .env trouvé" in r.message and str(chemin_absent) in r.message
        for r in caplog.records
    )


def test_retour_vrai_quand_fichier_present(tmp_path):
    """bootstrap_env retourne True quand le fichier existe et est chargé."""
    fichier = tmp_path / ".env"
    fichier.write_text("CLE_TEST=1\n", encoding="utf-8")

    assert env_bootstrap.bootstrap_env(fichier) is True


def test_message_cle_absente_sans_env(tmp_path):
    """Sans .env : le diagnostic nomme le chemin cherché (pas le .env accusé)."""
    env_bootstrap.bootstrap_env(tmp_path / "absent.env")

    message = env_bootstrap.missing_key_message("DEEPSEEK_API_KEY")

    assert "DEEPSEEK_API_KEY" in message
    assert "aucun fichier .env trouvé à" in message
    assert str(env_bootstrap.ENV_FILE) in message


def test_message_cle_absente_avec_env_charge(tmp_path):
    """.env chargé mais clé absente : le diagnostic distingue le cas."""
    fichier = tmp_path / ".env"
    fichier.write_text("AUTRE_CLE=1\n", encoding="utf-8")
    env_bootstrap.bootstrap_env(fichier)

    message = env_bootstrap.missing_key_message("DEEPSEEK_API_KEY")

    assert "DEEPSEEK_API_KEY" in message
    assert "chargé" in message
    assert "aucun fichier .env trouvé" not in message


def test_llm_gateway_passe_par_le_bootstrap():
    """core.llm_gateway importe le bootstrap ancré sur la racine du dépôt."""
    import core.llm_gateway as gateway_module

    assert gateway_module.bootstrap_env is env_bootstrap.bootstrap_env
    # Le chemin chargé est celui de la racine du dépôt, résolu depuis __file__.
    assert env_bootstrap.ENV_FILE == Path(gateway_module.__file__).resolve().parents[1] / ".env"
