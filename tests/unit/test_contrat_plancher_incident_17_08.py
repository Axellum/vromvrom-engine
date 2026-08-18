"""
test_contrat_plancher_incident_17_08.py — Le contrat approuvé devient un plancher.

Rejoue l'incident du 17/08/2026 (journal à l'appui) : un lot dont le livrable
(`tests/unit/test_regression_prod_17_08.py`) n'a JAMAIS été créé a été déclaré
satisfait, parce que :

1. le Planner du round correctif avait posé un NOUVEAU contrat portant sur deux
   fichiers du dépôt qui existaient depuis des mois ;
2. `ReviewLoop._criteres_du_plan()` prenait le contrat le plus RÉCENT de
   l'historique — le contrat approuvé, toujours présent mais plus bas dans la
   pile, n'était jamais consulté ;
3. la porte déterministe (contrat satisfait → `approved = True`) a donc été
   ouverte avec une clé refabriquée par celui qu'on juge.

Trois verrous posés par ce correctif, et testés ici :
- PLANCHER : `_criteres_du_plan()` rend l'UNION des contrats de l'historique ;
  un plan correctif peut enrichir le contrat, jamais le remplacer ni
  l'affaiblir ;
- ANTI-TAUTOLOGIE : l'état des critères fichier est constaté À LA POSE du plan
  (`constater_etat_initial`) ; un critère déjà vrai avant le travail est
  « satisfait d'avance » et ne compte ni comme preuve ni comme verdict — un
  contrat entièrement satisfait d'avance ne peut plus absoudre un DAG en erreur ;
- VISIBILITÉ : chaque verdict journalise la provenance des critères évalués
  (plancher ou plan correctif) et les substitutions appliquées.

Arbitrage retenu (documenté dans la PR) : un critère du plancher devenu
impossible peut être remplacé par une SUBSTITUTION DÉCLARÉE, tracée et
journalisée — jamais par un remplacement silencieux.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import hitl_store, runtime_db
from core.acceptance_contract import (
    CLE_CONTRAT,
    CLE_ETAT_INITIAL,
    CLE_SUBSTITUTIONS,
    Critere,
    RapportContrat,
    ResultatCritere,
    constater_etat_initial,
    union_contrats,
    verifier_contrat,
)
from core.review_loop import ReviewLoop
from core.state import GlobalState, StateUpdate


@pytest.fixture(autouse=True)
def _base_isolee(tmp_path):
    """Chaque test écrit dans sa propre base runtime (motif des tests HITL)."""
    ancien = runtime_db.get_db_path()
    runtime_db.override_db_path(str(tmp_path / "runtime_test.db"))
    runtime_db.get_connection().close()
    yield
    runtime_db.override_db_path(ancien)


@pytest.fixture
def espace(tmp_path, monkeypatch):
    """Déplace la frontière d'espace de travail sur un dossier temporaire."""
    racine = os.path.realpath(str(tmp_path))
    monkeypatch.setattr("tools.system._WORKSPACE_ROOT", racine)
    return racine


class _EngineMock:
    def __init__(self):
        self.state = GlobalState(session_id="sess_plancher")
        self._history_lock = asyncio.Lock()
        self.agents = {}


def _poser_contrat(engine, criteres, etat_initial=None, substitutions=None, agent="planner"):
    """Ajoute à l'historique un StateUpdate porteur de contrat, comme le Planner."""
    metadata = {CLE_CONTRAT: criteres}
    if etat_initial is not None:
        metadata[CLE_ETAT_INITIAL] = etat_initial
    if substitutions:
        metadata[CLE_SUBSTITUTIONS] = substitutions
    engine.state.history.append(StateUpdate(
        agent_name=agent, status="success", metadata=metadata,
    ))


FICHIER_LIVRABLE = "test_regression_prod_17_08.py"


