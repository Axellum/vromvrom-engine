#!/usr/bin/env python3
"""
Script de localisation ciblée des fichiers liés à la configuration audio du Tab5
(microphone i2s_audio, GPIO28, PDM).

Objectif : éviter le timeout (120 s) de la recherche grep/find exhaustive sur
tout le workspace, qui parcourait des répertoires volumineux (node_modules,
.git, __pycache__, multiples clones moteur_agents*).

Usage :
    python3 scripts/localiser_audio_tab5.py [racine]

Racine par défaut : /home/deck/dev_station
"""

import os
import sys

# Racine de recherche (par défaut : workspace dev_station)
RACINE = sys.argv[1] if len(sys.argv) > 1 else "/home/deck/dev_station"

# Répertoires à exclure (volumineux ou non pertinents)
EXCLUS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "esphome_venv",
    "chroma_db",
    "checkpoints",
    "memory",
    "logs",
    "reports",
    "_pre_codedeploy_1783842691",
    "_pre_codedeploy_20260722_185833",
    "_pre_codedeploy_20260722_190147",
    "_pre_codedeploy_20260722_190642",
    "_pre_codedeploy_20260724_055613",
    "_pre_codedeploy_20260725_065942",
    "_pre_codedeploy_t202_backup",
    "orphan_git_backup",
}

# Motifs audio du Tab5 à rechercher
MOTIFS = ("i2s_audio", "GPIO28", "PDM", "microphone")

# Extensions de fichiers pertinentes
EXTENSIONS = (".yaml", ".yml", ".md", ".py", ".json")


def est_exclu(chemin):
    """Vérifie si un chemin contient un répertoire exclu."""
    parties = chemin.split(os.sep)
    return any(p in EXCLUS for p in parties)


def localiser():
    """Parcourt la racine et liste les fichiers contenant les motifs audio."""
    resultats = []
    for racine, dossiers, fichiers in os.walk(RACINE):
        # Filtre les dossiers exclus en place (évite de descendre dedans)
        dossiers[:] = [d for d in dossiers if d not in EXCLUS]
        for fichier in fichiers:
            if not fichier.endswith(EXTENSIONS):
                continue
            chemin = os.path.join(racine, fichier)
            if est_exclu(chemin):
                continue
            try:
                with open(chemin, "r", encoding="utf-8", errors="ignore") as f:
                    contenu = f.read()
                if any(m in contenu for m in MOTIFS):
                    resultats.append(chemin)
            except (OSError, UnicodeDecodeError):
                continue
    return resultats


if __name__ == "__main__":
    print(f"Recherche des fichiers audio Tab5 sous : {RACINE}")
    print(f"Motifs : {', '.join(MOTIFS)}")
    print("-" * 60)
    fichiers = localiser()
    if fichiers:
        for f in sorted(fichiers):
            print(f)
        print("-" * 60)
        print(f"{len(fichiers)} fichier(s) trouvé(s).")
    else:
        print("Aucun fichier trouvé.")
