#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# install.sh — Installation du moteur multi-agents (#T255) — Linux / macOS
#
# Comportement identique à scripts/install.ps1 (Windows) :
#   1. Vérifie que Python ≥ 3.11 est disponible (échec clair sinon) ;
#   2. Crée le venv (.venv/) et installe requirements.txt ;
#   3. Génère .env À PARTIR DE .env.example s'il n'existe pas — et JAMAIS
#      s'il existe déjà (un écrasement détruirait les clés de l'utilisateur) ;
#   4. Pose une MOTEUR_API_KEY aléatoire (32 caractères hexadécimaux) —
#      l'API est fail-closed sans elle (routes /api → 503) ;
#   5. Lance `python seed_models_db.py` si models_registry.db est absente ;
#   6. Affiche la commande exacte de démarrage et l'URL.
#
# Idempotent : relancer le script ne casse rien et n'écrase aucun fichier.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Chemin racine du dépôt (le script vit dans scripts/) ──
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Installation du moteur multi-agents dans : $REPO_ROOT"

# ── 1. Version de Python (≥ 3.11 obligatoire) ──
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "ERREUR : Python introuvable. Installez Python ≥ 3.11 (https://www.python.org/downloads/) puis relancez ce script." >&2
    exit 1
fi
PY_MAJOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.minor)')
PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "ERREUR : Python $PY_VERSION détecté — le moteur exige Python ≥ 3.11. Mettez Python à jour puis relancez ce script." >&2
    exit 1
fi
echo "==> Python $PY_VERSION détecté (≥ 3.11 requis : OK)"

# ── 2. Venv + dépendances ──
# Emplacement du python du venv : bin/ (Linux/macOS) ou Scripts/ (Windows/Git
# Bash — un venv Windows ne crée pas bin/, et une junction de lecteur peut
# décaler les chemins). On accepte les deux.
VENV_PY=""
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    VENV_PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    VENV_PY="$REPO_ROOT/.venv/Scripts/python.exe"
fi
if [ -z "$VENV_PY" ]; then
    echo "==> Création du venv (.venv/)…"
    "$PYTHON_BIN" -m venv "$REPO_ROOT/.venv"
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        VENV_PY="$REPO_ROOT/.venv/bin/python"
    else
        VENV_PY="$REPO_ROOT/.venv/Scripts/python.exe"
    fi
else
    echo "==> Venv existant (.venv/) — réutilisation (rien n'est recréé)."
fi
echo "==> Installation des dépendances (requirements.txt)…"
"$VENV_PY" -m pip install --disable-pip-version-check -r requirements.txt

# ── 3. .env : généré UNIQUEMENT s'il n'existe pas (jamais d'écrasement) ──
if [ -f "$REPO_ROOT/.env" ]; then
    echo "==> .env déjà présent — aucune modification (un écrasement détruirait vos clés)."
else
    if [ ! -f "$REPO_ROOT/.env.example" ]; then
        echo "ERREUR : .env.example introuvable — dépôt incomplet ?" >&2
        exit 1
    fi
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    echo "==> .env généré depuis .env.example."

    # ── 4. MOTEUR_API_KEY aléatoire (32 caractères hexadécimaux) ──
    "$VENV_PY" - <<'PYEOF'
import re
import secrets
from pathlib import Path

path = Path(".env")
key = secrets.token_hex(16)  # 32 caractères hexadécimaux
text = path.read_text(encoding="utf-8", newline="")
if re.search(r"(?m)^MOTEUR_API_KEY=.*$", text):
    text = re.sub(r"(?m)^MOTEUR_API_KEY=.*$", "MOTEUR_API_KEY=" + key, text)
else:
    text = text.rstrip("\r\n") + "\nMOTEUR_API_KEY=" + key + "\n"
path.write_text(text, encoding="utf-8", newline="")
print("==> MOTEUR_API_KEY générée (32 caractères hexadécimaux).")
PYEOF
fi

# ── 5. Peuplement de models_registry.db si absent ──
# [Bugbot] Un seed interrompu laisse une base partielle qui ferait ignorer le
# seed aux exécutions suivantes : on supprime la base partielle et on échoue,
# pour que la prochaine exécution re-tente le seed proprement.
if [ -f "$REPO_ROOT/models_registry.db" ]; then
    echo "==> models_registry.db déjà présent — seed ignoré."
else
    echo "==> Peuplement de models_registry.db (python seed_models_db.py)…"
    if ! "$VENV_PY" seed_models_db.py; then
        echo "ERREUR : seed échoué — suppression de la base partielle (sera re-tentée à la prochaine exécution)." >&2
        rm -f "$REPO_ROOT/models_registry.db" "$REPO_ROOT/models_registry.db-wal" "$REPO_ROOT/models_registry.db-shm"
        exit 1
    fi
fi

# ── 6. Récapitulatif : commande de démarrage exacte + URL ──
echo
echo "✅ Installation terminée."
echo
echo "Démarrage du moteur :"
echo "    cd $REPO_ROOT"
if [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    echo "    .venv/Scripts/python.exe gui_server.py"
else
    echo "    .venv/bin/python gui_server.py"
fi
echo
echo "IHM      : http://127.0.0.1:8000/"
echo "Sonde    : http://127.0.0.1:8000/healthz"
echo
echo "MOTEUR_API_KEY : voir .env (clients : IHM, IDE — en-tête Authorization: Bearer <clé>)."
