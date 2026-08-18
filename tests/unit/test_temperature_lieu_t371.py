"""
[#T371] La température ne doit jamais venir d'une autre pièce que celle citée.

Trois défauts mesurés sur la prod du 18/08, tous « indiscernables d'une bonne
réponse » puisque la valeur servie était exacte :

  a) « quelle est la température de la buanderie ? » → capteur du SALON ;
  b) « quelle est la température extérieure ? »      → capteur du SALON, alors
     que l'étage météo (#T367) sait répondre et n'était jamais atteint ;
  c) « quelle est la température de la chambre ? »   → plus RIEN depuis #T364,
     alors que `sensor.bedroom_temperature` est vivant (24,49 °C
     relevé sur le HA de prod le 18/08).

Ces tests portent sur la DÉTECTION (`match_*`), donc sans aucun appel réseau.
"""

from services.ha_state_query import (
    _PIECES_CONNUES,
    _TEMPERATURE_SOURCES,
    match_ha_state_query,
)
from services.ha_weather_query import match_weather_query


class TestPieceInconnue:
    """(a) Un lieu nommé mais inconnu ne reçoit jamais le capteur du voisin."""

    def test_la_buanderie_ne_recoit_pas_la_temperature_du_salon(self):
        assert match_ha_state_query("quelle est la temperature de la buanderie ?") is None

    def test_meme_accentuee(self):
        # `normalize_ha_command_prompt` dépouille les accents ici — contrairement
        # à `core/vocal_host.py` (#T383). Le test l'atteste dans les deux formes.
        assert match_ha_state_query("quelle est la température de la buanderie ?") is None

    def test_sans_lieu_nomme_le_salon_reste_le_defaut(self):
        # « il fait combien ? » n'exprime aucun lieu : le repli reste légitime.
        q = match_ha_state_query("quelle est la temperature ?")
        assert q is not None and q.display.endswith("|salon")

    def test_de_degres_n_est_pas_un_lieu(self):
        # Garde-fou : la préposition « de » attrape « degres », qui n'est pas un
        # lieu. Sans l'exclusion, cette phrase perdrait sa réponse.
        q = match_ha_state_query("il fait combien de degres ?")
        assert q is not None and q.display.endswith("|salon")

    def test_la_maison_vaut_la_piece_de_vie(self):
        # Choix assumé : « la maison », pour le moteur, c'est le salon.
        q = match_ha_state_query("quelle est la temperature de la maison ?")
        assert q is not None and q.display.endswith("|salon")


class TestExterieur:
    """(b) L'extérieur appartient à la météo, pas à l'état domotique."""

    def test_l_etat_relache_la_temperature_exterieure(self):
        assert match_ha_state_query("quelle est la temperature exterieure ?") is None
        assert match_ha_state_query("quelle est la température extérieure ?") is None

    def test_et_la_meteo_la_reconnait(self):
        # Relâcher ne suffit pas : sans marqueur météo, la question tomberait au
        # chat. Les deux orthographes sont vérifiées car `ha_weather_query`
        # CONSERVE les accents (cause racine de #T383).
        assert match_weather_query("quelle est la temperature exterieure ?") is not None
        assert match_weather_query("quelle est la température extérieure ?") is not None

    def test_une_piece_nommee_reste_a_l_etat(self):
        # La météo ne doit pas voler les questions intérieures.
        assert match_weather_query("il fait combien dans le salon ?") is None


class TestChambre:
    """(c) La chambre a un capteur : elle doit répondre, et avec le sien."""

    def test_la_chambre_est_lue_sur_son_propre_capteur(self):
        q = match_ha_state_query("quelle est la temperature de la chambre ?")
        assert q is not None, "#T364 avait supprimé la réponse au lieu de la corriger"
        assert q.entity_id == "sensor.bedroom_temperature"
        assert q.display.endswith("|chambre")

    def test_le_salon_n_a_pas_bouge(self):
        q = match_ha_state_query("quelle est la temperature du salon ?")
        assert q is not None
        assert q.entity_id == "climate.living_room"

    def test_toute_source_declaree_est_une_piece_connue(self):
        # Cause racine figée : une pièce qui gagne un capteur doit du même coup
        # devenir « connue », sinon le repli salon la rattrape en silence.
        assert set(_TEMPERATURE_SOURCES) <= set(_PIECES_CONNUES)
