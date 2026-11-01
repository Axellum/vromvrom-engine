"""
[#T490] Un mot isolé ne doit jamais actionner la maison.

Mesuré en prod le 01/11 dans `vocal_audit_log` : « Coupe. » a éteint la lumière
du salon, parce que « coupe » est contenu dans la phrase du catalogue « coupe la
lumiere du salon ». Le même test d'inclusion faisait allumer le salon sur « La. »,
« Le. », « Du. », « Salon. » ou « Lumière. », et la chambre sur « Chambre. ».

Ces tests verrouillent les deux sens : un morceau sans action ou sans cible rend
la main à la cascade, un morceau qui nomme les deux reste une commande.
"""

import pytest

from services.execute_service import match_ha_command

# ── Un mot isolé, ou un morceau qui ne nomme rien, n'est pas une commande ──

@pytest.mark.parametrize("phrase", [
    # Formulation exacte relevée dans vocal_audit_log (prod, 01/11).
    "Coupe.",
    # Morceaux de phrases du catalogue, tous actionnaient une lumière.
    "La.",
    "Le.",
    "Du.",
    "Salon.",
    "Lumière.",
    "Chambre.",
    "Allume.",
    "Allume la.",
    "coupe le",
    # Morceau de mot : l'inclusion se fait par mots entiers.
    "lume la",
])
def test_un_morceau_sans_action_ni_cible_ne_produit_aucune_commande(phrase):
    assert match_ha_command(phrase) is None


# ── Un morceau qui nomme une action ET une cible reste une commande ──

@pytest.mark.parametrize("phrase,service,entity_id", [
    # STT qui a perdu la fin de la phrase (« … du salon »).
    ("allume la lumiere", "light.turn_on", "light.living_room"),
    ("eteins la lumiere", "light.turn_off", "light.living_room"),
    ("allume la lumiere de la", "light.turn_on", "light.bedroom"),
])
def test_un_morceau_avec_action_et_cible_reste_une_commande(phrase, service, entity_id):
    cmd = match_ha_command(phrase)
    assert cmd is not None, f"{phrase!r} doit rester une commande"
    assert (cmd.service, cmd.entity_id) == (service, entity_id)


def test_stop_seul_reste_reconnu():
    """« stop » est une phrase entière du catalogue (égalité, pas un morceau) ;
    `resolve_ha_command_for_execute` ne la garde que si le volet bouge."""
    cmd = match_ha_command("Stop.")
    assert cmd is not None and (cmd.service_data or {}).get("action") == "stop"
