#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# deploy/deploy_deck.sh — Déploiement du moteur sur le Steam Deck depuis GitHub
# ══════════════════════════════════════════════════════════════════════════
#
# Remplace la copie manuelle fichier par fichier (ssh + cat + tr -d '\r' +
# comparaison de sha256sum) qui avait fait diverger la production du dépôt.
#
# Usage — à lancer SUR L'HÔTE du Deck (pas dans le conteneur) :
#     ./deploy/deploy_deck.sh                 # déploie origin/master
#     ./deploy/deploy_deck.sh ma-branche      # déploie une autre branche
#     ./deploy/deploy_deck.sh master --dry-run
#     ./deploy/deploy_deck.sh master --force  # accepte d'écraser des modifs locales
#
# Depuis Windows :
#     ssh -i ~/.ssh/id_ed25519_deck deck@192.168.1.x \
#         '/home/deck/dev_station/moteur_agents/deploy/deploy_deck.sh master'
#
# Garanties :
#   - les fichiers NON SUIVIS ne sont jamais touchés (bases .db de production,
#     LoRA, datasets) : on utilise `git reset --hard`, jamais `git clean` ;
#   - config.json et pricing_strategy.json sont préservés, y compris à travers
#     un rollback : ce sont de l'état runtime réécrit par le moteur lui-même
#     (POST /api/config et POST /api/pricing → safe_json_write) ;
#   - refus de déployer si des fichiers suivis ont été modifiés à la main sur
#     la prod (c'est exactement ce qui a causé la divergence) — sauf --force ;
#   - TOUT échec après la bascule (dépendances, build frontend, redémarrage,
#     santé) déclenche le même rollback complet : code, dépendances et
#     frontend sont ramenés ensemble à la révision précédente.

set -euo pipefail

REPO="${MOTEUR_REPO:-/home/deck/dev_station/moteur_agents}"
SERVICE="${MOTEUR_SERVICE:-moteur_agents.service}"
CONTAINER="${MOTEUR_CONTAINER:-ubuntu-dev}"
HEALTH_URL="${MOTEUR_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
BACKUP_DIR="${MOTEUR_BACKUP_DIR:-/home/deck/backups}"
HEALTH_RETRIES=30
HEALTH_DELAY=2

# Fichiers réécrits en production par le moteur lui-même : ce sont de l'état
# runtime, pas du code. Ils sont sauvegardés avant la bascule puis restaurés
# après, y compris à travers un rollback.
#
# models/ml_router.pkl et son méta ne sont plus suivis sur master (.gitignore),
# mais ils le sont encore dans l'instantané de production antérieur au
# rattachement à origin. Sans cette préservation, le `git reset --hard` de la
# première bascule les supprimerait, et le routeur ML repartirait de zéro :
# `MLRouter.train()` exige min_samples=50 avant de pouvoir se réentraîner.
PRESERVE=(config.json pricing_strategy.json models/ml_router.pkl models/ml_router_meta.json)

BRANCH="master"
DRY_RUN=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --force)   FORCE=1 ;;
        -*)        echo "Option inconnue : $arg" >&2; exit 2 ;;
        *)         BRANCH="$arg" ;;
    esac
done

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
TS="$(date +%Y%m%d-%H%M%S)"
PRESERVE_DIR=""
log()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[deploy] ÉCHEC :\033[0m %s\n' "$*" >&2; exit 1; }

# Le répertoire des fichiers préservés ne doit disparaître qu'à la toute fin :
# le rollback en a besoin, sinon `git reset --hard` rendrait à la production
# les config.json/pricing_strategy.json de l'ancien commit au lieu des siens.
# `return 0` obligatoire : sans lui, quand PRESERVE_DIR est vide, le test échoue,
# bash reprend ce statut comme code de sortie du script et un déploiement RÉUSSI
# se termine en 1 — tout appel automatisé le prendrait pour un échec.
cleanup() { [[ -n "$PRESERVE_DIR" ]] && rm -rf "$PRESERVE_DIR"; return 0; }
trap cleanup EXIT

health_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || echo "000"; }

# Réécrit `.deploy_meta.json` pour que `/version` reflète ce qui tourne VRAIMENT.
# `/version` (gui_server.py) lit ce fichier en priorité sur git : sans ce stamp,
# un ancien méta d'époque « déploiement overlay » (28/07, caf38ce) reste en place
# et l'endpoint ment alors que le HEAD a bougé. Le fichier est gitignoré : c'est
# de l'état runtime, au même titre que config.json. Appelé après un déploiement
# réussi ET après un rollback (avec le SHA effectivement restauré).
write_deploy_meta() {
    local sha="$1" short full cdate
    short="$(git rev-parse --short "$sha")"
    full="$(git rev-parse "$sha")"
    cdate="$(git show -s --format=%cI "$sha")"
    cat > .deploy_meta.json <<EOF
{
  "git_hash": "$short",
  "git_full": "$full",
  "build_date": "$cdate",
  "deployed_at": "$TS"
}
EOF
    log ".deploy_meta.json stampé → $short"
}

