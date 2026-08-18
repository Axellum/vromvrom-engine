"""
Un volet sans retour de position est en `unknown` — c'est normal, pas une panne.

#T356 (PR #323) a livré un garde-fou légitime : ne pas annoncer un succès quand
la cible d'une commande est morte, parce que HA répond 200 même sur une entité
`unavailable`. Mais il a mis `unknown` dans le même sac.

Or, mesuré sur le vrai HA le 17/08 : `cover.living_room_blind` est la SEULE
entité pilotable en `unknown`, c'est le volet UNIQUE de la maison, il FONCTIONNE
(script relancé avec succès à 13:53) — c'est simplement un volet sans capteur de
position. Et `_entite_physique_a_verifier` redirige justement le script de volet
vers cette cover.

Sans #T385, le déploiement aurait donc fait répondre « le volet de la serre ne
répond pas » à chaque commande de volet, pendant que le volet bouge — sur la
commande vocale la plus utilisée de l'installation.

Ce banc fige les deux bords : le volet sans position passe, et tout le reste du
garde-fou de #T356 tient (y compris un volet QUI sait rendre sa position).
"""

import pytest

import services.execute_service as es


class _FakeSession:
    """Session HA qui enregistre les POST au lieu de les envoyer."""

    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("aucun POST ne devait partir dans ce test")


def _fake_read_ha_state(payload):
    async def _read(entity_id):
        return payload
    return _read


def _brancher_ha(monkeypatch, etat_lu, session=None):
    fake = session if session is not None else _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _fake_read_ha_state(etat_lu))
    return fake


# ── LE CAS CENTRAL : le volet unique de la maison ────────────────────────────

@pytest.mark.asyncio
async def test_le_volet_sans_position_en_unknown_nest_pas_bloque(monkeypatch):
    """`cover.living_room_blind` en `unknown` : la commande doit partir."""
    _brancher_ha(monkeypatch, {
        "entity_id": "cover.living_room_blind",
        "state": "unknown",
        "attributes": {"friendly_name": "Volet de la serre"},
    })

    blocage = await es._verifier_etat_entite("cover.open_cover", "cover.living_room_blind")

    assert blocage is None, (
        "un volet sans retour de position en 'unknown' est dans son état normal : "
        "la commande ne doit pas être bloquée"
    )


@pytest.mark.asyncio
async def test_le_script_de_volet_nest_pas_bloque_non_plus(monkeypatch):
    """
    Le chemin réel : les commandes de volet passent par le script (#T350), et
    `_entite_physique_a_verifier` les redirige vers la cover. C'est ce chemin-là
    qui aurait cassé en production.
    """
    _brancher_ha(monkeypatch, {
        "entity_id": es._VOLET_COVER_ENTITY,
        "state": "unknown",
        "attributes": {"friendly_name": "Volet de la serre"},
    })

    blocage = await es._verifier_etat_entite(es._VOLET_SCRIPT, "")

    assert blocage is None, "la commande vocale de volet ne doit pas être refusée"


# ── LES BORDS : #T356 doit rester entier partout ailleurs ────────────────────

@pytest.mark.asyncio
async def test_un_volet_unavailable_reste_bloque(monkeypatch):
    """`unavailable` prouve que HA ne joint pas l'appareil : on bloque toujours."""
    _brancher_ha(monkeypatch, {
        "entity_id": "cover.living_room_blind",
        "state": "unavailable",
        "attributes": {"friendly_name": "Volet de la serre"},
    })

    blocage = await es._verifier_etat_entite("cover.open_cover", "cover.living_room_blind")

    assert blocage is not None
    assert "ne répond pas" in blocage


@pytest.mark.asyncio
async def test_un_volet_qui_rend_sa_position_reste_suspect_en_unknown(monkeypatch):
    """
    L'exception est étroite : un volet QUI sait rendre sa position et tombe en
    `unknown` n'est pas dans son état normal — il reste traité comme suspect.
    """
    _brancher_ha(monkeypatch, {
        "entity_id": "cover.volet_avec_capteur",
        "state": "unknown",
        "attributes": {"friendly_name": "Volet du salon", "current_position": 0},
    })

    blocage = await es._verifier_etat_entite("cover.open_cover", "cover.volet_avec_capteur")

    assert blocage is not None, "l'exception ne doit couvrir que les volets sans position"


@pytest.mark.asyncio
async def test_une_lampe_en_unknown_reste_bloquee(monkeypatch):
    """Hors du domaine `cover`, #T356 est inchangé (cf. les 6 `light.sonoff_*` morts)."""
    _brancher_ha(monkeypatch, {
        "entity_id": "light.sonoff_x",
        "state": "unknown",
        "attributes": {"friendly_name": "Sonoff salon"},
    })

    blocage = await es._verifier_etat_entite("light.turn_on", "light.sonoff_x")

    assert blocage is not None
    assert "ne répond pas" in blocage


def test_le_predicat_est_bien_borne():
    """Garde-fou direct sur le helper, sans passer par HA."""
    sans_position = {"attributes": {"friendly_name": "Volet"}}
    avec_position = {"attributes": {"current_position": 42}}

    assert es._cover_sans_retour_de_position("cover.living_room_blind", sans_position) is True
    assert es._cover_sans_retour_de_position("cover.living_room_blind", avec_position) is False
    assert es._cover_sans_retour_de_position("light.living_room", sans_position) is False
    assert es._cover_sans_retour_de_position("switch.prise", {}) is False