def _critere_livrable_absent(espace) -> dict:
    """Le critère du contrat approuvé : le livrable doit exister. Le chemin
    est absolu et DANS l'espace de travail, pour que « absent » soit un échec
    net (et non « hors espace » qui serait non vérifiable)."""
    return {"type": "fichier_existe", "valeur": os.path.join(espace, FICHIER_LIVRABLE),
            "description": "Le test de régression est créé"}


# ── LE test central : rejouer la scène du 17/08 ─────────────────────────────


class TestSceneDu17Aout:
    """
    Historique : (a) contrat approuvé exigeant un fichier ABSENT, puis
    (b) contrat correctif composé uniquement de critères satisfaits d'avance
    sur des fichiers préexistants. Le verdict DOIT être un échec, et le
    journal doit nommer le critère hérité qui a échoué. C'est exactement le
    cas qui avait produit un faux succès.
    """

    def test_le_plancher_survit_au_contrat_correctif_tautologique(self, espace, monkeypatch, caplog):
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        engine = _EngineMock()

        # (a) Le contrat approuvé : le livrable doit exister. Il n'existe pas.
        _poser_contrat(engine, [_critere_livrable_absent(espace)], etat_initial={0: False})

        # (b) Le contrat correctif fautif : deux fichiers du dépôt qui
        # existent depuis des mois, constatés satisfaits d'avance à leur pose.
        preexistant_a = os.path.join(espace, "ha_state_query.py")
        preexistant_b = os.path.join(espace, "vocal_host.py")
        open(preexistant_a, "w").close()
        open(preexistant_b, "w").close()
        correctif = [
            {"type": "fichier_existe", "valeur": preexistant_a,
             "description": "Vérifie que le fichier ha_state_query.py est bien accessible."},
            {"type": "fichier_existe", "valeur": preexistant_b,
             "description": "Vérifie que le fichier vocal_host.py est bien accessible."},
        ]
        _poser_contrat(engine, correctif, etat_initial={0: True, 1: True})

        boucle = ReviewLoop(engine)
        import logging
        with caplog.at_level(logging.INFO, logger="core.review_loop"):
            verdict = asyncio.run(boucle.contrat_seul())

        # Avant le correctif, cette scène rendait True (faux succès).
        assert verdict is False
        # Le critère hérité qui a échoué est NOMMÉ dans le journal.
        assert any(FICHIER_LIVRABLE in r.message for r in caplog.records), (
            "le journal doit nommer le critère du plancher en échec"
        )
        assert any("plancher" in r.message for r in caplog.records), (
            "la trace doit dire que le critère provient du contrat d'origine"
        )

    def test_les_criteres_tautologiques_sont_reels_mais_exclus_du_verdict(self, espace):
        """Contre-épreuve mécanique : les critères correctifs passent bien,
        seul le plancher échoue — c'est lui qui doit trancher."""
        preexistant = os.path.join(espace, "deja_la.py")
        open(preexistant, "w").close()

        union = union_contrats([
            [_critere_livrable_absent(espace)],
            [{"type": "fichier_existe", "valeur": preexistant}],
        ])
        rapport = verifier_contrat(union, etats_initiaux={1: True})

        assert len(rapport.resultats) == 2
        assert len(rapport.verifiables) == 1          # le critère d'avance est exclu
        assert rapport.echecs[0].critere.valeur.endswith(FICHIER_LIVRABLE)
        assert rapport.satisfait is False


# ── Le cas légitime doit survivre : enrichir, oui ────────────────────────────


