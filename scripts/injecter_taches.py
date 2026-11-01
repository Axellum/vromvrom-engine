#!/usr/bin/env python3
"""
scripts/injecter_taches.py — Charge un lot de tâches dans le backlog du moteur.

Deux entrées possibles :

  1. Un Markdown structuré — une tâche par section de niveau 2 :

         ## [P1] Titre court de la tâche
         Description complète, autonome : l'agent n'aura ni le board, ni cette
         conversation, ni le reste du fichier sous les yeux.

         ## [P3] Autre tâche
         …

     La priorité `[P<n>]` est optionnelle (défaut : 2).

  2. Du texte libre (`--texte` ou `--fichier` sur un .txt), découpé en tâches
     par DeepSeek. Le découpage est une source d'erreur de plus : le résultat
     est TOUJOURS affiché pour relecture avant envoi.

Par défaut le script n'envoie RIEN : il affiche ce qui partirait. `--envoyer`
pousse pour de bon.

Usage :
    python scripts/injecter_taches.py lot.md
    python scripts/injecter_taches.py lot.md --envoyer
    python scripts/injecter_taches.py --texte "fais ceci, puis cela" --envoyer
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

DECK_URL = "http://192.168.1.10:8000"
CLE_SSH = os.path.expanduser("~/.ssh/id_ed25519")
DECK_HOTE = "deck@192.168.1.10"
DECK_ENV = "/opt/vromvrom-engine/.env"

# Rappel ajouté à CHAQUE description. L'agent de nuit n'a ni le board, ni
# l'historique : ce qui n'est pas écrit ici n'existe pas pour lui. Les trois
# points listés sont ceux qui ont réellement coûté du travail refait.
PREAMBULE = (
    "Contexte d'exécution : tu travailles seul, sans supervision, dans un clone "
    "Git dédié. Personne ne peut répondre à une question pendant l'exécution.\n\n"
    "Règles :\n"
    "- Vérifie l'état RÉEL du code avant d'écrire : la tâche a pu être faite "
    "depuis sa rédaction. Si c'est déjà fait, dis-le et ne touche à rien.\n"
    "- Utilise des chemins ABSOLUS pour tes propres commandes (lire, écrire, "
    "explorer). En revanche, le CODE que tu écris ne doit JAMAIS contenir de "
    "chemin absolu : il sera exécuté sur d'autres machines que la tienne. Dans "
    "un test, passe par `tmp_path`, `Path(__file__)` ou un chemin relatif.\n"
    "- Ne déclare un succès qu'après vérification réelle (test exécuté, fichier "
    "relu). Jamais à l'aveugle.\n"
    "- Ne touche à aucun fichier hors du périmètre décrit.\n"
    "- Pour COPIER un fichier existant (depuis une autre branche, un autre "
    "dossier), utilise la commande qui le copie — `git checkout <branche> -- "
    "<chemin>`, `cp` — JAMAIS « lis-le puis réécris-le ». Un contenu qui "
    "transite par ta fenêtre en ressort tronqué sans que rien ne le signale.\n\n"
    "Tâche :\n\n"
)

_RE_SECTION = re.compile(r"^##\s+(?:\[P(?P<prio>\d)\]\s*)?(?P<titre>.+?)\s*$", re.MULTILINE)


# ── Lecture des entrées ──────────────────────────────────────────────────────

def taches_depuis_markdown(contenu: str) -> list[dict]:
    """Découpe un Markdown en tâches, une par section de niveau 2."""
    contenu = (contenu or "").lstrip("\ufeff")
    taches: list[dict] = []
    sections = list(_RE_SECTION.finditer(contenu))
    for i, m in enumerate(sections):
        debut = m.end()
        fin = sections[i + 1].start() if i + 1 < len(sections) else len(contenu)
        description = contenu[debut:fin].strip()
        if not description:
            continue
        taches.append({
            "title": m.group("titre").strip(),
            "description": description,
            "priority": int(m.group("prio")) if m.group("prio") else 2,
        })
    return taches


def _cle_depuis_env_fichier(chemin: str, nom: str) -> str:
    """Lit `NOM=valeur` dans un .env, sans jamais journaliser la valeur."""
    if not os.path.exists(chemin):
        return ""
    with open(chemin, encoding="utf-8", errors="replace") as f:
        for ligne in f.read().splitlines():
            if ligne.strip().startswith(f"{nom}="):
                return ligne.partition("=")[2].strip().strip('"').strip("'")
    return ""


def cle_deepseek() -> str:
    """Clé locale, sinon celle du Deck (le script tourne depuis le PC)."""
    local = _cle_depuis_env_fichier(os.path.join(os.getcwd(), ".env"), "DEEPSEEK_API_KEY")
    if local:
        return local
    res = subprocess.run(
        ["ssh", "-i", CLE_SSH, "-o", "ConnectTimeout=8", DECK_HOTE,
         f"grep -m1 '^DEEPSEEK_API_KEY=' {DECK_ENV} | cut -d= -f2- | tr -d '\\r\\n'"],
        capture_output=True, text=True, timeout=30)
    return res.stdout.strip()


def taches_depuis_texte_libre(texte: str) -> list[dict]:
    """
    Fait découper un pavé de texte en tâches par DeepSeek.

    DeepSeek est le gouverneur du moteur : c'est lui qui planifie et relit les
    tâches de nuit. Autant que ce soit lui qui les délimite — le découpage
    hérite du même jugement que l'exécution.
    """
    cle = cle_deepseek()
    if not cle:
        print("❌ DEEPSEEK_API_KEY introuvable (ni en local, ni sur le Deck).", file=sys.stderr)
        return []

    consigne = (
        "Tu découpes une demande en tâches de développement INDÉPENDANTES, "
        "exécutables chacune par un agent séparé, en parallèle, sans se parler.\n"
        "Contraintes :\n"
        "- Chaque tâche doit tenir en ~10 minutes de travail.\n"
        "- Deux tâches ne doivent PAS modifier le même fichier (elles tournent "
        "en parallèle sur des clones distincts et fusionneraient mal).\n"
        "- La description doit être autonome : chemins, critère de succès, "
        "et ce qu'il ne faut PAS toucher.\n"
        "- priority : 1 = haute, 2 = normale, 3 = basse.\n"
        'Réponds UNIQUEMENT par du JSON : {"taches":[{"title":…,"description":…,"priority":…}]}'
    )
    corps = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": consigne},
                     {"role": "user", "content": texte}],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }).encode()

    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
                                 data=corps, method="POST")
    req.add_header("Authorization", f"Bearer {cle}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            brut = json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        print(f"❌ DeepSeek HTTP {e.code} : {e.read().decode()[:200]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"❌ Découpage impossible : {e}", file=sys.stderr)
        return []

    try:
        lot = json.loads(brut).get("taches", [])
    except json.JSONDecodeError:
        print(f"❌ DeepSeek n'a pas rendu de JSON exploitable : {brut[:200]}", file=sys.stderr)
        return []

    return [{"title": str(t.get("title", "")).strip(),
             "description": str(t.get("description", "")).strip(),
             "priority": int(t.get("priority", 2))}
            for t in lot if t.get("title") and t.get("description")]


# ── Contrôles avant envoi ────────────────────────────────────────────────────

def problemes(taches: list[dict]) -> list[str]:
    """
    Défauts qui coûteraient une nuit de travail. Bloquants, pas cosmétiques.

    Le doublon de titre est traité comme un vrai défaut : deux tâches du même
    nom donnent deux branches presque identiques et une PR à jeter.
    """
    trouves: list[str] = []
    vus: set[str] = set()
    for i, t in enumerate(taches, 1):
        if len(t["description"]) < 40:
            trouves.append(
                f"tâche {i} « {t['title'][:40]} » : description de "
                f"{len(t['description'])} caractères — trop maigre pour être exécutée seule")
        if t["priority"] not in (0, 1, 2, 3):
            trouves.append(f"tâche {i} : priorité {t['priority']} hors de 0-3")
        cle = t["title"].strip().lower()
        if cle in vus:
            trouves.append(f"tâche {i} : titre en double « {t['title'][:40]} »")
        vus.add(cle)
    return trouves


def _cible_est_deck(url: str) -> bool:
    """Le défaut d'envoi est le Deck ; localhost / 127.0.0.1 = moteur PC."""
    hote = (url or "").lower()
    return "192.168.1.10" in hote


