"""
tools/build_meta_analysis_batch.py — Construit le JSONL de requêtes Vertex Batch
pour la méta-analyse complète des conversations (P1, run complet gemini-2.5-pro).

Lit un/des corpus normalisés (sortie extract_conversations.py) et émet 1 requête batch
par session, au format Vertex Gemini attendu par vertex_dataset_factory.submit().

Garde-fou coût : chaque transcript est plafonné (--cap chars) — la méta-analyse capte
les patterns sans avaler les dumps d'outils. L'id/source sont encodés DANS le prompt
(Vertex ré-émet la requête en sortie → on les relit au collect).

Usage :
  python tools/build_meta_analysis_batch.py \
      --corpus corpus_claude_full.jsonl corpus_antigravity_full.jsonl \
      --out batch_meta_analyse.jsonl --cap 60000
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

PROMPT = Path("tools/prompts/meta_analyse_conversation.md").read_text(encoding="utf-8")

# Séparateurs Unicode qui cassent un découpage ligne-par-ligne en aval (Vertex) → espace.
_BAD = {ord(c): " " for c in "\x0b\x0c\x1c\x1d\x1e\x85  "}


def _clean(t: str) -> str:
    return t.translate(_BAD)


def session_user_text(s: dict, cap: int) -> str:
    head = (f"<<SESSION_ID>>{s['session_id']}<<SRC>>{s['source']}<<END_META>>\n"
            f"projet={s.get('project')} branche={s.get('git_branch')} "
            f"objectif={_clean(str(s.get('objective')))}")
    body = "\n".join(f"### {t['role'].upper()}\n{_clean(t['text'])}" for t in s["turns"])
    return (head + "\n\n" + body)[:cap]


def batch_line(s: dict, cap: int) -> dict:
    return {"request": {
        "systemInstruction": {"parts": [{"text": PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": session_user_text(s, cap)}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192,
                             "responseMimeType": "application/json"},
    }}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=60000, help="Plafond chars/session.")
    args = ap.parse_args()

    n = 0; tot_chars = 0
    with open(args.out, "w", encoding="utf-8") as fout:
        for cf in args.corpus:
            for line in open(cf, encoding="utf-8"):   # itère sur \n seulement (pas splitlines)
                if not line.strip():
                    continue
                s = json.loads(line)
                u = session_user_text(s, args.cap)
                tot_chars += len(u) + len(PROMPT)
                fout.write(json.dumps(batch_line(s, args.cap), ensure_ascii=False) + "\n")
                n += 1
    in_tok = tot_chars // 4
    # Estimation coût gemini-2.5-pro batch (≈ moitié online) : ~0,625$/M in, ~5$/M out.
    out_tok = n * 3000   # ~3k tokens d'analyse/session
    cost = in_tok/1e6*0.625 + out_tok/1e6*5
    print(f"[OK] {n} requêtes -> {args.out}")
    print(f"     entrée ~{in_tok/1e6:.1f}M tok · sortie est. ~{out_tok/1e6:.1f}M tok")
    print(f"     COÛT ESTIMÉ ~ {cost:.1f} $  (gemini-2.5-pro batch)")
    return 0


if __name__ == "__main__":
    import sys; sys.exit(main())
