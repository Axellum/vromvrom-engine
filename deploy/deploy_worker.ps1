<#
.SYNOPSIS
    Deploie le Worker Standalone sur la Freebox VM via SSH/SCP.

.DESCRIPTION
    Ce script copie les fichiers necessaires sur la VM Freebox,
    installe les dependances Python, et configure le service systemd.

.EXAMPLE
    .\deploy_worker.ps1
    .\deploy_worker.ps1 -FreeboxIP "192.168.1.x" -SSHPort 22222
#>

param(
    [string]$FreeboxIP = "192.168.1.x",
    [int]$SSHPort = 22,
    [string]$SSHUser = "axel",
    [string]$RemoteDir = "/config/moteur-worker",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $DeployDir

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Moteur V6 -- Deploiement Worker Freebox" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  IP Freebox : $FreeboxIP" -ForegroundColor White
Write-Host "  Port SSH   : $SSHPort" -ForegroundColor White
Write-Host "  User       : $SSHUser" -ForegroundColor White
Write-Host "  Remote Dir : $RemoteDir" -ForegroundColor White
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# Fichiers a deployer
$FilesToDeploy = @(
    (Join-Path $DeployDir "worker_standalone.py"),
    (Join-Path $DeployDir ".env"),
    (Join-Path $DeployDir "moteur-worker.service")
)

# Verification des fichiers
Write-Host "[1/5] Verification des fichiers source..." -ForegroundColor Yellow
foreach ($file in $FilesToDeploy) {
    if (-not (Test-Path $file)) {
        Write-Host "  [ERREUR] Fichier manquant : $file" -ForegroundColor Red
        exit 1
    }
    $size = (Get-Item $file).Length
    $leaf = Split-Path -Leaf $file
    Write-Host "  [OK] $leaf ($size bytes)" -ForegroundColor Green
}

# Test de connectivite SSH
Write-Host ""
Write-Host "[2/5] Test de connectivite SSH..." -ForegroundColor Yellow
$sshOK = $false
try {
    $testResult = & ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -p $SSHPort "${SSHUser}@${FreeboxIP}" "echo OK" 2>&1
    if ($testResult -match "OK") {
        Write-Host "  [OK] Connexion SSH etablie" -ForegroundColor Green
        $sshOK = $true
    } else {
        throw "Reponse inattendue : $testResult"
    }
} catch {
    Write-Host "  [ERREUR] Impossible de se connecter en SSH a ${FreeboxIP}:${SSHPort}" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Verifiez que :" -ForegroundColor Yellow
    Write-Host "    1. SSH est active sur la VM Freebox" -ForegroundColor White
    Write-Host "    2. L'IP $FreeboxIP est correcte" -ForegroundColor White
    Write-Host "    3. Le port SSH est $SSHPort (essayez 22 ou 22222)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Pour HA OS, activez SSH via :" -ForegroundColor Yellow
    Write-Host "    Parametres > Modules > SSH and Web Terminal > Demarrer" -ForegroundColor White
    Write-Host ""

    $continue = Read-Host "Afficher les instructions de copie manuelle ? (o/n)"
    if ($continue -eq "o") {
        Write-Host ""
        Write-Host "--- Instructions de copie manuelle ---" -ForegroundColor Cyan
        Write-Host "Copiez ces fichiers dans $RemoteDir sur la Freebox :" -ForegroundColor White
        foreach ($file in $FilesToDeploy) {
            Write-Host "   - $(Split-Path -Leaf $file)" -ForegroundColor White
        }
        Write-Host ""
        Write-Host "Puis executez sur la Freebox :" -ForegroundColor Cyan
        Write-Host "   mkdir -p $RemoteDir" -ForegroundColor White
        Write-Host "   pip3 install fastapi uvicorn aiohttp python-dotenv" -ForegroundColor White
        Write-Host "   cp moteur-worker.service /etc/systemd/system/" -ForegroundColor White
        Write-Host "   systemctl daemon-reload" -ForegroundColor White
        Write-Host "   systemctl enable --now moteur-worker" -ForegroundColor White
    }
    exit 0
}

# Creation du repertoire distant + copie des fichiers
Write-Host ""
Write-Host "[3/5] Copie des fichiers vers la Freebox..." -ForegroundColor Yellow

& ssh -o StrictHostKeyChecking=no -p $SSHPort "${SSHUser}@${FreeboxIP}" "mkdir -p $RemoteDir"

foreach ($file in $FilesToDeploy) {
    $fileName = Split-Path -Leaf $file
    Write-Host "  Envoi de $fileName..." -ForegroundColor White
    & scp -o StrictHostKeyChecking=no -P $SSHPort "$file" "${SSHUser}@${FreeboxIP}:${RemoteDir}/$fileName"
}
Write-Host "  [OK] Fichiers copies" -ForegroundColor Green

# Installation des dependances
if (-not $SkipInstall) {
    Write-Host ""
    Write-Host "[4/5] Installation des dependances Python..." -ForegroundColor Yellow
    try {
        & ssh -o StrictHostKeyChecking=no -p $SSHPort "${SSHUser}@${FreeboxIP}" "pip3 install --quiet fastapi uvicorn aiohttp python-dotenv 2>&1"
        Write-Host "  [OK] Dependances installees" -ForegroundColor Green
    } catch {
        Write-Host "  [WARN] pip3 non disponible ou erreur -- installez manuellement" -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "[4/5] Installation sautee (--SkipInstall)" -ForegroundColor DarkGray
}

# Configuration du service systemd
Write-Host ""
Write-Host "[5/5] Configuration du service systemd..." -ForegroundColor Yellow

$serviceCmd = "cp $RemoteDir/moteur-worker.service /etc/systemd/system/moteur-worker.service && systemctl daemon-reload && systemctl enable moteur-worker && systemctl restart moteur-worker && echo SERVICE_OK"

try {
    $result = & ssh -o StrictHostKeyChecking=no -p $SSHPort "${SSHUser}@${FreeboxIP}" $serviceCmd 2>&1
    if ("$result" -match "SERVICE_OK") {
        Write-Host "  [OK] Service systemd configure et demarre" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] systemd non disponible (HA OS ?) -- lancez manuellement :" -ForegroundColor Yellow
        Write-Host "     python3 $RemoteDir/worker_standalone.py --name worker-freebox --port 8780" -ForegroundColor White
    }
} catch {
    Write-Host "  [WARN] Erreur systemd -- lancez manuellement :" -ForegroundColor Yellow
    Write-Host "     python3 $RemoteDir/worker_standalone.py --name worker-freebox --port 8780" -ForegroundColor White
}

# Verification du deploiement
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Verification du worker..." -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Start-Sleep -Seconds 3

try {
    $healthCheck = Invoke-RestMethod -Uri "http://${FreeboxIP}:8780/health" -TimeoutSec 5
    Write-Host ""
    Write-Host "  [OK] Worker '$($healthCheck.worker)' operationnel !" -ForegroundColor Green
    Write-Host "     Status : $($healthCheck.status)" -ForegroundColor White
    Write-Host "     URL    : http://${FreeboxIP}:8780" -ForegroundColor White
    Write-Host "     Docs   : http://${FreeboxIP}:8780/docs" -ForegroundColor White
} catch {
    Write-Host ""
    Write-Host "  [ATTENTE] Le worker ne repond pas encore -- verifiez les logs :" -ForegroundColor Yellow
    Write-Host "     ssh -p $SSHPort ${SSHUser}@${FreeboxIP} 'journalctl -u moteur-worker -f'" -ForegroundColor White
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  Deploiement termine !" -ForegroundColor Green
Write-Host "  Le moteur utilisera automatiquement le worker" -ForegroundColor Green
Write-Host "  Freebox via workers.json." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
