"""
#T356 — Ne plus annoncer un succès sur un appareil déconnecté.

Le moteur n'inspectait jamais l'état de l'entité qu'il pilote : il envoyait le
service, lisait le code HTTP (200 = succès) et annonçait « Volet fermé » alors
que l'appareil `unavailable` n'avait rien exécuté. Mesuré en prod le 17/08 :
146 entités `unavailable`/`unknown`, dont 17 pilotables (cover.living_room_blind,
6 light.sonoff_*, 7 switch.sonoff_*…).

Correctif : avant d'exécuter une commande domotique, lire l'état de l'entité
visée (réutilise `read_ha_state`, déjà présent — pas de troisième chemin). Si
elle est `unavailable` ou `unknown`, ne PAS annoncer un succès : rendre une
phrase qui nomme l'appareil qui ne répond pas. Une lecture d'état indisponible
(HA muet, erreur réseau) n'est PAS « l'appareil est mort » : la commande part
quand même, comme avant.
"""

import pytest

import services.execute_service as es


class _FakeResp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""


class _FakeSession:
    """Enregistre les POST HA (et refuse tout GET réseau, jamais atteint ici)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json))
        return _FakeResp(200)


def _etat_vivant(entity_id: str = "light.living_room") -> dict:
    return {"entity_id": entity_id, "state": "off", "attributes": {}}


def _fake_read_ha_state(etat: dict | None):
    """Retourne une coroutine mockant `read_ha_state` (fonction async)."""
    async def _lecture(_entity_id: str) -> dict | None:
        return etat
    return _lecture


# ── LE TEST CENTRAL : entité `unavailable`, HA répond 200 → pas de succès ─────

@pytest.mark.asyncio
async def test_entite_unavailable_ne_annonce_pas_de_succes(monkeypatch):
    """
    Une commande visant une entité `unavailable` ne produit JAMAIS une phrase de
    succès, même si HA répondrait HTTP 200 au POST. La phrase rendue nomme
    l'appareil qui ne répond pas. Aucun POST ne doit partir (inutile d'envoyer
    une commande à un appareil mort).
    """
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    # L'entité visée est `unavailable` : HA répondrait 200 au POST, mais l'appareil
    # n'exécute rien. C'est exactement le mensonge que #T356 combat.
    monkeypatch.setattr(
        es,
        "read_ha_state",
        _fake_read_ha_state({
            "entity_id": "cover.living_room_blind",
            "state": "unavailable",
            "attributes": {"friendly_name": "Volet serre rideau"},
        }),
    )

    ok, texte = await es.execute_ha_service(
        "script.blind_action", "", service_data={"action": "close"},
    )

    assert ok is False, "une entité unavailable ne doit pas être annoncée comme exécutée"
    assert fake.calls == [], "aucun POST ne doit partir vers une entité morte"
    # La phrase nomme l'appareil qui ne répond pas — pas un refus générique.
    assert "ne répond pas" in texte
    assert "volet" in texte.lower()


@pytest.mark.asyncio
async def test_entite_unknown_ne_annonce_pas_de_succes(monkeypatch):
    """`unknown` est traité comme `unavailable` : pas de succès, phrase nommée."""
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(
        es,
        "read_ha_state",
        _fake_read_ha_state({
            "entity_id": "light.sonoff_x",
            "state": "unknown",
            "attributes": {"friendly_name": "Sonoff salon"},
        }),
    )

    ok, texte = await es.execute_ha_service("light.turn_on", "light.sonoff_x")

    assert ok is False
    assert fake.calls == []
    assert "ne répond pas" in texte
    assert "sonoff" in texte.lower()


# ── LE TEST JUMEAU : entité vivante → exactement la réponse d'aujourd'hui ─────

@pytest.mark.asyncio
async def test_entite_vivante_repond_exactement_comme_avant(monkeypatch):
    """
    Une commande vers une entité vivante répond exactement comme aujourd'hui :
    même phrase, et un seul POST (la lecture d'état ne déclenche aucun appel
    POST supplémentaire). C'est la non-régression du chemin nominal.
    """
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _fake_read_ha_state(_etat_vivant()))

    ok, texte = await es.execute_ha_service("light.turn_off", "light.living_room")

    assert ok is True
    # Phrase canonique TTS existante (cache) — identique à aujourd'hui.
    assert texte == "Lumière du salon éteinte.", f"la phrase a changé : {texte!r}"
    # Un seul POST, comme avant (aucun appel supplémentaire inutile).
    assert len(fake.calls) == 1
    url, _ = fake.calls[0]
    assert url.endswith("/api/services/light/turn_off")


# ── Lecture d'état indisponible → la commande part quand même ─────────────────

@pytest.mark.asyncio
async def test_lecture_etat_indisponible_laisse_partir_la_commande(monkeypatch):
    """
    Une lecture d'état en échec (HA muet, erreur réseau, entité absente du
    cache) n'est PAS « l'appareil est mort » : la commande part quand même et le
    comportement reste celui d'aujourd'hui. Un moteur qui refuse d'agir dès qu'il
    doute est inutilisable.
    """
    fake = _FakeSession()
    monkeypatch.setattr(es, "_get_ha_session", lambda: fake)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    # read_ha_state retourne None (lecture indisponible) — on ne sait pas si
    # l'appareil est mort, donc on exécute comme avant.
    monkeypatch.setattr(es, "read_ha_state", _fake_read_ha_state(None))

    ok, texte = await es.execute_ha_service("light.turn_off", "light.living_room")

    assert ok is True
    assert texte == "Lumière du salon éteinte."
    assert len(fake.calls) == 1, "la commande doit partir malgré la lecture indisponible"


# ── Le matcher privilégie l'entité vivante à score voisin ─────────────────────

def test_matcher_choisit_la_vivante_a_score_voisin():
    """
    Entre deux entités de score voisin dont une est morte, le matcher choisit la
    vivante. Le parc contient des doublons morts (six light.sonoff_* indisponibles)
    qui concurrencent les entités réelles au moment du choix.
    """
    from core.ha_fuzzy_matcher import HAFuzzyMatcher

    matcher = HAFuzzyMatcher("https://ha.local", "tok")

    # Deux entités « Salon » au nom identique : les scores SANS pénalité sont
    # EXACTEMENT égaux. L'une est vivante, l'autre morte (unavailable). Sans
    # correctif, le matcher serait ambigu (refus) ou choisirait au hasard ; avec
    # la pénalité sur la morte, la vivante doit l'emporter.
    entities = {
        "light.living_room": {
            "entity_id": "light.living_room",
            "domain": "light",
            "friendly_name": "Salon",
            "normalized_name": "salon",
            "normalized_id": "salon",
            "state": "on",
        },
        "light.kitchen": {
            "entity_id": "light.kitchen",
            "domain": "light",
            "friendly_name": "Salon",
            "normalized_name": "salon",
            "normalized_id": "salon",
            "state": "unavailable",
        },
    }

    # À score brut égal, la pénalité départage au profit de la vivante.
    score_vivante = matcher._score_entity("salon", entities["light.living_room"])
    score_morte = matcher._score_entity("salon", entities["light.kitchen"])
    assert score_vivante > score_morte, (
        "à score voisin, la vivante doit sortir devant la morte"
    )

    # Et surtout, le choix final via find_entity retient l'entité vivante.
    async def _fake_load():
        return entities

    matcher._load_entities = _fake_load  # type: ignore[assignment]
    matcher._lm_studio_online = False  # force le chemin difflib (pas d'embedding)

    import asyncio
    result = asyncio.run(matcher.find_entity("allume le salon"))

    assert result is not None
    assert result.entity_id == "light.living_room", (
        "le matcher doit choisir l'entité vivante, pas le doublon mort"
    )


def test_est_entite_morte():
    """`unavailable` et `unknown` sont mortes ; tout le reste est vivant."""
    from core.ha_fuzzy_matcher import _est_entite_morte

    assert _est_entite_morte({"state": "unavailable"}) is True
    assert _est_entite_morte({"state": "unknown"}) is True
    assert _est_entite_morte({"state": "on"}) is False
    assert _est_entite_morte({"state": "off"}) is False
    assert _est_entite_morte({}) is False
