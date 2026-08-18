"""
tools/system.py — Outils fichiers ("read_file"/"write_file") exposés aux agents.

Lecture/écriture directe sur le disque local, avec compression sémantique
optionnelle des lectures volumineuses via memory/context_manager.py.

[#T230] Deux protections contre les tâches DAG parallèles (`dag_runner.py`
jusqu'à 8 tâches simultanées, outils synchrones exécutés dans de vrais threads
OS via `asyncio.to_thread`) :
  1. un verrou par chemin `realpath` sérialise lectures/écritures — plus de
     contenu tronqué/entrelacé si deux tâches écrivent le même fichier ;
  2. une garde anti-lost-update : l'empreinte du fichier est mémorisée au
     `read_file` et revérifiée avant chaque `write_file`. Si le disque a bougé
     entre-temps (autre tâche, processus externe), l'écriture est refusée avec
     un message explicite demandant une relecture. Inspiration : per-file mutex
     d'OpenFox v2.0 (idée d'architecture, aucune ligne copiée).
Limite assumée : les écrits passés par d'autres chemins (`safe_io.py`,
`terminal`, scripts) ne sont pas couverts par la garde — elle protège le cycle
read_file → génération LLM → write_file des agents.
"""
import hashlib
import os
import threading

# [P0-audit-2026-07-09] Racine autorisée pour write_file/read_file. Par défaut le
# dossier PARENT de moteur_agents/ (workspace canonique documenté dans CLAUDE.md,
# ex. . ; sur Linux/Deck, parent du dossier de déploiement) —
# même profondeur (3 dirname depuis un fichier de tools/) que project_root plus bas
# dans validate_config_yaml(). Surchargeable via MOTEUR_WORKSPACE_ROOT si la
# topologie diffère. realpath() résout aussi les symlinks/junctions (E:/H:), même
# garde-fou que core/mcp_tools/homeassistant.py::validate_config_format.
_DEFAULT_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKSPACE_ROOT = os.path.realpath(
    os.environ.get("MOTEUR_WORKSPACE_ROOT", "").strip() or _DEFAULT_WORKSPACE_ROOT
)


def _resolve_within_workspace(filepath: str) -> tuple[str | None, str | None]:
    """Résout `filepath` (relatif = par rapport au CWD courant, comme open() nativement)
    et vérifie qu'il reste dans `_WORKSPACE_ROOT`. Retourne (chemin_résolu, None) si
    autorisé, ou (None, message_erreur) sinon — ne change AUCUN comportement pour un
    chemin déjà dans le workspace, ne fait que refuser ceux qui en sortent.
    """
    resolved = os.path.realpath(os.path.abspath(filepath))
    if resolved == _WORKSPACE_ROOT or resolved.startswith(_WORKSPACE_ROOT + os.sep):
        return resolved, None
    return None, (
        f"Erreur de sécurité : accès refusé, {filepath!r} sort du workspace autorisé "
        f"({_WORKSPACE_ROOT})."
    )


# [#T275] Nom public de la politique de chemins, pour que les autres outils
# l'appliquent au lieu d'en recopier une variante. `tools/terminal.py` s'en sert
# pour refuser les commandes qui écrivent hors du workspace : une seule
# définition de « dans le workspace » vaut mieux que deux qui divergeront.
resoudre_dans_workspace = _resolve_within_workspace


# ── [#T230] Verrous par chemin et empreintes anti-lost-update ──────────────

# Un verrou par chemin résolu : les tâches DAG tournent dans de vrais threads
# OS (`asyncio.to_thread` dans tools/tool_registry.py), donc threading.Lock est
# le bon primitif. Les verrous ne sont jamais retirés du registre : le nombre
# de chemins distincts écrits par les agents reste faible devant le coût d'un
# contenu entrelacé. `MOTEUR_FILE_WRITE_GUARD=off` désactive la garde (et le
# stamp d'empreinte) en cas de régression, sans toucher aux verrous.
_registry_lock = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}
_seen_fingerprints: dict[str, str] = {}
_GUARD_ENABLED = os.environ.get("MOTEUR_FILE_WRITE_GUARD", "1").strip().lower() not in (
    "0", "false", "off", "no",
)

_MAX_FULL_HASH = 5 * 1024 * 1024  # au-delà : hash borné (début + fin de fichier)


def _lock_for(resolved: str) -> threading.Lock:
    """Retourne (et crée si besoin) le verrou dédié au chemin résolu."""
    with _registry_lock:
        lock = _path_locks.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _path_locks[resolved] = lock
        return lock


