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
import contextvars
import hashlib
import os
import threading

# [P0-audit-2026-07-09] Racine autorisée pour write_file/read_file = l'écosystème
# (Tab5, docs, MiniTab), pas le parent immédiat du moteur. Un worktree imbriqué
# (`moteur_agents_into/moteur_agents`) faisait tomber `00ProjetTab` hors workspace.
# Détection : core.projects.racine_espace_de_travail (MOTEUR_WORKSPACE_ROOT prime).
try:
    from core.projects import racine_espace_de_travail as _racine_espace
    _DEFAULT_WORKSPACE_ROOT = _racine_espace()
except Exception:
    _DEFAULT_WORKSPACE_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
_WORKSPACE_ROOT = os.path.realpath(
    os.environ.get("MOTEUR_WORKSPACE_ROOT", "").strip() or _DEFAULT_WORKSPACE_ROOT
)


# [#T388] Racine de résolution des chemins RELATIFS pour la session courante.
#
# Une tâche DreamCoder travaille dans un clone dédié, mais le processus serveur
# a pour CWD le dossier de PRODUCTION. Un agent qui écrit `docs/travail/x.md`
# atterrissait donc dans la prod, pas dans son clone : le 18/08, quatre tâches
# ont été déclarées « réussies » avec quatre branches VIDES, pendant que leurs
# livrables se déposaient dans le dossier de prod (1,13 M de tokens pour rien).
#
# La consigne textuelle « utilise des chemins absolus », injectée dans le prompt,
# ne suffit pas : elle demande au modèle de compenser un défaut d'outillage. La
# racine est donc imposée ICI, hors de portée du prompt.
#
# ContextVar et non variable globale : les tâches d'un DAG tournent dans de vrais
# threads (`asyncio.to_thread`), qui héritent d'une COPIE du contexte — chaque
# tâche garde sa racine sans écraser celle des autres, ce qui est indispensable
# au drainage parallèle où trois clones sont actifs en même temps.
_racine_travail: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "racine_travail", default=None
)


def definir_racine_travail(chemin: str | None):
    """
    Fixe la racine des chemins relatifs pour la session courante.

    :return: le jeton de réinitialisation (`ContextVar.reset`), pour rendre la
        racine précédente en fin de session.
    """
    return _racine_travail.set(os.path.realpath(chemin) if chemin else None)


def reinitialiser_racine_travail(jeton) -> None:
    """Restaure la racine d'avant `definir_racine_travail`."""
    try:
        _racine_travail.reset(jeton)
    except (ValueError, LookupError):
        # Jeton posé dans un autre contexte (thread/tâche) : la copie de contexte
        # disparaît d'elle-même, il n'y a rien à restaurer.
        pass


def racine_travail_courante() -> str | None:
    """Racine de résolution active, ou None (comportement historique : le CWD)."""
    return _racine_travail.get()


def _resolve_within_workspace(filepath: str) -> tuple[str | None, str | None]:
    """Résout `filepath` et vérifie qu'il reste dans `_WORKSPACE_ROOT`.

    Un chemin relatif se résout contre la racine de travail de la session quand
    il y en a une (tâche DreamCoder dans son clone), sinon contre le CWD comme
    `open()` le fait nativement. Un chemin absolu n'est jamais réinterprété.

    Retourne (chemin_résolu, None) si autorisé, ou (None, message_erreur) sinon.
    """
    racine = _racine_travail.get()
    if racine and not os.path.isabs(filepath):
        candidat = os.path.join(racine, filepath)
    else:
        candidat = filepath

    resolved = os.path.realpath(os.path.abspath(candidat))
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