class TestEnrichissementLegitime:
    def test_un_plan_correctif_qui_ajoute_un_critere_reste_un_succes(self, espace, monkeypatch):
        """Contrat d'origine + un critère supplémentaire posé par la
        correction, tous deux produits par le lot → succès, sans appel LLM."""
        monkeypatch.setattr(ReviewLoop, "_has_modified_files", lambda self: True)
        livrable = os.path.join(espace, "livrable.py")
        fichier_correction = os.path.join(espace, "correction.py")
        open(livrable, "w").close()
        open(fichier_correction, "w").close()

        engine = _EngineMock()
        # Le lot a créé le fichier depuis la pose du contrat d'origine :
        # le constat d'avant-travail disait « absent ».
        _poser_contrat(engine, [{"type": "fichier_existe", "valeur": livrable}],
                       etat_initial={0: False})
        _poser_contrat(engine, [{"type": "fichier_existe", "valeur": fichier_correction}],
                       etat_initial={0: False})

        boucle = ReviewLoop(engine)
        reviewer = MagicMock()
        reviewer.invoke = AsyncMock()
        engine.agents["reviewer"] = reviewer

        assert asyncio.run(boucle.run_review("objectif")) is True
        reviewer.invoke.assert_not_called()  # la porte déterministe tranche seule

    def test_l_union_contient_tous_les_criteres(self):
        engine = _EngineMock()
        _poser_contrat(engine, [{"type": "commande", "valeur": "pytest"}])
        _poser_contrat(engine, [{"type": "fichier_existe", "valeur": "ajout.py"}])

        criteres = ReviewLoop(engine)._criteres_du_plan()

        valeurs = {c["valeur"] for c in criteres}
        assert valeurs == {"pytest", "ajout.py"}, "l'ancien critère a disparu de l'union"


# ── Anti-tautologie : un contrat satisfait d'avance ne prouve rien ──────────


class TestContratSatisfaitDAvance:
    def test_un_contrat_entierement_satisfait_d_avance_n_absout_pas(self, espace, monkeypatch):
        """DAG en erreur + contrat correctif dont chaque critère était vrai
        avant le lot : non déterminable → l'appelant garde son comportement
        d'avant (l'erreur du DAG n'est PAS absoute)."""
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        preexistant = os.path.join(espace, "ancien.py")
        open(preexistant, "w").close()

        engine = _EngineMock()
        _poser_contrat(engine, [{"type": "fichier_existe", "valeur": preexistant}],
                       etat_initial={0: True})

        boucle = ReviewLoop(engine)
        assert asyncio.run(boucle.contrat_seul()) is None

    def test_un_critere_satisfait_d_avance_ne_compte_pas_comme_preuve(self, espace):
        preexistant = os.path.join(espace, "temoin.py")
        open(preexistant, "w").close()

        rapport = verifier_contrat(
            [{"type": "fichier_existe", "valeur": preexistant}],
            etats_initiaux={0: True},
        )
        assert rapport.determinable is False
        assert rapport.satisfait is False
        # Le constat reste VISIBLE dans le rapport, pas silencieux.
        assert len(rapport.resultats_satisfaits_d_avance) == 1
        assert "satisfait d'avance" in rapport.resume()

    def test_sans_constat_le_critere_reste_une_preuve(self, espace):
        """Le doute rend le jugement plus strict, jamais plus permissif :
        sans constat d'état initial, le critère est évalué normalement."""
        preexistant = os.path.join(espace, "temoin2.py")
        open(preexistant, "w").close()

        rapport = verifier_contrat([{"type": "fichier_existe", "valeur": preexistant}])
        assert rapport.satisfait is True

    def test_constater_etat_initial_ne_constate_que_les_fichiers(self, espace):
        existant = os.path.join(espace, "la.py")
        open(existant, "w").close()

        etats = constater_etat_initial([
            {"type": "fichier_existe", "valeur": existant},
            {"type": "fichier_existe", "valeur": os.path.join(espace, "pas_la.py")},
            {"type": "commande", "valeur": "pytest"},  # non constatable : absent
        ])
        assert etats == {0: True, 1: False}


# ── Le contrat restauré par la reprise sert de plancher ──────────────────────


def _dag_serialise() -> list[dict]:
    return [{"task_objective": "Créer le livrable", "task_id": "t1", "depends_on": [],
             "available_tools": ["write_file"], "relevant_context": "", "metadata": {},
             "status": "pending", "assigned_agent": None, "result_summary": None}]


