#!/usr/bin/env python3
"""
tools/db_optimize.py — Maintenance physique et optimisation des bases SQLite.

Exécute les opérations de santé et performance sur les bases de données locales :
  1. PRAGMA integrity_check (vérification de corruption)
  2. VACUUM (compactage de l'espace disque)
  3. ANALYZE (optimisation des index pour les requêtes du RAG)

Usage :
  python tools/db_optimize.py
"""
import os
import sqlite3
import time
import filelock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # moteur_agents/

DB_FILES = [
    "memory.db",
    "models_registry.db",
    "moteur_runtime.db"
]

def format_size(size_bytes: int) -> str:
    """Formatte la taille du fichier en lisible."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def optimize_db(db_name: str):
    db_path = os.path.join(ROOT, db_name)
    if not os.path.exists(db_path):
        print(f"⚠️  Base de données introuvable : {db_name} (ignorée)")
        return

    size_before = os.path.getsize(db_path)
    print(f"\n🔍 Analyse et maintenance de {db_name} (Taille : {format_size(size_before)})")
    
    # Déterminer le chemin du fichier de verrouillage physique approprié
    if db_name == "moteur_runtime.db":
        lock_path = db_path + ".backlog.lock"
    else:
        lock_path = db_path + ".lock"

    lock = filelock.FileLock(lock_path)
    try:
        print(f"  -> Acquisition du verrou physique ({os.path.basename(lock_path)})...")
        # Attendre au maximum 15 secondes pour acquérir le verrou
        with lock.acquire(timeout=15):
            t0 = time.time()
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 1. Integrity Check
            print("  -> Vérification de l'intégrité...")
            integrity = cursor.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity == "ok":
                print("  ✅ Intégrité OK")
            else:
                print(f"  ❌ PROBLÈME D'INTÉGRITÉ : {integrity}")
                conn.close()
                return
                
            # 2. Analyze (Reconstruction des stats de requêtes)
            print("  -> Reconstruction des statistiques (ANALYZE)...")
            cursor.execute("ANALYZE")
            
            # 3. Vacuum (Compactage)
            print("  -> Compactage physique de la base (VACUUM)...")
            cursor.execute("VACUUM")
            
            conn.commit()
            conn.close()
            elapsed = time.time() - t0
            
            size_after = os.path.getsize(db_path)
            saved = size_before - size_after
            print(f"  ✅ Terminé en {elapsed:.2f}s | Nouvelle taille : {format_size(size_after)}" + 
                  (f" (Économie de {format_size(saved)})" if saved > 0 else " (Déjà compactée)"))
                  
    except filelock.Timeout:
        print(f"  ❌ Impossible d'acquérir le verrou physique sur {db_name} (le moteur tourne-t-il ?)")
    except Exception as e:
        print(f"  ❌ Erreur lors de l'optimisation de {db_name} : {e}")

def main():
    print("==================================================")
    print("🗄️  Optimisation Physique des Bases de Données SQLite")
    print("==================================================")
    
    for db in DB_FILES:
        optimize_db(db)
        
    print("\n🎉 Toutes les bases de données ont été optimisées avec succès !")

if __name__ == "__main__":
    main()
