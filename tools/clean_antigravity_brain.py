"""
tools/clean_antigravity_brain.py — Nettoyage et Archivage du dossier Antigravity Brain (Lever 3).

Appelle d'abord `extract_conversations.py` pour sauvegarder l'historique dans un jsonl 
(afin que la méta-analyse puisse les lire), puis archive les dossiers de conversation vieux
de plus de 14 jours dans un ZIP pour alléger l'IDE tout en gardant une sauvegarde.

Usage :
  python tools/clean_antigravity_brain.py [--dry-run]
"""
import argparse
import os
import shutil
import sys
import time
import zipfile
import subprocess
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
BRAIN_DIR = HOME / ".gemini" / "antigravity-ide" / "brain"
ARCHIVE_DIR = HOME / ".gemini" / "antigravity-ide" / "brain_archive"
RETENTION_DAYS = 14

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Ne rien archiver/supprimer.")
    args = parser.parse_args()

    print(f"=== Antigravity Brain Archiver (Rétention: {RETENTION_DAYS} jours) ===")

    # 1. Extraction (sauvegarde JSONL)
    print("\n[1/3] Extraction des conversations via extract_conversations.py...")
    extract_script = Path(__file__).parent / "extract_conversations.py"
    out_file = Path(__file__).parent.parent / "corpus_antigravity_hebdo.jsonl"
    
    if extract_script.exists():
        cmd = [sys.executable, str(extract_script), "--source", "antigravity", "--out", str(out_file)]
        if args.dry_run:
            print(f"(Dry Run) Exécuterait : {' '.join(cmd)}")
        else:
            subprocess.run(cmd, check=False)
            print(f"-> Extraction terminée : {out_file}")
    else:
        print(f"-> Attention : {extract_script} introuvable, on saute l'extraction.")

    # 2. Scanne du dossier Brain
    print("\n[2/3] Analyse du dossier Brain...")
    if not BRAIN_DIR.exists():
        print(f"Le dossier {BRAIN_DIR} n'existe pas. Rien à faire.")
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    
    now = time.time()
    cutoff_time = now - (RETENTION_DAYS * 86400)
    
    folders_to_archive = []
    
    for item in BRAIN_DIR.iterdir():
        if item.is_dir() and len(item.name) >= 30 and "-" in item.name: # ID UUID-like
            # Utilise mtime du dossier
            mtime = item.stat().st_mtime
            if mtime < cutoff_time:
                folders_to_archive.append(item)

    print(f"-> {len(folders_to_archive)} dossier(s) de plus de {RETENTION_DAYS} jours trouvés.")

    # 3. Archivage ZIP et suppression
    print("\n[3/3] Archivage et Nettoyage...")
    total_size_freed = 0
    
    for folder in folders_to_archive:
        folder_size = sum(f.stat().st_size for f in folder.rglob('*') if f.is_file())
        zip_path = ARCHIVE_DIR / f"{folder.name}.zip"
        
        print(f"  - Archivage de {folder.name} ({folder_size / 1024 / 1024:.2f} MB)...")
        if not args.dry_run:
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, start=folder)
                            zf.write(file_path, arcname)
                
                # Vérification que le ZIP est OK avant suppression
                if zip_path.exists() and zip_path.stat().st_size > 0:
                    shutil.rmtree(folder)
                    total_size_freed += folder_size
                else:
                    print(f"    ! Erreur: le ZIP {zip_path.name} est vide ou absent. Suppression annulée.")
            except Exception as e:
                print(f"    ! Erreur lors de l'archivage de {folder.name}: {e}")
        else:
            total_size_freed += folder_size

    if args.dry_run:
        print(f"\n=== Bilan (Dry Run) ===")
        print(f"Espace qui serait libéré dans le brain actif : {total_size_freed / 1024 / 1024:.2f} MB")
    else:
        print(f"\n=== Bilan ===")
        print(f"Espace libéré dans le brain actif : {total_size_freed / 1024 / 1024:.2f} MB")
        print(f"Archive stockée dans : {ARCHIVE_DIR}")

if __name__ == "__main__":
    main()
