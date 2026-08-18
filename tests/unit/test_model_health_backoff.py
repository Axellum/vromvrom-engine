"""
Sonde de vivacité — backoff, classement de la cause et alerte-une-fois.

Motif, mesuré en PROD (pas déduit) : 31/76 modèles éteints par la sonde, avec
`echecs_consecutifs = 112` pour toute la famille Claude et une latence de 3 000
à 3 900 ms par test. 112 cycles à re-confirmer la même panne, c'est ~4,7 jours
de re-tests inutiles (24×/jour, ~3 s + vrais tokens à chaque fois). Trois des
quatre familles de pannes ne guérissent jamais toutes seules (crédit épuisé,
workspace non trusté, binaire absent) : seul un humain peut les réparer.

Ce fichier verrouille :
  1. le backoff : un modèle éteint par la sonde n'est PAS re-testé à chaque
     cycle, mais l'est encore régulièrement — et un succès le réactive ;
  2. le classement : les trois messages d'erreur réels ci-dessus sont
     « actionnable », un timeout réseau ne l'est pas ;
  3. l'alerte-une-fois : une cause actionnable produit UN signal, pas N ;
  4. les garde-fous inchangés : panne globale et plancher de modèles actifs.
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


def _brancher(monkeypatch, catalogue, muets: set, erreur="HTTP 401 invalid_api_key"):
    """Branche le catalogue ; `muets` échouent avec `erreur`, les autres répondent."""
    monkeypatch.setattr("core.models_db.get_active_models", catalogue.get_active_models)
    monkeypatch.setattr("core.models_db.set_model_status", catalogue.set_model_status)

    async def _test(model_id, gateway=None):
        if model_id in muets:
            return False, erreur, 3000.0
        return True, "", 30.0

    monkeypatch.setattr(sonde, "tester_modele", _test)


def _horloge_avancante(monkeypatch, depart: float):
    """Horloge pilotée : `_maintenant()` renvoie une valeur que l'on avance."""
    etat = {"t": depart}
    monkeypatch.setattr(sonde, "_maintenant", lambda: etat["t"])

    def avancer(secondes: float) -> None:
        etat["t"] += secondes

    return avancer


# ── Test central : backoff puis réactivation ────────────────────────────────

@pytest.mark.asyncio
async def test_modele_a_112_echecs_reporte_puis_reactivable(monkeypatch):
    """
    Le cœur du backoff : un modèle à 112 échecs consécutifs (cas mesuré en prod)
    n'est PAS re-testé à chaque cycle, mais l'est encore régulièrement — et un
    succès le réactive immédiatement.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    erreur_credit = (
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits."
    )
    _brancher(monkeypatch, catalogue, muets={"m0"}, erreur=erreur_credit)

    # m0 : 112 échecs consécutifs, déjà éteint par la sonde (état prod mesuré).
    base = 1_000_000.0
    sonde._ecrire_sante(
        "m0", dernier_test=base, echecs_consecutifs=112, succes_consecutifs=0,
        desactive_par_sonde=1, derniere_erreur=erreur_credit, latence_ms=3000.0,
    )
    catalogue.set_model_status("m0", "inactive", revendique_par_sonde=True)
    avancer = _horloge_avancante(monkeypatch, base)

    # Cycle suivant, une heure plus tard : m0 est REPORTÉ (backoff), pas re-testé.
    avancer(sonde.INTERVALLE_DEFAUT_S)
    cr = await sonde.executer_cycle()
    assert cr["reportes_backoff"] >= 1
    # Seuls les 9 modèles actifs ont été testés : m0 (éteint) a été sauté.
    assert cr["testes"] == 9, "un modèle à backoff ne doit pas être re-testé ce cycle"
    assert cr["morts"] == 0

    # Jusqu'au plafond : m0 est bien re-testé (et échoue encore).
    avancer(sonde.PLAFOND_BACKOFF_S)
    cr = await sonde.executer_cycle()
    assert cr["morts"] >= 1, "après le plafond, le modèle doit être re-testé"
    assert sonde.lire_sante("m0")["echecs_consecutifs"] == 113

    # m0 répond de nouveau : réactivé immédiatement, sans intervention manuelle.
    _brancher(monkeypatch, catalogue, muets=set())
    avancer(sonde.PLAFOND_BACKOFF_S)
    cr = await sonde.executer_cycle()
    assert cr["reactives"] == ["m0"]
    assert catalogue.statuts["m0"] == "active"
    assert sonde.lire_sante("m0")["desactive_par_sonde"] == 0


@pytest.mark.asyncio
async def test_modele_eteint_est_reporte_mais_pas_oublie(monkeypatch):
    """
    Un modèle éteint par la sonde qui n'a pas encore atteint son intervalle de
    backoff est reporté au cycle suivant, mais reste dans la liste des éteints :
    il n'est pas perdu et sera re-testé quand l'intervalle sera écoulé.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(monkeypatch, catalogue, muets={"m0"})
    base = 2_000_000.0
    avancer = _horloge_avancante(monkeypatch, base)
    # m0 : 4 échecs (intervalle de backoff = 2 h), déjà éteint par la sonde.
    sonde._ecrire_sante(
        "m0", dernier_test=base, echecs_consecutifs=4, succes_consecutifs=0,
        desactive_par_sonde=1, derniere_erreur="HTTP 401 invalid_api_key",
        latence_ms=3000.0,
    )
    catalogue.set_model_status("m0", "inactive", revendique_par_sonde=True)

    # 1 h plus tard (moins que l'intervalle de 2 h) : m0 est reporté.
    avancer(sonde.INTERVALLE_DEFAUT_S)
    cr = await sonde.executer_cycle()
    assert cr["testes"] == 9, "m0 (éteint) n'a pas été re-testé ce cycle"
    assert cr["reportes_backoff"] >= 1
    # Toujours dans la liste des éteints par la sonde : il sera re-testé.
    assert "m0" in sonde.modeles_eteints_par_la_sonde()


