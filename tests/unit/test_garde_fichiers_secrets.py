"""
tests/unit/test_garde_fichiers_secrets.py — Aucun agent ne reecrit un .env (#T397).

Reproduction de l'incident du 18/08. La tache autonome #35, intitulee
« [T144] infra - CRLF dans .env du Deck », a recu pour mission de reecrire le
fichier de secrets de la PRODUCTION. Elle a lance un `run_terminal_command` sur
« ./.env », s'est declaree `completed` (91 142 tokens depenses), et son diff ne
mentionnait que `skills.json` : .env etant gitignore, aucun controle de diff ne
POUVAIT voir les degats.

Quinze minutes plus tard, les quatre providers sains du cycle de 19h00
(cohere-free, mistral-free, gemini-free, deepseek-free) etaient tous morts, le
moteur n'avait plus un seul modele joignable, et cinq taches du backlog
passaient en `failed` a 0 token.

Deux trous a fermer, tous les deux verifies ici :
  1. le chemin etait RELATIF (« ./.env »), or la garde ne regardait que les
     chemins absolus ;
  2. la commande de reecriture en place (`sed`, `tr`, `dos2unix`) ne figurait
     pas parmi les commandes d'ecriture surveillees.
"""

import os

import pytest

from tools.system import est_chemin_sensible, refus_si_sensible, write_file
from tools.terminal import _refus_garde_chemins


@pytest.mark.parametrize("chemin", [
    ".env",
    "./.env",
    "/opt/vromvrom-engine/.env",
    ".env.bak_crlf_17_08",
    ".env.pre_t326_1787045862.bak",
    "google_token.json",
    "credentials.json",
    "ha_logger_key.json",
    "cert.pem",
    "serveur.key",
    "~/.ssh/id_ed25519",
])
def test_un_fichier_de_secrets_est_reconnu(chemin):
    assert est_chemin_sensible(chemin) is True


@pytest.mark.parametrize("chemin", [
    ".env.example",
    ".env.template",
    "core/engine.py",
    "config.json",
    "docs/travail/TACHES.md",
    "skills.json",
    "",
])
def test_un_fichier_ordinaire_reste_modifiable(chemin):
    assert est_chemin_sensible(chemin) is False
    assert refus_si_sensible(chemin) is None


def test_write_file_refuse_un_env(tmp_path):
    """L'outil des agents refuse, et n'ecrit rien."""
    from core.env_bootstrap import REPO_ROOT

    cible = os.path.join(str(REPO_ROOT), "checkpoints", "test_t397", ".env")
    reponse = write_file(cible, "GEMINI_API_KEY=casse")
    assert "Erreur de securite" in reponse
    assert not os.path.exists(cible)


def test_le_terminal_refuse_la_commande_exacte_de_lincident():
    """« sed -i » sur ./.env : un chemin relatif, la forme exacte du 18/08."""
    refus = _refus_garde_chemins(["sed", "-i", "s/" + chr(13) + "//", "./.env"])
    assert refus is not None
    assert "secrets" in refus


@pytest.mark.parametrize("argv", [
    ["tr", "-d", chr(13), ".env"],
    ["cp", ".env.bak_crlf_17_08", ".env"],
    ["mv", "/tmp/env.tmp", "/opt/vromvrom-engine/.env"],
    ["dos2unix", ".env"],
    ["rm", ".env"],
    ["chmod", "600", "google_token.json"],
])
def test_les_autres_facons_de_toucher_au_secret_sont_refusees(argv):
    refus = _refus_garde_chemins(argv)
    assert refus is not None, f"non refuse : {argv}"


@pytest.mark.parametrize("argv", [
    ["sed", "-i", "s/a/b/", "core/engine.py"],
    ["cp", "config.json", "config.json.bak"],
    ["ls", "-la"],
    ["python3", "-m", "pytest", "tests/unit"],
])
def test_le_travail_legitime_passe_toujours(argv):
    assert _refus_garde_chemins(argv) is None
