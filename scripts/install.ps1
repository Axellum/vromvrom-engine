# ═══════════════════════════════════════════════════════════════════════════
# install.ps1 — Installation du moteur multi-agents (#T255) — Windows
#
# Comportement identique à scripts/install.sh (Linux / macOS) :
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

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Chemin racine du dépôt (le script vit dans scripts/) ──
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> Installation du moteur multi-agents dans : $RepoRoot"

# ── 1. Version de Python (≥ 3.11 obligatoire) ──
$Python = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $Python = $candidate; break }
}
if (-not $Python) {
    Write-Host "ERREUR : Python introuvable. Installez Python ≥ 3.11 (https://www.python.org/downloads/) puis relancez ce script." -ForegroundColor Red
    exit 1
}
# `py` est le lanceur Windows : forcer Python 3.
if ($Python -eq "py") { $PythonArgs = @("-3") } else { $PythonArgs = @() }
$PyMajor = & $Python @PythonArgs -c "import sys; print(sys.version_info.major)"
$PyMinor = & $Python @PythonArgs -c "import sys; print(sys.version_info.minor)"
$PyVersion = & $Python @PythonArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($PyMajor -notmatch '^\d+$') {
    Write-Host "ERREUR : impossible de déterminer la version de Python ($PyVersion)." -ForegroundColor Red
    exit 1
}
$Major = [int]$PyMajor
$Minor = [int]$PyMinor
if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 11)) {
    Write-Host "ERREUR : Python $PyVersion détecté — le moteur exige Python ≥ 3.11. Mettez Python à jour puis relancez ce script." -ForegroundColor Red
    exit 1
}
Write-Host "==> Python $PyVersion détecté (≥ 3.11 requis : OK)"

# ── 2. Venv + dépendances ──
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Création du venv (.venv/)…"
    & $Python @PythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERREUR : échec de la création du venv." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "==> Venv existant (.venv/) — réutilisation (rien n'est recréé)."
}
Write-Host "==> Installation des dépendances (requirements.txt)…"
& $VenvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERREUR : échec de pip install." -ForegroundColor Red
    exit 1
}

# ── 3. .env : généré UNIQUEMENT s'il n'existe pas (jamais d'écrasement) ──
$EnvFile = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"
if (Test-Path $EnvFile) {
    Write-Host "==> .env déjà présent — aucune modification (un écrasement détruirait vos clés)."
} else {
    if (-not (Test-Path $EnvExample)) {
        Write-Host "ERREUR : .env.example introuvable — dépôt incomplet ?" -ForegroundColor Red
        exit 1
    }
    Copy-Item $EnvExample $EnvFile
    Write-Host "==> .env généré depuis .env.example."

    # ── 4. MOTEUR_API_KEY aléatoire (32 caractères hexadécimaux) ──
    # PowerShell 5.1 déforme les guillemets doubles d'un code Python passé en
    # argument (-c) : on passe donc par un fichier temporaire (règle du repo :
    # fichiers temp pour les longs prompts côté Windows).
    $PythonCode = @'
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
'@
    $KeyScript = Join-Path $env:TEMP "moteur_gen_key_$PID.py"
    [System.IO.File]::WriteAllText($KeyScript, $PythonCode, [System.Text.UTF8Encoding]::new($false))
    try {
        & $VenvPython $KeyScript
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERREUR : échec de la génération de MOTEUR_API_KEY." -ForegroundColor Red
            exit 1
        }
    } finally {
        Remove-Item $KeyScript -Force -ErrorAction SilentlyContinue
    }
}

# ── 5. Peuplement de models_registry.db si absent ──
# [Bugbot] Un seed interrompu laisse une base partielle qui ferait ignorer le
# seed aux exécutions suivantes : on supprime la base partielle et on échoue,
# pour que la prochaine exécution re-tente le seed proprement.
$RegistryDb = Join-Path $RepoRoot "models_registry.db"
if (Test-Path $RegistryDb) {
    Write-Host "==> models_registry.db déjà présent — seed ignoré."
} else {
    Write-Host "==> Peuplement de models_registry.db (python seed_models_db.py)…"
    & $VenvPython seed_models_db.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERREUR : seed échoué — suppression de la base partielle (sera re-tentée à la prochaine exécution)." -ForegroundColor Red
        Remove-Item $RegistryDb -Force -ErrorAction SilentlyContinue
        Remove-Item "$RegistryDb-wal" -Force -ErrorAction SilentlyContinue
        Remove-Item "$RegistryDb-shm" -Force -ErrorAction SilentlyContinue
        exit 1
    }
}

# ── 6. Récapitulatif : commande de démarrage exacte + URL ──
Write-Host ""
Write-Host "✅ Installation terminée."
Write-Host ""
Write-Host "Démarrage du moteur :"
Write-Host "    cd $RepoRoot"
Write-Host "    .venv\Scripts\python.exe gui_server.py"
Write-Host ""
Write-Host "IHM      : http://127.0.0.1:8000/"
Write-Host "Sonde    : http://127.0.0.1:8000/healthz"
Write-Host ""
Write-Host "MOTEUR_API_KEY : voir .env (clients : IHM, IDE — en-tête Authorization: Bearer <clé>)."