class TestRepriseEtPlancher:
    @pytest.mark.asyncio
    async def test_le_contrat_restaure_est_le_plancher_de_la_reprise(self, espace, monkeypatch):
        """La reprise rejoue le DAG approuvé : le contrat restauré depuis
        `hitl_pending_approvals` doit être celui qui sert de plancher, et un
        contrat correctif tautologique posé pendant le rejeu ne doit pas
        l'évincer."""
        from core.engine import Engine
        from services.approval_resume_service import reprendre_apres_approbation

        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)

        hitl_store.enregistrer_demande(
            request_id="r_plancher", session_id="s_reprise", dag_tasks=_dag_serialise(),
            description="", criteres=[_critere_livrable_absent(espace)],
        )
        hitl_store.marquer_decision("r_plancher", approuve=True)

        captures: dict = {}

        def _faux_moteur(session_id, objectif, config):
            engine = Engine(session_id=session_id)
            bridge = MagicMock()
            bridge.start = AsyncMock()
            bridge.stop = AsyncMock()
            captures["engine"] = engine
            return engine, MagicMock(), bridge, MagicMock()

        monkeypatch.setattr("services.pipeline_service.monter_moteur", _faux_moteur)
        monkeypatch.setattr(
            "core.execution_budget.ExecutionBudget.from_config",
            classmethod(lambda cls, session_id: MagicMock(max_tokens=1000)),
        )

        async def _faux_dag(self, dag_tasks, objectif, max_session_tokens, budget=None):
            # Le rejeu échoue et un plan correctif pose un contrat tautologique
            # sur un fichier préexistant — exactement la scène du 17/08.
            preexistant = os.path.join(espace, "ancien_module.py")
            open(preexistant, "w").close()
            self.state.history.append(StateUpdate(
                agent_name="planner", status="success",
                metadata={
                    CLE_CONTRAT: [{"type": "fichier_existe", "valeur": preexistant}],
                    CLE_ETAT_INITIAL: {0: True},
                },
            ))
            captures["verdict_contrat"] = await self._review_loop.contrat_seul()
            return {"t1": "error"}, True

        monkeypatch.setattr(Engine, "executer_dag_et_controles", _faux_dag)

        res = await reprendre_apres_approbation("r_plancher")

        engine = captures["engine"]
        # 1. Le contrat restauré est en TÊTE d'historique : c'est le plancher.
        restaure = engine.state.history[0]
        assert restaure.metadata[CLE_CONTRAT] == [_critere_livrable_absent(espace)]
        # Le livrable n'existait pas à la reprise : pas « satisfait d'avance ».
        assert restaure.metadata[CLE_ETAT_INITIAL] == {0: False}
        # 2. Le verdict du contrat est un ÉCHEC malgré le contrat tautologique.
        assert captures["verdict_contrat"] is False
        # 3. La reprise ne déclare pas un faux succès.
        assert res["status"] == "error"

    @pytest.mark.asyncio
    async def test_le_plancher_est_la_premiere_entree_meme_apres_plusieurs_correctifs(self):
        engine = _EngineMock()
        _poser_contrat(engine, [{"type": "commande", "valeur": "pytest"}])
        _poser_contrat(engine, [{"type": "commande", "valeur": "ruff check core/"}])
        _poser_contrat(engine, [{"type": "commande", "valeur": "mypy core/"}])

        entrees = ReviewLoop(engine)._entrees_contrat()
        assert entrees[0]["criteres"][0]["valeur"] == "pytest", (
            "le plancher doit rester le premier contrat posé, quoi qu'il arrive ensuite"
        )


# ── Arbitrage : substitution déclarée, jamais silencieuse ────────────────────


