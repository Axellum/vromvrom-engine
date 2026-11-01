"""
tests/unit/test_aucune_cle_privee_suivie.py — aucune clé privée dans l'index git.

Une clé de compte de service suivie par git part dans chaque clone (prod,
worktrees, runners de CI, agents cloud). La règle `*_key.json` du .gitignore
n'y peut rien : elle ne s'applique qu'aux fichiers non encore suivis.

Ce test lit l'index (`git ls-files`) : un `git add -f` ou un `git add -A` fait
avant la règle d'exclusion y serait visible, contrairement à un contrôle du
.gitignore.
"""
import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]

# Noms de fichiers de secrets connus, et marqueurs de clé privée dans le contenu.
NOMS_INTERDITS = re.compile(
    r"(^|/)(\.env(\..*)?|.*_key\.json|.*service_account.*\.json|client_secret.*\.json"
    r"|google_token.*\.json|credentials\.json|id_(rsa|ed25519)|.*\.pem|.*\.key)$"
)
EXCEPTIONS = re.compile(r"(^|/)\.env\.(example|template)$")
# Marqueurs assemblés en deux morceaux : écrits d'un bloc, ils déclenchent le
# scanner anti-fuite du miroir public (scripts/oss_secrets_scan.py).
_DEBUT = "-----BEGIN "
MARQUEURS = (_DEBUT + "PRIVATE KEY-----", _DEBUT + "RSA PRIVATE KEY-----",
             _DEBUT + "OPENSSH PRIVATE KEY-----", '"type": "service_account"')


def _fichiers_suivis() -> list[str]:
    try:
        sortie = subprocess.run(["git", "-C", str(RACINE), "ls-files", "-z"],
                                capture_output=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("pas de dépôt git lisible ici")
    return [f for f in sortie.decode("utf-8", "replace").split("\0") if f]


def test_aucun_fichier_de_secret_suivi():
    suspects = [f for f in _fichiers_suivis()
                if NOMS_INTERDITS.search(f) and not EXCEPTIONS.search(f)]
    assert suspects == [], f"fichiers de secrets suivis par git : {suspects}"


def test_aucune_cle_privee_dans_un_json_ou_un_texte_suivi():
    trouves = []
    for f in _fichiers_suivis():
        chemin = RACINE / f
        if chemin.suffix not in {".json", ".txt", ".pem", ".key", ".env", ""}:
            continue
        try:
            contenu = chemin.read_text(encoding="utf-8", errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        if any(m in contenu for m in MARQUEURS):
            trouves.append(f)
    assert trouves == [], f"clé privée dans un fichier suivi : {trouves}"
