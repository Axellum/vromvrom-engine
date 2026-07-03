<#
.SYNOPSIS
    Prépare le dossier OSS vromvrom-engine à partir du repo privé.
    Copie le code source en excluant les données personnelles et secrets.
    
.USAGE
    .\prepare_oss_export.ps1
    
.NOTE
    À relancer après chaque mise à jour du moteur pour synchroniser l'OSS.
#>

param(
    [string]$Source = "H:\AuxFilsDesIdees\moteur_agents",
    [string]$Dest   = "H:\vromvrom-engine-oss"
)

Write-Host "=== Préparation export OSS vromvrom-engine ===" -ForegroundColor Cyan
Write-Host "Source : $Source"
Write-Host "Dest   : $Dest"
Write-Host ""

# ─────────────────────────────────────────────────────────────
# Dossiers à COPIER (code source uniquement)
# ─────────────────────────────────────────────────────────────
$DOSSIERS_A_COPIER = @(
    "agents",
    "api",
    "core",
    "deploy",
    "memory",
    "services",
    "static",
    "tests",
    "tools",
    "workflows",
    "plugins"   # Seulement le dossier example, pas auto_generated
)

# ─────────────────────────────────────────────────────────────
# Fichiers racine à COPIER
# ─────────────────────────────────────────────────────────────
$FICHIERS_A_COPIER = @(
    "gui_server.py",
    "main.py",
    "mcp_server.py",
    "workspace_mcp.py",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "seed_models_db.py",     # Peuplement BDD (sans données perso)
    "seed_github_models.py",
    "sync_db_to_markdown.py",
    "populate_embeddings.py",
    "start_moteur.sh",
    ".pre-commit-config.yaml",
    "Dockerfile.webhook",
    "claude_desktop_config.json.example",
    "package.json"
)

# ─────────────────────────────────────────────────────────────
# Extensions / patterns à EXCLURE même dans les dossiers copiés
# ─────────────────────────────────────────────────────────────
$EXCLUSIONS = @(
    "*.db", "*.db-wal", "*.db-shm",
    "*.sqlite3",
    "*.jsonl",
    "*.log", "*.err",
    "*.tmp",
    "__pycache__",
    ".pytest_cache",
    "*.pyc",
    "*.pyo",
    "chroma_db",
    "chromadb_data",
    "auto_generated",       # plugins générés automatiquement (perso)
    "screenshots",          # captures Visual-QA (perso)
    "*.png", "*.jpg", "*.jpeg", "*.gif"  # images perso dans static/
)