# ── Classement de la cause ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "erreur",
    [
        # Crédit épuisé — reproduit en direct sur api.anthropic.com.
        "Your credit balance is too low to access the Anthropic API. Please go "
        "to Plans & Billing to upgrade or purchase credits.",
        # Workspace non trusté — Claude CLI.
        'Claude CLI error (1): ... this workspace has not been trusted. Run '
        'Claude Code interactively here once and accept the trust dialog, or '
        'set projects["/opt/vromvrom-engine"].hasTrustDialogAccepted: true.',
        # Binaire CLI absent — Gemini *-cli.
        "[Errno 2] No such file or directory: '/usr/local/bin/gemini-cli'",
    ],
)
def test_erreurs_reelles_actionnables(erreur):
    """Les trois messages d'erreur RÉELS mesurés sont classés « actionnable »."""
    assert sonde.classer_erreur(erreur) == "actionnable"


def test_timeout_reseau_est_transitoire():
    """Une panne réseau passagère — la seule qui guérit toute seule — n'est PAS
    classée actionnable : elle mérite d'être retentée vite."""
    for erreur in (
        "TimeoutError: timed out after 20 seconds",
        "ConnectionError: [Errno -3] Temporary failure in name resolution",
        "cerise inconnue qui ne ressemble à rien de connu",
        "",
        None,
    ):
        assert sonde.classer_erreur(erreur) == "transitoire", erreur


def test_intervalle_backoff_progression_et_plafond():
    """Progression géométrique depuis l'heure, plafonnée à une journée."""
    assert sonde.intervalle_backoff(sonde.SEUIL_ECHECS) == sonde.INTERVALLE_DEFAUT_S
    assert sonde.intervalle_backoff(4) == 2 * sonde.INTERVALLE_DEFAUT_S
    assert sonde.intervalle_backoff(5) == 4 * sonde.INTERVALLE_DEFAUT_S
    # 112 échecs (cas prod) : plafonné à une journée, pas à 2^109 heures.
    assert sonde.intervalle_backoff(112) == sonde.PLAFOND_BACKOFF_S
    assert sonde.PLAFOND_BACKOFF_S == 24 * 3600


