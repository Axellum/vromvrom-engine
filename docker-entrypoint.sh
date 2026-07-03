#!/bin/sh
set -e

echo "⚙️ Initialisation de vromvrom-engine..."

# 1. Création des configurations par défaut si manquantes
if [ ! -f config.json ]; then
    echo "Création de config.json depuis config.example.json..."
    cp config.example.json config.json
fi

if [ ! -f .env ]; then
    echo "Création de .env depuis .env.example..."
    cp .env.example .env
fi

# 2. Initialisation des bases de données vides (pour éviter les crashs Docker)
if [ ! -f memory.db ]; then
    echo "Initialisation de memory.db..."
    touch memory.db
fi

if [ ! -f models_registry.db ]; then
    echo "Initialisation de models_registry.db..."
    touch models_registry.db
fi

# 3. Lancement du serveur passé en argument (CMD dans le Dockerfile)
echo "🚀 Démarrage du moteur..."
exec "$@"
