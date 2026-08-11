"""
test_contrat_sur_dag_echoue.py — Les deux verrous de #T253, corrigés.

Mesure du 10/08 en prod : 7 plans, **0 critère d'acceptation**, **0 revue lancée**.
Deux causes distinctes, deux correctifs, deux séries de tests :

1. le champ `criteres_acceptation` était OPTIONNEL au schéma du Planner — un
   modèle ne renseigne de façon fiable que ce que `required` impose ;
2. `core/engine.py` ne lançait la revue que si le DAG s'était terminé SANS
   erreur — or c'est justement quand une branche échoue qu'une preuve mécanique
   tranche mieux qu'un avis.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.acceptance_contract import Critere, RapportContrat, ResultatCritere
from core.review_loop import ReviewLoop
from core.state import GlobalState, StateUpdate


class _EngineMock:
    def __init__(self):
        self.state = GlobalState(session_id="sess_contrat_echec")
        self._history_lock = asyncio.Lock()
        self.agents = {}


def _boucle(criteres, rapport, monkeypatch):
    engine = _EngineMock()
    if criteres is not None:
        engine.state.history.append(StateUpdate(
            agent_name="planner", status="success",
            metadata={"contrat_acceptation": criteres},
        ))
    boucle = ReviewLoop(engine)
    if rapport is not None:
        monkeypatch.setattr("core.review_loop.verifier_contrat", lambda c, **kw: rapport)
    return boucle


def _rapport(*verdicts):
    return RapportContrat([
        ResultatCritere(Critere("commande", "pytest", description="les tests passent"), v, "détail")
        for v in verdicts
    ])


CRITERES = [{"type": "commande", "valeur": "pytest"}]


class TestContratSeul:
    """Gap 2 : le contrat est évalué même quand le DAG a échoué."""

    def test_contrat_satisfait_sauve_l_objectif(self, monkeypatch):
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        boucle = _boucle(CRITERES, _rapport(True, True), monkeypatch)

        assert asyncio.run(boucle.contrat_seul()) is True

    def test_contrat_en_echec_confirme_l_erreur(self, monkeypatch):
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        boucle = _boucle(CRITERES, _rapport(True, False), monkeypatch)

        assert asyncio.run(boucle.contrat_seul()) is False

    def test_sans_criteres_aucun_avis(self, monkeypatch):
        """Sans contrat, l'appelant doit garder son comportement d'avant."""
        boucle = _boucle(None, None, monkeypatch)
        assert asyncio.run(boucle.contrat_seul()) is None

    def test_contrat_non_determinable_aucun_avis(self, monkeypatch):
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        boucle = _boucle(CRITERES, _rapport(None, None), monkeypatch)
        assert asyncio.run(boucle.contrat_seul()) is None

    def test_aucun_appel_llm(self, monkeypatch):
        """Après un DAG en échec, payer un avis de complaisance n'a pas de sens."""
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        boucle = _boucle(CRITERES, _rapport(True), monkeypatch)
        reviewer = MagicMock()
        reviewer.invoke = AsyncMock()
        boucle._engine.agents["reviewer"] = reviewer

        asyncio.run(boucle.contrat_seul())

        reviewer.invoke.assert_not_called()

    def test_evenement_emis_avec_son_contexte(self, monkeypatch):
        monkeypatch.delenv("MOTEUR_CONTRAT_SUR_ECHEC", raising=False)
        boucle = _boucle(CRITERES, _rapport(True), monkeypatch)
        recus = []

        async def _on_event(nom, charge):
            recus.append((nom, charge))

        asyncio.run(boucle.contrat_seul(on_event=_on_event))

        assert recus[0][0] == "contract_checked"
        assert recus[0][1]["contexte"] == "dag_en_echec"
        assert recus[0][1]["satisfied"] is True

    @pytest.mark.parametrize("valeur", ["0", "false", "off", "no"])
    def test_kill_switch(self, monkeypatch, valeur):
        monkeypatch.setenv("MOTEUR_CONTRAT_SUR_ECHEC", valeur)
        boucle = _boucle(CRITERES, _rapport(True), monkeypatch)
        assert asyncio.run(boucle.contrat_seul()) is None


