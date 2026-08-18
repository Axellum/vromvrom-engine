"""
Sonde de vivacité des modèles (#T333) — surtout : ses garde-fous.

Motif, mesuré le 12/08 : **35 % des tentatives de cascade** partaient dans le
vide (51 tentatives, 18 échecs, 6 modèles disjonctés) parce que des modèles du
catalogue ne répondaient plus. Effet pour l'utilisateur : la même demande est
passée de 39 s à 104 s, sans aucun changement de code sur son chemin.

Cette sonde a le droit de modifier le routage de PRODUCTION. Les tests portent
donc d'abord sur ce qu'elle doit refuser de faire :

  1. ne jamais rallumer un modèle éteint par un humain ;
  2. ne rien désactiver quand la majorité du cycle échoue (panne locale) ;
  3. ne jamais descendre sous le plancher de modèles actifs ;
  4. n'éteindre qu'après N échecs CONSÉCUTIFS, pas au premier raté.

Le test de vivacité doit être une inférence réelle : mesuré sur DashScope, la
même clé rend HTTP 200 sur `GET /v1/models` et HTTP 401 sur une complétion. Une
sonde qui listerait au lieu d'inférer déclarerait tout le monde en bonne santé.
"""

import pytest

import core.model_health_probe as sonde
from core import runtime_db


@pytest.fixture(autouse=True)
def _base_temporaire(tmp_path, monkeypatch):
    """Base réelle au schéma du dépôt, et sonde en mode « action autorisée »."""
    runtime_db.override_db_path(str(tmp_path / "sante.db"))
    runtime_db.get_connection().close()  # déclenche _init_schema
    monkeypatch.setenv("MODEL_HEALTH_PROBE", "true")
    monkeypatch.setenv("MODEL_HEALTH_AUTO_DISABLE", "true")


class _FauxCatalogue:
    """Catalogue en mémoire : `get_active_models` + `set_model_status`."""

    def __init__(self, actifs):
        self.statuts = dict.fromkeys(actifs, "active")
        self.appels = []

    def get_active_models(self, provider_id=None):
        # Même clé que la vraie DB (`id`, PK models).
        return [{"id": m} for m, s in self.statuts.items() if s == "active"]

    def set_model_status(self, model_id, status, *, revendique_par_sonde=False):
        self.statuts[model_id] = status
        self.appels.append((model_id, status, revendique_par_sonde))
        if revendique_par_sonde:
            sonde._ecrire_sante(
                model_id, desactive_par_sonde=1 if status == "inactive" else 0,
            )
        else:
            sonde._ecrire_sante(model_id, desactive_par_sonde=0)
        return True


def _brancher(monkeypatch, catalogue, muets: set):
    """Branche le catalogue et fait échouer les modèles de `muets`."""
    monkeypatch.setattr("core.models_db.get_active_models", catalogue.get_active_models)
    monkeypatch.setattr("core.models_db.set_model_status", catalogue.set_model_status)

    async def _test(model_id, gateway=None):
        if model_id in muets:
            return False, "HTTP 401 invalid_api_key", 12.0
        return True, "", 30.0

    monkeypatch.setattr(sonde, "tester_modele", _test)


# ── Le cas mesuré ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_modele_muet_eteint_apres_le_seuil(monkeypatch):
    """3 cycles ratés d'affilée → extinction ; pas avant."""
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})

    for cycle in range(sonde.SEUIL_ECHECS - 1):
        cr = await sonde.executer_cycle()
        assert cr["desactives"] == [], f"éteint dès le cycle {cycle + 1}"
        assert catalogue.statuts["m0"] == "active"

    cr = await sonde.executer_cycle()
    assert cr["desactives"] == ["m0"]
    assert catalogue.statuts["m0"] == "inactive"
    assert sonde.lire_sante("m0")["desactive_par_sonde"] == 1


@pytest.mark.asyncio
async def test_un_succes_remet_le_compteur_a_zero(monkeypatch):
    """Une panne passagère ne doit pas s'accumuler jusqu'à l'extinction."""
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})
    await sonde.executer_cycle()
    await sonde.executer_cycle()

    _brancher(monkeypatch, catalogue, muets=set())  # m0 répond de nouveau
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")["echecs_consecutifs"] == 0

    _brancher(monkeypatch, catalogue, muets={"m0"})
    cr = await sonde.executer_cycle()
    assert cr["desactives"] == [], "le compteur n'a pas été remis à zéro"


