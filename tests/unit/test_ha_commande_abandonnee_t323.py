"""
Une commande domotique reconnue ne doit plus être abandonnée en silence (#T323).

Mesuré en PRODUCTION le 12/08 sur une vraie commande vocale d'Axel
(session `chat_681bf3cf3d`) :

    09:18:59,950  Match fuzzy 0.88 : « dessant le volet du salon »
                  ≈ « descend le volet du salon » → {'action': 'close'}
    09:18:59,951  Zero-LLM HA → script.blind_action()
    09:19:09,100  Zero-LLM HA échec : Server disconnected        ← 9,1 s
    09:19:09,377  « Je ne peux pas descendre le volet du salon depuis ici. »

Le volet n'a pas bougé, et l'utilisateur a reçu un **refus de compétence** au
lieu d'une panne. Deux défauts distincts, couverts séparément ici :

  (a) l'appel HA échoue sur une connexion keep-alive fermée par le serveur —
      ce qui frappe les appels ESPACÉS, donc les commandes vocales
      (2 échecs sur 5 appels zero-LLM en 7 jours) ;
  (b) l'exception remontait, la cascade repartait vers le chat, et le modèle —
      qui ignore tout de la commande reconnue — inventait une excuse plausible.

Le (b) est le plus coûteux : un mensonge plausible envoie chercher au mauvais
endroit, alors qu'une erreur oriente vers Home Assistant.
"""

import aiohttp
import pytest

import core.vocal_host as vh
import services.execute_service as es
from services.execute_service import _est_action_rejouable


# [T356] Depuis que execute_ha_service vérifie l'état de l'entité avant le POST,
# les tests du POST doivent simuler une entité VIVANTE (sinon la lecture d'état
# ferait un vrai GET réseau, bloqué par le garde-fou). read_ha_state est async.
async def _etat_vivant(entity_id: str) -> dict:
    return {"entity_id": entity_id, "state": "off", "attributes": {}}

# ── (a) Reprise sur connexion fermée, strictement bornée ─────────────────────

class _FakeResp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""


class _SessionQuiCoupe:
    """Coupe la connexion (comme un keep-alive expiré) les N premières fois."""

    def __init__(self, coupures: int):
        self.coupures = coupures
        self.tentatives = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.tentatives += 1
        if self.tentatives <= self.coupures:
            return _RespQuiCoupe()
        return _FakeResp(200)


class _RespQuiCoupe:
    async def __aenter__(self):
        raise aiohttp.ServerDisconnectedError("Server disconnected")

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_action_rejouable_repart_sur_une_connexion_neuve(monkeypatch):
    """Service rejouable : la connexion morte du pool ne doit pas perdre la commande."""
    session = _SessionQuiCoupe(coupures=1)
    monkeypatch.setattr(es, "_get_ha_session", lambda: session)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    ok, texte = await es.execute_ha_service(
        "light.turn_off", "light.living_room",
    )

    assert ok, "la commande a été perdue alors qu'elle était rejouable sans risque"
    assert session.tentatives == 2
    assert texte


@pytest.mark.asyncio
async def test_script_volet_n_est_plus_rejoue(monkeypatch):
    """
    Depuis #T350 le volet passe par script.turn_on et son script est en
    `mode: restart` : rejouer annulerait la première exécution. Une connexion
    coupée doit donc échouer franchement, sans seconde tentative.
    """
    session = _SessionQuiCoupe(coupures=1)
    monkeypatch.setattr(es, "_get_ha_session", lambda: session)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    with pytest.raises(aiohttp.ServerDisconnectedError):
        await es.execute_ha_service(
            "script.blind_action", "cover.volet_salon",
            service_data={"action": "close"},
        )

    assert session.tentatives == 1, (
        "le script volet a été rejoué alors que mode:restart annulerait "
        "l'exécution déjà lancée"
    )