class TestSubstitutionTracee:
    def test_une_substitution_valide_remplace_le_critere_du_plancher(self, espace, monkeypatch, caplog):
        """Le plan correctif a légitimement changé de chemin : il DÉCLARE la
        substitution, elle est journalisée, et seul le remplaçant est évalué."""
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        nouveau_fichier = os.path.join(espace, "livrable_renomme.py")
        open(nouveau_fichier, "w").close()

        engine = _EngineMock()
        _poser_contrat(engine, [_critere_livrable_absent(espace)], etat_initial={0: False})
        _poser_contrat(
            engine,
            [{"type": "fichier_existe", "valeur": nouveau_fichier}],
            etat_initial={0: False},
            substitutions=[{
                "origine": _critere_livrable_absent(espace),
                "remplacant": {"type": "fichier_existe", "valeur": nouveau_fichier},
                "motif": "le correctif a choisi un autre nom de fichier",
            }],
        )

        import logging
        with caplog.at_level(logging.WARNING, logger="core.review_loop"):
            verdict = asyncio.run(ReviewLoop(engine).contrat_seul())

        assert verdict is True
        assert any("SUBSTITUTION TRACÉE" in r.message for r in caplog.records)

    def test_une_substitution_sans_declaration_n_existe_pas(self, espace, monkeypatch):
        """Contre-épreuve du 17/08 : un plan correctif qui pose simplement un
        AUTRE critère sans le déclarer comme substitution ne remplace rien —
        le critère du plancher reste évalué et fait échouer le contrat."""
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        autre_fichier = os.path.join(espace, "autre.py")
        open(autre_fichier, "w").close()

        engine = _EngineMock()
        _poser_contrat(engine, [_critere_livrable_absent(espace)], etat_initial={0: False})
        _poser_contrat(engine, [{"type": "fichier_existe", "valeur": autre_fichier}],
                       etat_initial={0: False})  # pas de déclaration de substitution

        assert asyncio.run(ReviewLoop(engine).contrat_seul()) is False

    def test_une_substitution_vers_un_critere_satisfait_d_avance_est_refusee(self, espace, monkeypatch):
        """Le remplaçant était déjà vrai avant le travail : substitution
        refusée, le critère du plancher reste évalué (et échoue)."""
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        preexistant = os.path.join(espace, "deja_present.py")
        open(preexistant, "w").close()

        engine = _EngineMock()
        _poser_contrat(engine, [_critere_livrable_absent(espace)], etat_initial={0: False})
        _poser_contrat(
            engine,
            [{"type": "fichier_existe", "valeur": preexistant}],
            etat_initial={0: True},
            substitutions=[{
                "origine": _critere_livrable_absent(espace),
                "remplacant": {"type": "fichier_existe", "valeur": preexistant},
            }],
        )

        assert asyncio.run(ReviewLoop(engine).contrat_seul()) is False

    def test_substitution_hors_plancher_ignoree(self, espace):
        """Seul un critère du contrat d'origine peut être substitué : une
        déclaration qui cible autre chose est écartée."""
        engine = _EngineMock()
        _poser_contrat(engine, [_critere_livrable_absent(espace)], etat_initial={0: False})
        fantome = {"type": "fichier_existe", "valeur": "inexistant_nulle_part.py"}
        _poser_contrat(
            engine,
            [{"type": "commande", "valeur": "pytest"}],
            substitutions=[{"origine": fantome, "remplacant": {"type": "commande", "valeur": "pytest"}}],
        )

        _criteres, _etats, substitutions = ReviewLoop(engine)._contrat_complet()
        assert substitutions == []


# ── Le Planner constate l'état initial à la pose du plan ────────────────────


def _plan_minimal(criteres, substitutions=None):
    reponse = {
        "plan": [{"task_id": "t1", "objective": "Créer le livrable",
                  "target_agent": "executor", "model_tier": "leger", "depends_on": []}],
        "criteres_acceptation": criteres,
    }
    if substitutions is not None:
        reponse["substitutions"] = substitutions
    return reponse


def _lancer_planner(monkeypatch, reponse, espace=None):
    from agents.planner import PlannerAgent
    from core.state import TaskPayload

    async def _generate_structured_async(system_prompt, user_prompt, schema, **kw):
        return reponse

    provider = MagicMock()
    provider.generate_structured_async = _generate_structured_async
    gateway = MagicMock()
    gateway.get_provider.return_value = provider
    gateway.get_provider_for_tier.return_value = ("tier-fort", provider)

    planner = PlannerAgent(llm_gateway=gateway, provider_name="fort")
    return asyncio.run(planner.invoke(TaskPayload(
        task_objective="objectif", metadata={"session_id": "s_planner"},
    )))


