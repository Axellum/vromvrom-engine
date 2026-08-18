"""
[#T363] Une question ne doit jamais actionner la maison.

Mesuré en prod le 17/08 par balayage de l'usage réel : « est-ce que la lumière
du salon est allumée ? » produisait `light.turn_on` sur `light.living_room`, et
« État de la lumière de la chambre. » produisait `light.turn_off` sur
`light.bedroom`. La réponse rendue (« Lumière de la chambre éteinte. ») est
indiscernable d'un rapport d'état : l'utilisateur croit interroger, il commande.

Ces tests verrouillent les deux sens : les questions rendent la main à la
cascade (qui les traitera en lecture d'état), les ordres restent des ordres.
"""

import pytest

from services.execute_service import match_ha_command

# ── Les questions ne sont pas des ordres ──

@pytest.mark.parametrize("phrase", [
    # Formulations exactes relevées dans vocal_audit_log (prod).
    "est-ce que la lumiere du salon est allumee ?",
    "Est-elle a lumiere de la chambre ?",
    "Est-elle un lumiere de la chambre ?",
    "Etat de la lumiere de la chambre.",
    # Variantes de la même famille.
    "la clim est allumee ?",
    "le volet du salon est-il ouvert ?",
    "quel est l'etat de la lumiere du salon ?",
    "combien de degres dans le salon ?",
])
def test_une_question_ne_produit_aucune_commande(phrase):
    assert match_ha_command(phrase) is None


# ── Les ordres restent des ordres ──

@pytest.mark.parametrize("phrase,service", [
    ("Allume la lumiere de la chambre.", "light.turn_on"),
    ("eteins la lumiere du salon", "light.turn_off"),
    ("ferme le volet", "script.blind_action"),
    ("mets la clim a 23 degres", "climate.set_temperature"),
    # Ordre poli formulé en question : l'infinitif d'action tranche.
    ("tu peux allumer la lumiere du salon ?", "light.turn_on"),
    ("peux-tu fermer le volet ?", "script.blind_action"),
    # Transcriptions STT réelles d'ordres, sans point d'interrogation.
    ("Decembre le volet du salon", "script.blind_action"),
    ("et tel les lumieres du salon", "light.turn_off"),
])
def test_un_ordre_reste_une_commande(phrase, service):
    cmd = match_ha_command(phrase)
    assert cmd is not None, f"{phrase!r} doit rester une commande"
    assert cmd.service == service


def test_est_elle_sans_point_interrogation_reste_une_commande():
    """
    Arbitrage assumé : `normalize_vocal_stt` réécrit « est-elle » en « éteins »
    (`core/vocal_stt_normalize.py:38`) parce que le STT confond réellement les
    deux. Sans point d'interrogation, on garde donc la lecture « ordre » — sinon
    on perdrait de vraies commandes mal transcrites. C'est le « ? » qui tranche.
    """
    cmd = match_ha_command("Est-elle la lumiere de la chambre")
    assert cmd is not None and cmd.service == "light.turn_off"


def test_premier_mot_imperatif_prime_sur_la_forme_interrogative():
    """« Allume la lumière ? » reste un ordre : l'impératif explicite l'emporte."""
    cmd = match_ha_command("Allume la lumiere de la chambre ?")
    assert cmd is not None and cmd.service == "light.turn_on"
