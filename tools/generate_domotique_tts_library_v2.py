#!/usr/bin/env python3
"""
tools/generate_domotique_tts_library_v2.py — Bibliothèque audio domotique (v2).

Améliorations vs la v1 (de Gemini) :
  - Liste de phrases ÉLARGIE et curée (~90 phrases, + de catégories, bugs FR corrigés).
  - Voix MODERNES : Chirp3-HD (les plus naturelles) avec repli Neural2/Studio.
  - SSML (pauses/prosodie) pour les voix qui le supportent (Neural2/Studio).
    NB : les voix Chirp3-HD ignorent le SSML → on leur envoie du texte brut.
  - Mode A/B : génère la MÊME phrase sur plusieurs voix pour que tu choisisses à l'oreille.
  - Idempotent (ne régénère pas un fichier existant), sort dans un dossier DISTINCT
    de la v1 (ne clobbe pas le cache de Gemini).

Usage :
  python tools/generate_domotique_tts_library_v2.py --samples      # échantillons A/B à juger
  python tools/generate_domotique_tts_library_v2.py --voice fr-FR-Chirp3-HD-Achernar  # génère tout
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass

from tools.cloud_tts import CloudTTSProvider

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("tts_v2")

DEST_DEFAULT = Path(r"E:\AuxFilsDesIdees\00ProjetTab\Tab5\tts_library_v2")

# Voix Chirp3 = pas de SSML. On le détecte sur le nom.
def _supports_ssml(voice_name: str) -> bool:
    return "Chirp" not in voice_name  # Neural2/Studio/Wavenet OK ; Chirp/Chirp3 non.


def _wrap_ssml(text: str) -> str:
    """SSML léger : petites pauses après ponctuation forte pour un débit naturel."""
    t = (text.replace(". ", '. <break time="350ms"/> ')
              .replace("? ", '? <break time="350ms"/> ')
              .replace("! ", '! <break time="350ms"/> ')
              .replace(", ", ', <break time="120ms"/> '))
    return f"<speak>{t}</speak>"


# ── Bibliothèque de phrases (curée, ~90) ─────────────────────────────────────
PHRASES_DOMOTIQUE = {
    "salutations": {
        "bonjour_axel": "Bonjour Axel, bienvenue à la maison. J'espère que vous passez une bonne journée.",
        "bonsoir_axel": "Bonsoir Axel, ravi de vous revoir. Le système domotique est opérationnel.",
        "bonne_nuit": "Bonne nuit Axel. La maison passe en mode sommeil. À demain.",
        "depart_maison": "Bonne journée. La maison passe en mode absent et sécurise les accès.",
        "retour_maison": "Bon retour Axel. Je rétablis vos réglages habituels.",
    },
    "eclairage": {
        "lumieres_on": "J'allume les lumières.",
        "lumieres_off": "J'éteins les lumières.",
        "lumieres_salon_on": "Éclairage du salon allumé.",
        "lumieres_salon_off": "Éclairage du salon éteint.",
        "ambiance_soiree": "Activation de l'ambiance soirée. Lumières tamisées.",
        "tout_eteint": "Toutes les lumières de la maison sont maintenant éteintes.",
    },
    "volets_et_serre": {
        "volet_ouverture": "Ouverture du volet de la serre en cours.",
        "volet_fermeture": "Fermeture du volet de la serre en cours.",
        "volet_ouvert_complet": "Le volet de la serre est maintenant complètement ouvert.",
        "volet_ferme_complet": "Le volet de la serre est maintenant complètement fermé.",
        "volet_bloque": "Attention, le volet de la serre semble bloqué. Veuillez vérifier manuellement.",
        "alerte_vent_serre": "Alerte vent fort détectée. Sécurisation du volet de la serre en cours.",
        "volets_maison_fermes": "Les volets de la maison sont fermés pour la nuit.",
    },
    "climatisation": {
        "clim_on": "La climatisation du salon est allumée.",
        "clim_off": "La climatisation du salon est éteinte.",
        "clim_temp_consigne": "La température de consigne a été mise à jour.",
        "clim_mode_auto": "La climatisation est configurée en mode automatique.",
        "clim_eco": "Passage de la climatisation en mode économique.",
    },
    "chauffage": {
        "chauffage_on": "Chauffage activé. La température va monter progressivement.",
        "chauffage_off": "Chauffage coupé.",
        "consigne_confort": "Passage en mode confort. Température de consigne : vingt et un degrés.",
        "consigne_nuit": "Passage en mode nuit. Réduction de la température.",
    },
    "plantes": {
        "plantes_arrosage_requis": "Attention, certaines plantes ont besoin d'eau. Veuillez vérifier les capteurs d'humidité.",
        "plantes_arrosage_fait": "L'arrosage des plantes a été effectué avec succès.",
        "humidite_sol_faible": "L'humidité du sol est trop basse dans le pot numéro un.",
        "reservoir_vide": "Le réservoir d'eau d'arrosage est vide. Pensez à le remplir.",
    },
    "securite": {
        "alarme_armee": "Système d'alarme armé. La maison est sous surveillance.",
        "alarme_desarmee": "Système d'alarme désarmé. Bon retour.",
        "porte_ouverte": "Attention, une porte est restée ouverte.",
        "mouvement_detecte": "Mouvement détecté à l'extérieur.",
        "fenetre_ouverte_pluie": "Une fenêtre est ouverte alors que de la pluie est prévue.",
        "fuite_eau": "Alerte. Une fuite d'eau a été détectée. Coupure de l'arrivée d'eau recommandée.",
        "fumee_detectee": "Alerte fumée détectée. Vérifiez immédiatement la zone concernée.",
    },
    "presence_energie": {
        "personne_a_la_maison": "Il n'y a plus personne à la maison. Passage en mode économie d'énergie.",
        "conso_elevee": "La consommation électrique est anormalement élevée en ce moment.",
        "batterie_domotique_faible": "La batterie de secours du système domotique est faible.",
        "production_solaire_ok": "La production solaire couvre actuellement la consommation de la maison.",
    },
    "alertes_meteo": {
        "alerte_orange": "Attention, une alerte orange de Météo France est en cours pour le département des Landes. Soyez prudent.",
        "alerte_rouge": "Alerte rouge de Météo France en cours. Veuillez restreindre vos déplacements et rester vigilant.",
        "pluie_imminente": "Attention, de la pluie est attendue dans les prochaines minutes à Saint-Vincent-de-Tyrosse.",
        "pluie_1h": "Alerte pluie dans l'heure en cours.",
        "gel_nuit": "Risque de gel cette nuit. Pensez à protéger les plantes sensibles.",
        "canicule": "Épisode de forte chaleur attendu aujourd'hui. Pensez à fermer les volets.",
        "vent_violent": "Rafales de vent violentes annoncées. Sécurisation des équipements extérieurs conseillée.",
    },
    "systeme": {
        "tab5_batterie_faible": "Attention, la batterie de la tablette Tab 5 est faible.",
        "tab5_reboot": "Redémarrage du système de la tablette en cours.",
        "moteur_ready": "Le tab5-engine cognitifs est initialisé et prêt à recevoir vos commandes.",
        "moteur_error": "Une erreur s'est produite lors de l'exécution de la requête. Veuillez réessayer.",
        "connexion_perdue": "Connexion avec le serveur Home Assistant interrompue.",
        "connexion_retrouvee": "Connexion avec le serveur Home Assistant rétablie.",
        "mise_a_jour_dispo": "Une mise à jour est disponible pour le système domotique.",
        "sauvegarde_ok": "Sauvegarde du système effectuée avec succès.",
    },
    "confirmations": {
        "ok_fait": "C'est fait.",
        "bien_recu": "Bien reçu.",
        "commande_executee": "Commande exécutée.",
        "non_compris": "Je n'ai pas compris la demande. Pouvez-vous reformuler ?",
        "patientez": "Un instant, je traite votre demande.",
    },
}


def _flatten():
    for cat, phrases in PHRASES_DOMOTIQUE.items():
        for key, text in phrases.items():
            yield cat, key, text


def _pick_voices(tts: CloudTTSProvider):
    """Sélectionne des voix réelles pour l'A/B : Chirp3-HD F/M, Studio, Neural2."""
    vs = tts.list_voices("fr-FR")
    by = {v["name"]: v.get("gender", "") for v in vs}
    names = list(by)
    def first(pred):
        return next((n for n in names if pred(n)), None)
    chirp_f = first(lambda n: "Chirp3-HD" in n and by[n] == "FEMALE")
    chirp_m = first(lambda n: "Chirp3-HD" in n and by[n] == "MALE")
    studio = first(lambda n: "Studio" in n) or "fr-FR-Studio-A"
    return [
        ("neural2A_brut", "fr-FR-Neural2-A", False),
        ("neural2A_ssml", "fr-FR-Neural2-A", True),
        ("studio_ssml", studio, True),
        ("chirp3hd_femme", chirp_f or "fr-FR-Chirp3-HD-Achernar", False),
        ("chirp3hd_homme", chirp_m or "fr-FR-Chirp3-HD-Algenib", False),
    ]


