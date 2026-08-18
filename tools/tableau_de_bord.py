"""
tools/tableau_de_bord.py — Les chiffres de prod qui referment les tâches (#T245, #T253, #T240, #T243).

Le moteur accumulait des livraisons déployées et non mesurées : #T240 (générations
structurées réparées), #T245 (fan-out DAG), #T253-A (contrat d'acceptation) portent
toutes la même mention « reste à vérifier en prod ». Écrire encore du code avant de
mesurer celui d'hier, c'est le mode d'échec que le board documente depuis juin.

Ce script sort, en une commande, les chiffres qui tranchent — et il les compare aux
**valeurs de référence relevées AVANT déploiement**, pour qu'un écart se lise sans
avoir à se souvenir de rien.

Deux sources, parce qu'aucune ne suffit :
- `moteur_runtime.db` : tâches DAG, tiers demandés, appels et coûts par modèle ;
- le **journal systemd** : les verdicts de contrat et les échecs de circuit breaker
  ne sont écrits nulle part en base (l'event store ne porte que `request_received`
  et `response_sent`, vérifié le 10/08). Sans le journal, ces deux sections sont
  aveugles — le script le dit au lieu d'afficher zéro.

Usage :
    python tools/tableau_de_bord.py                 # bases locales, journal si dispo
    python tools/tableau_de_bord.py --jours 7       # fenêtre de journal élargie
    python tools/tableau_de_bord.py --json          # sortie machine
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITE_SYSTEMD = "moteur_agents.service"

# ── Références relevées le 10/08/2026 AVANT le déploiement de #T245 ────────
# (mesurées sur la prod du Deck, `moteur_runtime.db` en lecture seule)
REFERENCE = {
    "taches_dag": 48,
    "sessions_dag": 27,
    "fanout_max": 2,
    "taches_mapreduce": 0,
    "tiers_demandes": {"leger": 31, "moyen": 17, "fort": 0},
    "modeles_appeles": 10,
}


# ══════════════════════════════════════════════════════════════════
# Sources
# ══════════════════════════════════════════════════════════════════

def ouvrir(base: str, racine: str | None = None) -> sqlite3.Connection | None:
    """
    Ouvre une base en lecture seule, cherchée dans l'ordre : racine explicite,
    dossier du dépôt, puis répertoire courant. Le repli par le CWD compte : le
    script est souvent exécuté depuis /tmp sur le Deck, et sans lui il annonçait
    « base introuvable » alors que la base était sous les pieds de l'appelant.
    """
    for candidat in (racine, RACINE, os.getcwd()):
        if not candidat:
            continue
        chemin = base if os.path.isabs(base) else os.path.join(candidat, base)
        if os.path.exists(chemin):
            return sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    return None


def lire_journal(jours: int) -> tuple[list[str], str | None]:
    """Lignes du journal systemd, ou (liste vide, raison) si indisponible."""
    if not shutil.which("journalctl"):
        return [], "journalctl absent (poste Windows ?) — sections journal aveugles"
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}") if hasattr(os, "getuid") else None
    try:
        proc = subprocess.run(
            ["journalctl", "--user", "-u", UNITE_SYSTEMD, "--since", f"{jours} days ago", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=120, env=env,
        )
    except Exception as err:
        return [], f"lecture du journal impossible : {err}"
    if proc.returncode != 0 and not proc.stdout:
        return [], f"journalctl a échoué : {(proc.stderr or '').strip()[:120]}"
    return proc.stdout.splitlines(), None


# ══════════════════════════════════════════════════════════════════
# Mesures
# ══════════════════════════════════════════════════════════════════

def mesurer_dag(conn) -> dict:
    """#T245 — le fan-out est-il devenu réel ?"""
    lignes = conn.execute("select session_id, task_id, inputs_json from dag_tasks").fetchall()
    par_session, tiers, roles = {}, Counter(), Counter()
    for session_id, _task_id, inputs in lignes:
        try:
            meta = (json.loads(inputs) or {}).get("metadata") or {}
        except Exception:
            meta = {}
        par_session.setdefault(session_id, Counter())[meta.get("stage_id")] += 1
        tiers[meta.get("model_tier")] += 1
        if meta.get("node_tier_role"):
            roles[meta["node_tier_role"]] += 1

    fanouts = [max(c.values()) for c in par_session.values()] or [0]
    return {
        "taches_dag": len(lignes),
        "sessions_dag": len(par_session),
        "sessions_avec_stage_parallele": sum(1 for f in fanouts if f >= 2),
        "fanout_max": max(fanouts),
        "taches_mapreduce": sum(1 for _, t, _ in lignes if "mapreduce" in (t or "")),
        "tiers_demandes": dict(tiers),
        "roles_de_noeud": dict(roles),
    }


