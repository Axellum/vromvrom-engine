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

Hôte du Deck — trois niveaux de priorité (option > variable d'environnement >
défaut) :
    --hote <hôte>                    # priorité la plus haute
    MOTEUR_RECOLTE_HOTE=<hôte>       # surcharge machine, non suivie par git
    (défaut) DECK_HOTE               # valeur historique, rien ne casse

L'adresse du Deck n'est pas dupliquée ici : elle vit dans la constante
`DECK_HOTE` (valeur par défaut exigée par la tâche) et doit être surchargée
via `MOTEUR_RECOLTE_HOTE` sur toute machine qui n'est pas le Deck. Ne jamais
commiter d'adresse en clair ailleurs dans le dépôt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# Le compteur de rendement vit dans le moteur (`core/`) : ce script tourne
# depuis le clone local, on ajoute la racine au path pour l'alimenter. Absent
# (moteur non installé ici) → la récolte fonctionne quand même, sans compteur.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from core.rendement_dreamcoder import enregistrer_recolte, synchroniser_merges
except Exception:  # pragma: no cover — dépend de l'installation locale
    enregistrer_recolte = None
    synchroniser_merges = None

# ── Valeurs par défaut, alignées sur l'installation réelle ───────────────────
# DECK_HOTE est la valeur historique : la tâche exige qu'elle reste inchangée
# pour ne rien casser. Toute autre machine doit surcharger via MOTEUR_RECOLTE_HOTE.
DECK_HOTE = "deck@192.168.1.10"
DECK_CLONE = "/opt/vromvrom-engine_dreamcoder"  # repli historique
DECK_PROD = "/opt/vromvrom-engine"  # porte les rapports de tâche
CLE_DECK = os.path.expanduser("~/.ssh/id_ed25519")

# Surcharge machine, propre à l'hôte qui lance la récolte : config.json est
# suivi par git, ce réglage ne doit pas y entrer. Priorité : option argparse >
# cette variable > DECK_HOTE.
ENV_HOTE = "MOTEUR_RECOLTE_HOTE"


def clone_dreamcoder_du_deck(hote: str, cle: str, prod: str) -> str | None:
    """
    [#T407] Où DreamCoder travaille VRAIMENT, d'après la config du Deck.

    Le 18/08, la récolte a annoncé « 0 branche sur le Deck » alors que quatre
    branches `task/*` venaient d'être produites : ce script visait
    `moteur_agents_dreamcoder`, tandis que le moteur avait été reconfiguré sur
    `moteur_agents_dc3` (`persistent_agents.dreamcoder_repo_paths`). Deux
    notions du même dossier, dans deux fichiers, qui ont divergé en silence —
    et la récolte a rendu un « rien à faire » parfaitement rassurant.

    La source de vérité est la config du moteur. En cas d'échec de lecture, on
    garde le repli historique plutôt que d'interrompre une récolte.
    """
    code, sortie, _err = _run([
        "ssh", "-i", cle, "-o", "ConnectTimeout=8", hote,
        f"cat {prod}/config.json",
    ], timeout=30.0)
    if code != 0 or not sortie:
        return None
    try:
        config = json.loads(sortie)
    except json.JSONDecodeError:
        return None
    pa = config.get("persistent_agents", {}) or {}
    chemins = pa.get("dreamcoder_repo_paths") or []
    if isinstance(chemins, str):
        chemins = [chemins]
    chemins = [c for c in chemins if c] or [pa.get("dreamcoder_repo_path") or ""]
    chemins = [c for c in chemins if c]
    return chemins[0] if chemins else None


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


def _base_du_depot(depot: str) -> str:
    """
    Branche par défaut du dépôt visé, `master` si rien ne répond.

    `gh` d'abord (il fait autorité et il est déjà requis par ce script), puis
    `origin/HEAD` du clone local. Sans ça, la récolte ne publiait que vers
    `master` : rien ne pouvait atterrir sur un dépôt en `main`.
    """
    code, sortie, _ = _run(
        ["gh", "repo", "view", "--json", "defaultBranchRef",
         "--jq", ".defaultBranchRef.name"], cwd=depot, timeout=30.0)
    if code == 0 and sortie.strip():
        return sortie.strip()

    code, sortie, _ = _run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=depot, timeout=15.0)
    if code == 0 and sortie.strip():
        return sortie.strip().split("/", 1)[-1]

    return "master"


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
        # [#T421] Le résumé est désormais construit par le moteur : mesures
        # (fichiers du commit, rejeu des tests, coût) puis prose de l'agent,
        # séparées et étiquetées. Avant, ce champ recevait le transcript brut du
        # DAG — « Plan DAG généré avec 6 étapes », blocs `<think>`, dumps de
        # `ls` — et aucune PR de nuit n'était lisible sans ouvrir le diff.
        "### Ce qui a été livré",
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
        "- [ ] La CI de la PR est verte (elle tourne — le diagnostic « quota "
        "Actions épuisé » de #T382 était faux)",
        "- [ ] Aucun secret, aucune base, aucun fichier de runtime dans le diff",
        "",
        "🤖 Récolté par `scripts/recolte_dreamcoder.py`",
    ]
    return "\n".join(lignes)