wait_healthy() {
    local i code
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        code="$(health_code)"
        [[ "$code" == "200" ]] && { log "/healthz → 200 (après ${i} tentative(s))"; return 0; }
        sleep "$HEALTH_DELAY"
    done
    warn "/healthz → $code après $((HEALTH_RETRIES * HEALTH_DELAY))s"
    return 1
}

restore_preserved() {
    local p
    for p in "${PRESERVE[@]}"; do
        [[ -f "$PRESERVE_DIR/$p" ]] || continue
        # `mkdir -p` obligatoire : le répertoire parent peut ne plus exister
        # après la bascule (models/ ne contient aucun fichier suivi sur master,
        # git le supprime donc en même temps que son dernier fichier). Sans lui
        # `cp` échoue ; comme il suit le dernier `&&` d'une liste, `set -e` ne
        # l'exempte pas : le script s'arrêterait juste après le reset, sans
        # rollback, et le trap EXIT détruirait PRESERVE_DIR — soit la perte des
        # fichiers mêmes que l'on cherchait à préserver.
        mkdir -p "$(dirname "$p")"
        cp -p "$PRESERVE_DIR/$p" "$p"
    done
    return 0
}

changed_between() { ! git diff --quiet "$1" "$2" -- "$3"; }

# $1 >= $2 au sens des numéros de version (sort -V).
version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

# La chaîne de build du frontend (vite, rolldown, @vitejs/plugin-react) déclare
# `engines.node: ^20.19.0 || >=22.12.0`. Sous Node 18, rolldown échoue à l'import
# sur `styleText` (absent de node:util avant 20.12) — et l'échec survient APRÈS la
# bascule, donc part en rollback. Autant refuser avant de toucher à quoi que ce
# soit : un prérequis d'infrastructure manquant n'est pas une raison de dérouler
# un déploiement complet pour l'apprendre à la fin.
NODE_MIN_20="20.19.0"
NODE_MIN_22="22.12.0"
check_node_version() {
    local v major
    v="$(podman exec -u deck "$CONTAINER" node --version 2>/dev/null | tr -d 'v')"
    if [[ -z "$v" ]]; then
        warn "impossible de lire la version de node dans $CONTAINER — contrôle ignoré."
        return 0
    fi
    major="${v%%.*}"
    if version_ge "$v" "$NODE_MIN_22"; then return 0; fi
    if [[ "$major" == "20" ]] && version_ge "$v" "$NODE_MIN_20"; then return 0; fi
    die "node $v dans $CONTAINER est trop ancien pour construire ihm-v2 (requis : ^$NODE_MIN_20 ou >=$NODE_MIN_22).
       Mettre à niveau, puis relancer :
         podman exec -u root $CONTAINER bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs'"
}

install_deps() {
    # --break-system-packages : le conteneur ubuntu-dev (Ubuntu 24.04, Python
    # 3.12) marque son interpréteur « externally managed » (PEP 668) et refuse
    # toute installation système sans ce drapeau. Le conteneur EST l'environnement
    # isolé — la commande de production y lance le python3 système — et c'est
    # ainsi que les dépendances y ont été posées à l'origine. Sans ce drapeau,
    # tout déploiement touchant requirements.txt échoue et part en rollback.
    log "requirements.txt a changé — installation des dépendances dans $CONTAINER"
    podman exec -u deck --workdir "$REPO" "$CONTAINER" \
        python3 -m pip install --quiet --break-system-packages -r requirements.txt
}

build_frontend() {
    # ihm-v2/dist est monté par gui_server.py (StaticFiles sur /). Il n'est pas
    # suivi par git : sans rebuild, l'IHM reste sur l'ancien build alors que
    # /healthz répond 200. npm n'existe que dans le conteneur, pas sur l'hôte.
    log "ihm-v2 a changé — build du frontend dans $CONTAINER"
    podman exec -u deck --workdir "$REPO/ihm-v2" "$CONTAINER" npm ci --silent \
        && podman exec -u deck --workdir "$REPO/ihm-v2" "$CONTAINER" npm run build
}

