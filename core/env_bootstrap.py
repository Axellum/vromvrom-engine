"""
core/env_bootstrap.py — Chargement UNIQUE et centralisé du fichier .env (T284).

UNIQUE point de chargement du .env du moteur : deux appels = un seul chargement,
et le fichier cherché est TOUJOURS celui de la racine du dépôt, résolue depuis
`__file__` (`parents[1]` de core/) — jamais depuis le répertoire courant. Un
point d'entrée lancé depuis n'importe quel CWD (IDE, cron, systemd, scripts/)
obtient donc les mêmes clés, sans avoir à penser au chargement lui-même.

Avant ce module, chaque point d'entrée rechargeait le .env à sa façon (certains
en chemin relatif) et tout point d'entrée qui l'oubliait obtenait un moteur SANS
AUCUNE CLÉ — un après-midi de campagne de tests invalidé le 11/08 (#T284).

Modèle de style : core/ha_tls.py et core/ha_token.py centralisent déjà une
politique de configuration ; ce module centralise la politique de chargement
du .env, dont le diagnostic (« aucun .env trouvé » ≠ « .env chargé mais la clé
absente »).

⚠️ override=False est OBLIGATOIRE — c'est le défaut de python-dotenv, on ne le
change pas. En production (Steam Deck), l'environnement est injecté par systemd ;
un .env périmé traînant sur le disque ne doit JAMAIS écraser la vraie valeur.
Une variable déjà présente dans os.environ reste prioritaire, quoi qu'en dise
le .env.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Racine du dépôt : parents[1] de core/env_bootstrap.py -> moteur_agents/
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"

_loaded = False
_env_file_found: bool | None = None


def bootstrap_env(env_file: Path | str | None = None) -> bool:
    """Charge le .env de la racine du dépôt, une seule fois par processus.

    Idempotent : le premier appel gagne, les suivants ne font rien (même avec
    un autre chemin — `env_file` n'existe que pour les tests unitaires).

    ⚠️ override=False (défaut de python-dotenv, volontairement conservé) : un
    .env périmé sur le disque ne doit JAMAIS écraser une variable déjà présente
    dans l'environnement — en production, les vraies valeurs viennent de systemd.

    Retourne True si un fichier .env existait (et a été chargé), False sinon.
    Ne lève JAMAIS d'exception : .env absent = moteur sans clés, pas crash.
    """
    global _loaded, _env_file_found
    if _loaded:
        return bool(_env_file_found)

    env_path = Path(env_file).resolve() if env_file is not None else ENV_FILE
    _env_file_found = env_path.is_file()
    if _env_file_found:
        # override=False : voir le docstring du module — les variables déjà
        # définies (systemd, shell) restent prioritaires sur le .env.
        load_dotenv(dotenv_path=str(env_path), override=False)
        logger.info(
            "[ENV] Fichier %s chargé (variables déjà définies non écrasées).",
            env_path,
        )
    else:
        logger.warning(
            "[ENV] Aucun fichier .env trouvé à %s — seules les variables déjà "
            "présentes dans l'environnement sont disponibles.",
            env_path,
        )
    _loaded = True
    return _env_file_found


def missing_key_message(key: str) -> str:
    """Diagnostic HONNÊTE pour une clé absente de l'environnement.

    Distingue « aucun .env trouvé à <chemin> » de « .env chargé mais la clé X
    n'y est pas ». Ne contient JAMAIS de valeur de clé — seulement sa présence
    ou son absence.
    """
    if not _loaded:
        bootstrap_env()
    if _env_file_found:
        return (
            f"{key} absente du fichier {ENV_FILE} chargé "
            "(et non définie dans l'environnement)"
        )
    return f"{key} non définie — aucun fichier .env trouvé à {ENV_FILE}"
