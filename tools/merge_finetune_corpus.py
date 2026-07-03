#!/usr/bin/env python3
"""
tools/merge_finetune_corpus.py — Fusion + déduplication des datasets d'instruction
pour produire UN corpus de fine-tuning propre, prêt pour `finetune_qlora_local.py`.

Sources fusionnées (auto-détectées, les manquantes sont ignorées sans erreur) :
  - Datasets scorés par Gemini (matin)  : dataset_moteur_scored.jsonl, dataset_tab5_scored.jsonl
  - Jobs Vertex de ce soir (à collecter) : claude_dataset_moteur_gold.jsonl,
                                           claude_dataset_contexte_tab5_gen.jsonl,
                                           claude_dataset_ha_tab5_gen.jsonl
  - + tout .jsonl passé en argument positionnel.

Schémas tolérés (tous ramenés à instruction/input/output) :
  instruction|prompt , input(optionnel) , output|completion|response ,
  score|quality(optionnel, 0-10) , tags(optionnel).

Traitements :
  1) Filtre qualité : garde score/quality >= --min-score (défaut 7).
     Les paires SANS champ de score sont gardées (déjà filtrées à la collecte).
  2) Dédup exacte : clé = hash(instruction_normalisée + output_normalisé).
     Normalisation = strip + espaces compactés + casefold (clé seulement, texte intact).
  3) Option --collapse-instructions : si une même instruction apparaît avec des
     réponses différentes, ne garde QUE la mieux notée (évite les doublons mous).
  4) Sortie minimale {instruction, input, output}, prête pour le trainer.

Exemples :
  # Maintenant (partiel, seuls les fichiers de Gemini présents) — valide la mécanique :
  python tools/merge_finetune_corpus.py --out dataset_finetune_final.jsonl --dry-run
  # Quand les 3 jobs sont collectés — corpus complet, mélangé, dédupliqué :
  python tools/merge_finetune_corpus.py --out dataset_finetune_final.jsonl --shuffle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# Fichiers attendus par défaut (relatifs au cwd = racine moteur_agents).
DEFAULT_INPUTS = [
    "dataset_moteur_scored.jsonl",            # Gemini (matin)
    "dataset_tab5_scored.jsonl",              # Gemini (matin)
    "claude_dataset_moteur_gold.jsonl",       # scoring consolidé (ce soir)
    "claude_dataset_contexte_tab5_gen.jsonl", # génération contexte_ia + 00ProjetTab
    "claude_dataset_ha_tab5_gen.jsonl",       # génération HA + Tab5 déployé
]

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Normalise pour la clé de dédup (n'altère PAS le texte conservé)."""
    return _WS.sub(" ", (text or "").strip()).casefold()


def _get(ex: dict, *keys: str) -> str:
    """Premier champ non vide parmi `keys`."""
    for k in keys:
        v = ex.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _score(ex: dict):
    """Score 0-10 si présent (score ou quality), sinon None (= déjà filtré)."""
    for k in ("score", "quality"):
        v = ex.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return None


