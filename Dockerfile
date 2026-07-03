FROM python:3.11-slim

# Metadatas
LABEL maintainer="Axellum"
LABEL description="vromvrom-engine: Async multi-agent LLM orchestrator"

# Eviter l'ecriture de fichiers .pyc et forcer l'output direct sur le terminal
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installation des dépendances système nécessaires
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copie des requirements en premier pour utiliser le cache Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du code source
COPY . .

# Exposition du port FastAPI
EXPOSE 8000

# Lancement du serveur
CMD ["python", "gui_server.py"]