def pr_existante(depot: str, branche: str) -> str | None:
    """
    URL d'une PR DÉJÀ JUGÉE pour cette branche, sinon None (récolte rejouable).

    [#T407] `--state all` est volontaire : une PR **fermée** signifie que le
    travail a été examiné et REJETÉ, pas qu'il reste à faire. La re-récolter
    reproduirait la même PR et redemanderait la même relecture.

    Le libellé rend l'état, parce que l'ancien message (« PR déjà ouverte »)
    affiché sur une PR fermée a induit en erreur le 18/08 : dix branches de la
    veille semblaient en attente de relecture alors qu'elles avaient toutes été
    rejetées.
    """
    code, sortie, _ = _run(["gh", "pr", "list", "--head", branche, "--state", "all",
                            "--json", "url,state", "--limit", "1"], cwd=depot)
    if code != 0 or not sortie:
        return None
    try:
        lot = json.loads(sortie)
        if not lot:
            return None
        # L'état n'est ajouté que s'il est connu : le contrat de retour reste
        # « une URL, ou None », que les appelants testent par vérité booléenne.
        etat = {"OPEN": "ouverte", "CLOSED": "fermée (rejetée)",
                "MERGED": "mergée"}.get(lot[0].get("state", ""), "")
        url = lot[0]["url"]
        return f"{url} [{etat}]" if etat else url
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def lister_branches_distantes(depot: str) -> list[dict]:
    """
    Branches `task/*` déjà présentes sur `origin` (GitHub), sans passage par le Deck.

    Depuis #T356 le Deck pousse ses branches LUI-MÊME par jeton, et l'outil de
    ménage des branches d'agent (#T372) peut ensuite nettoyer les branches
    LOCALES du clone. Conséquence mesurée le 18/08 : 4 branches task/* étaient
    sur GitHub sans PR, invisibles de la récolte qui ne lisait que le Deck —
    le travail était orphelin au stade récolte. Ce repli les repère via
    `git ls-remote` : elles n'ont plus besoin d'être fetchées/poussées, il ne
    manque que la PR.
    """
    code, sortie, _ = _run(["git", "ls-remote", "origin", "refs/heads/task/*"], cwd=depot)
    branches = []
    if code != 0:
        return branches
    for ligne in sortie.splitlines():
        if "\t" not in ligne:
            continue
        nom = ligne.split("\t", 1)[1].removeprefix("refs/heads/")
        m = _RE_BRANCHE.match(nom)
        if m:
            branches.append({"branche": nom, "task_id": int(m.group(1)),
                             "sur_github": True})
    return branches


