"""
tools/generate_domotique_tts_library.py — Génération en masse de phrases audio domotiques.

Ce script utilise CloudTTSProvider (Google Cloud TTS v1) pour pré-enregistrer 
une bibliothèque de phrases audio de haute qualité (voix fr-FR-Neural2).
Ces fichiers sont sauvegardés localement et peuvent ensuite être diffusés 
instantanément par le Tab5 ou Home Assistant sans latence réseau et à coût zéro.
"""

import os
import sys
import json
import logging
from pathlib import Path

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Charger les variables d'environnement
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
except ImportError:
    pass

from tools.cloud_tts import CloudTTSProvider

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("generate_domotique_tts")

# Bibliothèque de phrases domotiques par catégorie
PHRASES_DOMOTIQUE = {
    "salutations": {
        "bonjour_axel": "Bonjour Axel, bienvenue à la maison. J'espère que vous passez une bonne journée.",
        "bonsoir_axel": "Bonsoir Axel, ravi de vous revoir. Le système domotique est opérationnel.",
        "bonne_nuit": "Bonne nuit Axel. La maison passe en mode sommeil. À demain."
    },
    "serre_et_volet": {
        "volet_ouverture": "Ouverture du volet de la serre en cours.",
        "volet_fermeture": "Fermeture du volet de la serre en cours.",
        "volet_ouvert_complet": "Le volet de la serre est maintenant complètement ouvert.",
        "volet_ferme_complet": "Le volet de la serre est maintenant complètement fermé.",
        "volet_bloque": "Attention, le volet de la serre semble bloqué. Veuillez vérifier manuellement.",
        "alerte_vent_serre": "Alerte vent fort détectée. Sécurisation du volet de la serre en cours."
    },
    "climatisation": {
        "clim_on": "La climatisation du salon is allumée.",
        "clim_off": "La climatisation du salon est éteinte.",
        "clim_temp_consigne": "La température de consigne a été mise à jour.",
        "clim_mode_auto": "La climatisation est configurée en mode automatique."
    },
    "plantes": {
        "plantes_arrosage_requis": "Attention, certaines plantes ont besoin d'eau. Veuillez vérifier les capteurs d'humidité.",
        "plantes_arrosage_fait": "L'arrosage des plantes a été effectué avec succès.",
        "humidite_sol_faible": "L'humidité du sol est trop basse dans le pot numéro un."
    },
    "systeme": {
        "tab5_batterie_faible": "Attention, la batterie de la tablette Tab 5 est faible.",
        "tab5_reboot": "Redémarrage du système de la tablette en cours.",
        "moteur_ready": "Le tab5-engine cognitifs est initialisé et prêt à recevoir vos commandes.",
        "moteur_error": "Une erreur s'est produite lors de l'exécution de la requête. Veuillez réessayer.",
        "connexion_perdue": "Connexion avec le serveur Home Assistant interrompue.",
        "connexion_retrouvee": "Connexion avec le serveur Home Assistant rétablie."
    },
    "alertes_meteo": {
        "alerte_orange": "Attention, une alerte orange de météo France est en cours pour le département des Landes. Soyez prudent.",
        "alerte_rouge": "Alerte rouge de météo France en cours. Veuillez restreindre vos déplacements et rester vigilant.",
        "pluie_imminente": "Attention, de la pluie est attendue dans les prochaines minutes à Saint-Vincent-de-Tyrosse.",
        "pluie_1h": "Alerte pluie dans l'heure en cours."
    }
}

def main():
    dest_dir = Path(r"E:\AuxFilsDesIdees\00ProjetTab\Tab5\tts_library")
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialiser le provider
    tts = CloudTTSProvider()
    if not tts.available:
        logger.error("Cloud TTS n'est pas disponible (vérifiez CLOUD_API_KEY dans votre fichier .env).")
        sys.exit(1)
        
    logger.info("=== GÉNÉRATION DE LA BIBLIOTHÈQUE AUDIO DOMOTIQUE ===")
    logger.info(f"Dossier de destination : {dest_dir}")
    
    success_count = 0
    fail_count = 0
    
    # Parcourir les catégories et phrases
    for category, phrases in PHRASES_DOMOTIQUE.items():
        cat_dir = dest_dir / category
        cat_dir.mkdir(exist_ok=True)
        
        logger.info(f"\nCatégorie : {category.upper()}")
        
        for key, text in phrases.items():
            filename = f"{key}.mp3"
            filepath = cat_dir / filename
            
            logger.info(f"  Génération de '{key}' -> {filepath.name}...")
            
            # Appeler l'API Cloud TTS (voix fr-FR-Neural2-A par défaut)
            result = tts.synthesize(
                text=text,
                output_path=str(filepath),
                language="fr-FR",
                voice_name="fr-FR-Neural2-A", # Voix premium féminine
                audio_encoding="MP3"
            )
            
            if result:
                success_count += 1
            else:
                fail_count += 1
                logger.error(f"  ❌ Échec de génération pour '{key}'")
                
    logger.info("\n=== SYNTHÈSE TERMINÉE ===")
    logger.info(f"  Réussis : {success_count}")
    logger.info(f"  Échecs  : {fail_count}")
    logger.info(f"  Dossier final : {dest_dir}")

if __name__ == "__main__":
    main()
