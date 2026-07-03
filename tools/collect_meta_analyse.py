"""
tools/collect_meta_analyse.py — Collecte & consolide les résultats du job Vertex Batch
de méta-analyse des conversations (P1, run complet).

Lit les prédictions GCS du job, relie chaque analyse à sa session (marqueurs
<<SESSION_ID>>/<<SRC>>), puis produit :
  - <out>.jsonl                 : 1 analyse JSON par session (brut)
  - <out>_rapport.md            : rapport consolidé (patterns récurrents agrégés)
  - <out>_candidats_memoire.md  : faits/habitudes/règles à intégrer en L1/L3

Usage :
  python tools/collect_meta_analyse.py \
      --job projects/.../batchPredictionJobs/5348760062061969408 \
      --bucket gs://ha-delta-corpus-axell --project ha-delta \
      --out meta_analyse_complete
"""
from __future__ import annotations
import argparse, json, re
from collections import Counter
from pathlib import Path

import vertex_dataset_factory as vf   # même dossier : réutilise les helpers GCS

_META = re.compile(r"<<SESSION_ID>>(.*?)<<SRC>>(.*?)<<END_META>>", re.DOTALL)
KEYS = ["erreurs", "hallucinations", "faux_positifs", "incomprehensions",
        "demandes_repetitives", "faits_a_memoriser", "habitudes_utilisateur",
        "ameliorations_regles"]


def _parse_json(raw: str) -> dict:
    raw = re.sub(r'^```(?:json)?|```$', '', raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", default="meta_analyse_complete")
    args = ap.parse_args()

    analyses = []
    for pred in vf._iter_gcs_predictions(args.bucket, args.project, args.job):
        user = vf._echoed_user_text(pred)
        m = _META.search(user)
        sid, src = (m.group(1).strip(), m.group(2).strip()) if m else ("?", "?")
        a = _parse_json(vf._response_text(pred))
        if a:
            analyses.append({"session_id": sid, "source": src, "analyse": a})

    raw_path = Path(f"{args.out}.jsonl")
    with raw_path.open("w", encoding="utf-8") as f:
        for r in analyses:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Agrégats
    buckets = {k: [] for k in KEYS}
    src_count = Counter()
    for r in analyses:
        src_count[r["source"]] += 1
        for k in KEYS:
            for it in r["analyse"].get(k, []) or []:
                if isinstance(it, str):
                    buckets[k].append(it)

    rep = Path(f"{args.out}_rapport.md")
    with rep.open("w", encoding="utf-8") as f:
        f.write(f"# Méta-analyse complète des conversations — rapport\n\n")
        f.write(f"{len(analyses)} sessions analysées "
                f"({dict(src_count)}).\n\n")
        for k in KEYS:
            f.write(f"## {k.replace('_',' ').title()} — {len(buckets[k])} occurrences\n\n")
            for it in buckets[k]:
                f.write(f"- {it}\n")
            f.write("\n")

    mem = Path(f"{args.out}_candidats_memoire.md")
    with mem.open("w", encoding="utf-8") as f:
        f.write("# Candidats-mémoire (à trier vers contexte_ia / règles / notes)\n\n")
        for k in ["faits_a_memoriser", "habitudes_utilisateur", "ameliorations_regles"]:
            f.write(f"## {k.replace('_',' ').title()}\n\n")
            for it in sorted(set(buckets[k])):
                f.write(f"- [ ] {it}\n")
            f.write("\n")

    print(f"[OK] {len(analyses)} analyses collectées")
    print(f"     {raw_path}")
    print(f"     {rep}  (rapport consolidé)")
    print(f"     {mem}  (candidats-mémoire)")
    for k in KEYS:
        print(f"       {k:24} {len(buckets[k])}")
    return 0


if __name__ == "__main__":
    import sys; sys.exit(main())
