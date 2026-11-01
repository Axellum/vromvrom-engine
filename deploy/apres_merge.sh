#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# deploy/apres_merge.sh — Après un merge sur master : déployer, vérifier le
# jeton, synchroniser les clones DreamCoder, injecter le lot CODE, drainer.
#
# À lancer SUR L'HÔTE du Deck (pas depuis un cloud, pas dans le conteneur) :
#     ssh -i ~/.ssh/id_ed25519 deck@192.168.1.10 \
#         '/opt/vromvrom-engine/deploy/apres_merge.sh'
#
# Ce script ne lit jamais la valeur d'un secret : il teste la PRÉSENCE du
# jeton, et passe la clé d'API en en-tête HTTP sans l'afficher.
# ══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO="${MOTEUR_REPO:-/opt/vromvrom-engine}"
HEALTH_URL="${MOTEUR_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
API_URL="${MOTEUR_API_URL:-http://127.0.0.1:8000}"
LOT="${MOTEUR_LOT:-scripts/lots/lot_code_post_t432.md}"

log()  { printf '\033[36m[apres]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[apres]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[apres] ÉCHEC :\033[0m %s\n' "$*" >&2; exit 1; }

cd "$REPO" || die "dépôt introuvable : $REPO"

# ── 1. Déploiement ────────────────────────────────────────────────────────
if [[ -x deploy/deploy_deck.sh ]]; then
    log "déploiement origin/master"
    ./deploy/deploy_deck.sh master
else
    die "deploy/deploy_deck.sh absent — la prod n'est pas à jour"
fi

# ── 2. Santé ──────────────────────────────────────────────────────────────
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_URL" || echo 000)"
[[ "$code" == "200" ]] || die "/healthz → $code"
log "/healthz → 200"

# ── 3. Jeton GitHub (présence seulement) ──────────────────────────────────
if [[ -f .env ]] && grep -qE '^MOTEUR_GITHUB_PUSH_TOKEN=.+' .env; then
    log "MOTEUR_GITHUB_PUSH_TOKEN : présent (les PR peuvent s'ouvrir)"
else
    warn "MOTEUR_GITHUB_PUSH_TOKEN : ABSENT — les branches partiront sans PR"
fi

if [[ -f config.json ]] && grep -q 'ollama_pc' config.json; then
    warn "config.json mentionne encore ollama_pc (fichier préservé au deploy)."
    warn "Le repli Python n'en a plus ; le fast-path live lit CE fichier."
fi

# ── 4. Fast-forward des clones DreamCoder ─────────────────────────────────
python3 - <<'PY'
import json, os, subprocess, sys
from pathlib import Path

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
pa = cfg.get("persistent_agents") or {}
chemins = pa.get("dreamcoder_repo_paths") or []
if isinstance(chemins, str):
    chemins = [chemins]
chemins = [c for c in chemins if c] or [pa.get("dreamcoder_repo_path") or ""]
chemins = [c for c in chemins if c]
if not chemins:
    print("[apres] aucun clone DreamCoder configuré — drainage dans la prod, à éviter")
    sys.exit(0)
for p in chemins:
    if not os.path.isdir(os.path.join(p, ".git")) and not os.path.isfile(os.path.join(p, ".git")):
        print(f"[apres] clone absent : {p}", file=sys.stderr)
        continue
    subprocess.check_call(["git", "-C", p, "fetch", "--quiet", "origin", "master"])
    r = subprocess.run(["git", "-C", p, "merge-base", "--is-ancestor", "HEAD", "origin/master"])
    if r.returncode != 0:
        print(f"[apres] {p} n'est pas un ancêtre de origin/master — non touché", file=sys.stderr)
        continue
    subprocess.check_call(["git", "-C", p, "merge", "--ff-only", "--quiet", "origin/master"])
    sha = subprocess.check_output(["git", "-C", p, "rev-parse", "--short", "HEAD"], text=True).strip()
    print(f"[apres] clone {p} → {sha}")
PY

# ── 5. Injection du lot CODE ──────────────────────────────────────────────
if [[ -f "$LOT" ]]; then
    log "injection $LOT"
    python3 scripts/injecter_taches.py "$LOT" --envoyer --url "$API_URL"
else
    warn "lot introuvable ($LOT) — le drainage reprendra les failed existantes (#T432)"
fi

# ── 6. Drainage immédiat ──────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    die ".env absent — impossible d'appeler /api/dreamcoder/drain"
fi
# shellcheck disable=SC1091
set +x
CLE="$(grep -m1 '^MOTEUR_API_KEY=' .env | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')"
[[ -n "$CLE" ]] || die "MOTEUR_API_KEY absente du .env"
log "POST /api/dreamcoder/drain"
    curl -sS --max-time 2400 \
        -H "Authorization: Bearer ${CLE}" \
        -X POST "$API_URL/api/dreamcoder/drain" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("[apres] drain:", d.get("status"), (d.get("drain") or {}).get("processed"), "tâche(s)")'
unset CLE

log "terminé"
