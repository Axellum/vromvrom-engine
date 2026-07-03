"""
tools/extract_conversations.py — Extraction & normalisation des transcripts (P1).

Phase P1 de la « mémoire unifiée » : rassemble les conversations éparpillées
(Claude Code, Antigravity IDE) en UN corpus normalisé, prêt pour la méta-analyse
Gemini (erreurs, hallucinations, redondances, habitudes, faits à mémoriser).

Sources gérées :
  - claude     : ~/.claude/projects/<projet>/*.jsonl  (format propre, 1 ligne/évènement)
  - antigravity: ~/.gemini/antigravity-ide/conversations/*.db (SQLite « trajectory »,
                 payloads protobuf → extraction heuristique de chaînes)

Sortie : 1 objet JSON par session :
  {session_id, source, project, date_debut, date_fin, git_branch, n_turns,
   objective, turns:[{role, text}]}

Usage :
  python tools/extract_conversations.py --source claude --out corpus_pilote.jsonl --limit 5
  python tools/extract_conversations.py --source claude --out corpus_claude.jsonl
  python tools/extract_conversations.py --source antigravity --out corpus_antigravity.jsonl
"""
from __future__ import annotations
import argparse, glob, json, os, re, sqlite3, sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
ANTIGRAVITY_CONV = HOME / ".gemini" / "antigravity-ide" / "conversations"

# Projets Claude considérés comme LE même projet logique (split d'identité E--/h--).
CLAUDE_PROJECT_GLOBS = ["*AuxFilsDesIdees-moteur-agents", "*AuxFilsDesIdees",
                        "*Workspace-Nuit"]

MAX_TOOL_CHARS = 280   # on tronque les sorties d'outils : la méta-analyse veut le fil, pas les dumps


# ──────────────────────────────────────────────────────────────────────────
# Source Claude (.jsonl)
# ──────────────────────────────────────────────────────────────────────────