class TestPlannerConstateLEtatInitial:
    def test_le_planner_joint_le_constat_d_avant_travail(self, espace, monkeypatch):
        existant = os.path.join(espace, "present.py")
        open(existant, "w").close()
        absent = os.path.join(espace, "a_creer.py")

        update = _lancer_planner(monkeypatch, _plan_minimal([
            {"type": "fichier_existe", "valeur": existant},
            {"type": "fichier_existe", "valeur": absent},
        ]))

        assert update.status == "success"
        assert update.metadata[CLE_ETAT_INITIAL] == {0: True, 1: False}

    def test_le_planner_transporte_les_substitutions_declarees(self, espace, monkeypatch):
        declaration = {
            "origine": _critere_livrable_absent(espace),
            "remplacant": {"type": "fichier_existe", "valeur": os.path.join(espace, "ailleurs.py")},
            "motif": "changement de chemin décidé par la correction",
        }
        update = _lancer_planner(
            monkeypatch,
            _plan_minimal(
                [{"type": "fichier_existe", "valeur": os.path.join(espace, "ailleurs.py")}],
                substitutions=[declaration],
            ),
        )

        assert update.metadata[CLE_SUBSTITUTIONS] == [declaration]

    def test_le_schema_du_planner_expose_les_substitutions(self):
        """Le champ `substitutions` est déclaré au schéma : c'est le seul
        canal par lequel un remplacement de critère peut advenir."""
        from agents.planner import PlannerAgent
        from core.state import TaskPayload

        captures = {}

        async def _generate_structured_async(system_prompt, user_prompt, schema, **kw):
            captures["schema"] = schema
            return {"plan": [], "criteres_acceptation": []}

        provider = MagicMock()
        provider.generate_structured_async = _generate_structured_async
        gateway = MagicMock()
        gateway.get_provider.return_value = provider
        gateway.get_provider_for_tier.return_value = ("tier-fort", provider)

        planner = PlannerAgent(llm_gateway=gateway, provider_name="fort")
        asyncio.run(planner.invoke(TaskPayload(
            task_objective="objectif", metadata={"session_id": "s_schema"},
        )))

        assert "substitutions" in captures["schema"]["properties"]
        # La clé reste facultative : un plan qui ne substitue rien laisse [].
        assert "substitutions" not in captures["schema"]["required"]


# ── Sémantique du rapport avec critères satisfaits d'avance ─────────────────


class TestRapportAvecEtatInitial:
    def _res(self, satisfait, d_avance=False):
        return ResultatCritere(
            Critere("commande", "pytest", satisfait_d_avance=d_avance), satisfait, "détail",
        )

    def test_seul_critere_satisfait_d_avance_ne_tranche_pas(self):
        rapport = RapportContrat([self._res(True, d_avance=True)])
        assert rapport.determinable is False
        assert rapport.satisfait is False

    def test_une_vraie_preuve_suffit_meme_avec_un_critere_d_avance(self):
        rapport = RapportContrat([self._res(True, d_avance=True), self._res(True)])
        assert rapport.determinable is True
        assert rapport.satisfait is True

    def test_echec_satisfait_d_avance_bloque_comme_regression(self):
        """Un critère vrai avant le lot qui échoue maintenant est une
        régression : elle bloque le verdict, tout en restant identifiée comme
        « satisfait d'avance » dans le rapport."""
        rapport = RapportContrat([self._res(False, d_avance=True), self._res(True)])
        assert rapport.satisfait is False
        assert len(rapport.echecs_satisfaits_d_avance) == 1
        assert "satisfait d'avance" in rapport.resume()

    def test_un_echec_reel_invalide_meme_avec_des_criteres_d_avance(self):
        rapport = RapportContrat([self._res(True, d_avance=True), self._res(False)])
        assert rapport.satisfait is False