# ─────────────────────────────────────────────────────────────
# Fonction de copie avec exclusions
# ─────────────────────────────────────────────────────────────
function Copy-WithExclusions {
    param($Src, $Dst)
    
    if (-not (Test-Path $Src)) {
        Write-Host "  [SKIP] Dossier absent : $Src" -ForegroundColor Yellow
        return
    }
    
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    
    Get-ChildItem -Path $Src -Recurse | ForEach-Object {
        $item = $_
        $relativePath = $item.FullName.Substring($Src.Length).TrimStart('\', '/')
        $destPath = Join-Path $Dst $relativePath
        
        # Vérifier les exclusions
        $excluded = $false
        foreach ($pattern in $EXCLUSIONS) {
            if ($item.Name -like $pattern -or $relativePath -like "*\$pattern\*" -or $relativePath -like "*/$pattern/*") {
                $excluded = $true
                break
            }
        }
        
        if (-not $excluded) {
            if ($item.PSIsContainer) {
                New-Item -ItemType Directory -Path $destPath -Force | Out-Null
            } else {
                $parentDir = Split-Path $destPath -Parent
                if (-not (Test-Path $parentDir)) {
                    New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
                }
                Copy-Item -Path $item.FullName -Destination $destPath -Force
            }
        }
    }
}

# ─────────────────────────────────────────────────────────────
# Copie des dossiers de code source
# ─────────────────────────────────────────────────────────────
Write-Host ">> Copie des dossiers source..." -ForegroundColor Green
foreach ($dossier in $DOSSIERS_A_COPIER) {
    $src = Join-Path $Source $dossier
    $dst = Join-Path $Dest $dossier
    Write-Host "   $dossier/" -NoNewline
    Copy-WithExclusions -Src $src -Dst $dst
    Write-Host " ✓" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# Copie des fichiers racine
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ">> Copie des fichiers racine..." -ForegroundColor Green
foreach ($fichier in $FICHIERS_A_COPIER) {
    $src = Join-Path $Source $fichier
    $dst = Join-Path $Dest $fichier
    if (Test-Path $src) {
        # Créer les sous-dossiers si nécessaire
        $parentDir = Split-Path $dst -Parent
        if (-not (Test-Path $parentDir)) {
            New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        }
        Copy-Item -Path $src -Destination $dst -Force
        Write-Host "   $fichier ✓" -ForegroundColor Green
    } else {
        Write-Host "   $fichier [absent dans la source]" -ForegroundColor Yellow
    }
}

# ─────────────────────────────────────────────────────────────
# Copie de docs/ARCHITECTURE.md uniquement (pas les audits perso)
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ">> Copie de la documentation publique..." -ForegroundColor Green
$docsDestDir = Join-Path $Dest "docs"
New-Item -ItemType Directory -Path $docsDestDir -Force | Out-Null

$archSrc = Join-Path $Source "docs\ARCHITECTURE.md"
if (Test-Path $archSrc) {
    Copy-Item -Path $archSrc -Destination (Join-Path $docsDestDir "ARCHITECTURE.md") -Force
    Write-Host "   docs/ARCHITECTURE.md ✓" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────
# Vérification de sécurité — scanner les secrets potentiels
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ">> Scan de sécurité (recherche de secrets)..." -ForegroundColor Yellow

$PATTERNS_SECRETS = @(
    'sk-[a-zA-Z0-9]{20,}',    # Clés OpenAI/DeepSeek style
    'AIza[0-9A-Za-z-_]{35}',  # Clés Google API
    'ghp_[a-zA-Z0-9]{36}',    # GitHub tokens
    '192\.168\.\d+\.\d+',     # IPs locales hardcodées
    'Bearer [a-zA-Z0-9]{20,}' # Tokens Bearer
)

$secretsFound = $false
Get-ChildItem -Path $Dest -Recurse -Include "*.py","*.json","*.yaml","*.yml","*.toml","*.md" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
    if ($content) {
        foreach ($pattern in $PATTERNS_SECRETS) {
            if ($content -match $pattern) {
                Write-Host "   ⚠️  ALERTE SECRET dans : $($_.FullName)" -ForegroundColor Red
                Write-Host "      Pattern détecté : $pattern" -ForegroundColor Red
                $secretsFound = $true
            }
        }
    }
}

if (-not $secretsFound) {
    Write-Host "   ✅ Aucun secret détecté dans les fichiers copiés." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "   ⛔ DES SECRETS ONT ÉTÉ DÉTECTÉS — NE PAS PUSHER avant correction !" -ForegroundColor Red
}

# ─────────────────────────────────────────────────────────────
# Résumé
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Export terminé ===" -ForegroundColor Cyan
$fileCount = (Get-ChildItem -Path $Dest -Recurse -File | Measure-Object).Count
$sizeKB = [math]::Round((Get-ChildItem -Path $Dest -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1KB)
Write-Host "   Fichiers copiés : $fileCount"
Write-Host "   Taille totale  : $sizeKB KB"
Write-Host ""
Write-Host "Prochaines étapes :" -ForegroundColor Cyan
Write-Host "  1. Vérifier le scan de sécurité ci-dessus"
Write-Host "  2. cd H:\vromvrom-engine-oss"
Write-Host "  3. git init && git remote add origin <URL_DU_REPO_GITHUB>"
Write-Host "  4. git add -p   (revue patch par patch)"
Write-Host "  5. git commit -m 'feat: initial OSS release — moteur multi-agents V12'"
Write-Host "  6. git push -u origin main"