@pytest.mark.asyncio
async def test_action_non_rejouable_echoue_franchement(monkeypatch):
    """`toggle` ne doit JAMAIS être rejoué : deux inversions = retour à l'état initial."""
    session = _SessionQuiCoupe(coupures=1)
    monkeypatch.setattr(es, "_get_ha_session", lambda: session)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    with pytest.raises(aiohttp.ServerDisconnectedError):
        await es.execute_ha_service("light.toggle", "light.living_room")

    assert session.tentatives == 1, "toggle a été rejoué — risque de double commande physique"


@pytest.mark.asyncio
async def test_deux_coupures_de_suite_ne_boucle_pas(monkeypatch):
    """La reprise est unique : une panne durable doit remonter, pas boucler."""
    session = _SessionQuiCoupe(coupures=5)
    monkeypatch.setattr(es, "_get_ha_session", lambda: session)
    monkeypatch.setattr(es, "_read_ha_credentials", lambda: ("tok", "https://ha.local"))
    monkeypatch.setattr(es, "read_ha_state", _etat_vivant)

    with pytest.raises(aiohttp.ServerDisconnectedError):
        await es.execute_ha_service(
            "light.turn_off", "light.living_room",
        )

    assert session.tentatives == 2


def test_liste_blanche_des_actions_rejouables():
    """L'idempotence de l'effet PHYSIQUE est le seul critère.

    Depuis #T350 le script volet n'est plus rejouable : il passe par
    script.turn_on et son script HA est en `mode: restart` (une seconde
    commande annulerait la première).
    """
    for rejouable in ("light.turn_on", "cover.close_cover", "cover.open_cover",
                      "climate.set_temperature"):
        assert _est_action_rejouable(rejouable), rejouable
    for interdit in ("light.toggle", "cover.toggle", "script.un_script_inconnu",
                     "vacuum.start", "lock.unlock", "script.blind_action"):
        assert not _est_action_rejouable(interdit), interdit


# ── (b) Une commande reconnue ne retombe plus dans la conversation ───────────

class _Cmd:
    service = "script.blind_action"
    entity_id = "cover.volet_salon"
    service_data = {"action": "close"}


@pytest.mark.asyncio
async def test_commande_reconnue_mais_en_panne_annonce_la_panne(monkeypatch):
    """
    Le cœur de #T323 : l'échec ne doit PAS rendre None, sinon la cascade repart
    vers le chat et le modèle invente « je ne peux pas faire ça depuis ici ».
    """
    async def _resolve(_prompt):
        return _Cmd()

    async def _execute(*a, **k):
        raise aiohttp.ServerDisconnectedError("Server disconnected")

    monkeypatch.setattr(es, "resolve_ha_command_for_execute", _resolve)
    monkeypatch.setattr(es, "execute_ha_service", _execute)

    reponse = await vh._try_zero_llm_ha_command("descend le volet du salon")

    assert reponse is not None, (
        "l'échec est retombé dans la cascade : le chat va inventer une excuse "
        "alors que la commande avait été reconnue"
    )
    assert "Home Assistant" in reponse
    assert "n'a pas été exécutée" in reponse
    # Et surtout : aucune formulation qui ferait croire à une incapacité du moteur
    assert "depuis ici" not in reponse


@pytest.mark.asyncio
async def test_demande_non_domotique_laisse_passer_la_cascade(monkeypatch):
    """Seul cas où None est correct : ce n'était pas une commande domotique."""
    async def _resolve(_prompt):
        return None

    monkeypatch.setattr(es, "resolve_ha_command_for_execute", _resolve)

    assert await vh._try_zero_llm_ha_command("raconte-moi une blague") is None


@pytest.mark.asyncio
async def test_commande_reussie_rend_sa_phrase(monkeypatch):
    """Non-régression du chemin nominal."""
    async def _resolve(_prompt):
        return _Cmd()

    async def _execute(*a, **k):
        return True, "Volet du salon fermé."

    monkeypatch.setattr(es, "resolve_ha_command_for_execute", _resolve)
    monkeypatch.setattr(es, "execute_ha_service", _execute)

    assert await vh._try_zero_llm_ha_command("descend le volet") == "Volet du salon fermé."