def _claude_text_from_content(content) -> str:
    """Aplatit le `message.content` Anthropic (str ou liste de blocs) en texte lisible."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            out.append(b.get("text", "").strip())
        elif t == "tool_use":
            inp = json.dumps(b.get("input", {}), ensure_ascii=False)
            out.append(f"[outil:{b.get('name')} {inp[:MAX_TOOL_CHARS]}]")
        elif t == "tool_result":
            c = b.get("content", "")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            out.append(f"[résultat: {str(c)[:MAX_TOOL_CHARS]}]")
    return "\n".join(x for x in out if x)


def extract_claude(limit: int | None) -> list[dict]:
    sessions = []
    seen_dirs = []
    for g in CLAUDE_PROJECT_GLOBS:
        seen_dirs += glob.glob(str(CLAUDE_PROJECTS / g))
    files = []
    for d in sorted(set(seen_dirs)):
        files += glob.glob(os.path.join(d, "*.jsonl"))
    files.sort(key=os.path.getmtime, reverse=True)
    if limit:
        files = files[:limit]
    for f in files:
        turns, objective, branch, project = [], None, None, Path(f).parent.name
        t0 = t1 = None
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            typ = d.get("type")
            if typ not in ("user", "assistant"):
                continue
            ts = d.get("timestamp")
            if ts:
                t0 = t0 or ts
                t1 = ts
            branch = branch or d.get("gitBranch")
            txt = _claude_text_from_content(d.get("message", {}).get("content"))
            if not txt:
                continue
            role = "user" if typ == "user" else "assistant"
            # ignorer les injections harness (system-reminder, etc.) trop longues côté user
            if role == "user" and txt.startswith("<") and len(txt) > 4000:
                continue
            if role == "user" and objective is None and not txt.startswith("["):
                objective = txt[:200]
            turns.append({"role": role, "text": txt})
        if not turns:
            continue
        sessions.append({
            "session_id": Path(f).stem, "source": "claude", "project": project,
            "date_debut": t0, "date_fin": t1, "git_branch": branch,
            "n_turns": len(turns), "objective": objective, "turns": turns,
        })
    return sessions


# ──────────────────────────────────────────────────────────────────────────
# Source Antigravity (.db SQLite, payloads protobuf → heuristique)
# ──────────────────────────────────────────────────────────────────────────

_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-', re.I)

def _read_varint(b: bytes, i: int):
    """Lit un varint protobuf. Retourne (valeur, nouvel_index) ou (None, i) si invalide."""
    shift = 0; val = 0
    while i < len(b):
        c = b[i]; val |= (c & 0x7f) << shift; i += 1
        if not (c & 0x80):
            return val, i
        shift += 7
        if shift > 63:
            return None, i
    return None, i


def _is_natural_text(s: str) -> bool:
    """Heuristique : garde le langage naturel / markdown, jette UUID, ids, args JSON techniques."""
    s = s.strip()
    if len(s) < 15 or _UUID.match(s) or s.startswith("{"):
        return False
    letters = sum(c.isalpha() for c in s)
    if letters / len(s) < 0.45:           # trop de symboles/chiffres → pas du texte
        return False
    return " " in s                       # une vraie phrase a des espaces


def _protobuf_text_leaves(blob: bytes, depth: int = 0, out: list | None = None) -> list[str]:
    """Parcourt le format wire protobuf et collecte les feuilles texte (récursif, sans schéma)."""
    if out is None:
        out = []
    if depth > 6 or not blob:
        return out
    i = 0; n = len(blob)
    while i < n:
        tag, i = _read_varint(blob, i)
        if tag is None:
            break
        wt = tag & 7
        if wt == 0:                       # varint
            _, i = _read_varint(blob, i)
        elif wt == 2:                     # length-delimited (string/bytes/sous-message)
            ln, i = _read_varint(blob, i)
            if ln is None or i + ln > n:
                break
            chunk = blob[i:i + ln]; i += ln
            try:
                s = chunk.decode("utf-8")
                printable = sum(1 for c in s if c.isprintable() or c in "\n\r\t") / max(1, len(s))
            except Exception:
                s = None; printable = 0
            if s is not None and printable > 0.9 and _is_natural_text(s):
                out.append(s.strip())
            elif chunk:                   # sous-message probable → récursion
                _protobuf_text_leaves(chunk, depth + 1, out)
        elif wt == 5:                     # 32-bit
            i += 4
        elif wt == 1:                     # 64-bit
            i += 8
        else:
            break
    return out


def extract_antigravity(limit: int | None) -> list[dict]:
    dbs = sorted(glob.glob(str(ANTIGRAVITY_CONV / "*.db")), key=os.path.getmtime, reverse=True)
    if limit:
        dbs = dbs[:limit]
    sessions = []
    for f in dbs:
        try:
            c = sqlite3.connect(f"file:{f}?mode=ro&immutable=1", uri=True)
            rows = c.execute("SELECT step_type, step_payload FROM steps ORDER BY idx").fetchall()
            c.close()
        except Exception as e:
            print(f"  ! {Path(f).name}: {e}", file=sys.stderr)
            continue
        turns = []
        seen = set()
        for st, payload in rows:
            if not isinstance(payload, (bytes, bytearray)):
                continue
            frags = _protobuf_text_leaves(bytes(payload))
            # dédup intra-session (les trajectoires répètent souvent le même texte)
            uniq = [s for s in frags if not (s in seen or seen.add(s))]
            text = "\n".join(uniq)
            if len(text) > 20:
                turns.append({"role": f"step:{st}", "text": text[:3000]})
        if not turns:
            continue
        sessions.append({
            "session_id": Path(f).stem, "source": "antigravity", "project": "antigravity-ide",
            "date_debut": None, "date_fin": None, "git_branch": None,
            "n_turns": len(turns), "objective": turns[0]["text"][:200] if turns else None,
            "turns": turns,
        })
    return sessions


# ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["claude", "antigravity"], required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=None, help="N sessions les plus récentes (pilote).")
    args = p.parse_args()

    fn = {"claude": extract_claude, "antigravity": extract_antigravity}[args.source]
    sessions = fn(args.limit)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sessions:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    tot_turns = sum(s["n_turns"] for s in sessions)
    print(f"[OK] {len(sessions)} sessions, {tot_turns} tours -> {args.out}")
    for s in sessions[:8]:
        print(f"   {s['source']:11} {s['n_turns']:4d}t  {(s['objective'] or '')[:64]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
