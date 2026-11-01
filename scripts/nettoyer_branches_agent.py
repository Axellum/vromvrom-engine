#!/usr/bin/env python3
"""
scripts/nettoyer_branches_agent.py — Inventaire et nettoyage des branches d'agent.

Le moteur crée deux familles de branches éphémères :
  * `agent/run_<session>_<ts>` — une par session ;
  * `task/<id>_<ts>`           — une par tâche DreamCoder, **poussée sur origin**
    depuis #T327.

Quand une session échoue, la branche reste. Ce script les inventorie et ne
supprime que celles dont il peut PROUVER qu'elles ne portent aucun travail.

Règle de sûreté (leçon du 18/08, #T396) : une branche qui porte des commits non
fusionnés n'est JAMAIS proposée à la suppression, même distante. Ce jour-là,
cinq branches `task/*` ont failli être perdues avec 2,9 M tokens de travail
dedans, parce que la base avait oublié leur nom. Un outil de ménage qui efface
du travail non fusionné aggrave le problème qu'il prétend régler.

Usage :
    python scripts/nettoyer_branches_agent.py                    # inventaire local
    python scripts/nettoyer_branches_agent.py --distantes        # inventaire origin
    python scripts/nettoyer_branches_agent.py --supprimer        # nettoie en local
    python scripts/nettoyer_branches_agent.py --distantes --supprimer
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Les deux familles de branches éphémères du moteur.
PREFIXES = ("agent/run_", "task/")


def _run(args: list[str], cwd: str | None = None, timeout: float = 60.0) -> tuple[int, str, str]:
    """Exécute une commande sans shell. Retourne (code, stdout, stderr)."""
    try:
        res = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                             timeout=timeout, encoding="utf-8", errors="replace")
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"délai dépassé après {timeout} s"
    except Exception as e:  # git absent, chemin invalide…
        return -1, "", str(e)


def est_depot_git(depot: str) -> bool:
    """
    Vrai si `depot` est dans un arbre de travail Git.

    On interroge git plutôt que de chercher un dossier `.git` : dans un worktree
    (le dépôt en utilise), `.git` est un FICHIER, et un test par `os.path.isdir`
    y répond faussement « pas un dépôt ».
    """
    code, sortie, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=depot)
    return code == 0 and sortie == "true"


def detecter_base(depot: str) -> str:
    """Branche principale du dépôt : `main` ou `master` selon ce qui existe."""
    code, sortie, _ = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=depot)
    if code == 0 and sortie:
        return sortie.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        code, _, _ = _run(["git", "show-ref", "--verify", f"refs/heads/{candidate}"], cwd=depot)
        if code == 0:
            return candidate
    return "master"


def lister_branches(depot: str, distantes: bool) -> list[str]:
    """Branches éphémères, locales ou sur origin."""
    args = ["git", "branch", "--format=%(refname:short)"]
    if distantes:
        args += ["--remotes", "--list", "origin/*"]
    code, sortie, err = _run(args, cwd=depot)
    if code != 0:
        print(f"❌ Listing des branches impossible : {err}", file=sys.stderr)
        return []

    branches = []
    for ligne in sortie.splitlines():
        nom = ligne.strip()
        if not nom or "->" in nom:  # ignore « origin/HEAD -> origin/master »
            continue
        court = nom[len("origin/"):] if distantes and nom.startswith("origin/") else nom
        if court.startswith(PREFIXES):
            branches.append(nom)
    return sorted(branches)


def commits_au_dela(depot: str, base: str, branche: str) -> int | None:
    """Nombre de commits que `branche` porte au-delà de `base`. None si illisible."""
    code, sortie, err = _run(["git", "rev-list", "--count", f"{base}..{branche}"], cwd=depot)
    if code != 0:
        print(f"❌ Comptage impossible pour {branche} : {err}", file=sys.stderr)
        return None
    try:
        return int(sortie)
    except ValueError:
        print(f"❌ Comptage illisible pour {branche} : {sortie!r}", file=sys.stderr)
        return None


def quitter_branche_ephemere(depot: str, base: str, supprimer: bool) -> bool:
    """
    Une session mal terminée laisse le dépôt SUR sa branche éphémère, qui devient
    alors indéboulonnable. On revient sur la base — mais seulement si on nettoie
    vraiment : un inventaire ne doit pas déplacer le HEAD de l'utilisateur.
    """
    code, actuelle, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=depot)
    if code != 0:
        print(f"❌ Branche courante illisible : {err}", file=sys.stderr)
        return False
    if not actuelle.startswith(PREFIXES):
        return True

    print(f"⚠️  Session mal terminée : le dépôt est SUR {actuelle}")
    if not supprimer:
        print("ℹ️  (inventaire seul : le HEAD n'est pas déplacé)")
        return True
    code, _, err = _run(["git", "checkout", base], cwd=depot)
    if code != 0:
        print(f"❌ Retour sur {base} impossible : {err}", file=sys.stderr)
        return False
    print(f"🔄 Retour sur {base}.")
    return True


def nettoyer(depot: str, base: str | None, supprimer: bool, distantes: bool) -> int:
    if base is None:
        base = detecter_base(depot)
        print(f"ℹ️  Branche de base détectée : {base}")
    reference = f"origin/{base}" if distantes else base

    if not quitter_branche_ephemere(depot, base, supprimer):
        return 1

    branches = lister_branches(depot, distantes)
    if not branches:
        lieu = "sur origin" if distantes else "en local"
        print(f"Aucune branche éphémère {lieu}.")
        return 0

    portee = "distante" if distantes else "locale"
    print(f"Analyse de {len(branches)} branche(s) {portee}(s) par rapport à {reference}…\n")

    vides: list[str] = []
    gardees = 0
    erreurs = 0
    for branche in branches:
        nb = commits_au_dela(depot, reference, branche)
        if nb is None:
            erreurs += 1
            continue
        if nb == 0:
            vides.append(branche)
        else:
            # Le cœur de la règle : du travail non fusionné ne se supprime pas
            # tout seul. On le signale pour qu'il soit récolté, pas effacé.
            print(f"🛡️  Gardée : {branche} — {nb} commit(s) NON fusionné(s), à récolter")
            gardees += 1

    for branche in vides:
        if not supprimer:
            print(f"🔍 [inventaire] Vide, serait supprimée : {branche}")
            continue
        if distantes:
            court = branche[len("origin/"):]
            code, _, err = _run(["git", "push", "origin", "--delete", court], cwd=depot)
        else:
            # `-d` et non `-D` : à zéro commit d'écart il réussit toujours. S'il
            # refuse, c'est que la branche n'est pas ce qu'on croit — on garde.
            code, _, err = _run(["git", "branch", "-d", branche], cwd=depot)
        if code == 0:
            print(f"🗑️  Supprimée : {branche} (0 commit)")
        else:
            print(f"❌ Échec de la suppression de {branche} : {err}", file=sys.stderr)
            erreurs += 1

    verbe = "supprimée(s)" if supprimer else "vide(s) à supprimer"
    print(f"\nRésumé : {len(vides)} branche(s) {verbe}, {gardees} portant du travail.")
    if gardees:
        print("👉 Les branches gardées portent des commits : les récolter par PR.")
    if vides and not supprimer:
        print("👉 Relancer avec --supprimer pour nettoyer.")
    return 1 if erreurs else 0


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Inventorie et nettoie les branches éphémères d'agent "
                    "(agent/run_*, task/*) qui ne portent aucun travail.")
    parseur.add_argument("--depot", default=".", help="Chemin du dépôt (défaut : courant)")
    parseur.add_argument("--base", default=None,
                         help="Branche de référence (défaut : détection main/master)")
    parseur.add_argument("--supprimer", action="store_true",
                         help="Supprime réellement les branches vides")
    parseur.add_argument("--distantes", action="store_true",
                         help="Travaille sur les branches d'origin au lieu des locales")
    args = parseur.parse_args()

    if not est_depot_git(args.depot):
        print(f"❌ {args.depot} n'est pas un dépôt Git.", file=sys.stderr)
        return 1
    return nettoyer(args.depot, args.base, args.supprimer, args.distantes)


if __name__ == "__main__":
    sys.exit(main())
