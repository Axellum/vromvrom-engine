#!/bin/ash
if [ -d "/homeassistant/moteur-master" ]; then
    cd /homeassistant/moteur-master
    LOG_PATH="/homeassistant/moteur-master/moteur.log"
else
    cd /config/moteur-master
    LOG_PATH="/config/moteur-master/moteur.log"
fi

# Tuer toute instance précédente s'il y en a une sur le port 8000
pkill -9 -f "gui_server.py" || true
# Lancement du serveur FastAPI en tâche de fond sans reload (stable pour la VM)
nohup python3 -u gui_server.py --host 0.0.0.0 --port 8000 > "$LOG_PATH" 2>&1 &
echo "Moteur d'Agents démarré en tâche de fond sur le port 8000."