def recolter(args) -> int:
    """Récolte complète : liste, fetch, push, PR. Retourne un code de sortie."""
    env = _env_ssh(args.cle)
    # [#T407] La config du moteur prime sur la valeur par défaut de ce script.
    clone_configure = clone_dreamcoder_du_deck(args.hote, args.cle, args.prod)
    if clone_configure and clone_configure != args.clone:
        print(f"ℹ️  clone DreamCoder lu dans la config du Deck : {clone_configure} "
              f"(défaut du script : {args.clone})")
        args.clone = clone_configure

    url_deck = f"{args.hote}:{args.clone}"

    try:
        branches_deck = lister_branches_taches(args.hote, args.cle, args.clone, args.base)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    # Les branches que le Deck a poussées LUI-MÊME (#T356) sont déjà sur
    # GitHub : la récolte doit les voir même si le clone du Deck a été nettoyé.
    branches_github = lister_branches_distantes(args.depot)
    vues = {b["branche"] for b in branches_deck}
    branches = branches_deck + [b for b in branches_github if b["branche"] not in vues]

    # Une même tâche rejouée laisse plusieurs branches task/{id}_{ts} : seule
    # la plus récente porte le dernier état du travail. Récolter les deux
    # ouvrirait des PR en double du même diff.
    par_tache: dict[int, dict] = {}
    for b in branches:
        existante = par_tache.get(b["task_id"])
        if existante is None or b["branche"] > existante["branche"]:
            par_tache[b["task_id"]] = b
    ecartees = [b for b in branches if par_tache.get(b["task_id"]) is not b]
    branches = sorted(par_tache.values(), key=lambda b: b["task_id"])
    for b in ecartees:
        print(f"⏭️  {b['branche']} — rejeu plus ancien de la tâche {b['task_id']}, ignorée.")

    a_pousser = [b for b in branches if not b.get("sur_github")]

    if not branches:
        print("Aucune branche task/* à récolter — "
              "le backlog n'a rien produit depuis la dernière fois.")
        return 0

    print(f"{len(branches)} branche(s) de tâche trouvée(s) "
          f"({len(branches_deck)} sur le Deck, {len(branches) - len(branches_deck)} déjà sur GitHub) :")
    for b in branches:
        suffixe = " [déjà sur GitHub]" if b.get("sur_github") else ""
        print(f"  · {b['branche']}{suffixe}")
    print()

    # Un seul fetch pour tout le lot, dans un espace de refs dédié : le dépôt
    # local d'Axel ne voit apparaître ni branche locale, ni modification d'index.
    # Inutile pour les branches déjà sur GitHub — il ne leur manque que la PR.
    if a_pousser:
        code, _, err = _run(["git", "fetch", url_deck,
                             "+refs/heads/task/*:refs/remotes/deck-dreamcoder/task/*"],
                            cwd=args.depot, env=env, timeout=300.0)
        if code != 0:
            print(f"❌ Fetch du clone Deck impossible : {err}", file=sys.stderr)
            return 1

    echecs = 0
    recoltees: list[dict] = []
    for b in branches:
        branche, task_id = b["branche"], b["task_id"]

        deja = pr_existante(args.depot, branche)
        if deja:
            print(f"⏭️  {branche} — PR déjà jugée : {deja}")
            recoltees.append({"branche": branche, "pr_url": deja})
            continue

        rapport = lire_rapport_tache(args.hote, args.cle, args.prod, task_id)
        titre_pr = rapport.get("title") or f"Tâche autonome #{task_id}"

        if args.dry_run:
            action = ("ouvrirait"
                      if b.get("sur_github")
                      else "pousserait puis ouvrirait")
            print(f"[dry-run] {action} la PR « {titre_pr} » pour {branche}")
            continue

        if not b.get("sur_github"):
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

        url_pr = sortie.splitlines()[-1] if sortie else ""
        recoltees.append({"branche": branche, "pr_url": url_pr})
        print(f"✅ {branche} → {url_pr or 'PR créée'}")

    # Entonnoir de rendement, stades RÉCOLTÉ puis MERGÉ — best effort : un
    # compteur indisponible ne doit jamais faire échouer la récolte elle-même.
    if not args.dry_run and recoltees and enregistrer_recolte is not None:
        try:
            enregistrer_recolte(recoltees)
            if synchroniser_merges is not None:
                nouveaux = synchroniser_merges(args.depot)
                if nouveaux:
                    print(f"📈 {nouveaux} PR(s) de tâche mergée(s) constatée(s) en plus.")
        except Exception as e:
            print(f"⚠️  Compteur de rendement non alimenté (non bloquant) : {e}",
                  file=sys.stderr)

    return 1 if echecs else 0


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Publie les branches task/* produites par DreamCoder sur le Deck.")
    # Priorité : option argparse > variable d'environnement MOTEUR_RECOLTE_HOTE
    # > DECK_HOTE. La variable est lue au moment du parse, donc une surcharge
    # machine n'entre jamais dans config.json (suivi par git).
    hote_defaut = os.environ.get(ENV_HOTE, DECK_HOTE)
    parseur.add_argument("--hote", default=hote_defaut,
                         help=f"hôte SSH du Deck (défaut : {hote_defaut})")
    parseur.add_argument("--clone", default=DECK_CLONE,
                         help="clone de travail DreamCoder sur le Deck")
    parseur.add_argument("--prod", default=DECK_PROD,
                         help="racine du runtime de prod (rapports de tâche)")
    parseur.add_argument("--cle", default=CLE_DECK, help="clé SSH du Deck")
    parseur.add_argument("--depot", default=".", help="dépôt local depuis lequel pousser")
    parseur.add_argument("--remote", default="origin", help="remote GitHub cible")
    # Défaut `None` : la base est déduite du dépôt visé au moment de la récolte.
    # En dur à `master`, ce script ne pouvait rien publier sur un dépôt en
    # `main` — c'est ce qui a laissé 14 branches Tab5 sans PR (24/08).
    parseur.add_argument("--base", default=None,
                         help="branche de base des PR (défaut : branche par "
                              "défaut du dépôt)")
    parseur.add_argument("--dry-run", action="store_true",
                         help="montre ce qui serait publié, sans rien pousser ni ouvrir")
    args = parseur.parse_args()

    if not os.path.exists(args.cle):
        print(f"❌ Clé SSH introuvable : {args.cle}", file=sys.stderr)
        return 1

    if not args.base:
        args.base = _base_du_depot(args.depot)
        print(f"ℹ️  Base des PR déduite du dépôt : {args.base}")

    return recolter(args)


if __name__ == "__main__":
    sys.exit(main())
