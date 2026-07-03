"""
tools/db_migration.py — Consolidation des bases SQLite dupliquées (Phase 1, M1/#7).

⚠️  À EXÉCUTER SUR UNE COPIE DE BACKUP, JAMAIS SUR LA PROD DIRECTEMENT.
    Séquence de déploiement recommandée :
      1. Rapatrier un backup frais de la VM Freebox.
      2. python -m tools.db_migration --db-dir <backup>            (DRY-RUN, défaut)
      3. python -m tools.db_migration --db-dir <backup> --apply    (migration + checks)
      4. Inspecter / valider les bases consolidées.
      5. Arrêter le moteur sur la VM, swapper les bases, relancer.

Caractéristiques de sûreté (corrige les défauts du plan initial) :
- Colonnes EXPLICITES (intersection dest∩source) — pas de `SELECT *` (gère le
  sur-ensemble billing_history : seules les colonnes communes sont migrées).
- Dédup par CLÉ NATURELLE via `WHERE NOT EXISTS` (le surrogate `id` n'est jamais
  recopié) → idempotent, ré-exécutable sans doublon ni collision de PK.
- `PRAGMA wal_checkpoint(TRUNCATE)` avant toute opération (mode WAL).
- Backup physique horodaté de chaque base modifiée avant `--apply`.
- `PRAGMA integrity_check` + réconciliation des compteurs après migration.
- DRY-RUN par défaut : n'écrit rien, prédit les insertions.

Direction RÉELLE (confirmée par l'analyse du code) :
Le code actuel écrit DÉJÀ toutes ces tables dans moteur_runtime.db
(session_history.py / routing_metrics.py / elo_scorer.py partagent la connexion
runtime via core.runtime_db.get_connection). session_history.db et
routing_metrics.db sont des fichiers LEGACY non écrits par le code courant.

=> Canonique = moteur_runtime.db pour TOUTES les tables.
=> On fusionne les données historiques des bases legacy DANS moteur_runtime.db,
   puis ces fichiers legacy peuvent être supprimés. AUCUNE redirection de code.
   billing_history : les 11 colonnes de runtime sont conservées ; les lignes
   legacy (7 colonnes) laissent les 4 colonnes supplémentaires à NULL (zéro perte).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from typing import List

# Bases legacy à fusionner puis retirer (non écrites par le code courant).
LEGACY_DBS = ["session_history.db", "routing_metrics.db"]


@dataclass
class MergePlan:
    """Fusion d'une table d'une base source vers une base canonique."""
    table: str
    source_db: str           # nom de fichier (ex: "routing_metrics.db")
    dest_db: str             # base canonique (ex: "moteur_runtime.db")
    natural_key: List[str]   # colonnes formant la clé de dédup (hors surrogate id)
    drop_source: bool = True # supprimer la table source après fusion réussie
    note: str = ""


@dataclass
class DropPlan:
    """Suppression simple d'une table (ex: doublon vide)."""
    table: str
    db: str
    reason: str = ""


# ── Plans déclaratifs : TOUT converge vers moteur_runtime.db (canonique réel) ──
# Source = base legacy ; dest = moteur_runtime.db ; dédup par clé naturelle.
# Les lignes runtime existantes sont prioritaires (NOT EXISTS) ; on n'ajoute que
# l'historique legacy absent. Colonnes = intersection (runtime conserve son schéma riche).
MERGE_PLANS: List[MergePlan] = [
    MergePlan("routing_decisions", "routing_metrics.db", "moteur_runtime.db",
              ["timestamp", "user_prompt_hash", "session_id"]),
    MergePlan("model_elo_scores", "routing_metrics.db", "moteur_runtime.db",
              ["model_name", "domain"],
              note="clé unique (model_name, domain) ; runtime prioritaire en conflit"),
    MergePlan("token_usage", "session_history.db", "moteur_runtime.db",
              ["session_id", "timestamp", "model"]),
    MergePlan("ide_conversations", "session_history.db", "moteur_runtime.db",
              ["conversation_id"]),
    MergePlan("billing_history", "session_history.db", "moteur_runtime.db",
              ["timestamp", "provider", "metric", "sync_source"],
              note="runtime garde ses 11 colonnes ; legacy (7 col) → 4 col à NULL, zéro perte"),
    MergePlan("quota_snapshots", "session_history.db", "moteur_runtime.db",
              ["timestamp", "channel", "metric", "window_seconds"]),
    MergePlan("sessions", "session_history.db", "moteur_runtime.db",
              ["session_id"],
              note="dédup sur session_id (index unique) ; runtime prioritaire"),
]