class TestAbsolutionGitApresContrat:
    """
    Bugbot High : `has_error=False` ne suffit pas — `_finalize_git` exige
    que TOUTES les tâches soient « success », sinon rollback Git.
    """

    def test_finalize_git_rollback_si_tache_encore_en_erreur(self):
        from core.engine import Engine

        engine = Engine(session_id="t253-git-rollback")
        captured = {}

        def fake(branch, success, sid, repo_path="."):
            captured["success"] = success
            return "ok"

        engine._finalize_git(
            "agent/run", False, {"ok": "success", "ko": "error"}, fake,
        )
        assert captured["success"] is False

    def test_finalize_git_merge_apres_absolution_des_statuts(self):
        from core.engine import Engine

        engine = Engine(session_id="t253-git-merge")
        captured = {}

        def fake(branch, success, sid, repo_path="."):
            captured["success"] = success
            return "ok"

        # Même logique que `_handle_dag_execution` quand contrat_seul → True
        tasks_status = {"ok": "success", "ko": "error", "bloquee": "blocked"}
        for tid, statut in list(tasks_status.items()):
            if statut in ("error", "blocked"):
                tasks_status[tid] = "success"

        engine._finalize_git("agent/run", False, tasks_status, fake)
        assert captured["success"] is True
        assert all(s == "success" for s in tasks_status.values())


class TestSchemaDuPlanner:
    """Gap 1 : le champ doit être exigé, sinon il n'est jamais produit."""

    def test_criteres_acceptation_est_obligatoire(self):
        """
        Le schéma est construit dans `invoke()` : on capture celui qui part
        réellement au provider, plutôt que de relire le fichier source.
        """
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
        asyncio.run(planner.invoke(TaskPayload(task_objective="objectif", metadata={"session_id": "s"})))

        schema = captures["schema"]
        assert "criteres_acceptation" in schema["required"], (
            "le champ est optionnel : mesuré en prod, un modèle ne le produit alors jamais"
        )
        # La liste VIDE reste valide : on impose la clé, pas l'invention de critères.
        assert schema["properties"]["criteres_acceptation"]["type"] == "array"


class TestContratSansModificationDeFichier:
    """
    Troisième verrou, trouvé en mesurant le 10/08 : `run_review` court-circuitait
    sur « aucun fichier modifié » AVANT d'atteindre le contrat. Ce raccourci a été
    écrit pour la revue de CODE — mais un contrat vérifie l'OBJECTIF, qui peut être
    atteint sans toucher un fichier (lire, calculer, répondre).
    """

    def _boucle_sans_modif(self, criteres, rapport, monkeypatch):
        boucle = _boucle(criteres, rapport, monkeypatch)
        boucle._engine.agents["reviewer"] = MagicMock()
        monkeypatch.setattr(boucle, "_has_modified_files", lambda: False)
        return boucle

    def test_contrat_satisfait_sans_fichier_modifie(self, monkeypatch):
        boucle = self._boucle_sans_modif(CRITERES, _rapport(True, True), monkeypatch)
        assert asyncio.run(boucle.run_review("objectif")) is True

    def test_contrat_en_echec_sans_fichier_modifie_rejette(self, monkeypatch):
        """Avant le correctif, ce cas renvoyait True : auto-approbation aveugle."""
        boucle = self._boucle_sans_modif(CRITERES, _rapport(True, False), monkeypatch)
        assert asyncio.run(boucle.run_review("objectif")) is False

    def test_sans_contrat_le_raccourci_reste_intact(self, monkeypatch):
        boucle = self._boucle_sans_modif(None, None, monkeypatch)
        assert asyncio.run(boucle.run_review("objectif")) is True

    def test_contrat_non_determinable_garde_le_raccourci(self, monkeypatch):
        boucle = self._boucle_sans_modif(CRITERES, _rapport(None), monkeypatch)
        assert asyncio.run(boucle.run_review("objectif")) is True

    def test_evenement_porte_son_contexte(self, monkeypatch):
        boucle = self._boucle_sans_modif(CRITERES, _rapport(True), monkeypatch)
        recus = []

        async def _on_event(nom, charge):
            recus.append((nom, charge))

        asyncio.run(boucle.run_review("objectif", on_event=_on_event))
        contrats = [c for n, c in recus if n == "contract_checked"]
        assert contrats and contrats[0]["contexte"] == "sans_modification_de_fichier"

    def test_aucun_appel_au_reviewer(self, monkeypatch):
        boucle = self._boucle_sans_modif(CRITERES, _rapport(True), monkeypatch)
        reviewer = MagicMock()
        reviewer.invoke = AsyncMock()
        boucle._engine.agents["reviewer"] = reviewer

        asyncio.run(boucle.run_review("objectif"))

        reviewer.invoke.assert_not_called()