@pytest.mark.asyncio
async def test_reactivation_quand_le_modele_repond(monkeypatch):
    """
    Cycle complet : éteint, puis rallumé de lui-même au retour du service.
    Depuis l'ajout du backoff, un modèle éteint n'est plus re-testé à chaque
    cycle : on avance l'horloge au-delà du plafond pour prouver que le re-test a
    bien lieu et que le succès réactive le modèle.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})
    for _ in range(sonde.SEUIL_ECHECS):
        await sonde.executer_cycle()
    assert catalogue.statuts["m0"] == "inactive"

    _brancher(monkeypatch, catalogue, muets=set())
    # Simule le temps écoulé jusqu'au re-test (plafond du backoff).
    base = sonde._maintenant()
    monkeypatch.setattr(sonde, "_maintenant", lambda: base + sonde.PLAFOND_BACKOFF_S + 1)
    cr = await sonde.executer_cycle()

    assert cr["reactives"] == ["m0"]
    assert catalogue.statuts["m0"] == "active"
    assert sonde.lire_sante("m0")["desactive_par_sonde"] == 0


# ── Garde-fous ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ne_rallume_jamais_un_modele_eteint_par_un_humain(monkeypatch):
    """
    Garde-fou n°1, le plus important : une décision humaine n'est pas une panne.
    Un modèle qu'Axel a désactivé (coût, préférence, CGU) doit le rester, même
    s'il répond parfaitement.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    catalogue.statuts["m3"] = "inactive"  # décision humaine, hors sonde
    _brancher(monkeypatch, catalogue, muets=set())

    cr = await sonde.executer_cycle()

    assert "m3" not in cr["reactives"]
    assert catalogue.statuts["m3"] == "inactive"
    assert not any(a[0] == "m3" and a[1] == "active" for a in catalogue.appels)


