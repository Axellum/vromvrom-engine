"""
tests/unit/test_agent_trace.py — L'agent qui déclenche un appel LLM est tracé (#T296).

Avant ce correctif, `token_usage.agent_name` était NULL sur 100 % des lignes
(54/54 mesurées le 11/08 sur 24 h) : `record_usage()` n'avait pas le paramètre,
et les providers n'ont de toute façon pas connaissance de l'agent.

Les tests de bout en bout écrivent dans une base créée par le VRAI schéma
(`runtime_db._init_schema` via `override_db_path`), jamais par un `CREATE TABLE`
écrit à la main : un test qui fabriquerait son propre schéma validerait son
hypothèse de schéma au lieu du schéma réel.
"""
import asyncio
import sqlite3

import pytest

from agents.base_agent import BaseAgent
from core.agent_trace import (
    lire_agent_courant,
    poser_agent_courant,
    restaurer_agent_courant,
)
from core.state import StateUpdate, TaskPayload


class _AgentTemoin(BaseAgent):
    """Agent minimal qui rapporte l'agent vu par la ContextVar pendant son invoke."""

    def __init__(self, name="temoin", enfant=None):
        super().__init__(name=name, system_prompt="")
        self.enfant = enfant
        self.vu_pendant = None
        self.vu_apres_enfant = None

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        self.vu_pendant = lire_agent_courant()
        if self.enfant is not None:
            await self.enfant.invoke(payload)
            # Après le retour de l'enfant, le parent doit avoir retrouvé son nom.
            self.vu_apres_enfant = lire_agent_courant()
        return StateUpdate(agent_name=self.name, status="success", result_data="ok")


def _payload():
    return TaskPayload(task_objective="objectif de test")


class TestContextVarAgent:
    """Le mécanisme de trace lui-même."""

    def test_hors_agent_la_valeur_est_none(self):
        """Un appel LLM hors agent (routeur, script) n'invente pas de nom."""
        assert lire_agent_courant() is None

    def test_pose_et_restauration(self):
        jeton = poser_agent_courant("planner")
        assert lire_agent_courant() == "planner"
        restaurer_agent_courant(jeton)
        assert lire_agent_courant() is None

    @pytest.mark.asyncio
    async def test_deux_taches_concurrentes_ne_se_melangent_pas(self):
        """Le DAG lance chaque nœud dans sa propre Task : les noms ne doivent pas fuiter."""
        vus = {}

        async def _agent(nom):
            poser_agent_courant(nom)
            await asyncio.sleep(0)  # laisse l'autre tâche s'exécuter entre-temps
            vus[nom] = lire_agent_courant()

        await asyncio.gather(_agent("executor"), _agent("reviewer"))

        assert vus == {"executor": "executor", "reviewer": "reviewer"}
        assert lire_agent_courant() is None, "la tâche mère ne doit pas être polluée"


class TestEnveloppeBaseAgent:
    """La pose est automatique : aucun agent, aucun appelant n'a à y penser."""

    @pytest.mark.asyncio
    async def test_invoke_pose_le_nom_de_l_agent(self):
        agent = _AgentTemoin(name="executor")
        await agent.invoke(_payload())
        assert agent.vu_pendant == "executor"

    @pytest.mark.asyncio
    async def test_le_contexte_est_rendu_apres_invoke(self):
        await _AgentTemoin(name="executor").invoke(_payload())
        assert lire_agent_courant() is None

    @pytest.mark.asyncio
    async def test_agent_imbrique_le_parent_retrouve_son_nom(self):
        """healing.py et review_loop.py invoquent un agent DEPUIS un agent.

        Sans restauration par jeton, le parent finirait ses propres appels LLM
        sous le nom de son enfant — une colonne qui ment coûte plus cher qu'une
        colonne vide (#T294).
        """
        enfant = _AgentTemoin(name="planner")
        parent = _AgentTemoin(name="healer", enfant=enfant)

        await parent.invoke(_payload())

        assert enfant.vu_pendant == "planner"
        assert parent.vu_pendant == "healer"
        assert parent.vu_apres_enfant == "healer"

    @pytest.mark.asyncio
    async def test_sous_classe_heritant_de_invoke_n_est_pas_enveloppee_deux_fois(self):
        class _Derive(_AgentTemoin):
            pass  # n'override pas invoke : réutilise l'enveloppe du parent

        agent = _Derive(name="derive")
        await agent.invoke(_payload())
        assert agent.vu_pendant == "derive"
        assert lire_agent_courant() is None


class TestEcritureEnBase:
    """Bout en bout : le nom arrive réellement dans token_usage."""

    @pytest.mark.asyncio
    async def test_record_usage_renseigne_agent_name(self, tmp_path):
        from core import runtime_db

        db = tmp_path / "trace.db"
        runtime_db.override_db_path(str(db))
        runtime_db.get_connection().close()  # déclenche _init_schema (schéma réel)

        from core.token_tracker import record_usage

        class _AgentQuiConsomme(BaseAgent):
            async def invoke(self, payload: TaskPayload) -> StateUpdate:
                record_usage("gemma-4-31b", 100, 50, session_id="s_t296")
                return StateUpdate(agent_name=self.name, status="success", result_data="ok")

        await _AgentQuiConsomme(name="executor", system_prompt="").invoke(_payload())

        conn = sqlite3.connect(str(db))
        lignes = conn.execute(
            "SELECT model, agent_name, session_id FROM token_usage"
        ).fetchall()
        conn.close()

        assert lignes == [("gemma-4-31b", "executor", "s_t296")], lignes

    @pytest.mark.asyncio
    async def test_hors_agent_agent_name_reste_null(self, tmp_path):
        """Un appel du routeur ou d'un script ne doit pas être attribué à un agent."""
        from core import runtime_db

        db = tmp_path / "hors_agent.db"
        runtime_db.override_db_path(str(db))
        runtime_db.get_connection().close()

        from core.token_tracker import record_usage

        record_usage("gemma-4-31b", 10, 5, session_id="s_hors_agent")

        conn = sqlite3.connect(str(db))
        (agent_name,) = conn.execute("SELECT agent_name FROM token_usage").fetchone()
        conn.close()

        assert agent_name is None