# ── Alerte-une-fois ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cause_actionnable_signalee_une_fois(monkeypatch, caplog):
    """
    Une cause actionnable produit UN message clair, pas un par cycle : le
    marqueur `cause_actionnable_signalee` est posé à la première détection et
    le message n'est pas re-loggué tant que la cause ne change pas.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="core.model_health_probe")
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    erreur_credit = "Your credit balance is too low to access the Anthropic API."
    _brancher(monkeypatch, catalogue, muets={"m0"}, erreur=erreur_credit)
    avancer = _horloge_avancante(monkeypatch, 3_000_000.0)

    # m0 actif, en panne actionnable (crédit).
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")[sonde._COLONNE_ALERTE] == "credit"
    nb_credit = sum("ACTIONNABLE (credit)" in r.message for r in caplog.records)
    assert nb_credit == 1

    # Cycles suivants, même cause : AUCUN nouveau message.
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    nb_credit = sum("ACTIONNABLE (credit)" in r.message for r in caplog.records)
    assert nb_credit == 1, "la même cause ne doit pas être re-signalée à chaque cycle"


@pytest.mark.asyncio
async def test_cause_qui_change_est_re_signalee(monkeypatch, caplog):
    """
    Quand la cause actionnable change (crédit → binaire absent), un nouveau
    message est émis — c'est une information nouvelle pour l'humain.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="core.model_health_probe")
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    _brancher(
        monkeypatch, catalogue, muets={"m0"},
        erreur="Your credit balance is too low to access the Anthropic API.",
    )
    avancer = _horloge_avancante(monkeypatch, 4_000_000.0)
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")[sonde._COLONNE_ALERTE] == "credit"

    # La cause change : binaire absent.
    _brancher(
        monkeypatch, catalogue, muets={"m0"},
        erreur="[Errno 2] No such file or directory: '/usr/local/bin/gemini-cli'",
    )
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")[sonde._COLONNE_ALERTE] == "binaire"
    nb_binaire = sum("ACTIONNABLE (binaire)" in r.message for r in caplog.records)
    assert nb_binaire == 1


@pytest.mark.asyncio
async def test_succes_efface_le_marqueur_d_alerte(monkeypatch):
    """
    Un succès répare la cause : le marqueur est effacé, donc une prochaine panne
    actionnable (même cause) sera re-signalée et non avalée.
    """
    catalogue = _FauxCatalogue([f"m{i}" for i in range(10)])
    erreur_credit = "Your credit balance is too low to access the Anthropic API."
    _brancher(monkeypatch, catalogue, muets={"m0"}, erreur=erreur_credit)
    avancer = _horloge_avancante(monkeypatch, 5_000_000.0)
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")[sonde._COLONNE_ALERTE] == "credit"

    # m0 répond : marqueur effacé.
    _brancher(monkeypatch, catalogue, muets=set())
    avancer(sonde.INTERVALLE_DEFAUT_S)
    await sonde.executer_cycle()
    assert sonde.lire_sante("m0")[sonde._COLONNE_ALERTE] is None


# ── Garde-fous inchangés ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_panne_globale_ne_desactive_toujours_rien(monkeypatch):
    """
    Garde-fou n°2 (inchangé) : quand >80 % du cycle échoue, la cause est locale
    (réseau, DNS, FAI) — on ne désactive RIEN.
    """
    modeles = [f"m{i}" for i in range(10)]
    catalogue = _FauxCatalogue(modeles)
    _brancher(monkeypatch, catalogue, muets=set(modeles))

    avancer = _horloge_avancante(monkeypatch, 6_000_000.0)
    for _ in range(sonde.SEUIL_ECHECS + 2):
        avancer(sonde.INTERVALLE_DEFAUT_S)
        cr = await sonde.executer_cycle()

    assert cr["gele"] is True
    assert cr["desactives"] == []
    assert all(statut == "active" for statut in catalogue.statuts.values())


@pytest.mark.asyncio
async def test_plancher_de_modeles_actifs_tient_toujours(monkeypatch):
    """
    Garde-fou n°3 (inchangé) : la sonde ne descend jamais sous
    MIN_MODELES_ACTIFS, même si un modèle de plus tombe en panne.
    """
    modeles = [f"m{i}" for i in range(6)]
    catalogue = _FauxCatalogue(modeles)
    _brancher(monkeypatch, catalogue, muets={"m0"})

    avancer = _horloge_avancante(monkeypatch, 7_000_000.0)
    for _ in range(sonde.SEUIL_ECHECS + 1):
        avancer(sonde.INTERVALLE_DEFAUT_S)
        cr = await sonde.executer_cycle()

    actifs = sum(1 for s in catalogue.statuts.values() if s == "active")
    assert actifs >= sonde.MIN_MODELES_ACTIFS
    assert cr["desactives"] == [], "le plancher n'a pas retenu l'extinction"