def _cle_depuis_ssh_deck() -> str:
    """Lit MOTEUR_API_KEY du .env Deck. Jamais journalisée."""
    try:
        res = subprocess.run(
            ["ssh", "-i", CLE_SSH, "-o", "ConnectTimeout=8", DECK_HOTE,
             f"grep -m1 '^MOTEUR_API_KEY=' {DECK_ENV} | cut -d= -f2- | tr -d '\\r\\n'"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (res.stdout or "").strip()


def cle_api_moteur(url: str | None = None) -> str:
    """
    Clé du moteur *cible*, pas celle de la machine qui lance le script.

    PC et Deck ont chacun leur `MOTEUR_API_KEY` (mesuré : longueurs
    distinctes). Prendre le `.env` local pour un POST vers le Deck
    rend HTTP 401 sur les 13 tâches. Donc : URL Deck → SSH Deck,
    sans repli sur la clé PC. URL locale / absente → `.env` du CWD,
    SSH seulement s'il n'y a pas de clé locale (script lancé sur le Deck
    sans fichier, cas rare).
    """
    if url and _cible_est_deck(url):
        return _cle_depuis_ssh_deck()
    local = _cle_depuis_env_fichier(os.path.join(os.getcwd(), ".env"), "MOTEUR_API_KEY")
    if local:
        return local
    return _cle_depuis_ssh_deck()


def cle_de_lot(origine: str, titre: str) -> str:
    """
    Empreinte stable d'une tâche dans son lot : `lot:<origine>#<titre>`.

    Le backlog refuse depuis #T397 une `discovery_key` déjà vue, quel que soit
    le statut de la tâche d'origine. Mais cette route n'en recevait jamais :
    toute injection de lot passait avec `NULL`, donc sans protection. Mesuré
    le 24/08 : le lot de la semaine injecté deux fois → **13 paires** de tâches
    identiques, chacune payée deux fois, jusqu'à 3,8 M de jetons pour une
    seule d'entre elles.

    L'empreinte porte le nom du fichier de lot ET le titre : réinjecter le même
    fichier ne crée rien, mais un lot différent qui reprend un titre reste
    libre de passer. Le texte libre (`--texte`) n'a pas d'origine stable : il
    garde `None` et le comportement d'avant.
    """
    empreinte_titre = hashlib.sha256(titre.strip().encode("utf-8")).hexdigest()[:12]
    return f"lot:{origine}#{empreinte_titre}"


def envoyer(taches: list[dict], url: str, prefixer: bool,
            origine: str | None = None) -> int:
    cle = cle_api_moteur(url)
    if not cle:
        if _cible_est_deck(url):
            print("❌ MOTEUR_API_KEY Deck introuvable (SSH). "
                  "Ne pas envoyer la clé PC : les deux .env divergent.",
                  file=sys.stderr)
        else:
            print("❌ MOTEUR_API_KEY introuvable (ni .env local, ni SSH Deck).",
                  file=sys.stderr)
        return 1

    echecs = 0
    for t in taches:
        charge = {
            "title": t["title"],
            "description": (PREAMBULE + t["description"]) if prefixer else t["description"],
            "priority": t["priority"],
        }
        if origine:
            charge["discovery_key"] = cle_de_lot(origine, t["title"])
        req = urllib.request.Request(f"{url}/api/backlog/tasks",
                                     data=json.dumps(charge).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {cle}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                rep = json.loads(r.read())
                print(f"  ✅ #{rep.get('task_id')} P{t['priority']} — {t['title'][:60]}")
        except urllib.error.HTTPError as e:
            extra = ""
            if e.code == 401 and _cible_est_deck(url):
                extra = " — clé refusée par le Deck (PC et Deck n'ont pas la même MOTEUR_API_KEY)"
            print(f"  ❌ {t['title'][:60]} : HTTP Error {e.code}: {e.reason}{extra}",
                  file=sys.stderr)
            echecs += 1
        except Exception as e:
            print(f"  ❌ {t['title'][:60]} : {e}", file=sys.stderr)
            echecs += 1
    return 1 if echecs else 0


def apercu(taches: list[dict]) -> None:
    print(f"\n{len(taches)} tâche(s) prête(s) :\n")
    for i, t in enumerate(taches, 1):
        premiere = t["description"].strip().splitlines()[0] if t["description"].strip() else ""
        print(f"  {i}. [P{t['priority']}] {t['title']}")
        print(f"       {premiere[:95]}")
        print(f"       ({len(t['description'])} caractères de description)")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Charge un lot de tâches dans le backlog du moteur.")
    p.add_argument("fichier", nargs="?", help="Markdown structuré (## par tâche) ou .txt libre")
    p.add_argument("--texte", help="texte libre passé directement, découpé par DeepSeek")
    p.add_argument("--decouper", action="store_true",
                   help="force le découpage DeepSeek même sur un .md")
    p.add_argument("--envoyer", action="store_true",
                   help="pousse réellement (sans ce drapeau : aperçu seul)")
    p.add_argument("--sans-preambule", action="store_true",
                   help="n'ajoute pas le rappel de contexte aux descriptions")
    p.add_argument("--url", default=DECK_URL, help=f"moteur cible (défaut : {DECK_URL})")
    args = p.parse_args()

    if not args.fichier and not args.texte:
        p.error("il faut un fichier ou --texte")

    if args.texte:
        contenu, libre = args.texte, True
    else:
        if not os.path.exists(args.fichier):
            print(f"❌ Fichier introuvable : {args.fichier}", file=sys.stderr)
            return 1
        with open(args.fichier, encoding="utf-8-sig") as f:
            contenu = f.read()
        libre = args.decouper or not args.fichier.lower().endswith(".md")

    if libre:
        print("Découpage du texte libre par DeepSeek…")
        taches = taches_depuis_texte_libre(contenu)
    else:
        taches = taches_depuis_markdown(contenu)

    if not taches:
        print("❌ Aucune tâche exploitable en entrée.", file=sys.stderr)
        return 1

    apercu(taches)

    soucis = problemes(taches)
    if soucis:
        print("⚠️  À corriger avant envoi :")
        for s in soucis:
            print(f"   · {s}")
        print()
        return 1

    if not args.envoyer:
        print("Aperçu seulement. Relance avec --envoyer pour pousser dans le backlog.")
        return 0

    print(f"Envoi vers {args.url} …")
    # Un fichier de lot a une origine stable : son nom sert d'empreinte et rend
    # la réinjection sans effet. Le texte libre n'en a pas — il garde `None`.
    origine = os.path.basename(args.fichier) if args.fichier and not args.texte else None
    return envoyer(taches, args.url, prefixer=not args.sans_preambule,
                   origine=origine)


if __name__ == "__main__":
    sys.exit(main())
