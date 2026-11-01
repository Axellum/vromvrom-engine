"""
core/projects.py — Registre des projets gérés par le moteur.

Chaque projet a un dossier local ET un dépôt Git. Sans ça, tout le travail
autonome atterrit dans le clone du moteur — y compris le firmware Tab5, qui
vit dans `00ProjetTab` / `Axellum/M5-Tab5-ESPHome-LVGL`.

Mesuré 23/08 : `git_safety` n'acceptait que `Axellum/moteur_agents` ; les
écritures hors de ce clone étaient refusées (#T433) ; le prompt disait
explicitement « une analyse Tab5 se LIT là-bas et s'ÉCRIT ici ».

Chemins machine-locaux : overlay `projects.json` (gitignoré) et variables
`MOTEUR_PROJECT_<ID>_PATH`. Le fichier suivi est `projects.example.json`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_OVERLAY_PATH = _ENGINE_ROOT / "projects.json"

# Premier segment de zone du board → id de projet. Le reste tombe sur `moteur`.
_PREFIXES_ZONE: dict[str, str] = {
    "tab5": "tab5",
    "camha": "tab5",
    "home_assistant": "ecosystem",
    "minitab": "ecosystem",
    "ha": "ecosystem",
}

# Indices lexicaux (titre + description) quand la zone du board manque.
_MOTIFS_PROJET: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tab5", re.compile(
        r"\b(tab5|esphome|lvgl|wake[\s_-]?word|esp32-p4|00projettab|"
        r"m5-tab5|firmware\s+tab)\b",
        re.IGNORECASE,
    )),
    ("ecosystem", re.compile(
        r"\b(contexte_ia|minitab|ha-mcp|serveurha|auxfilsdesidees)\b",
        re.IGNORECASE,
    )),
)

# Politique d'écriture autonome :
#   clone  — dépôt dédié aux agents (`reset --hard` / `clean` autorisés)
#   humain — copie de travail d'Axel : branche éphémère + stash, jamais de wipe
POLITIQUE_CLONE = "clone"
POLITIQUE_HUMAIN = "humain"


@dataclass(frozen=True)
class Projet:
    """Un projet = un git canonique + au moins un dossier local."""

    id: str
    label: str
    git_remote: str
    default_branch: str
    zone_prefixes: tuple[str, ...] = ()
    politique: str = POLITIQUE_HUMAIN
    chemins_candidats: tuple[str, ...] = ()
    work_paths: tuple[str, ...] = ()

    def chemin_local(self) -> str | None:
        """Premier dossier existant (copie humaine, celle qu'on montre dans l'IHM)."""
        for brut in self.chemins_candidats:
            if not brut:
                continue
            chemin = os.path.realpath(os.path.abspath(os.path.expandvars(brut)))
            if os.path.isdir(chemin):
                return chemin
        return None

    def chemin_travail_agent(self) -> str | None:
        """
        Où un agent a le droit d'écrire et de commiter.

        Un clone dédié (`work_paths`) prime : c'est l'équivalent Tab5 de
        `moteur_agents_dc3`. À défaut, la copie humaine — seulement si la
        politique est `humain` (stash + branche, pas de `reset --hard`).
        """
        for brut in self.work_paths:
            if not brut:
                continue
            chemin = os.path.realpath(os.path.abspath(os.path.expandvars(brut)))
            if os.path.isdir(chemin) and os.path.isdir(os.path.join(chemin, ".git")):
                return chemin
            # Worktree : `.git` est un fichier, pas un dossier.
            if os.path.isdir(chemin) and os.path.isfile(os.path.join(chemin, ".git")):
                return chemin
        if self.politique == POLITIQUE_HUMAIN:
            local = self.chemin_local()
            if local and (
                os.path.isdir(os.path.join(local, ".git"))
                or os.path.isfile(os.path.join(local, ".git"))
            ):
                return local
        return None

    def est_clone_agent(self) -> bool:
        """True si l'agent travaille dans un clone dédié (assainissement OK)."""
        travail = self.chemin_travail_agent()
        local = self.chemin_local()
        if not travail:
            return False
        if self.politique == POLITIQUE_CLONE:
            return True
        if local and os.path.realpath(travail) != os.path.realpath(local):
            return True
        return False


def racine_espace_de_travail() -> str:
    """
    Racine de LECTURE de l'écosystème (tous les projets y vivent).

    Historiquement : parent immédiat de `moteur_agents/`. Faux dès que le
    moteur tourne dans un worktree imbriqué (`moteur_agents_into/moteur_agents`) :
    `00ProjetTab` et `contexte_ia` sont alors HORS workspace, donc illisibles
    ET ininscriptibles. On remonte jusqu'au dossier qui les contient.
    """
    env = (os.environ.get("MOTEUR_WORKSPACE_ROOT") or "").strip()
    if env:
        return os.path.realpath(env)

    courant = _ENGINE_ROOT.resolve()
    for candidat in (courant, *courant.parents):
        if (candidat / "00ProjetTab").is_dir() or (candidat / "contexte_ia").is_dir():
            return str(candidat)
    return str(courant.parent)


