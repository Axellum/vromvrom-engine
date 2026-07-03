#!/bin/bash
# install_worker.sh — Script d'installation pour HA OS (Alpine Linux)
# Copier-coller ce script LIGNE PAR LIGNE dans le terminal web HA

echo "=== Installation du Worker Moteur V6 ==="

# 1. Installer pip si absent
if ! command -v pip3 &> /dev/null; then
    echo "[1] Installation de pip3..."
    apk add --no-cache py3-pip python3-dev
else
    echo "[1] pip3 deja installe"
fi

# 2. Installer les dependances Python
echo "[2] Installation des dependances..."
pip3 install --break-system-packages fastapi uvicorn aiohttp python-dotenv 2>/dev/null || pip3 install fastapi uvicorn aiohttp python-dotenv

# 3. Tester le worker
echo "[3] Test du worker..."
cd /config/moteur-worker
python3 -c "from fastapi import FastAPI; print('[OK] FastAPI installe')"

# 4. Lancer le worker
echo "[4] Lancement du worker..."
nohup python3 /config/moteur-worker/worker_standalone.py --name worker-freebox --port 8780 > /config/moteur-worker/worker.log 2>&1 &
sleep 2

# 5. Verifier
if curl -s http://localhost:8780/health > /dev/null 2>&1; then
    echo "=== [OK] Worker operationnel sur le port 8780 ==="
    curl -s http://localhost:8780/status
else
    echo "=== [ERREUR] Le worker ne repond pas ==="
    cat /config/moteur-worker/worker.log
fi