# [#T397] Fichiers de SECRETS : interdits d'ecriture aux outils d'agent, ou
# qu'ils soient dans le workspace.
#
# Incident du 18/08 : la tache autonome #35 (« [T144] infra - CRLF dans .env du
# Deck ») a recu pour mission de reecrire le .env de PRODUCTION. Elle a lance un
# `run_terminal_command` sur `./.env`, s'est declaree `completed` (91 142
# tokens), et son diff ne montrait que `skills.json` -- .env etant gitignore,
# AUCUN garde-fou ne pouvait voir les degats. Quinze minutes plus tard les 4
# providers sains du cycle de 19h00 etaient tous morts (cles perdues ou
# perimees), le moteur n'avait plus un seul modele joignable, et cinq taches du
# backlog passaient en `failed` a 0 token pour une panne qui n'etait pas la
# leur.
#
# La lecon n'est pas « mieux valider le diff » : un fichier ignore par git est
# invisible aux garde-fous de diff, donc la seule barriere possible est en
# amont, sur le chemin. Rotation et depannage des cles restent des gestes
# humains.
_MOTIFS_FICHIERS_SENSIBLES = (
    ".env",           # + .env.bak, .env.local, .env.pre_t326… (prefixe)
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "google_token.json",
)
_SUFFIXES_SENSIBLES = (".pem", ".key", ".p12", ".pfx", "_key.json", "_secret.json")
# Les gabarits SANS valeur reelle restent modifiables : documenter une nouvelle
# variable fait partie du travail legitime d'un agent.
_EXCEPTIONS_SENSIBLES = (".env.example", ".env.template", ".env.sample", ".env.dist")


def est_chemin_sensible(chemin: str) -> bool:
    """Vrai si `chemin` designe un fichier de secrets (cles, jetons, .env)."""
    nom = os.path.basename((chemin or "").replace("\\", "/").rstrip("/")).lower()
    if not nom or nom in _EXCEPTIONS_SENSIBLES:
        return False
    if any(nom.endswith(suffixe) for suffixe in _SUFFIXES_SENSIBLES):
        return True
    return any(nom == motif or nom.startswith(motif + ".") for motif in _MOTIFS_FICHIERS_SENSIBLES)


def refus_si_sensible(chemin: str) -> str | None:
    """Message de refus si `chemin` est un fichier de secrets, None sinon."""
    if not est_chemin_sensible(chemin):
        return None
    return (
        f"Erreur de securite : ecriture refusee sur {chemin!r} -- c'est un fichier de "
        "secrets (cles d'API, jetons). Aucun agent ne le modifie : une cle perdue "
        "coupe tout le moteur sans qu'aucun test ni aucun diff ne le voie "
        "(incident du 18/08). Si la tache l'exige vraiment, elle doit etre rendue "
        "a un humain."
    )


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

def _refus_hors_depot_de_travail(resolved: str) -> str | None:
    """
    Refuse une ÉCRITURE hors du dépôt de travail d'une tâche autonome.

    [#T433] Quand une racine de travail est posée (tâche DreamCoder dans son
    clone), l'agent n'a rien à écrire ailleurs. Mesuré le 21/08 : l'agent a
    déposé `analyse_wake_word.py`, `filter_audio_results.py` et
    `find_active_configs.py` à la RACINE DE L'ESPACE DE TRAVAIL, à côté du
    dépôt. `git add -A` dans le clone n'a donc rien vu, la branche est restée
    vide, et la tâche a été comptée en échec alors que le travail existait.

    `_resolve_within_workspace` autorise tout l'espace — c'est ce qu'il faut
    pour LIRE les autres projets (#T425). Mais écrire est autre chose : sans
    cette garde, un agent peut aussi écrire dans le dépôt de production.

    Hors tâche autonome (aucune racine posée), rien ne change.
    """
    racine = racine_travail_courante()
    if not racine:
        return None
    reel = os.path.realpath(resolved)
    racine_reelle = os.path.realpath(racine)
    if reel == racine_reelle or reel.startswith(racine_reelle + os.sep):
        return None
    return (
        f"Erreur: écriture refusée hors du dépôt de travail. "
        f"'{resolved}' est en dehors de '{racine}'. Un fichier écrit ailleurs "
        f"n'entre dans aucun commit : il serait perdu et la tâche comptée en "
        f"échec. Écris sous '{racine}' — la LECTURE, elle, reste ouverte au "
        f"reste de l'espace de travail."
    )


def write_file(filepath: str, content: str) -> str:
    """Écrit du texte dans un fichier (crée les dossiers parents si nécessaire)."""
    resolved, error = _resolve_within_workspace(filepath)
    if error:
        return error
    # [#T433] Une tâche autonome écrit dans SON clone, nulle part ailleurs.
    hors_depot = _refus_hors_depot_de_travail(resolved)
    if hors_depot:
        return hors_depot
    # [#T397] Un fichier de secrets n'est jamais reecrit par un agent.
    refus = refus_si_sensible(resolved)
    if refus:
        return refus
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

