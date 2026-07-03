"""
populate_embeddings.py — Calcule et stocke les embeddings vectoriels
des fichiers de contexte 3-Layers dans memory.db via l'API Gemini.

Usage : python populate_embeddings.py [--force]

Prérequis :
  - Variable d'environnement GEMINI_API_KEY définie dans .env
  - memory.db déjà peuplée (via seed_memory_db.py)
"""

import os
import sys
import struct
import time
import logging

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Charger les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    load_dotenv(env_path)
except ImportError:
    pass  # dotenv non installé, les variables doivent être dans l'environnement

from memory.memory_db import MemoryDB
from memory.gemini_embedding_fn import GeminiEmbeddingFunction

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("populate_embeddings")

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except:
    pass

# Fichiers de contexte à indexer (les 3-Layers + leçons thématiques)
FILES_TO_EMBED = [
    r"e:\AuxFilsDesIdees\contexte_ia\01_Core\rules_global.md",
    r"e:\AuxFilsDesIdees\contexte_ia\01_Core\hardware_pc_ia_locale.md",
    r"e:\AuxFilsDesIdees\contexte_ia\01_Core\lecons_infra_windows.md",
    r"e:\AuxFilsDesIdees\contexte_ia\02_Hardware\rules_esphome.md",
    r"e:\AuxFilsDesIdees\contexte_ia\02_Hardware\02_MATERIEL_ET_ECRANS.md",
    r"e:\AuxFilsDesIdees\contexte_ia\02_Hardware\lecons_esphome_hardware.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\rules_home_assistant.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\rules_moteur_agents.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\01_SERVEUR_HA.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\03_LOGIQUE_ET_APIS.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\05_MOTEUR_AGENTS_PYTHON.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\lecons_moteur_agents.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\lecons_gcp_apis.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\lecons_hmi.md",
]

# Taille maximale d'un chunk (en caractères) pour l'indexation
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list:
    """
    Découpe un texte en chunks avec overlap pour la recherche sémantique.
    Découpe en priorité sur les sauts de ligne doubles (paragraphes).
    """
    # Découper par paragraphes d'abord
    paragraphs = text.split('\n\n')
    
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # Si le paragraphe seul dépasse la taille du chunk, le découper
        if len(para) > chunk_size:
            # Sauvegarder le chunk en cours
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            
            # Découper le paragraphe long en sous-chunks
            for i in range(0, len(para), chunk_size - overlap):
                sub = para[i:i + chunk_size]
                if sub.strip():
                    chunks.append(sub.strip())
        elif len(current_chunk) + len(para) + 2 > chunk_size:
            # Le chunk courant est plein, le sauvegarder
            chunks.append(current_chunk.strip())
            # Commencer un nouveau chunk avec un overlap
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
            current_chunk = overlap_text + "\n\n" + para
        else:
            # Ajouter au chunk courant
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
    
    # Dernier chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks


def embedding_to_bytes(embedding: list) -> bytes:
    """Convertit un vecteur d'embedding (liste de floats) en bytes pour le stockage SQLite."""
    return struct.pack(f'{len(embedding)}f', *embedding)


def bytes_to_embedding(data: bytes) -> list:
    """Convertit des bytes en vecteur d'embedding (liste de floats)."""
    n = len(data) // 4  # 4 bytes par float
    return list(struct.unpack(f'{n}f', data))


