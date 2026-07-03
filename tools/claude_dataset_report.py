# -*- coding: utf-8 -*-
"""
tools/claude_dataset_report.py — Rapport qualité d'un dataset JSONL d'instructions.

Outil de la session autonome (cloisonné). Calcule des statistiques utiles avant
fine-tuning : volume, doublons (instruction normalisée), distribution des longueurs
(instruction/output), et cas suspects (sorties très courtes, troncatures probables).

Usage:
  python tools/claude_dataset_report.py claude_dataset_moteur_consolide.jsonl \
      --out docs/reports/claude-auto/dataset_quality.md
"""

import argparse
import json
import re
import statistics as stats
from pathlib import Path


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower()).strip(" .?!:;")


def _pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
    return vals[k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", help="Fichier JSONL {instruction, output}.")
    ap.add_argument("--out", required=True, help="Rapport Markdown de sortie.")
    ap.add_argument("--short-output", type=int, default=40,
                    help="Seuil (chars) en-dessous duquel une sortie est jugée suspecte.")
    args = ap.parse_args()

    p = Path(args.dataset)
    instr_lens, out_lens = [], []
    seen, dups, n, short, truncated, parse_err = set(), 0, 0, 0, 0, 0

    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            parse_err += 1
            continue
        n += 1
        instr, out = obj.get("instruction", ""), obj.get("output", "")
        instr_lens.append(len(instr))
        out_lens.append(len(out))
        key = _norm(instr)
        if key in seen:
            dups += 1
        else:
            seen.add(key)
        if len((out or "").strip()) < args.short_output:
            short += 1
        # Troncature probable : sortie sans ponctuation finale et longue.
        if out and len(out) > 200 and out.rstrip()[-1:] not in ".!?»\")]`":
            truncated += 1

    def block(name, vals):
        if not vals:
            return f"- **{name}** : (vide)\n"
        return (
            f"- **{name}** : min={min(vals)} · médiane={int(stats.median(vals))} · "
            f"moy={int(stats.fmean(vals))} · p90={_pct(vals, 90)} · max={max(vals)}\n"
        )

    md = [
        f"# Rapport qualité — `{p.name}`",
        "",
        f"- **Paires** : {n}",
        f"- **Instructions uniques** : {len(seen)}  ·  **Doublons** : {dups}",
        f"- **Sorties suspectes (< {args.short_output} chars)** : {short}",
        f"- **Troncatures probables** (sortie longue sans ponctuation finale) : {truncated}",
        f"- **Lignes non parseables** : {parse_err}",
        "",
        "## Distribution des longueurs (caractères)",
        block("Instruction", instr_lens),
        block("Output", out_lens),
        "",
        "_Généré par `tools/claude_dataset_report.py` (session autonome)._",
    ]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[OK] Rapport écrit → {args.out}")
    print(f"     Paires={n}  Uniques={len(seen)}  Doublons={dups}  Suspectes={short}  Troncatures={truncated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
