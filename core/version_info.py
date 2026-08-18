"""
core/version_info.py — Identité du code en cours d'exécution (SHA + date du commit).

Problème résolu (#T355) : aucune surface du moteur ne disait quelle version du
code tournait. Le 17/08 la prod tournait sur `e64ebac` alors que `master` était à
`bfcb7fb`, neuf PR plus loin. Le seul moyen de le savoir a été d'ouvrir une session
SSH et de lancer `git log`.

Contrat :
- La résolution est effectuée UNE seule fois (au premier appel) puis mise en cache :
  c'est une information qui ne change pas pendant la vie du process, et lancer `git`
  à chaque requête sur une sonde appelée en boucle par Home Assistant serait une
  régression de performance.
- Si le commit ne peut PAS être déterminé (git absent, dépôt absent, commande en
  erreur), la réponse le dit EXPLICITEMENT et sans ambiguïté : `commit` et
  `commit_date` valent `None` et `reason` porte un motif lisible. On ne renvoie
  JAMAIS une valeur d'apparence valide (« unknown », chaîne vide, version de
  fichier non régénéré) : un exploitant qui lit une version croit savoir ce qui
  tourne.
- `git` est disponible en prod (conteneur podman, dépôt monté) mais PAS garanti
  partout : d'où le repli explicite ci-dessus plutôt qu'une exception.
"""

import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Racine du dépôt : le parent du package `core` (ce fichier vit dans core/).
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Cache de la résolution (une seule exécution de `git` pour toute la vie du process).
_resolved = False
_cached_info: dict | None = None
_lock = threading.Lock()


def _run_git(args: list[str]) -> str | None:
    """Exécute `git <args>` dans la racine du dépôt ; retourne stdout nettoyé ou None."""
    try:
        proc = subprocess.run(  # noqa: S603, S607 — usage sûr, pas de shell
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        # git absent (FileNotFoundError), dépôt inaccessible, timeout, etc.
        logger.warning("[VERSION] `git %s` indisponible : %s", " ".join(args), exc)
        return None
    if proc.returncode != 0:
        logger.warning("[VERSION] `git %s` a échoué (code %s)", " ".join(args), proc.returncode)
        return None
    return proc.stdout.strip()


def _resolve() -> dict:
    """Résout SHA court + date du commit HEAD. Ne doit jamais renvoyer de valeur valide en échec."""
    short_commit = _run_git(["rev-parse", "--short", "HEAD"])
    commit_date = _run_git(["show", "-s", "--format=%cI", "HEAD"])

    if short_commit is None or commit_date is None:
        # On ne connaît pas l'identité du code : le dire explicitement.
        if short_commit is None:
            reason = "commit indéterminable : git a échoué ou est absent sur ce process"
        else:
            reason = "date du commit indéterminable : git a échoué ou est absent sur ce process"
        logger.warning("[VERSION] %s", reason)
        return {"commit": None, "commit_date": None, "reason": reason}

    return {
        "commit": short_commit,
        "commit_date": commit_date,
        "reason": None,
    }


def get_version_info() -> dict:
    """Retourne l'identité du code en cours, résolue une seule fois puis mise en cache.

    Retourne un dict avec les clés :
    - `commit`      : SHA court (str) ou `None` si indéterminable.
    - `commit_date` : date ISO du commit (str) ou `None` si indéterminable.
    - `reason`      : motif lisible si indéterminable, sinon `None`.
    """
    global _resolved, _cached_info
    if _resolved:
        return _cached_info
    with _lock:
        if not _resolved:
            _cached_info = _resolve()
            _resolved = True
    return _cached_info


def get_short_commit() -> str | None:
    """Retourne uniquement le SHA court (pour la sonde publique /healthz), ou None.

    N'ajoute RIEN d'autre (pas de chemin, pas de branche, pas de nom de machine)
    sur la route publique. Réutilise le cache : aucun `git` supplémentaire.
    """
    return get_version_info().get("commit")