def _fingerprint(resolved: str) -> str:
    """Empreinte du contenu disque : sha256 complet jusqu'à 5 Mo, sinon
    taille + mtime + sha256 des 64 premiers/derniers Ko. mtime seul ne suffit
    pas (résolution trop grossière sur certains systèmes de fichiers)."""
    st = os.stat(resolved)
    h = hashlib.sha256()
    if st.st_size <= _MAX_FULL_HASH:
        with open(resolved, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"
    with open(resolved, "rb") as f:
        h.update(f.read(65536))
        f.seek(max(0, st.st_size - 65536))
        h.update(f.read(65536))
    return f"taille:{st.st_size};mtime:{st.st_mtime_ns};sha256:{h.hexdigest()}"


def read_file(filepath: str) -> str:
    """Lit le contenu d'un fichier depuis le disque local."""
    resolved, error = _resolve_within_workspace(filepath)
    if error:
        return error
    if not os.path.exists(resolved):
        return f"Erreur: Le fichier {filepath} n'existe pas."
    try:
        # [#T230] Lecture sous verrou : jamais de contenu lu à moitié pendant
        # qu'une autre tâche écrit. L'empreinte est mémorisée pour la garde
        # anti-lost-update de write_file.
        with _lock_for(resolved):
            with open(resolved, encoding='utf-8') as f:
                content = f.read()
            if _GUARD_ENABLED:
                with _registry_lock:
                    _seen_fingerprints[resolved] = _fingerprint(resolved)

        # Intégration du cache sémantique ContextManager s'il est initialisé
        from memory.context_manager import ContextManager
        manager = ContextManager.get_instance()
        if manager:
            content = manager.optimize_file_read(resolved, content)

        return content
    except Exception as e:
        return f"Erreur de lecture: {e}"

def write_file(filepath: str, content: str) -> str:
    """Écrit du texte dans un fichier (crée les dossiers parents si nécessaire)."""
    resolved, error = _resolve_within_workspace(filepath)
    if error:
        return error
    try:
        with _lock_for(resolved):
            existed = os.path.exists(resolved)
            # [#T230] Garde anti-lost-update : si ce module a lu ce fichier,
            # le disque doit être exactement dans l'état vu à la lecture.
            # Sinon quelqu'un d'autre a écrit entre-temps (tâche DAG parallèle,
            # processus externe) et on refuse au lieu d'écraser en silence —
            # l'agent doit relire pour fusionner consciemment.
            if _GUARD_ENABLED and existed:
                with _registry_lock:
                    attendu = _seen_fingerprints.get(resolved)
                if attendu is not None and _fingerprint(resolved) != attendu:
                    return (
                        f"Erreur d'écriture: {filepath} a été modifié depuis la dernière "
                        "lecture (écriture concurrente ou modification externe). "
                        "Relance read_file pour recharger le contenu, puis réessaie."
                    )
            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            with open(resolved, 'w', encoding='utf-8') as f:
                f.write(content)
            # Création : l'auteur connaît l'état qu'il vient de poser, on le
            # mémorise. Modification : volontairement NON mémorisé — l'agent
            # n'a pas « vu » le nouvel état disque, une réécriture sans
            # relecture doit être refusée (c'est elle qui causait le lost
            # update silencieux).
            if _GUARD_ENABLED and not existed:
                with _registry_lock:
                    _seen_fingerprints[resolved] = _fingerprint(resolved)
        return f"Succès: Fichier {filepath} créé/modifié."
    except Exception as e:
        return f"Erreur d'écriture: {e}"

def validate_config_yaml(file_path: str) -> str:
    """Valide un fichier de configuration YAML à l'aide du linter ESPHome."""
    import re
    import subprocess
    if not file_path.endswith(('.yaml', '.yml')):
        return f"Erreur: {file_path} n'est pas un fichier YAML."
    if not os.path.exists(file_path):
        return f"Erreur: Le fichier {file_path} n'existe pas."

    # Résoudre le chemin de l'exécutable esphome dans le .venv
    # .\tools\system.py -> le dossier parent de moteur_agents est .
    # et .venv est à .\.venv
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    is_windows = (os.name == 'nt')
    if is_windows:
        esphome_path = os.path.join(project_root, ".venv", "Scripts", "esphome.exe")
    else:
        esphome_path = os.path.join(project_root, ".venv", "bin", "esphome")

    if not os.path.exists(esphome_path):
        # Essayer esphome dans le PATH par défaut si .venv est manquant
        esphome_path = "esphome"

    try:
        # Exécuter la commande: esphome config <file_path>
        result = subprocess.run(
            [esphome_path, "config", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode == 0:
            return f"Succès: Le fichier {file_path} est valide."
        else:
            # Nettoyer d'éventuels codes d'échappement ANSI de la console esphome
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            clean_stdout = ansi_escape.sub('', result.stdout)
            clean_stderr = ansi_escape.sub('', result.stderr)
            return f"Erreur de validation pour {file_path} :\nSTDOUT:\n{clean_stdout}\nSTDERR:\n{clean_stderr}"
    except FileNotFoundError:
        return f"Succès: Validation ESPHome sautée pour {file_path} (commande esphome absente sur cette machine)."
    except Exception as e:
        return f"Erreur lors de l'exécution de la validation ESPHome : {e}"