def mesurer_exploitation(conn) -> dict:
    """#T243 — quelle part du portefeuille tourne vraiment, et pour combien ?"""
    modeles = conn.execute(
        "select model, count(*), coalesce(sum(cost_usd), 0), coalesce(sum(total_tokens), 0) "
        "from token_usage group by model order by count(*) desc"
    ).fetchall()
    canaux = dict(conn.execute("select channel, count(*) from token_usage group by channel").fetchall())
    return {
        "modeles_appeles": len(modeles),
        "appels_total": sum(n for _, n, _, _ in modeles),
        "cout_cumule_usd": round(sum(c for _, _, c, _ in modeles), 4),
        "tokens_cumules": sum(t for _, _, _, t in modeles),
        "top_modeles": [
            {"modele": m, "appels": n, "cout_usd": round(c, 4)} for m, n, c, _ in modeles[:5]
        ],
        "canaux": canaux,
    }


MOTIFS = {
    # re.I : les messages de `contrat_seul` (chemin DAG en échec) mettent parfois
    # « contrat » en minuscule / « SATISFAIT » en majuscules — sans ça le tableau
    # de bord rate les verdicts du chemin post-échec (#T253).
    "contrat_satisfait": re.compile(
        r"\[T253\].*(?:Contrat d'acceptation satisfait|contrat d'acceptation est SATISFAIT)",
        re.I,
    ),
    "contrat_echec": re.compile(
        r"\[T253\].*(?:Contrat d'acceptation en échec|contrat non satisfait)",
        re.I,
    ),
    "contrat_indeterminable": re.compile(r"\[T253\].*contrat non déterminable", re.I),
    # [#T253] Distingue « le Planner n'a produit aucun critère » de « la revue n'a
    # pas tourné » : les deux donnaient « 0 contrat évalué » le 10/08, avec des
    # remèdes opposés.
    "plans_sans_contrat": re.compile(r"\[T253\] Aucun critère d'acceptation produit"),
    "plans_avec_contrat": re.compile(r"\[T253\] Contrat d'acceptation : \d+ critère"),
    "contrat_sur_dag_echoue": re.compile(r"\[T253\].*DAG a signalé une erreur, mais le contrat"),
    "plans_normalises": re.compile(r"\[T245\] Tiers normalisés"),
    "mapreduce_demarre": re.compile(r"MapReduce dynamique démarré"),
    "outil_map_reduce": re.compile(r"\[MAP_REDUCE\] Outil déclenché"),
    "revue_llm": re.compile(r"\[REVIEW\] (✅|❌) Round"),
}
CB_OUVERT = re.compile(r"Circuit ouvert pour le (?:modèle|modèle structuré|stream) '([^']+)'")
CB_ECHEC = re.compile(r"Échec du modèle ([^\s:]+)")
# Le nom du modèle est suivi d'un point de phrase (« … sur X. Disjoncteur
# déclenché. »), mais les noms de modèles CONTIENNENT des points
# (`gemini-3.5-flash`) : on capture jusqu'à l'espace puis on retire le point
# final. Exclure le point dans la classe donnerait « gemini-3 ».
CB_DECLENCHE = re.compile(r"\[CIRCUIT BREAKER\] Rate limit \(429\) détecté sur (\S+)")


def analyser_journal(lignes: list[str]) -> dict:
    """#T253 et #T240 — verdicts de contrat et santé de la cascade, depuis le journal."""
    compteurs = Counter()
    cb_ouvert, cb_echec = Counter(), Counter()
    for ligne in lignes:
        for cle, motif in MOTIFS.items():
            if motif.search(ligne):
                compteurs[cle] += 1
        if (m := CB_OUVERT.search(ligne)):
            cb_ouvert[m.group(1)] += 1
        if (m := CB_ECHEC.search(ligne)):
            cb_echec[m.group(1)] += 1
        if (m := CB_DECLENCHE.search(ligne)):
            cb_ouvert[m.group(1).rstrip(".")] += 1

    tranches_contrat = compteurs["contrat_satisfait"] + compteurs["contrat_echec"]
    total_revues = tranches_contrat + compteurs["revue_llm"]
    return {
        "lignes_analysees": len(lignes),
        "contrat_satisfait": compteurs["contrat_satisfait"],
        "contrat_echec": compteurs["contrat_echec"],
        "contrat_indeterminable": compteurs["contrat_indeterminable"],
        "plans_sans_contrat": compteurs["plans_sans_contrat"],
        "plans_avec_contrat": compteurs["plans_avec_contrat"],
        "contrat_sur_dag_echoue": compteurs["contrat_sur_dag_echoue"],
        "revues_llm": compteurs["revue_llm"],
        "part_tranchee_par_contrat": (
            round(100 * tranches_contrat / total_revues) if total_revues else None
        ),
        "plans_normalises": compteurs["plans_normalises"],
        "mapreduce_demarres": compteurs["mapreduce_demarre"],
        "outil_map_reduce_appele": compteurs["outil_map_reduce"],
        "circuits_ouverts": dict(cb_ouvert.most_common(8)),
        "echecs_par_modele": dict(cb_echec.most_common(8)),
    }


