#!/usr/bin/env python3
"""
scripts/recolte_dreamcoder.py — Récolte le travail de nuit de DreamCoder.

Le Deck exécute les tâches du backlog et commite chacune sur une branche
`task/*` de son clone dédié. Il ne peut pas les publier lui-même : sa clé de
déploiement GitHub est en LECTURE SEULE (`~/.ssh/config` du Deck, 07/08). Ce
script fait la dernière marche depuis le PC, où `gh` est authentifié :

    clone du Deck --(fetch ssh)--> dépôt local --(push)--> GitHub --> PR

Il ne touche JAMAIS le répertoire de travail local : pas de checkout, pas de
merge, pas de stash. Tout passe par des refspecs explicites, parce qu'Axel
travaille dans ce dépôt pendant que la récolte tourne.

Usage :
    python scripts/recolte_dreamcoder.py --dry-run     # ce qui serait publié
    python scripts/recolte_dreamcoder.py               # push + PR
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# ── Valeurs par défaut, alignées sur l'installation réelle ───────────────────
DECK_HOTE = "deck@192.168.1.10"
DECK_CLONE = "/opt/vromvrom-engine_dreamcoder"
DECK_PROD = "/opt/vromvrom-engine"  # porte les rapports de tâche
CLE_DECK = os.path.expanduser("~/.ssh/id_ed25519")

# `task/<id>_<timestamp>` — cf. git_prepare_agent_branch(prefix="task/")
_RE_BRANCHE = re.compile(r"^task/(\d+)_\d+$")


def _run(args: list[str], cwd: str | None = None, env: dict | None = None,
         timeout: float = 120.0) -> tuple[int, str, str]:
    """Exécute une commande sans shell. Retourne (code, stdout, stderr)."""
    try:
        res = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                             text=True, timeout=timeout, encoding="utf-8",
                             errors="replace")
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout après {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def _env_ssh(cle: str) -> dict:
    """
    Env qui force la clé du Deck pour les transports git.

    Sans ça, git prend la clé par défaut de l'agent et se fait refuser
    (`Permission denied (publickey)`) — vérifié sur ce poste.
    """
    return {
        **os.environ,
        "GIT_SSH_COMMAND": f'ssh -i "{cle}" -o StrictHostKeyChecking=no -o BatchMode=yes',
        "GIT_TERMINAL_PROMPT": "0",
    }


def _ssh_deck(hote: str, cle: str, commande: str,
              timeout: float = 60.0) -> tuple[int, str, str]:
    """Exécute une commande sur le Deck (lecture seule dans ce script)."""
    return _run(["ssh", "-i", cle, "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", hote, commande],
                timeout=timeout)


def lister_branches_taches(hote: str, cle: str, clone: str, base: str) -> list[dict]:
    """
    Branches `task/*` du clone Deck qui portent du travail non encore dans `base`.

    Le filtre se fait CÔTÉ DECK (`git branch --no-merged`) : une branche déjà
    fusionnée dans master n'a plus rien à publier, et la récolte doit être
    rejouable sans créer de doublons.

    :raises RuntimeError: si le Deck est injoignable — l'appelant doit pouvoir
        distinguer « rien produit cette nuit » de « je n'ai pas pu regarder ».
    """
    # Les quotes sont indispensables : `%(refname:short)` non protégé est
    # interprété par le shell distant ("syntax error near unexpected token `('").
    commande = (f"cd {clone} && git branch --no-merged {base} "
                f"--format='%(refname:short)'")
    code, sortie, err = _ssh_deck(hote, cle, commande)
    if code != 0:
        # Une panne de lecture n'est PAS « rien à récolter » : rendre 0 ici
        # ferait passer un Deck injoignable pour une nuit sans production.
        raise RuntimeError(f"lecture des branches impossible : {err or sortie}")

    branches = []
    for ligne in sortie.splitlines():
        nom = ligne.strip()
        m = _RE_BRANCHE.match(nom)
        if m:
            branches.append({"branche": nom, "task_id": int(m.group(1))})
    return branches


def lire_rapport_tache(hote: str, cle: str, prod: str, task_id: int) -> dict:
    """
    Rapport écrit par DreamCoder à la fin de la tâche (titre, résumé, coût).

    Il vit dans le runtime de PROD, pas dans le clone : `_ENGINE_ROOT` reste le
    dossier du serveur même quand le travail Git se fait ailleurs (#T202).
    Absent = non bloquant, la PR sera juste plus maigre.
    """
    chemin = f"{prod}/checkpoints/dreamcoder_results/task_{task_id}.json"
    code, sortie, _ = _ssh_deck(hote, cle, f"cat {chemin} 2>/dev/null")
    if code != 0 or not sortie:
        return {}
    try:
        return json.loads(sortie)
    except json.JSONDecodeError:
        return {}


def corps_de_pr(task_id: int, rapport: dict) -> str:
    """Corps de PR : ce que l'agent a fait, ce que ça a coûté, ce qu'il reste à vérifier."""
    titre = rapport.get("title", "(titre indisponible)")
    resume = (rapport.get("summary") or "(pas de résumé)").strip()
    cout = rapport.get("cost_usd")
    jetons = rapport.get("tokens_used")
    cout_texte = f"${cout:.4f}" if isinstance(cout, (int, float)) else "inconnu"

    lignes = [
        "## Tâche autonome DreamCoder",
        "",
        f"**Tâche #{task_id}** — {titre}",
        "",
        "### Ce que l'agent rapporte",
        "",
        resume[:4000],
        "",
        "### Exécution",
        "",
        f"- Coût : {cout_texte}",
        f"- Jetons : {jetons if jetons is not None else 'inconnu'}",
        "- Produit sur le Deck, hors surveillance humaine.",
        "",
        "### À vérifier avant merge",
        "",
        "- [ ] Le diff fait ce que la tâche demandait",
        "- [ ] `pytest tests/unit` au vert en local (la CI GitHub est morte, #T382)",
        "- [ ] Aucun secret, aucune base, aucun fichier de runtime dans le diff",
        "",
        "🤖 Récolté par `scripts/recolte_dreamcoder.py`",
    ]
    return "\n".join(lignes)


def pr_existante(depot: str, branche: str) -> str | None:
    """URL d'une PR déjà ouverte pour cette branche, sinon None (récolte rejouable)."""
    code, sortie, _ = _run(["gh", "pr", "list", "--head", branche, "--state", "all",
                            "--json", "url", "--limit", "1"], cwd=depot)
    if code != 0 or not sortie:
        return None
    try:
        lot = json.loads(sortie)
        return lot[0]["url"] if lot else None
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def recolter(args) -> int:
    """Récolte complète : liste, fetch, push, PR. Retourne un code de sortie."""
    env = _env_ssh(args.cle)
    url_deck = f"{args.hote}:{args.clone}"

    try:
        branches = lister_branches_taches(args.hote, args.cle, args.clone, args.base)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    if not branches:
        print("Aucune branche task/* à récolter — "
              "le backlog n'a rien produit depuis la dernière fois.")
        return 0

    print(f"{len(branches)} branche(s) de tâche trouvée(s) sur le Deck :")
    for b in branches:
        print(f"  · {b['branche']}")
    print()

    # Un seul fetch pour tout le lot, dans un espace de refs dédié : le dépôt
    # local d'Axel ne voit apparaître ni branche locale, ni modification d'index.
    code, _, err = _run(["git", "fetch", url_deck,
                         "+refs/heads/task/*:refs/remotes/deck-dreamcoder/task/*"],
                        cwd=args.depot, env=env, timeout=300.0)
    if code != 0:
        print(f"❌ Fetch du clone Deck impossible : {err}", file=sys.stderr)
        return 1

    echecs = 0
    for b in branches:
        branche, task_id = b["branche"], b["task_id"]

        deja = pr_existante(args.depot, branche)
        if deja:
            print(f"⏭️  {branche} — PR déjà ouverte : {deja}")
            continue

        rapport = lire_rapport_tache(args.hote, args.cle, args.prod, task_id)
        titre_pr = rapport.get("title") or f"Tâche autonome #{task_id}"

        if args.dry_run:
            print(f"[dry-run] pousserait {branche} et ouvrirait la PR « {titre_pr} »")
            continue

        code, _, err = _run(["git", "push", args.remote,
                             f"refs/remotes/deck-dreamcoder/{branche}:refs/heads/{branche}"],
                            cwd=args.depot, env=env, timeout=300.0)
        if code != 0:
            print(f"❌ {branche} — push refusé : {err[:200]}", file=sys.stderr)
            echecs += 1
            continue

        code, sortie, err = _run(
            ["gh", "pr", "create", "--base", args.base, "--head", branche,
             "--title", f"[auto] {titre_pr}", "--body", corps_de_pr(task_id, rapport)],
            cwd=args.depot, timeout=120.0)
        if code != 0:
            print(f"⚠️  {branche} — poussée, mais PR non créée : {err[:200]}", file=sys.stderr)
            echecs += 1
            continue

        print(f"✅ {branche} → {sortie.splitlines()[-1] if sortie else 'PR créée'}")

    return 1 if echecs else 0


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Publie les branches task/* produites par DreamCoder sur le Deck.")
    parseur.add_argument("--hote", default=DECK_HOTE,
                         help=f"hôte SSH du Deck (défaut : {DECK_HOTE})")
    parseur.add_argument("--clone", default=DECK_CLONE,
                         help="clone de travail DreamCoder sur le Deck")
    parseur.add_argument("--prod", default=DECK_PROD,
                         help="racine du runtime de prod (rapports de tâche)")
    parseur.add_argument("--cle", default=CLE_DECK, help="clé SSH du Deck")
    parseur.add_argument("--depot", default=".", help="dépôt local depuis lequel pousser")
    parseur.add_argument("--remote", default="origin", help="remote GitHub cible")
    parseur.add_argument("--base", default="master", help="branche de base des PR")
    parseur.add_argument("--dry-run", action="store_true",
                         help="montre ce qui serait publié, sans rien pousser ni ouvrir")
    args = parseur.parse_args()

    if not os.path.exists(args.cle):
        print(f"❌ Clé SSH introuvable : {args.cle}", file=sys.stderr)
        return 1

    return recolter(args)


if __name__ == "__main__":
    sys.exit(main())
