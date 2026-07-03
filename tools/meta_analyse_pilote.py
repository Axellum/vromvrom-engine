"""
tools/meta_analyse_pilote.py — Méta-analyse des sessions (P1, mode synchrone/pilote).

Lit un corpus normalisé (sortie de extract_conversations.py), envoie chaque session à
Gemini avec le prompt méta-analyste, et écrit l'analyse JSON par session. Sert à VALIDER
le prompt sur quelques sessions avant de lancer le job Vertex Batch complet.

KeyPool : rotation des clés GEMINI_API_KEY* du .env sur erreur 429 (rate limit Free Tier).

Usage :
  python tools/meta_analyse_pilote.py --corpus corpus_pilote_claude.jsonl \
      --out pilote_analyses.jsonl --model gemini-2.5-flash --limit 5
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

PROMPT = Path("tools/prompts/meta_analyse_conversation.md").read_text(encoding="utf-8")
MAX_CHARS = 200_000   # garde-fou contexte (Flash encaisse large)


def load_keys() -> list[str]:
    keys = []
    for l in Path(".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r'^(GEMINI_API_KEY[0-9_]*)=(.+)$', l.strip())
        if m:
            keys.append(m.group(2).strip().strip('"').strip("'"))
    return [k for k in keys if k]


def call_gemini(text: str, model: str, keys: list[str]) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192,
                             "responseMimeType": "application/json"},
    }).encode()
    last = ""
    for k in keys:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}"
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=120))
            return r["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            last = f"{e.code} {e.read()[:120]}"
            if e.code in (429, 503):       # rate limit / surcharge → clé suivante
                time.sleep(1); continue
            break
        except Exception as e:
            last = str(e)[:160]; continue
    raise RuntimeError(f"toutes les clés ont échoué: {last}")


def session_to_text(s: dict) -> str:
    head = (f"SESSION {s['session_id']} | source {s['source']} | projet {s.get('project')} | "
            f"branche {s.get('git_branch')} | objectif: {s.get('objective')}")
    body = "\n".join(f"### {t['role'].upper()}\n{t['text']}" for t in s["turns"])
    full = f"{PROMPT}\n\n=== TRANSCRIPT À ANALYSER ===\n\n{head}\n\n{body}"
    return full[:MAX_CHARS]


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?|```$', '', raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group(0)) if m else {"_parse_error": raw[:500]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    keys = load_keys()
    print(f"{len(keys)} clés Gemini chargées, modèle={args.model}")
    sessions = [json.loads(l) for l in Path(args.corpus).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        sessions = sessions[:args.limit]
    out = open(args.out, "w", encoding="utf-8")
    for i, s in enumerate(sessions, 1):
        t0 = time.time()
        try:
            raw = call_gemini(session_to_text(s), args.model, keys)
            analyse = extract_json(raw)
            err = analyse.get("_parse_error")
        except Exception as e:
            analyse, err = {"_error": str(e)}, str(e)
        rec = {"session_id": s["session_id"], "source": s["source"],
               "objective": s.get("objective"), "n_turns": s["n_turns"], "analyse": analyse}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
        n = lambda k: len(analyse.get(k, [])) if isinstance(analyse.get(k), list) else "-"
        print(f"  [{i}/{len(sessions)}] {s['n_turns']:4d}t {time.time()-t0:4.0f}s  "
              f"err={n('erreurs')} hallu={n('hallucinations')} repet={n('demandes_repetitives')} "
              f"mem={n('faits_a_memoriser')}" + (f"  ⚠️{err[:40]}" if err else ""))
    out.close()
    print(f"[OK] -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