def _overlay() -> dict:
    """Surcharges machine-locales (`projects.json`), dict vide si absent."""
    if not _OVERLAY_PATH.is_file():
        return {}
    try:
        data = json.loads(_OVERLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[PROJECTS] Overlay illisible (%s) : %s", _OVERLAY_PATH, exc)
        return {}
    projets = data.get("projects") if isinstance(data, dict) else None
    return projets if isinstance(projets, dict) else {}


def _chemin_env(projet_id: str) -> str:
    return (os.environ.get(f"MOTEUR_PROJECT_{projet_id.upper()}_PATH") or "").strip()


def _chemins_moteur() -> tuple[str, ...]:
    env = _chemin_env("moteur")
    overlay = ((_overlay().get("moteur") or {}).get("local_path") or "").strip()
    return tuple(c for c in (env, overlay, str(_ENGINE_ROOT)) if c)


def _chemins_tab5() -> tuple[str, ...]:
    eco = racine_espace_de_travail()
    env = _chemin_env("tab5")
    overlay = ((_overlay().get("tab5") or {}).get("local_path") or "").strip()
    return tuple(c for c in (
        env,
        overlay,
        os.path.join(eco, "00ProjetTab"),
        str(_ENGINE_ROOT / "00ProjetTab"),
    ) if c)


def _chemins_ecosystem() -> tuple[str, ...]:
    eco = racine_espace_de_travail()
    env = _chemin_env("ecosystem")
    overlay = ((_overlay().get("ecosystem") or {}).get("local_path") or "").strip()
    return tuple(c for c in (env, overlay, eco) if c)


def _work_paths(projet_id: str) -> tuple[str, ...]:
    brut = (_overlay().get(projet_id) or {}).get("work_paths") or []
    if isinstance(brut, str):
        brut = [brut]
    env = (os.environ.get(f"MOTEUR_PROJECT_{projet_id.upper()}_WORK_PATHS") or "").strip()
    if env:
        brut = [c.strip() for c in env.split(os.pathsep) if c.strip()] + list(brut)
    return tuple(c for c in brut if c)


def _politique(projet_id: str, defaut: str) -> str:
    overlay = (_overlay().get(projet_id) or {}).get("politique")
    if overlay in (POLITIQUE_CLONE, POLITIQUE_HUMAIN):
        return overlay
    return defaut


def lister_projets() -> list[Projet]:
    """Les projets connus, moteur en tête (repli par défaut)."""
    return [
        Projet(
            id="moteur",
            label="tab5-engine",
            git_remote="Axellum/moteur_agents",
            default_branch="master",
            zone_prefixes=("core", "agents", "api", "ihm-v2", "dreamcoder",
                           "runtime", "vocal", "cli", "infra", "hygiene",
                           "hygiène", "archi", "autonome", "tests", "tools",
                           "memory", "scripts", "billing", "catalogue"),
            politique=_politique("moteur", POLITIQUE_CLONE),
            chemins_candidats=_chemins_moteur(),
            work_paths=_work_paths("moteur"),
        ),
        Projet(
            id="tab5",
            label="Tab5 V2 (firmware ESPHome)",
            git_remote="Axellum/M5-Tab5-ESPHome-LVGL",
            default_branch="main",
            zone_prefixes=("tab5", "camha"),
            politique=_politique("tab5", POLITIQUE_HUMAIN),
            chemins_candidats=_chemins_tab5(),
            work_paths=_work_paths("tab5"),
        ),
        Projet(
            id="ecosystem",
            label="Écosystème AuxFilsDesIdees",
            git_remote="Axellum/AuxFilsDesIdees",
            default_branch="master",
            zone_prefixes=("home_assistant", "minitab", "ha"),
            politique=_politique("ecosystem", POLITIQUE_HUMAIN),
            chemins_candidats=_chemins_ecosystem(),
            work_paths=_work_paths("ecosystem"),
        ),
    ]


def projet_par_id(projet_id: str | None) -> Projet | None:
    if not projet_id:
        return None
    cle = projet_id.strip().lower()
    for projet in lister_projets():
        if projet.id == cle:
            return projet
    return None


def projet_moteur() -> Projet:
    return lister_projets()[0]


def remotes_enregistres() -> tuple[str, ...]:
    """Substrings `origin` acceptés par git_safety (plus seulement le moteur)."""
    return tuple(p.git_remote for p in lister_projets())


def projet_pour_chemin(chemin: str) -> Projet | None:
    """Projet dont le dossier local (ou un clone) contient `chemin`."""
    reel = os.path.realpath(os.path.abspath(chemin))
    for projet in lister_projets():
        for candidat in (*projet.chemins_candidats, *projet.work_paths):
            if not candidat:
                continue
            racine = os.path.realpath(os.path.abspath(os.path.expandvars(candidat)))
            if reel == racine or reel.startswith(racine + os.sep):
                return projet
    return None


_RE_ZONE_TITRE = re.compile(
    r"\[T\d+\]\s*\((?P<zone>[\w\-/.]+)\)|"
    r"^\[?T\d+\]?\s+(?P<zone2>[\w\-/.]+)",
    re.IGNORECASE,
)


def _zone_depuis_tache(task: dict) -> str:
    if task.get("zone"):
        return str(task["zone"]).strip().lower()
    titre = (task.get("title") or "").strip()
    m = _RE_ZONE_TITRE.search(titre)
    if not m:
        return ""
    return (m.group("zone") or m.group("zone2") or "").strip().lower()


def _id_depuis_zone(zone: str) -> str | None:
    if not zone:
        return None
    premier = zone.replace("\\", "/").split("/")[0].strip().lower()
    if premier in _PREFIXES_ZONE:
        return _PREFIXES_ZONE[premier]
    for projet in lister_projets():
        if premier in projet.zone_prefixes or premier == projet.id:
            return projet.id
    return None


def resoudre_projet_pour_tache(task: dict | None, objectif: str = "") -> Projet:
    """
    Projet cible d'une tâche (backlog, /api/run, découverte).

    Ordre : `project_id` explicite → zone du board → motifs lexicaux → moteur.
    """
    task = task or {}
    explicite = projet_par_id(task.get("project_id") or task.get("projet_id"))
    if explicite:
        return explicite

    depuis_zone = _id_depuis_zone(_zone_depuis_tache(task))
    if depuis_zone:
        trouve = projet_par_id(depuis_zone)
        if trouve:
            return trouve

    texte = " ".join(
        str(task.get(k) or "") for k in ("title", "description", "prompt")
    )
    texte = f"{texte} {objectif}"
    for projet_id, motif in _MOTIFS_PROJET:
        if motif.search(texte):
            trouve = projet_par_id(projet_id)
            if trouve:
                return trouve
    return projet_moteur()


def sonder_projet(projet: Projet) -> dict:
    """État effectif d'un projet (chemins, git) — pour l'API / l'IHM."""
    local = projet.chemin_local()
    travail = projet.chemin_travail_agent()
    remote = ""
    branche = ""
    git_ok = False
    cible = travail or local
    if cible and (
        os.path.isdir(os.path.join(cible, ".git"))
        or os.path.isfile(os.path.join(cible, ".git"))
    ):
        try:
            from tools.git_safety import _run_git, remote_correspond
            code, remote, _ = _run_git(["remote", "get-url", "origin"], cwd=cible)
            if code == 0:
                git_ok = remote_correspond(projet.git_remote, remote or "")
            code_b, branche, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cible)
            if code_b != 0:
                branche = ""
        except Exception as exc:
            logger.debug("[PROJECTS] Sonde git %s : %s", projet.id, exc)

    statut = "ok"
    detail = "Dossier local et remote Git alignés."
    if not local:
        statut = "error"
        detail = "Aucun dossier local trouvé. Pose MOTEUR_PROJECT_%s_PATH ou projects.json." % (
            projet.id.upper(),
        )
    elif not cible:
        statut = "error"
        detail = (
            "Dossier trouvé mais ce n'est pas un dépôt Git, et aucun clone "
            "agent (work_paths) n'est déclaré."
        )
    elif not git_ok:
        statut = "warn"
        detail = (
            f"Le remote origin ({remote or 'absent'}) ne correspond pas à "
            f"{projet.git_remote}."
        )

    return {
        "id": projet.id,
        "label": projet.label,
        "git_remote": projet.git_remote,
        "default_branch": projet.default_branch,
        "politique": projet.politique,
        "zone_prefixes": list(projet.zone_prefixes),
        "local_path": local,
        "work_path": travail,
        "est_clone_agent": projet.est_clone_agent(),
        "git_origin": remote or None,
        "git_branch": branche or None,
        "git_ok": git_ok,
        "status": statut,
        "detail": detail,
    }


def snapshot_projets() -> dict:
    """Payload de GET /api/projects."""
    projets = lister_projets()
    sondes = [sonder_projet(p) for p in projets]
    return {
        "workspace_root": racine_espace_de_travail(),
        "overlay": str(_OVERLAY_PATH) if _OVERLAY_PATH.is_file() else None,
        "projects": sondes,
        "ok": all(s["status"] != "error" for s in sondes),
    }