# ══════════════════════════════════════════════════════════════════
# Rendu
# ══════════════════════════════════════════════════════════════════

def est_prod(racine: str | None = None) -> bool:
    """
    Les références ont été relevées sur la PROD (Deck). Les comparer aux chiffres
    d'un poste de dev n'a aucun sens — deux bases différentes. Hors prod, on
    affiche la référence sans calculer d'écart.
    """
    cible = os.path.abspath(racine or RACINE).replace("\\", "/")
    return cible.startswith("/home/deck/") or os.path.isdir("/opt/vromvrom-engine")


def _delta(actuel, reference, racine=None) -> str:
    if reference is None:
        return str(actuel)
    if not est_prod(racine):
        return f"{actuel}   (réf. prod : {reference} — machine locale, écart non calculé)"
    if actuel == reference:
        return f"{actuel}  (inchangé)"
    signe = "+" if actuel > reference else ""
    return f"{actuel}  ({signe}{actuel - reference} vs {reference} avant déploiement)"


def rendre(dag: dict, expl: dict, jrn: dict | None, raison_journal: str | None, jours: int,
           racine: str | None = None) -> str:
    s = []
    s.append("═" * 74)
    s.append("  TABLEAU DE BORD — ce que la prod prouve (et ce qu'elle ne prouve pas)")
    source = os.path.abspath(racine or RACINE)
    s.append(f"  source : {source}   "
             f"[{'PROD (Deck)' if est_prod(racine) else 'poste local — chiffres non comparables à la prod'}]")
    s.append("═" * 74)

    s.append("\n── #T245 · Fan-out du DAG ──────────────────────────────────────")
    if dag:
        s.append(f"  tâches DAG                 : {_delta(dag['taches_dag'], REFERENCE['taches_dag'])}")
        s.append(f"  sessions                   : {_delta(dag['sessions_dag'], REFERENCE['sessions_dag'])}")
        s.append(f"  sessions à stage parallèle : {dag['sessions_avec_stage_parallele']} / {dag['sessions_dag']}")
        s.append(f"  fan-out maximum observé    : {_delta(dag['fanout_max'], REFERENCE['fanout_max'])}")
        s.append(f"  tâches mapreduce_*         : {_delta(dag['taches_mapreduce'], REFERENCE['taches_mapreduce'])}")
        s.append(f"  tiers demandés             : {dag['tiers_demandes']}")
        s.append(f"                     référence : {REFERENCE['tiers_demandes']}")
        roles = dag["roles_de_noeud"]
        s.append(f"  rôles de nœud (#T245)      : {roles or 'AUCUN — la politique n a encore rien étiqueté'}")
        if not roles:
            s.append("  ⚠️  Aucune tâche ne porte `node_tier_role` : soit aucun plan n'a tourné depuis")
            s.append("      le déploiement, soit aucun n'a produit de stage parallèle. Dans les deux cas")
            s.append("      le verdict de #T245 est EN ATTENTE — surtout pas « ça marche ».")
    else:
        s.append("  base runtime introuvable")

    s.append("\n── #T253 · Contrat d'acceptation ───────────────────────────────")
    if jrn:
        total = jrn["contrat_satisfait"] + jrn["contrat_echec"] + jrn["contrat_indeterminable"]
        if total == 0:
            if jrn["revues_llm"]:
                # Pas de [T253] mais des [REVIEW] Round : la revue LLM a tourné
                # sans critères (contrat non posé) — ne pas écrire « aucune revue ».
                s.append(
                    f"  aucun contrat évalué en {jours} jour(s), mais "
                    f"{jrn['revues_llm']} revue(s) LLM ont tourné sans critères."
                )
                s.append("  Verdict #T253 : EN ATTENTE (le chemin contrat n'a pas été emprunté).")
            else:
                s.append(
                    f"  aucun contrat évalué en {jours} jour(s) — soit aucun plan n'a produit "
                    "de critères,"
                )
                s.append(
                    "  soit aucune revue n'a tourné. À creuser avant de conclure quoi que ce soit."
                )
                s.append("  Verdict #T253 : EN ATTENTE.")
        else:
            s.append(f"  contrats satisfaits        : {jrn['contrat_satisfait']}  (revue LLM évitée)")
            s.append(f"  contrats en échec          : {jrn['contrat_echec']}  (correction déclenchée sans avis LLM)")
            s.append(f"  non déterminables          : {jrn['contrat_indeterminable']}  (repli sur la revue LLM)")
            s.append(f"  revues tranchées par le LLM: {jrn['revues_llm']}")
            s.append(f"  → part tranchée par preuve : {jrn['part_tranchee_par_contrat']} %")
        s.append(f"  plans AVEC contrat         : {jrn['plans_avec_contrat']}"
                 f"   ·   plans SANS contrat : {jrn['plans_sans_contrat']}")
        if jrn["plans_sans_contrat"] and not jrn["plans_avec_contrat"]:
            s.append("  → le Planner ne produit toujours aucun critère : c'est LUI le verrou,")
            s.append("    pas la boucle de revue.")
        if jrn["contrat_sur_dag_echoue"]:
            s.append(f"  objectifs sauvés par le contrat malgré un DAG en échec : {jrn['contrat_sur_dag_echoue']}")
        s.append(f"  plans normalisés (#T245)   : {jrn['plans_normalises']}")
        s.append(f"  MapReduce démarrés         : {jrn['mapreduce_demarres']}"
                 f" (dont {jrn['outil_map_reduce_appele']} via l'outil map_reduce)")
    else:
        s.append(f"  ⚠️  {raison_journal}")

    s.append("\n── #T240 · Santé de la cascade ─────────────────────────────────")
    if jrn:
        if jrn["circuits_ouverts"] or jrn["echecs_par_modele"]:
            s.append(f"  circuits ouverts par modèle: {jrn['circuits_ouverts'] or 'aucun'}")
            s.append(f"  échecs par modèle          : {jrn['echecs_par_modele'] or 'aucun'}")
        else:
            s.append(f"  aucun échec de cascade ni circuit ouvert en {jours} jour(s).")
            s.append("  Cohérent avec le correctif #T240, mais NON concluant tant que les providers")
            s.append("  compat n'ont pas servi à une génération structurée — vérifier le volume ci-dessous.")
    else:
        s.append(f"  ⚠️  {raison_journal}")

    s.append("\n── #T243 · Exploitation du portefeuille ────────────────────────")
    if expl:
        s.append(f"  modèles réellement appelés : {_delta(expl['modeles_appeles'], REFERENCE['modeles_appeles'])}")
        s.append(f"  appels cumulés             : {expl['appels_total']}"
                 f"   tokens : {expl['tokens_cumules']:,}".replace(",", " "))
        s.append(f"  coût cumulé                : {expl['cout_cumule_usd']} $")
        s.append(f"  canaux                     : {expl['canaux']}")
        for m in expl["top_modeles"]:
            s.append(f"     {m['modele']:38s} {m['appels']:4d} appels   {m['cout_usd']} $")
    else:
        s.append("  base runtime introuvable")

    s.append("\n" + "═" * 74)
    return "\n".join(s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Chiffres de prod du moteur (#T245/#T253/#T240/#T243).")
    ap.add_argument("--jours", type=int, default=2, help="fenêtre de journal en jours (défaut : 2)")
    ap.add_argument("--json", action="store_true", help="sortie JSON au lieu du rapport lisible")
    ap.add_argument("--sans-journal", action="store_true", help="ne pas lire le journal systemd")
    ap.add_argument("--racine", help="dossier contenant les bases (défaut : dépôt, puis répertoire courant)")
    args = ap.parse_args()

    conn = ouvrir("moteur_runtime.db", args.racine)
    dag = mesurer_dag(conn) if conn else {}
    expl = mesurer_exploitation(conn) if conn else {}

    jrn, raison = None, "lecture du journal désactivée (--sans-journal)"
    if not args.sans_journal:
        lignes, raison = lire_journal(args.jours)
        # Journal disponible (raison is None) même s'il est vide : on analyse pour
        # afficher EN ATTENTE, pas « ⚠️ None » comme si journalctl avait échoué.
        if raison is None:
            jrn = analyser_journal(lignes)

    if args.json:
        print(json.dumps({"dag": dag, "exploitation": expl, "journal": jrn,
                          "journal_indisponible": raison if not jrn else None,
                          "reference": REFERENCE}, ensure_ascii=False, indent=2))
    else:
        print(rendre(dag, expl, jrn, raison, args.jours, args.racine))
    return 0


if __name__ == "__main__":
    sys.exit(main())