def main():
    force = "--force" in sys.argv
    
    # Vérification de la clé API
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERREUR: Variable GEMINI_API_KEY non definie dans .env")
        print("Definissez-la dans moteur_agents/.env")
        sys.exit(1)
    
    db = MemoryDB.get_instance()
    embed_fn = GeminiEmbeddingFunction(model="gemini-embedding-2", api_key=api_key)
    
    if not embed_fn.available:
        print("ERREUR: Fonction d'embedding non disponible")
        sys.exit(1)
    
    # Vérifier si déjà peuplé
    stats = db.get_stats()
    if stats["embeddings"] > 0 and not force:
        print(f"Embeddings deja peuples ({stats['embeddings']} chunks). "
              f"Utilisez --force pour re-indexer.")
        return
    
    print("=== POPULATE EMBEDDINGS ===\n")
    print(f"Modele: gemini-embedding-2 (dimension 3072)")
    print(f"Fichiers a indexer: {len(FILES_TO_EMBED)}")
    print(f"Taille des chunks: {CHUNK_SIZE} chars (overlap {CHUNK_OVERLAP})\n")
    
    total_chunks = 0
    total_files = 0
    
    for filepath in FILES_TO_EMBED:
        if not os.path.exists(filepath):
            print(f"  SKIP: {os.path.basename(filepath)} (fichier inexistant)")
            continue
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extraire le nom relatif pour l'identification
        rel_name = os.path.basename(filepath)
        
        # Découper en chunks
        chunks = chunk_text(content)
        
        if not chunks:
            print(f"  SKIP: {rel_name} (aucun contenu)")
            continue
        
        print(f"  {rel_name}: {len(chunks)} chunks...", end=" ", flush=True)
        
        try:
            # Appel batch à l'API Gemini (max 100 par appel)
            embeddings = embed_fn(chunks)
            
            # Stocker chaque chunk + embedding dans memory.db
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                emb_bytes = embedding_to_bytes(emb)
                db.store_embedding(
                    source_type="context_file",
                    source_id=rel_name,
                    chunk_text=chunk,
                    embedding=emb_bytes,
                    model_name="gemini-embedding-2",
                    dimension=3072,
                )
            
            total_chunks += len(chunks)
            total_files += 1
            print(f"OK ({len(embeddings)} vecteurs)")
            
            # Petite pause pour respecter les quotas API (15 RPM max sur embedding)
            time.sleep(4.5)
            
        except Exception as e:
            print(f"ERREUR: {e}")
            continue
    
    # Indexer aussi les faits de la base (titres + contenus courts)
    print(f"\n  Indexation des faits en base...", end=" ", flush=True)
    try:
        facts_texts = []
        facts_ids = []
        
        conn = db._get_conn()
        rows = conn.execute("SELECT id, title, content FROM facts").fetchall()
        conn.close()
        
        for row in rows:
            # Combiner titre + contenu pour un embedding plus riche
            combined = f"{row['title']}: {row['content'][:500]}"
            facts_texts.append(combined)
            facts_ids.append(str(row['id']))
        
        if facts_texts:
            # Batch par 15 (au lieu de 100 pour respecter la limite TPM du Free Tier)
            batch_size = 15
            for i in range(0, len(facts_texts), batch_size):
                batch_texts = facts_texts[i:i+batch_size]
                batch_ids = facts_ids[i:i+batch_size]
                
                embeddings = embed_fn(batch_texts)
                
                for text, emb, fid in zip(batch_texts, embeddings, batch_ids):
                    emb_bytes = embedding_to_bytes(emb)
                    db.store_embedding(
                        source_type="fact",
                        source_id=fid,
                        chunk_text=text,
                        embedding=emb_bytes,
                        model_name="gemini-embedding-2",
                        dimension=3072,
                    )
                
                total_chunks += len(batch_texts)
                time.sleep(4.5)
            
            print(f"OK ({len(facts_texts)} faits)")
    except Exception as e:
        print(f"ERREUR: {e}")
    
    # Metadata
    db.set_sync_metadata("last_embedding_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
    db.set_sync_metadata("embedding_model", "gemini-embedding-2")
    db.set_sync_metadata("embedding_dimension", "3072")
    
    # Stats finales
    print(f"\n--- Resultats ---")
    print(f"  Fichiers indexes : {total_files}/{len(FILES_TO_EMBED)}")
    print(f"  Total chunks     : {total_chunks}")
    print(f"  Base DB          : {db.get_stats()['db_size_kb']} Ko")
    print(f"\n=== POPULATE EMBEDDINGS TERMINE ===")


if __name__ == "__main__":
    main()