def run_samples(tts: CloudTTSProvider, dest: Path):
    """Génère la même phrase sur plusieurs voix pour comparaison."""
    out = dest / "_samples_AB"
    out.mkdir(parents=True, exist_ok=True)
    phrase = ("Attention, une alerte orange de Météo France est en cours pour les Landes. "
              "De la pluie est attendue dans l'heure. Soyez prudent.")
    print(f"=== Échantillons A/B -> {out} ===")
    for label, voice, use_ssml in _pick_voices(tts):
        ssml_ok = use_ssml and _supports_ssml(voice)
        text = _wrap_ssml(phrase) if ssml_ok else phrase
        path = out / f"{label}.mp3"
        r = tts.synthesize(text=text, output_path=str(path), language="fr-FR",
                           voice_name=voice, ssml=ssml_ok,
                           speaking_rate=1.0, audio_encoding="MP3")
        tag = "SSML" if ssml_ok else "brut"
        print(f"  {'OK ' if r else 'ÉCHEC'} {label:16s} voix={voice:28s} ({tag})")
    print(f"\nÉcoute les 5 fichiers dans {out} et dis-moi lequel tu préfères.")


def run_library(tts: CloudTTSProvider, dest: Path, voice: str, rate: float):
    """Génère toute la bibliothèque avec la voix choisie."""
    ssml_ok = _supports_ssml(voice)
    ok = skip = fail = 0
    print(f"=== Bibliothèque -> {dest}  (voix={voice}, SSML={'oui' if ssml_ok else 'non'}) ===")
    for cat, key, text in _flatten():
        cat_dir = dest / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        path = cat_dir / f"{key}.mp3"
        if path.exists():
            skip += 1
            continue
        payload = _wrap_ssml(text) if ssml_ok else text
        r = tts.synthesize(text=payload, output_path=str(path), language="fr-FR",
                           voice_name=voice, ssml=ssml_ok,
                           speaking_rate=rate, audio_encoding="MP3")
        if r:
            ok += 1
        else:
            fail += 1
            logger.error(f"  échec : {cat}/{key}")
    total_chars = sum(len(t) for _, _, t in _flatten())
    print(f"\n=== Terminé : {ok} générés, {skip} déjà présents, {fail} échecs ===")
    print(f"    (~{total_chars} caractères ; Free Tier Neural2/Studio = 1M/mois)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="Génère les échantillons A/B.")
    ap.add_argument("--voice", default="fr-FR-Neural2-A", help="Voix pour la génération complète.")
    ap.add_argument("--rate", type=float, default=1.0, help="Débit de parole (0.25-4.0).")
    ap.add_argument("--dest", type=Path, default=DEST_DEFAULT)
    args = ap.parse_args()

    tts = CloudTTSProvider()
    if not tts.available:
        logger.error("Cloud TTS indisponible (CLOUD_API_KEY manquante dans .env).")
        return 1

    if args.samples:
        run_samples(tts, args.dest)
    else:
        run_library(tts, args.dest, args.voice, args.rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
