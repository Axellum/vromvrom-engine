"""
tools/db_consolidation.py — Analyseur de consolidation des bases SQLite (Phase 1, M1/#7).

⚠️  STRICTEMENT NON DESTRUCTIF : ouvre toutes les bases en lecture seule (mode=ro),
    n'écrit JAMAIS dans une base. Produit un rapport + un plan de consolidation
    recommandé que TU exécutes toi-même après revue.

Contexte (audit V12) : 6 bases SQLite avec des tables dupliquées
(routing_decisions, token_usage, ide_conversations, billing_history, sessions,
model_elo_scores) entre moteur_runtime.db / session_history.db / routing_metrics.db.
Les bases de PRODUCTION vivent sur la VM Freebox ; ce script doit donc être lancé
sur le répertoire des bases de prod (ou une copie de backup) :

    python -m tools.db_consolidation --db-dir /chemin/vers/bases [--json rapport.json]

Sans argument, il analyse le répertoire du moteur (sandbox Windows).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from typing import Dict, List

# Propriétaire canonique RECOMMANDÉ par table dupliquée (d'après l'audit).
# "decision" => nécessite un arbitrage humain (volumétries très différentes).
CANONICAL_OWNER = {
    "routing_decisions": ("moteur_runtime.db", "Runtime = source courante (audit §2.4)"),
    "model_elo_scores": ("moteur_runtime.db", "Runtime = source courante du scoring Elo"),
    "token_usage": ("moteur_runtime.db", "Runtime = comptage détaillé courant"),
    "ide_conversations": ("moteur_runtime.db", "Runtime = conversations IDE récentes"),
    "billing_history": ("decision", "session_history.db = historique long terme VS runtime = récent"),
    "sessions": ("decision", "À arbitrer : runtime (en cours) VS session_history (archive)"),
}

# Dossiers à ignorer lors de la découverte des bases.
_IGNORE_DIRS = {"node_modules", ".git", ".chrome_scraper_profile", ".chrome_debug_profile",
                "__pycache__", "backups_prod", ".venv", "venv"}


def discover_dbs(db_dir: str, max_depth: int = 2) -> List[str]:
    """Découvre les fichiers *.db sous db_dir (profondeur limitée), hors dossiers ignorés."""
    found: List[str] = []
    base_depth = db_dir.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(db_dir):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        if root.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.endswith(".db"):
                found.append(os.path.join(root, f))
    return sorted(found)


def inspect_db(path: str) -> Dict[str, dict]:
    """Retourne {table: {count, columns, schema_sql}} pour une base (lecture seule)."""
    tables: Dict[str, dict] = {}
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for name, schema_sql in rows:
            # Ignorer les tables internes FTS5 (shadow tables).
            if name.startswith("fts_") and any(
                name.endswith(s) for s in ("_data", "_idx", "_docsize", "_content", "_config")
            ):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
            except sqlite3.Error as e:
                count = f"?({e})"
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{name}')").fetchall()]
            tables[name] = {"count": count, "columns": cols, "schema_sql": schema_sql}
    finally:
        conn.close()
    return tables


def analyze(db_dir: str) -> dict:
    """Construit le rapport d'analyse complet (sans aucune écriture)."""
    db_paths = discover_dbs(db_dir)
    per_db: Dict[str, Dict[str, dict]] = {}
    for p in db_paths:
        try:
            per_db[p] = inspect_db(p)
        except sqlite3.Error as e:
            per_db[p] = {"__error__": str(e)}

    # Index inverse : table -> [bases qui la contiennent]
    table_locations: Dict[str, List[str]] = defaultdict(list)
    for p, tables in per_db.items():
        for t in tables:
            if t != "__error__":
                table_locations[t].append(p)

    collisions = {t: locs for t, locs in table_locations.items() if len(locs) > 1}
    return {"db_dir": db_dir, "databases": per_db, "collisions": collisions}


def _basename(path: str) -> str:
    return os.path.basename(path)


