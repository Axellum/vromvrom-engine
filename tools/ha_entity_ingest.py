"""
ha_entity_ingest.py — Ingestion automatique des entités domotiques dans le graphe de connaissances.

Ce script :
1. Se connecte à l'API REST locale de Home Assistant (HASS_URL + HASS_TOKEN).
2. Récupère tous les états des entités actives.
3. Filtre les entités du M5Stack Tab5 V2 et de l'environnement physique.
4. Peuple graph_entities et graph_relations dans memory.db.
5. Politique TLS centralisée (core.ha_tls) : vérifiée par défaut, opt-out explicite
   via HA_VERIFY_TLS=false ou CA dédiée HA_CA_BUNDLE pour un certificat auto-signé.

Auteur : Antigravity IDE
Créé le : 2026-06-04
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from memory.memory_db import MemoryDB

# Configurer le logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ha_entity_ingest")


async def ingest_ha_entities():
    """Récupère les entités de Home Assistant et les insère dans le graphe SQLite."""
    ha_token = os.environ.get("HASS_TOKEN")
    ha_url = os.environ.get("HASS_URL", "http://${HA_HOST:-192.168.1.x}:8123")
    
    if not ha_token:
        logger.error("HASS_TOKEN n'est pas configuré dans l'environnement / .env. Abandon.")
        return False
        
    logger.info(f"Connexion à Home Assistant sur : {ha_url} ...")
    
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    
    import aiohttp
    
    # 1. Requêter HA
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ha_url}/api/states",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ha_ssl_context(),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Erreur HTTP HA: {resp.status}")
                    return False
                    
                entities = await resp.json()
                logger.info(f"Récupéré {len(entities)} entités de Home Assistant.")
    except Exception as e:
        logger.error(f"Erreur de connexion à Home Assistant : {e}")
        return False
        
    # 2. Filtrer les entités pertinentes pour le Tab5 V2
    # Cible : les entités contenant "tab5" ou les entités critiques validées
    tab5_entities = []
    for entity in entities:
        entity_id = entity.get("entity_id", "")
        if "tab5" in entity_id.lower() or entity_id in (
            "switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active",
            "sensor.m5stack_tab5_home_assistant_hmi_tab5_core_temp",
            "media_player.m5stack_tab5_home_assistant_hmi_tab5_media_player"
        ):
            tab5_entities.append(entity)
            
    logger.info(f"Trouvé {len(tab5_entities)} entités matérielles/logiques liées au Tab5 V2.")
    
    if not tab5_entities:
        logger.warning("Aucune entité Tab5 V2 trouvée. Ingestion annulée.")
        return False
        
    # 3. Initialiser MemoryDB
    db = MemoryDB.get_instance()
    
    # 4. Insérer l'entité matérielle principale Tab5 V2
    await db.upsert_graph_entity_async(
        name="m5stack_tab5_v2",
        entity_type="hardware",
        observations=[
            "Tablette physique domotique M5Stack Tab5 V2",
            "ESP32-S3 (Xtensa, PSRAM 8MB, Flash 16MB)",
            f"Dernière synchronisation : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ]
    )
    
    # 5. Insérer chaque entité HA et créer la relation
    count = 0
    for entity in tab5_entities:
        entity_id = entity["entity_id"]
        state = entity.get("state", "unknown")
        attributes = entity.get("attributes", {})
        friendly_name = attributes.get("friendly_name", entity_id)
        last_changed = entity.get("last_changed", "")
        
        observations = [
            f"État actuel : {state}",
            f"Nom d'affichage : {friendly_name}",
            f"Dernière modification : {last_changed}"
        ]
        
        # Ajouter quelques attributs utiles s'ils existent
        if "unit_of_measurement" in attributes:
            observations.append(f"Unité : {attributes['unit_of_measurement']}")
            
        # Ingestion de l'entité graphe
        await db.upsert_graph_entity_async(
            name=entity_id,
            entity_type="HA_Entity",
            observations=observations
        )
        
        # Ingestion de la relation : m5stack_tab5_v2 -> has_entity -> entity_id
        db.upsert_graph_relation(
            from_entity="m5stack_tab5_v2",
            to_entity=entity_id,
            relation_type="has_entity"
        )
        
        count += 1
        logger.info(f"  Ingéré : {entity_id} (état: {state})")
        
    logger.info(f"Succès : {count} entités et relations ingérées avec succès dans memory.db.")
    return True


if __name__ == "__main__":
    asyncio.run(ingest_ha_entities())
