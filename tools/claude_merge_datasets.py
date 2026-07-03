# -*- coding: utf-8 -*-
"""
tools/claude_merge_datasets.py — Fusion + déduplication de datasets JSONL d'instructions.

Outil de la session autonome (cloisonné, préfixe claude_). Fusionne plusieurs
fichiers .jsonl {"instruction": ..., "output": ...}, déduplique sur l'instruction
normalisée (et écarte les sorties vides), puis écrit un corpus consolidé.

Usage:
  python tools/claude_merge_datasets.py --out claude_dataset_moteur_consolide.jsonl \
      claude_dataset_moteur_gen.jsonl claude_dataset_core_gen.jsonl dataset_moteur_scored.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

def normalize(text: str) -> str:
    """Clé de déduplication : minuscules, espaces compactés, ponctuation de bord ôtée."""
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .?!:;")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="Fichiers JSONL source.")
    ap.add_argument("--out", required=True, help="Fichier consolidé de sortie.")
    ap.add_argument("--min-output-chars", type=int, default=20,
                    help="Longueur minimale de la sortie pour être conservée.")
    args = ap.parse_args()

    seen: set[str] = set()
    kept, dups, skipped = 0, 0, 0
    per_source: dict[str, int] = {}

    with open(args.out, "w", encoding="utf-8") as out:
        for src in args.inputs:
            p = Path(src)
            if not p.exists():
                print(f"  ⚠️  introuvable, ignoré : {src}")
                continue
            n_src = 0
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                instr = obj.get("instruction", "")
                output = obj.get("output", "")
                if len((output or "").strip()) < args.min_output_chars:
                    skipped += 1
                    continue
                key = normalize(instr)
                if not key or key in seen:
                    dups += 1
                    continue
                seen.add(key)
                out.write(json.dumps({"instruction": instr, "output": output}, ensure_ascii=False) + "\n")
                kept += 1
                n_src += 1
            per_source[src] = n_src

    print(f"[OK] Consolidé : {args.out}")
    print(f"     Gardés={kept}  Doublons={dups}  Écartés(vides/parse)={skipped}")
    for s, n in per_source.items():
        print(f"       + {n:>5} uniques depuis {s}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
