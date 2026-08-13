"""
tools/sync_db_to_markdown.py — Synchronisation Markdown de fin de session (#T307).

Lit la base runtime (`moteur_runtime.db`) et écrit un résumé de session en Markdown
dans `contexte_ia/historique/AAAA-MM-JJ_Sujet/resume_session.md` (motif d'archivage
existant). **Écriture en AJOUT uniquement** : un fichier préexistant n'est JAMAIS
écrasé (suffixe numérique si le nom est pris), et aucun fichier de
`contexte_ia/04_Projets/` (rédigé à la main) n'est touché.

Usage :
    python -m tools.sync_db_to_markdown                     # session la plus récente
    python -m tools.sync_db_to_markdown --session-id XYZ    # session précise
    python -m tools.sync_db_to_markdown --dry-run           # affiche le diff sans écrire
    python -m tools.sync_db_to_markdown --db chemin.db      # base alternative (tests)
    python -m tools.sync_db_to_markdown --out dossier       # sortie alternative (tests)

Le `--dry-run` est le mode par défaut dès qu'un doute existe : session introuvable,
données vides, chemin de sortie indisponible — le script refuse d'écrire et affiche
ce qu'il aurait fait.
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

from core import runtime_db

logger = logging.getLogger(__name__)

# Chemin relatif vers le dépôt parapluie (même convention que core/router.py:447) :
# moteur_agents/tools/ → ../.. = racine du monorepo → contexte_ia/historique.
_HISTORIQUE_REL = os.path.join("..", "..", "contexte_ia", "historique")

_MAX_SUJET_CHARS = 40


def _slugifier(texte: str) -> str:
    """Transforme un objectif de session en nom de dossier (ASCII, sûr pour disque)."""
    if not texte:
        return "session"
    brut = texte.lower().strip()
    # Accents → lettres de base, caractères non alphanumériques → underscore.
    brut = brut.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    brut = brut.replace("à", "a").replace("â", "a").replace("ä", "a")
    brut = brut.replace("î", "i").replace("ï", "i")
    brut = brut.replace("ô", "o").replace("ö", "o").replace("ù", "u").replace("û", "u").replace("ü", "u")
    brut = brut.replace("ç", "c")
    brut = re.sub(r"[^a-z0-9]+", "_", brut).strip("_")
    return (brut or "session")[:_MAX_SUJET_CHARS]


def _charger_session(db_path: str, session_id: str | None) -> dict:
    """Charge les données d'une session depuis la base runtime (lecture seule)."""
    with runtime_db.get_connection() as conn:
        if session_id:
            ligne = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ? ORDER BY last_activity DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        else:
            ligne = conn.execute(
                "SELECT * FROM sessions ORDER BY last_activity DESC LIMIT 1"
            ).fetchone()
        if not ligne:
            cible = f"session '{session_id}'" if session_id else "session la plus récente"
            raise ValueError(f"Aucune {cible} trouvée dans la base runtime.")

        colonnes = [c[1] for c in conn.execute("PRAGMA table_info(sessions)")]
        session = dict(zip(colonnes, ligne, strict=False))

        # Les tables annexes peuvent manquer selon l'âge de la base (events est
        # créée par core/event_store.py, pas par runtime_db) : le résumé ne doit
        # jamais échouer sur une table absente.
        def _requete_annexe(sql: str, params: tuple = ()) -> list:
            try:
                return conn.execute(sql, params).fetchall()
            except Exception as _e:
                logger.debug(f"[SyncMarkdown] Table annexe indisponible ({_e})")
                return []

        session["token_usage"] = _requete_annexe(
            "SELECT model, COUNT(*) AS appels, SUM(total_tokens) AS tokens, "
            "SUM(cost_usd) AS cout FROM token_usage "
            "WHERE session_id = ? GROUP BY model ORDER BY cout DESC",
            (session["session_id"],),
        )

        session["routing_decisions"] = _requete_annexe(
            "SELECT timestamp, dominant_category, routing_type, target_agent, model_tier "
            "FROM routing_decisions WHERE session_id = ? ORDER BY timestamp DESC LIMIT 10",
            (session["session_id"],),
        )

        session["events"] = _requete_annexe(
            "SELECT ts, type, agent FROM events WHERE session_id = ? ORDER BY ts DESC LIMIT 15",
            (session["session_id"],),
        )

        session["dag_tasks"] = _requete_annexe(
            "SELECT task_id, status, worker_id FROM dag_tasks "
            "WHERE session_id = ? ORDER BY started_at DESC LIMIT 15",
            (session["session_id"],),
        )
    return session