def print_report(report: dict) -> None:
    """Affiche un rapport lisible + un plan de consolidation recommandé."""
    print("=" * 78)
    print(f" ANALYSE DE CONSOLIDATION SQLITE — {report['db_dir']}")
    print(" (lecture seule — aucune base n'a été modifiée)")
    print("=" * 78)

    print("\n## Bases découvertes\n")
    for p, tables in report["databases"].items():
        if "__error__" in tables:
            print(f"  ⚠️  {_basename(p)} : ERREUR {tables['__error__']}")
            continue
        total = sum(v["count"] for v in tables.values() if isinstance(v["count"], int))
        print(f"  • {_basename(p):<24} {len(tables)} tables, {total:,} lignes")

    collisions = report["collisions"]
    if not collisions:
        print("\n✅ Aucune table dupliquée entre bases dans ce répertoire.")
        print("   (Les bases de prod session_history.db / routing_metrics.db ne sont")
        print("    peut-être pas présentes ici — relancer sur le répertoire de prod.)")
        return

    print(f"\n## ⚠️  Tables dupliquées détectées : {len(collisions)}\n")
    plan: List[str] = []
    for table, locs in sorted(collisions.items()):
        print(f"  ── {table} ──")
        # Comparaison de schéma (colonnes) entre copies.
        col_sets = {}
        for p in locs:
            cols = report["databases"][p][table]["columns"]
            count = report["databases"][p][table]["count"]
            col_sets[p] = tuple(cols)
            print(f"      {_basename(p):<24} {count:>8} lignes, {len(cols)} colonnes")
        schemas_identical = len(set(col_sets.values())) == 1
        print(f"      Schémas identiques : {'oui' if schemas_identical else 'NON — fusion manuelle requise'}")

        owner, why = CANONICAL_OWNER.get(table, ("decision", "table non répertoriée dans l'audit"))
        present_basenames = {_basename(p) for p in locs}
        if owner == "decision":
            print(f"      → ⚖️  ARBITRAGE HUMAIN requis : {why}")
            plan.append(f"# [{table}] ARBITRAGE : {why}")
        elif owner in present_basenames:
            redundant = sorted(present_basenames - {owner})
            print(f"      → Canonique recommandé : {owner} ({why})")
            print(f"        Copies redondantes à migrer puis retirer : {', '.join(redundant)}")
            plan.append(
                f"# [{table}] canonique={owner} ; vérifier puis migrer/retirer: {', '.join(redundant)}"
            )
        else:
            print(f"      → Canonique recommandé ({owner}) absent ici ; relancer sur la prod.")
        print()

    print("## Plan de consolidation recommandé (à exécuter MANUELLEMENT après revue)\n")
    print("  Étapes générales pour chaque table dupliquée à schéma identique :")
    print("    1. Sauvegarder les deux bases (copie à froid).")
    print("    2. ATTACH de la base source ; INSERT OR IGNORE INTO canonique SELECT * FROM source.")
    print("    3. Vérifier les compteurs et l'intégrité (PRAGMA integrity_check).")
    print("    4. DROP TABLE dans la base non-canonique (ou la marquer dépréciée).")
    print("    5. Rediriger le module propriétaire vers la base canonique.\n")
    for line in plan:
        print(f"    {line}")
    print("\n  ⚠️  Schémas divergents et tables 'ARBITRAGE' : ne PAS automatiser — décider d'abord.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyseur de consolidation SQLite (lecture seule).")
    default_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--db-dir", default=default_dir, help="Répertoire des bases (défaut : racine moteur).")
    parser.add_argument("--json", dest="json_out", default=None, help="Écrit le rapport JSON dans ce fichier.")
    args = parser.parse_args()

    report = analyze(args.db_dir)
    print_report(report)

    if args.json_out:
        # Le schema_sql est volumineux — on l'allège pour le JSON.
        slim = {
            "db_dir": report["db_dir"],
            "collisions": {t: [_basename(p) for p in locs] for t, locs in report["collisions"].items()},
            "databases": {
                _basename(p): {t: {"count": v["count"], "columns": v["columns"]}
                               for t, v in tables.items() if t != "__error__"}
                for p, tables in report["databases"].items()
            },
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=2, ensure_ascii=False)
        print(f"\n📄 Rapport JSON écrit : {args.json_out}")


if __name__ == "__main__":
    main()
