"""
Tests unitaires pour le script de localisation des fichiers audio du Tab5.
Vérifie que la recherche ciblée fonctionne et ne timeout pas.
"""

import os
import sys
import tempfile

# Ajoute le dossier scripts au path pour importer le module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import localiser_audio_tab5 as mod


def test_est_exclu():
    """Vérifie la détection des répertoires exclus."""
    assert mod.est_exclu("/opt/vromvrom-engine/ihm-v2/node_modules/x.yaml")
    assert mod.est_exclu("/opt/vromvrom-engine/.git/config")
    assert not mod.est_exclu("/opt/vromvrom-engine/ha_corpus_src/esphome/Tab5/tab5-hardware.yaml")


def test_localiser_dans_repertoire_temporaire():
    """Vérifie que la recherche trouve les fichiers contenant les motifs."""
    with tempfile.TemporaryDirectory() as tmp:
        # Crée un fichier contenant un motif audio
        fichier_audio = os.path.join(tmp, "tab5-hardware.yaml")
        with open(fichier_audio, "w") as f:
            f.write("microphone:\n  - platform: i2s_audio\n    i2s_din_pin: GPIO28\n")

        # Crée un fichier sans motif
        fichier_autre = os.path.join(tmp, "autre.yaml")
        with open(fichier_autre, "w") as f:
            f.write("sensor:\n  - platform: wifi_signal\n")

        # Sauvegarde et restaure la racine
        racine_originale = mod.RACINE
        mod.RACINE = tmp
        try:
            resultats = mod.localiser()
            assert fichier_audio in resultats
            assert fichier_autre not in resultats
        finally:
            mod.RACINE = racine_originale


def test_localiser_exclut_node_modules():
    """Vérifie que les répertoires exclus ne sont pas parcourus."""
    with tempfile.TemporaryDirectory() as tmp:
        # Crée un répertoire node_modules avec un fichier contenant un motif
        nm = os.path.join(tmp, "node_modules")
        os.makedirs(nm)
        with open(os.path.join(nm, "audio.mjs"), "w") as f:
            f.write("i2s_audio GPIO28")

        # Crée un fichier pertinent à la racine
        with open(os.path.join(tmp, "tab5.yaml"), "w") as f:
            f.write("microphone: i2s_audio GPIO28")

        racine_originale = mod.RACINE
        mod.RACINE = tmp
        try:
            resultats = mod.localiser()
            # Le fichier dans node_modules ne doit PAS être trouvé
            assert not any("node_modules" in r for r in resultats)
            # Le fichier pertinent doit être trouvé
            assert any(r.endswith("tab5.yaml") for r in resultats)
        finally:
            mod.RACINE = racine_originale