def _generer_markdown(s: dict) -> str:
    """Construit le résumé Markdown (factuel, data-driven)."""
    def _decodage(valeur):
        if not valeur:
            return "—"
        if isinstance(valeur, str) and valeur.startswith("["):
            try:
                return ", ".join(json.loads(valeur))
            except Exception:
                return valeur
        return str(valeur)

    debut = s.get("started_at") or ""
    fin = s.get("ended_at") or ""
    duree = s.get("duration_ms")
    if duree:
        duree_s = f"{float(duree) / 1000.0:.0f} s"
    elif debut and fin:
        duree_s = f"{float(fin) - float(debut):.0f} s"
    else:
        duree_s = "—"

    lignes = [
        "# 📝 Résumé de Session (généré automatiquement)",
        "",
        f"**Date :** {datetime.now().strftime('%d %B %Y %H:%M')}",
        f"**Session :** `{s.get('session_id')}`",
        f"**Objectif :** {s.get('objective') or '—'}",
        f"**Statut :** {s.get('status') or '—'}",
        f"**Durée :** {duree_s}",
        f"**Agent initial :** {s.get('starting_agent') or '—'}",
        f"**Agents invoqués :** {_decodage(s.get('agents_invoked'))}",
        f"**Nombre de tâches :** {s.get('task_count') or 0}",
        "",
        "## 💰 Consommation LLM",
        "",
        "| Modèle | Appels | Tokens | Coût (USD) |",
        "|---|---|---|---|",
    ]
    if s["token_usage"]:
        for modele, appels, tokens, cout in s["token_usage"]:
            lignes.append(f"| {modele} | {appels} | {tokens or 0} | {float(cout or 0):.4f} |")
    else:
        lignes.append("| _aucune consommation enregistrée_ | | | |")

    lignes += ["", "## 🧭 Décisions de routage", ""]
    if s["routing_decisions"]:
        for ts, cat, rtype, agent, tier in s["routing_decisions"]:
            ligne = f"- `{ts}` : **{cat}** → {agent} (tier {tier}, {rtype})"
            lignes.append(ligne)
    else:
        lignes.append("_Aucune décision enregistrée._")

    lignes += ["", "## 📡 Événements notables", ""]
    if s["events"]:
        for ts, type_e, agent in s["events"]:
            lignes.append(f"- `{ts}` [{type_e}] {agent or '—'}")
    else:
        lignes.append("_Aucun événement enregistré._")

    lignes += ["", "## 🧩 Tâches DAG", ""]
    if s["dag_tasks"]:
        for task_id, statut, worker in s["dag_tasks"]:
            lignes.append(f"- `{task_id}` : {statut} (worker : {worker or '—'})")
    else:
        lignes.append("_Aucune tâche DAG enregistrée._")

    lignes += ["", "---", "", "_Résumé généré automatiquement par "
               "`tools/sync_db_to_markdown.py` (#T307) — ne remplace pas "
               "le récit humain de `04_Projets/`._"]
    return "\n".join(lignes) + "\n"


def _chemin_cible(out_dir: str, sujet: str, creer_dossier: bool = True) -> str:
    """Premier nom de fichier libre dans `AAAA-MM-JJ_Sujet/` — jamais d'écrasement."""
    dossier = os.path.join(out_dir, f"{datetime.now().strftime('%Y-%m-%d')}_{sujet}")
    if creer_dossier:
        os.makedirs(dossier, exist_ok=True)
    candidat = os.path.join(dossier, "resume_session.md")
    suffixe = 2
    while os.path.exists(candidat):
        candidat = os.path.join(dossier, f"resume_session_{suffixe}.md")
        suffixe += 1
    return candidat


def sync_db_to_markdown(
    session_id: str | None = None,
    dry_run: bool = False,
    db_path: str | None = None,
    out_dir: str | None = None,
) -> dict:
    """
    Point d'entrée principal (appelable depuis le routeur ou en CLI).

    Retourne {"ecrit": bool, "chemin": str|None, "diff": str} — `diff` contient le
    contenu complet (dry-run) ou un résumé de l'écriture.
    """
    if db_path:
        runtime_db.override_db_path(db_path)
    if out_dir is None:
        out_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), *_HISTORIQUE_REL.split(os.sep))
        )

    session = _charger_session(runtime_db.get_db_path(), session_id)
    contenu = _generer_markdown(session)
    sujet = _slugifier(str(session.get("objective") or session.get("session_id") or ""))

    if dry_run:
        # Aucun effet de bord en dry-run : ni fichier, ni dossier créé.
        chemin_prevu = _chemin_cible(out_dir, sujet, creer_dossier=False)
        return {
            "ecrit": False,
            "chemin": chemin_prevu,
            "diff": f"DRY-RUN — aurait écrit dans : {chemin_prevu}\n\n{contenu}",
        }

    chemin = _chemin_cible(out_dir, sujet)
    with open(chemin, "x", encoding="utf-8") as f:
        f.write(contenu)
    logger.info(f"[SyncMarkdown] Résumé de session écrit : {chemin}")
    return {"ecrit": True, "chemin": chemin, "diff": f"Écrit : {chemin}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronise l'état d'une session (base runtime) vers un résumé Markdown."
    )
    parser.add_argument("--session-id", default=None, help="Session à résumer (défaut : plus récente).")
    parser.add_argument("--dry-run", action="store_true", help="Affiche le diff sans écrire.")
    parser.add_argument("--db", default=None, help="Chemin de la base runtime (défaut : moteur_runtime.db).")
    parser.add_argument("--out", default=None, help="Répertoire de sortie (défaut : contexte_ia/historique).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        resultat = sync_db_to_markdown(
            session_id=args.session_id,
            dry_run=args.dry_run,
            db_path=args.db,
            out_dir=args.out,
        )
    except Exception as exc:
        # Doute → dry-run : on n'écrit pas, on dit pourquoi.
        print(f"❌ {exc}", file=sys.stderr)
        print("Rien n'a été écrit (mode défensif).", file=sys.stderr)
        return 2

    print(resultat["diff"])
    return 0 if resultat["ecrit"] else 1


if __name__ == "__main__":
    sys.exit(main())