# Ramène le code ET son environnement (dépendances, build frontend) à OLD_SHA.
rollback() {
    warn "$1 → rollback vers $(git rev-parse --short "$OLD_SHA")"
    git reset --hard --quiet "$OLD_SHA"

    # Restaurer le périmètre git depuis l'archive d'avant-bascule.
    #
    # Indispensable, et pas seulement par prudence : un fichier NON SUIVI en
    # production dont la cible, elle, assure le suivi est écrasé par le
    # `git reset --hard origin/master` (il devient suivi), puis SUPPRIMÉ par le
    # reset de ce rollback (suivi dans le HEAD courant, absent de OLD_SHA). Il
    # disparaît alors qu'il n'a jamais été committé nulle part.
    #
    # C'est ce qui a mis la production à terre le 2026-08-09 : api/routes/health.py
    # était non suivi sur le Deck et suivi sur master ; après rollback,
    # gui_server.py bouclait sur ModuleNotFoundError. Même cause pour les sources
    # frontend (views/, api/, components/ui/) et donc pour l'échec du rebuild.
    #
    # L'archive contient `git ls-files` ET `git ls-files --others
    # --exclude-standard` : elle couvre exactement ces fichiers. Les fichiers
    # ignorés (bases de production, LoRA, datasets, chroma_db/) n'y sont pas et
    # ne sont donc pas touchés. Pour les fichiers suivis l'extraction est un
    # no-op, leur contenu correspondant déjà à OLD_SHA après le reset.
    if [[ -s "$ARCHIVE" ]]; then
        log "restauration du périmètre git depuis $ARCHIVE"
        tar -xzf "$ARCHIVE" -C "$REPO" \
            || warn "extraction de l'archive en échec — vérifier $ARCHIVE à la main"
    else
        warn "archive introuvable ou vide : les fichiers non suivis adoptés par la bascule ne peuvent pas être restaurés."
    fi

    restore_preserved
    if changed_between "$OLD_SHA" "$NEW_SHA" requirements.txt; then
        install_deps || warn "réinstallation des dépendances d'origine en échec"
    fi
    if changed_between "$OLD_SHA" "$NEW_SHA" ihm-v2/; then
        build_frontend || warn "rebuild du frontend d'origine en échec"
    fi
    systemctl --user restart "$SERVICE" || warn "le redémarrage après rollback a échoué"
    # Le méta doit refléter l'état restauré, sinon /version annoncerait la cible
    # du déploiement annulé. Tolérant à l'échec : un rollback ne doit pas
    # s'arrêter pour un problème de cosmétique.
    write_deploy_meta "$OLD_SHA" || warn "stamp du méta en échec après rollback"
    if wait_healthy; then
        die "déploiement annulé, service restauré sur $(git rev-parse --short HEAD). Archive : $ARCHIVE"
    fi
    die "déploiement ET rollback en échec — intervention manuelle requise. Archive : $ARCHIVE"
}

# ── 1. Vérifications préalables ───────────────────────────────────────────
cd "$REPO" || die "dépôt introuvable : $REPO"
git rev-parse --git-dir >/dev/null 2>&1 || die "$REPO n'est pas un dépôt git"
git remote get-url origin >/dev/null 2>&1 \
    || die "aucun remote 'origin'. Poser : git remote add origin git@github.com:Axellum/moteur_agents.git"

PRE_HEALTH="$(health_code)"
log "état avant déploiement : /healthz → $PRE_HEALTH"
[[ "$PRE_HEALTH" == "200" ]] || warn "le service ne répondait pas 200 AVANT le déploiement"

# Modifications locales sur des fichiers suivis = signal de dérive manuelle.
DIRTY="$(git status --porcelain --untracked-files=no | awk '{print $2}')"
UNEXPECTED=""
for f in $DIRTY; do
    keep=0
    for p in "${PRESERVE[@]}"; do [[ "$f" == "$p" ]] && keep=1; done
    [[ $keep -eq 0 ]] && UNEXPECTED+="  $f"$'\n'
done
if [[ -n "$UNEXPECTED" ]]; then
    warn "fichiers suivis modifiés directement sur la prod :"
    printf '%s' "$UNEXPECTED" >&2
    if [[ $FORCE -eq 0 ]]; then
        die "ces modifications seraient perdues. Les remonter en PR, ou relancer avec --force."
    fi
    warn "--force : ces modifications vont être écrasées (sauvegardées dans l'archive)."
fi

# ── 2. Récupération et aperçu ─────────────────────────────────────────────
log "git fetch origin"
git fetch --quiet origin "$BRANCH" || die "git fetch a échoué"
OLD_SHA="$(git rev-parse HEAD)"
NEW_SHA="$(git rev-parse "origin/$BRANCH")"

if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
    log "déjà à jour sur origin/$BRANCH ($(git rev-parse --short "$NEW_SHA")) — rien à déployer."
    exit 0