@pytest.mark.asyncio
async def test_humain_qui_garde_inactif_n_est_pas_rallume(monkeypatch):
    """
    Bugbot : après extinction par la sonde, un toggle IHM (même vers inactive)
    doit effacer `desactive_par_sonde`, sinon le prochain ping rallume le modèle.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})
    for _ in range(sonde.SEUIL_ECHECS):
        await sonde.executer_cycle()
    assert sonde.lire_sante("m0")["desactive_par_sonde"] == 1

    # Décision humaine : rester inactif (ou basculer puis re-basculer — l'appel
    # IHM passe sans revendique_par_sonde).
    catalogue.set_model_status("m0", "inactive", revendique_par_sonde=False)
    assert sonde.lire_sante("m0")["desactive_par_sonde"] == 0

    _brancher(monkeypatch, catalogue, muets=set())
    cr = await sonde.executer_cycle()
    assert "m0" not in cr["reactives"]
    assert catalogue.statuts["m0"] == "inactive"


@pytest.mark.asyncio
async def test_panne_globale_ne_desactive_rien(monkeypatch):
    """
    Garde-fou n°2 : quand tout tombe en même temps, la cause est locale (box,
    DNS, FAI). Éteindre le catalogue entier serait le pire résultat possible
    pour un mécanisme censé protéger la production.
    """
    modeles = [f"m{i}" for i in range(10)]
    catalogue = _FauxCatalogue(modeles)
    _brancher(monkeypatch, catalogue, muets=set(modeles))

    for _ in range(sonde.SEUIL_ECHECS + 2):
        cr = await sonde.executer_cycle()

    assert cr["gele"] is True
    assert cr["desactives"] == []
    assert all(statut == "active" for statut in catalogue.statuts.values())


@pytest.mark.asyncio
async def test_plancher_de_modeles_actifs(monkeypatch):
    """
    Garde-fou n°3 : la sonde ne doit jamais rendre le moteur muet. Sous le
    plancher, un modèle en panne reste allumé — mieux vaut un modèle qui échoue
    qu'aucun modèle du tout.
    """
    modeles = [f"m{i}" for i in range(6)]
    catalogue = _FauxCatalogue(modeles)
    # 1 muet sur 6 : sous le seuil de panne globale, donc l'extinction est permise
    _brancher(monkeypatch, catalogue, muets={"m0"})

    for _ in range(sonde.SEUIL_ECHECS + 1):
        cr = await sonde.executer_cycle()

    actifs = sum(1 for s in catalogue.statuts.values() if s == "active")
    assert actifs >= sonde.MIN_MODELES_ACTIFS
    assert cr["desactives"] == [], "le plancher n'a pas retenu l'extinction"


@pytest.mark.asyncio
async def test_mode_observation_ne_touche_a_rien(monkeypatch):
    """MODEL_HEALTH_AUTO_DISABLE=false : la sonde mesure et se tait."""
    monkeypatch.setenv("MODEL_HEALTH_AUTO_DISABLE", "false")
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})

    for _ in range(sonde.SEUIL_ECHECS + 1):
        cr = await sonde.executer_cycle()

    assert cr["desactives"] == []
    assert catalogue.appels == []
    # Les compteurs sont tenus quand même : le diagnostic reste disponible.
    assert sonde.lire_sante("m0")["echecs_consecutifs"] >= sonde.SEUIL_ECHECS


@pytest.mark.asyncio
async def test_modele_non_cable_n_est_pas_une_panne(monkeypatch):
    """
    Un modèle au catalogue mais absent du gateway (#T186) n'est pas en panne :
    l'éteindre n'apporterait rien, il n'est de toute façon jamais choisi.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    monkeypatch.setattr("core.models_db.get_active_models", catalogue.get_active_models)
    monkeypatch.setattr("core.models_db.set_model_status", catalogue.set_model_status)

    class _GatewaySansModele:
        def get_provider(self, model_id):
            raise ValueError("non câblé")

    vivant, erreur, _ = await sonde.tester_modele("m0", gateway=_GatewaySansModele())

    assert vivant is None
    assert erreur == "non_cable"


@pytest.mark.asyncio
async def test_non_cables_n_empechent_pas_la_panne_globale(monkeypatch):
    """
    Bugbot : des modèles non câblés « vivants » ne doivent pas diluer le ratio
    d'échecs ni satisfaire le plancher — sinon tous les providers réels peuvent
    être éteints pendant qu'un catalogue fantôme reste « OK ».
    """
    cables = [f"c{i}" for i in range(5)]
    fantomes = [f"f{i}" for i in range(20)]
    catalogue = _FauxCatalogue(cables + fantomes)
    monkeypatch.setattr("core.models_db.get_active_models", catalogue.get_active_models)
    monkeypatch.setattr("core.models_db.set_model_status", catalogue.set_model_status)

    async def _test(model_id, gateway=None):
        if model_id.startswith("f"):
            return None, "non_cable", 0.0
        return False, "HTTP 401", 10.0

    monkeypatch.setattr(sonde, "tester_modele", _test)

    cr = await sonde.executer_cycle()
    assert cr["gele"] is True
    assert cr["desactives"] == []
    assert cr["ignores_non_cables"] == 20
    assert cr["morts"] == 5


@pytest.mark.asyncio
async def test_etat_persiste_entre_les_cycles(monkeypatch):
    """Les compteurs survivent d'un cycle à l'autre — sinon aucun seuil ne tient."""
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})

    await sonde.executer_cycle()
    premier = sonde.lire_sante("m0")
    await sonde.executer_cycle()
    second = sonde.lire_sante("m0")

    assert premier["echecs_consecutifs"] == 1
    assert second["echecs_consecutifs"] == 2
    assert second["derniere_erreur"] and "401" in second["derniere_erreur"]


def test_interrupteurs_de_configuration(monkeypatch):
    """La sonde doit pouvoir être neutralisée sans toucher au code."""
    monkeypatch.setenv("MODEL_HEALTH_PROBE", "false")
    assert sonde.sonde_activee() is False
    monkeypatch.setenv("MODEL_HEALTH_PROBE", "true")
    assert sonde.sonde_activee() is True

    monkeypatch.setenv("MODEL_HEALTH_AUTO_DISABLE", "non")
    assert sonde.peut_desactiver() is False

    monkeypatch.setenv("MODEL_HEALTH_INTERVAL_SECONDS", "120")
    assert sonde.intervalle_sonde() == 120
    monkeypatch.setenv("MODEL_HEALTH_INTERVAL_SECONDS", "pas-un-nombre")
    assert sonde.intervalle_sonde() == sonde.INTERVALLE_DEFAUT_S