def load_rows(path: Path, min_score: int, stats: Counter):
    """Itère les paires valides et filtrées d'un fichier .jsonl."""
    with path.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                ex = json.loads(ln)
            except json.JSONDecodeError:
                stats["json_invalide"] += 1
                continue
            instr = _get(ex, "instruction", "prompt")
            out = _get(ex, "output", "completion", "response")
            if not instr or not out:
                stats["champ_manquant"] += 1
                continue
            sc = _score(ex)
            if sc is not None and sc < min_score:
                stats["sous_seuil"] += 1
                continue
            yield {
                "instruction": instr,
                "input": ex.get("input", "") if isinstance(ex.get("input"), str) else "",
                "output": out,
                "_score": sc if sc is not None else 999,  # sans score = priorité haute
                "_tags": ex.get("tags", []),
            }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fusion + dédup des datasets de fine-tuning.")
    ap.add_argument("inputs", nargs="*", help="Fichiers .jsonl additionnels.")
    ap.add_argument("--out", type=Path, default=Path("dataset_finetune_final.jsonl"))
    ap.add_argument("--min-score", type=int, default=7)
    ap.add_argument("--collapse-instructions", action="store_true",
                    help="Une seule réponse (la mieux notée) par instruction.")
    ap.add_argument("--shuffle", action="store_true", help="Mélange (seed fixe).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true", help="N'écrit rien, juste le bilan.")
    args = ap.parse_args()

    # Construit la liste des fichiers : défauts présents + positionnels.
    candidates = [Path(p) for p in DEFAULT_INPUTS] + [Path(p) for p in args.inputs]
    present, missing = [], []
    seen_paths = set()
    for p in candidates:
        if str(p) in seen_paths:
            continue
        seen_paths.add(str(p))
        (present if p.is_file() else missing).append(p)

    if not present:
        print("ERREUR : aucun fichier d'entrée trouvé.", file=sys.stderr)
        return 1

    print("=== Sources ===")
    for p in present:
        print(f"  [présent] {p}")
    for p in missing:
        print(f"  [absent ] {p}  (job pas encore collecté ?)")
    print()

    # Chargement + dédup exacte.
    by_key: dict[str, dict] = {}          # clé instr+out -> meilleure ligne
    by_instr: dict[str, dict] = {}        # clé instr -> meilleure ligne (si collapse)
    per_source = Counter()
    stats = Counter()
    dup_exact = 0
    instr_collisions = 0

    for p in present:
        n_before = sum(per_source.values())
        for row in load_rows(p, args.min_score, stats):
            row["_source"] = p.name
            k = hashlib.sha1(
                (_norm(row["instruction"]) + "\x00" + _norm(row["output"])).encode()
            ).hexdigest()
            if k in by_key:
                dup_exact += 1
                continue
            by_key[k] = row
            per_source[p.name] += 1

            if args.collapse_instructions:
                ik = hashlib.sha1(_norm(row["instruction"]).encode()).hexdigest()
                prev = by_instr.get(ik)
                if prev is None:
                    by_instr[ik] = row
                else:
                    instr_collisions += 1
                    if row["_score"] > prev["_score"]:
                        by_instr[ik] = row
        _ = n_before  # (lisibilité)

    rows = list(by_instr.values()) if args.collapse_instructions else list(by_key.values())

    if args.shuffle:
        random.Random(args.seed).shuffle(rows)

    # Bilan.
    print("=== Bilan ===")
    for src, n in per_source.most_common():
        print(f"  {n:6d}  {src}")
    print(f"  ------")
    print(f"  {sum(per_source.values()):6d}  uniques après dédup exacte")
    print(f"  rejets : sous_seuil={stats['sous_seuil']} champ_manquant={stats['champ_manquant']} "
          f"json_invalide={stats['json_invalide']}  dups_exacts={dup_exact}")
    if args.collapse_instructions:
        print(f"  collisions d'instruction fusionnées={instr_collisions}")
    print(f"  >>> CORPUS FINAL : {len(rows)} paires")

    # Répartition par tag (top 12) — utile pour voir l'équilibre domaine.
    tagc = Counter(t for r in rows for t in (r.get("_tags") or []) if isinstance(t, str))
    if tagc:
        print("  tags principaux : " + ", ".join(f"{t}({n})" for t, n in tagc.most_common(12)))

    if args.dry_run:
        print("\n[dry-run] rien écrit.")
        return 0

    with args.out.open("w", encoding="utf-8") as fout:
        for r in rows:
            fout.write(json.dumps(
                {"instruction": r["instruction"], "input": r["input"], "output": r["output"]},
                ensure_ascii=False) + "\n")
    print(f"\n[OK] Écrit {len(rows)} paires -> {args.out}")
    print(f"     Entraînement : python tools/finetune_qlora_local.py --data {args.out} ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