fi

log "bascule : $(git rev-parse --short "$OLD_SHA") → $(git rev-parse --short "$NEW_SHA") (origin/$BRANCH)"
git diff --stat "$OLD_SHA" "$NEW_SHA" | tail -25

# Fichiers non suivis en production que la cible, elle, suit : la bascule va les
# écraser avec la version du dépôt (ils deviennent suivis). Leur contenu local
# n'existe alors plus que dans l'archive. Le rollback sait les restaurer, mais
# l'opérateur doit les voir : c'est de la divergence non committée, et le
# --dry-run est le seul moment où l'on peut encore la remonter en PR.
ADOPTED="$(comm -12 \
    <(git ls-files --others --exclude-standard | sort) \
    <(git ls-tree -r --name-only "$NEW_SHA" | sort))"
if [[ -n "$ADOPTED" ]]; then
    warn "fichiers non suivis en prod que $BRANCH suit — ils seront écrasés par la version du dépôt :"
    printf '%s\n' "$ADOPTED" | sed 's/^/  /' >&2
    warn "leur contenu actuel ne survivra que dans l'archive d'avant-bascule."
fi

# Prérequis d'infrastructure : à vérifier tant que rien n'a bougé. Le --dry-run
# doit signaler ce blocage, sinon on ne l'apprend qu'après la bascule.
if changed_between "$OLD_SHA" "$NEW_SHA" ihm-v2/; then
    check_node_version
fi

if [[ $DRY_RUN -eq 1 ]]; then
    log "--dry-run : aucune modification appliquée."
    exit 0
fi

# ── 3. Sauvegarde ─────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
ARCHIVE="$BACKUP_DIR/predeploy_${TS}.tar.gz"
LIST="/tmp/deploy_files_${TS}.z"
log "sauvegarde du périmètre git → $ARCHIVE"
{ git ls-files -z; git ls-files --others --exclude-standard -z; } | sort -zu > "$LIST"

# tar renvoie 1 si un fichier a changé pendant la lecture (le service tourne) :
# c'est tolérable. Un code > 1 est une vraie erreur et doit stopper le déploiement,
# sinon on basculerait sans filet de sécurité.
set +e
tar --null -czf "$ARCHIVE" -T "$LIST"
TAR_RC=$?
set -e
rm -f "$LIST"
[[ $TAR_RC -gt 1 ]] && die "sauvegarde impossible (tar a renvoyé $TAR_RC) — déploiement annulé."
[[ $TAR_RC -eq 1 ]] && warn "tar : des fichiers ont changé pendant la lecture (service actif), archive exploitable."
[[ -s "$ARCHIVE" ]] || die "l'archive de sauvegarde est vide — déploiement annulé."
log "archive vérifiée ($(du -h "$ARCHIVE" | cut -f1))"

git tag -f "deploy/pre-${TS}" "$OLD_SHA" >/dev/null
log "tag de rollback posé : deploy/pre-${TS}"

PRESERVE_DIR="$(mktemp -d)"
for p in "${PRESERVE[@]}"; do
    [[ -f "$p" ]] && { mkdir -p "$PRESERVE_DIR/$(dirname "$p")"; cp -p "$p" "$PRESERVE_DIR/$p"; }
done
log "fichiers d'état préservés : ${PRESERVE[*]}"

# ── 4. Bascule ────────────────────────────────────────────────────────────
# `reset --hard` ne supprime PAS les fichiers non suivis : bases de production,
# LoRA et datasets restent intacts. Ne JAMAIS ajouter `git clean` ici.
log "git reset --hard origin/$BRANCH"
git reset --hard --quiet "origin/$BRANCH"
restore_preserved

# ── 5. Environnement : dépendances puis frontend ──────────────────────────
if changed_between "$OLD_SHA" "$NEW_SHA" requirements.txt; then
    install_deps || rollback "installation des dépendances en échec"
fi
if changed_between "$OLD_SHA" "$NEW_SHA" ihm-v2/; then
    build_frontend || rollback "build du frontend en échec"
fi

# ── 6. Redémarrage et contrôle de santé ───────────────────────────────────
log "redémarrage de $SERVICE"
systemctl --user restart "$SERVICE" || rollback "le redémarrage de $SERVICE a échoué"

wait_healthy || rollback "contrôle de santé en échec"

write_deploy_meta "$NEW_SHA" || warn "stamp du méta en échec (sans gravité : /version retombera sur git)"

log "déploiement réussi : $(git rev-parse --short HEAD) ($BRANCH)"
log "rollback si besoin : git reset --hard deploy/pre-${TS} && systemctl --user restart $SERVICE"
