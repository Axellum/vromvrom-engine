"""
sync_db_to_markdown.py — Synchronise les faits de memory.db vers les fichiers Markdown 3-Layers.

Permet de s'assurer que toutes les leçons apprises enregistrées dans la base SQLite SQLite
durant la session sont écrites dans les fichiers Markdown correspondants avant la fin de session,
garantissant la pérennité sous Git.

Usage : python sync_db_to_markdown.py
"""

import os
import re
import sys
import time
import logging

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.memory_db import MemoryDB

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("sync_db_to_markdown")

def sync_category(db: MemoryDB, category: str) -> int:
    md_path = db._get_lecon_md_path(category)
    if not md_path:
        logger.warning(f"Pas de chemin Markdown défini pour la catégorie : {category}")
        return 0
        
    # Créer le fichier s'il n'existe pas
    if not os.path.exists(md_path):
        try:
            os.makedirs(os.path.dirname(md_path), exist_ok=True)
            with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(f"# Leçons Apprises — Catégorie {category.upper()}\n\n")
            logger.info(f"Fichier Markdown créé : {os.path.basename(md_path)}")
        except Exception as e:
            logger.error(f"Impossible de créer le fichier {md_path} : {e}")
            return 0
            
    # Lire le contenu actuel du fichier MD pour extraire les titres existants
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
    except Exception as e:
        logger.error(f"Impossible de lire le fichier {md_path} : {e}")
        return 0
        
    # Extraire les titres existants de la forme "- **Titre**"
    existing_titles = set(re.findall(r'^- \*\*(.+?)\*\*', md_content, re.MULTILINE))
    
    # Charger les faits de cette catégorie depuis la base de données
    facts = db.get_facts_by_category(category)
    
    # Filtrer les faits de faible qualité pour ne pas polluer les Markdown
    # Garder les faits avec quality_score >= 0.5 OU NULL (rétrocompatibilité
    # avec les faits existants enregistrés avant l'ajout du scoring V9)
    filtered_facts = [
        f for f in facts
        if f.get("quality_score") is None or f.get("quality_score", 0.0) >= 0.5
    ]
    logger.info(
        f"Catégorie {category} : {len(facts)} faits en base, "
        f"{len(filtered_facts)} retenus (quality >= 0.5 ou non scorés)"
    )
    
    added_count = 0
    
    # Ouvrir le fichier en mode append si de nouveaux faits doivent être écrits
    new_entries = []
    for fact in filtered_facts:
        title = fact["title"].strip()
        # Si le fait n'est pas déjà présent dans le fichier Markdown
        if title not in existing_titles:
            content = fact["content"].strip()
            timestamp = time.strftime("%Y-%m-%d")
            
            meta_parts = [f"Synchronisé depuis la base de données le {timestamp}"]
            if fact.get("severity") and fact["severity"] != 'minor':
                meta_parts.append(f"severity: {fact['severity']}")
            if fact.get("commit_hash"):
                meta_parts.append(f"commit: `{fact['commit_hash']}`")
                
            entry = (
                f"\n- **{title}** : {content}\n"
                f"  - *{'; '.join(meta_parts)}*\n"
            )
            new_entries.append(entry)
            existing_titles.add(title)  # Éviter les doublons au sein du même run
            added_count += 1
            
    if new_entries:
        try:
            with open(md_path, 'a', encoding='utf-8', newline='\n') as f:
                f.write("".join(new_entries))
            logger.info(f"Catégorie {category:8s} : {added_count:2d} fait(s) synchronisé(s) vers {os.path.basename(md_path)}")
        except Exception as e:
            logger.error(f"Erreur d'écriture dans {md_path} : {e}")
            return 0
    else:
        logger.info(f"Catégorie {category:8s} : À jour (0 fait synchronisé)")
        
    return added_count

def main():
    print("=== SYNCHRONISATION DE LA MÉMOIRE SQLITE VERS MARKDOWN ===\n")
    
    db = MemoryDB.get_instance()
    categories = ["esphome", "moteur", "gcp", "hmi", "infra"]
    
    total_added = 0
    for cat in categories:
        total_added += sync_category(db, cat)
        
    print(f"\nTotal de faits synchronisés vers les fichiers Markdown : {total_added}")
    print("=== FIN DE SYNCHRONISATION ===")

if __name__ == "__main__":
    main()