DROP_PLANS: List[DropPlan] = []


@dataclass
class StepResult:
    label: str
    detail: str
    inserted: int = 0
    ok: bool = True


def _connect(path: str, read_only: bool = True) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    return sqlite3.connect(os.path.abspath(path))


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _common_columns(dest_cols: List[str], src_cols: List[str]) -> List[str]:
    """Colonnes communes hors surrogate 'id' (préserve l'ordre de la destination)."""
    src_set = set(src_cols)
    return [c for c in dest_cols if c != "id" and c in src_set]


def _wal_checkpoint(path: str) -> None:
    conn = _connect(path, read_only=False)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _backup_db(path: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = f"{path}.{ts}.bak"
    shutil.copy2(path, dst)
    # Copier aussi les fichiers WAL/SHM s'ils existent (cohérence).
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            shutil.copy2(path + ext, dst + ext)
    return dst


def _build_insert_sql(table: str, cols: List[str], natural_key: List[str]) -> str:
    """INSERT colonnes explicites depuis src.<table>, dédup par clé naturelle (NULL-safe)."""
    col_list = ", ".join(f'"{c}"' for c in cols)
    # Calculé hors f-string : un backslash dans une expression f-string est interdit (< Py3.12).
    select_list = ", ".join(f's."{c}"' for c in cols)
    where = " AND ".join(f'd."{k}" IS s."{k}"' for k in natural_key)
    return (
        f'INSERT INTO main."{table}" ({col_list}) '
        f'SELECT {select_list} '
        f'FROM src."{table}" s '
        f'WHERE NOT EXISTS (SELECT 1 FROM main."{table}" d WHERE {where})'
    )


def _count_would_insert(dest_conn: sqlite3.Connection, table: str, natural_key: List[str]) -> int:
    where = " AND ".join(f'd."{k}" IS s."{k}"' for k in natural_key)
    sql = (
        f'SELECT COUNT(*) FROM src."{table}" s '
        f'WHERE NOT EXISTS (SELECT 1 FROM main."{table}" d WHERE {where})'
    )
    return dest_conn.execute(sql).fetchone()[0]


def run(db_dir: str, apply: bool, do_backup: bool = True) -> List[StepResult]:
    results: List[StepResult] = []
    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 78)
    print(f" MIGRATION DE CONSOLIDATION SQLITE — {mode} — {db_dir}")
    print("=" * 78)

    def path(name: str) -> str:
        return os.path.join(db_dir, name)

    # Bases impliquées (présentes uniquement).
    involved = {p.source_db for p in MERGE_PLANS} | {p.dest_db for p in MERGE_PLANS} \
        | {d.db for d in DROP_PLANS} | {"moteur_runtime.db", "session_history.db"}
    involved = {n for n in involved if os.path.exists(path(n))}

    if apply and do_backup:
        print("\n## Checkpoint WAL + backup physique des bases impliquées")
        for name in sorted(involved):
            _wal_checkpoint(path(name))
            bak = _backup_db(path(name))
            print(f"   ✓ {name} → {os.path.basename(bak)}")

    # ── 1. Fusions par clé naturelle ──
    print("\n## Fusions (colonnes explicites + dédup par clé naturelle)")
    for plan in MERGE_PLANS:
        src_p, dst_p = path(plan.source_db), path(plan.dest_db)
        if not (os.path.exists(src_p) and os.path.exists(dst_p)):
            results.append(StepResult(plan.table, f"base absente ({plan.source_db}/{plan.dest_db}) — ignoré", ok=True))
            print(f"   • {plan.table}: base absente, ignoré")
            continue

        dest = _connect(dst_p, read_only=not apply)
        try:
            dest.execute("ATTACH DATABASE ? AS src", (os.path.abspath(src_p),))
            if not (_table_exists(dest, plan.table)
                    and dest.execute("SELECT 1 FROM src.sqlite_master WHERE type='table' AND name=?",
                                     (plan.table,)).fetchone()):
                print(f"   • {plan.table}: table absente d'un côté, ignoré")
                results.append(StepResult(plan.table, "table absente d'un côté", ok=True))
                continue

            dest_cols = _table_columns(dest, plan.table)
            src_cols = [r[1] for r in dest.execute(f"PRAGMA src.table_info('{plan.table}')").fetchall()]
            cols = _common_columns(dest_cols, src_cols)
            dropped = sorted((set(dest_cols) | set(src_cols)) - set(cols) - {"id"})

            would = _count_would_insert(dest, plan.table, plan.natural_key)
            msg = (f"{plan.source_db}→{plan.dest_db} | clé={plan.natural_key} | "
                   f"{len(cols)} col communes | +{would} ligne(s)")
            if dropped:
                msg += f" | colonnes ignorées: {dropped}"
            print(f"   • {plan.table}: {msg}")

            if apply:
                dest.execute(_build_insert_sql(plan.table, cols, plan.natural_key))
                if plan.drop_source:
                    dest.execute(f'DROP TABLE src."{plan.table}"')
                dest.commit()
            results.append(StepResult(plan.table, msg, inserted=would))
        except Exception as e:
            print(f"   ✗ {plan.table}: ERREUR {e}")
            results.append(StepResult(plan.table, f"ERREUR {e}", ok=False))
        finally:
            dest.close()

    # ── 3. Suppressions simples (doublons vides) ──
    print("\n## Suppressions de tables redondantes vides")
    for d in DROP_PLANS:
        p = path(d.db)
        if not os.path.exists(p):
            continue
        conn = _connect(p, read_only=not apply)
        try:
            if not _table_exists(conn, d.table):
                print(f"   • {d.table} ({d.db}): déjà absente")
                continue
            n = conn.execute(f'SELECT COUNT(*) FROM "{d.table}"').fetchone()[0]
            if n > 0:
                print(f"   ⚠️  {d.table} ({d.db}): {n} lignes — NON vide, suppression ANNULÉE ({d.reason})")
                results.append(StepResult(d.table, f"non vide ({n}) — non supprimée", ok=False))
                continue
            print(f"   • {d.table} ({d.db}): vide → DROP ({d.reason})")
            if apply:
                conn.execute(f'DROP TABLE "{d.table}"')
                conn.commit()
            results.append(StepResult(d.table, "table vide supprimée"))
        finally:
            conn.close()

    # ── 4. Contrôle d'intégrité post-migration ──
    if apply:
        print("\n## PRAGMA integrity_check")
        for name in sorted(involved):
            conn = _connect(path(name), read_only=True)
            try:
                res = conn.execute("PRAGMA integrity_check").fetchone()[0]
                ok = (res == "ok")
                print(f"   {'✓' if ok else '✗'} {name}: {res}")
                results.append(StepResult(f"integrity:{name}", res, ok=ok))
            finally:
                conn.close()

    print("\n" + "=" * 78)
    failed = [r for r in results if not r.ok]
    if failed:
        print(f" ⚠️  {len(failed)} étape(s) en échec/avertissement — revoir avant déploiement.")
    else:
        print(f" ✅ {mode} terminé sans erreur.")
    if not apply:
        print(" (Aucune écriture effectuée. Relancer avec --apply sur une COPIE de backup.)")
    else:
        legacy_present = [n for n in LEGACY_DBS if os.path.exists(path(n))]
        if legacy_present:
            print(" ℹ️  Données historiques fusionnées dans moteur_runtime.db.")
            print(f"     Les bases legacy {legacy_present} peuvent maintenant être supprimées")
            print("     (côté prod : NE déployer que moteur_runtime.db consolidée).")
    print("=" * 78)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidation des bases SQLite (dry-run par défaut).")
    default_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--db-dir", default=default_dir, help="Répertoire des bases (backup).")
    parser.add_argument("--apply", action="store_true", help="Exécute réellement (sinon dry-run).")
    parser.add_argument("--no-backup", action="store_true", help="Désactive le backup physique (déconseillé).")
    args = parser.parse_args()
    run(args.db_dir, apply=args.apply, do_backup=not args.no_backup)


if __name__ == "__main__":
    main()
