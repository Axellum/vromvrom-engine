"""
test_dag_node_tiers.py — Tier de modèle par rôle de nœud DAG (#T245).

Vérifie que le MapReduce n'hérite plus aveuglément du tier du parent :
- les tâches Map descendent sur le tier léger (panier gratuit/low-cost),
- le nœud Reduce monte sur le tier fort (l'arbitrage final),
- la précédence surcharge appelant > config.json > défauts,
- le kill-switch `MOTEUR_DAG_NODE_TIERS=0` et la valeur `herite` rétablissent
  exactement le comportement d'avant,
- et que **les deux chemins** (DAG actif et fallback synchrone) appliquent la
  même politique — c'est la divergence qui coûterait cher, pas la valeur du tier.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dag.node_tier_policy import (
    TIERS_DEFAUT,
    appliquer_tier_role,
    normaliser_tiers_du_plan,
    politique_active,
    tier_pour_role,
)
from core.dag_runner import DAGRunner
from core.state import GlobalState, StateUpdate, TaskPayload


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch):
    """Politique active par défaut et config.json ignorée (déterminisme)."""
    monkeypatch.delenv("MOTEUR_DAG_NODE_TIERS", raising=False)
    # Même lecture que la vraie fonction, sans le repli sur config.json du poste :
    # un `dag_node_tiers` posé localement ne doit pas faire varier les tests.
    monkeypatch.setattr(
        "core.dag.node_tier_policy._tiers_de_config",
        lambda config: (config or {}).get("dag_node_tiers") or {},
    )


class TestPolitiqueTier:
    """Résolution du tier par rôle."""

    def test_defauts_map_leger_reduce_fort(self):
        assert tier_pour_role("map") == "leger"
        assert tier_pour_role("reduce") == "fort"
        assert TIERS_DEFAUT == {"map": "leger", "reduce": "fort"}

    def test_role_inconnu_ne_force_rien(self):
        assert tier_pour_role("planner") is None

    def test_surcharge_appelant_prioritaire(self):
        meta = {"map_model_tier": "moyen", "reduce_model_tier": "moyen"}
        assert tier_pour_role("map", meta) == "moyen"
        assert tier_pour_role("reduce", meta) == "moyen"

    def test_config_prioritaire_sur_defaut(self, monkeypatch):
        monkeypatch.setattr(
            "core.dag.node_tier_policy._tiers_de_config",
            lambda config: {"map": "moyen", "reduce": "moyen"},
        )
        assert tier_pour_role("map") == "moyen"
        # …mais la surcharge de l'appelant reste au-dessus de la config
        assert tier_pour_role("map", {"map_model_tier": "leger"}) == "leger"

    def test_config_explicite_passee_en_parametre(self):
        config = {"dag_node_tiers": {"reduce": "moyen"}}
        assert tier_pour_role("reduce", None, config) == "moyen"
        assert tier_pour_role("map", None, config) == "leger"  # non déclaré → défaut

    def test_valeur_herite_retablit_heritage(self, monkeypatch):
        assert tier_pour_role("map", {"map_model_tier": "herite"}) is None
        monkeypatch.setattr(
            "core.dag.node_tier_policy._tiers_de_config",
            lambda config: {"reduce": "herite"},
        )
        assert tier_pour_role("reduce") is None

    def test_valeur_vide_ignoree(self):
        assert tier_pour_role("map", {"map_model_tier": "   "}) == "leger"

    @pytest.mark.parametrize("valeur", ["0", "false", "off", "no", "OFF"])
    def test_kill_switch(self, monkeypatch, valeur):
        monkeypatch.setenv("MOTEUR_DAG_NODE_TIERS", valeur)
        assert politique_active() is False
        assert tier_pour_role("map") is None
        assert tier_pour_role("reduce") is None


class TestApplicationMetadata:
    """Projection de la politique sur les metadata d'un payload."""

    def test_force_le_tier_et_trace_le_role(self):
        parent = {"model_tier": "fort", "target_agent": "executor"}
        meta = appliquer_tier_role("map", parent)
        assert meta["model_tier"] == "leger"
        assert meta["node_tier_role"] == "map"
        assert meta["target_agent"] == "executor"

    def test_ne_mute_pas_le_dict_parent(self):
        parent = {"model_tier": "fort"}
        appliquer_tier_role("map", parent)
        assert parent["model_tier"] == "fort"

    def test_kill_switch_preserve_l_heritage(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_NODE_TIERS", "0")
        meta = appliquer_tier_role("map", {"model_tier": "fort"})
        assert meta == {"model_tier": "fort"}
        assert "node_tier_role" not in meta

    def test_metadata_vide_accepte(self):
        assert appliquer_tier_role("reduce", None)["model_tier"] == "fort"


class _AgentCapteur:
    """Agent mocké qui mémorise les payloads reçus."""

    def __init__(self, name="executor"):
        self.name = name
        self.payloads = []

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        self.payloads.append(payload)
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=f"ok {payload.task_id}",
            metadata={},
        )


class _EngineMock:
    def __init__(self, agent):
        self.state = GlobalState(session_id="test_node_tiers")
        self._history_lock = asyncio.Lock()
        self.on_event = None
        self.context_manager = None
        self.agents = {"executor": agent}


class TestChemainsMapReduce:
    """Les deux chemins du DAGRunner appliquent la même politique."""

    def test_chemin_dag_maps_leger_reduce_fort(self):
        runner = DAGRunner(_EngineMock(_AgentCapteur()))
        parent = TaskPayload(
            task_objective="Analyser le corpus",
            metadata={"model_tier": "fort", "target_agent": "executor", "scope_level": 2},
            task_id="t1",
        )

        maps, reduce_p = runner._construire_payloads_mapreduce(
            parent, ["a", "b", "c"], "Fusionner", "mapreduce_t1", "t1"
        )

        assert len(maps) == 3
        assert [m.metadata["model_tier"] for m in maps] == ["leger"] * 3
        assert reduce_p.metadata["model_tier"] == "fort"
        # Le reste du contrat existant ne bouge pas
        assert reduce_p.depends_on == [m.task_id for m in maps]
        assert maps[0].metadata["scope_level"] == 2
        assert maps[0].metadata["parent_scope_id"] == "t1"
        assert maps[1].metadata["map_index"] == 1
        assert reduce_p.metadata["is_reduce"] is True

    def test_chemin_dag_parent_leger_fait_quand_meme_monter_le_reduce(self):
        runner = DAGRunner(_EngineMock(_AgentCapteur()))
        parent = TaskPayload(
            task_objective="Analyser",
            metadata={"model_tier": "leger", "target_agent": "executor"},
            task_id="t2",
        )

        maps, reduce_p = runner._construire_payloads_mapreduce(
            parent, ["a"], "", "mapreduce_t2", "t2"
        )

        assert maps[0].metadata["model_tier"] == "leger"
        assert reduce_p.metadata["model_tier"] == "fort"

    def test_chemin_fallback_applique_la_meme_politique(self):
        agent = _AgentCapteur()
        runner = DAGRunner(_EngineMock(agent))
        parent = TaskPayload(
            task_objective="Analyser",
            metadata={"model_tier": "fort", "target_agent": "executor"},
            task_id="t3",
        )

        resultat = asyncio.run(
            runner._execute_map_reduce_fallback(parent, ["a", "b"], "Fusionner")
        )

        assert resultat.status == "success"
        tiers = {p.task_id: p.metadata["model_tier"] for p in agent.payloads}
        assert tiers["mapreduce_t3_map_0"] == "leger"
        assert tiers["mapreduce_t3_map_1"] == "leger"
        assert tiers["mapreduce_t3_reduce"] == "fort"

    def test_kill_switch_restaure_l_heritage_sur_les_deux_chemins(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_NODE_TIERS", "0")
        agent = _AgentCapteur()
        runner = DAGRunner(_EngineMock(agent))
        parent = TaskPayload(
            task_objective="Analyser",
            metadata={"model_tier": "moyen", "target_agent": "executor"},
            task_id="t4",
        )

        maps, reduce_p = runner._construire_payloads_mapreduce(
            parent, ["a"], "", "mapreduce_t4", "t4"
        )
        assert maps[0].metadata["model_tier"] == "moyen"
        assert reduce_p.metadata["model_tier"] == "moyen"

        asyncio.run(runner._execute_map_reduce_fallback(parent, ["a"], "Fusionner"))
        assert all(p.metadata["model_tier"] == "moyen" for p in agent.payloads)


def _tache(task_id, stage, tier="moyen", deps=None, agent="executor"):
    """Fabrique une tâche de plan telle que la produit le Planner."""
    deps = deps or []
    return TaskPayload(
        task_objective=f"objectif {task_id}",
        task_id=task_id,
        depends_on=deps,
        metadata={
            "task_id": task_id,
            "depends_on": deps,
            "stage_id": stage,
            "target_agent": agent,
            "model_tier": tier,
        },
    )


class TestNormalisationDuPlan:
    """Politique de rôle appliquée au plan du Planner (stages parallèles)."""

    def test_eventail_maps_leger_agregation_fort(self):
        plan = [
            _tache("lire_a", 1, "moyen"),
            _tache("lire_b", 1, "fort"),
            _tache("lire_c", 1, "moyen"),
            _tache("synthese", 2, "moyen", deps=["lire_a", "lire_b", "lire_c"]),
        ]

        rapport = normaliser_tiers_du_plan(plan)

        assert [t.metadata["model_tier"] for t in plan[:3]] == ["leger"] * 3
        assert plan[3].metadata["model_tier"] == "fort"
        assert rapport["reduce"] == "synthese"
        assert sorted(rapport["maps"]) == ["lire_a", "lire_b", "lire_c"]
        # Le tier demandé par le Planner reste lisible
        assert plan[1].metadata["model_tier_planner"] == "fort"
        assert plan[0].metadata["node_tier_role"] == "map"

    def test_plan_lineaire_inchange(self):
        plan = [
            _tache("lire", 1, "moyen"),
            _tache("ecrire", 2, "fort", deps=["lire"]),
            _tache("tester", 3, "moyen", deps=["ecrire"]),
        ]

        rapport = normaliser_tiers_du_plan(plan)

        assert [t.metadata["model_tier"] for t in plan] == ["moyen", "fort", "moyen"]
        assert rapport == {"maps": [], "reduce": None, "inchangees": 3}

    def test_verification_jamais_degradee(self):
        plan = [
            _tache("ecrire_a", 1, "moyen"),
            _tache("verify_syntaxe", 1, "moyen", agent="reviewer"),
            _tache("relecture", 1, "fort", agent="reviewer"),
        ]

        normaliser_tiers_du_plan(plan)

        assert plan[0].metadata["model_tier"] == "leger"
        assert plan[1].metadata["model_tier"] == "moyen"   # verify_* exemptée
        assert plan[2].metadata["model_tier"] == "fort"    # reviewer exempté

    def test_deuxieme_vague_reste_en_maps(self):
        # Deux tâches parallèles ayant chacune plusieurs dépendances : ce sont
        # des maps d'une seconde vague, surtout pas deux « reduce ».
        plan = [
            _tache("a", 1, "moyen"),
            _tache("b", 1, "moyen"),
            _tache("c", 2, "fort", deps=["a", "b"]),
            _tache("d", 2, "fort", deps=["a", "b"]),
        ]

        rapport = normaliser_tiers_du_plan(plan)

        assert rapport["reduce"] is None
        assert [t.metadata["model_tier"] for t in plan] == ["leger"] * 4

    def test_agregation_sur_une_seule_dependance_non_promue(self):
        plan = [
            _tache("a", 1, "leger"),
            _tache("suite", 2, "leger", deps=["a"]),
        ]

        rapport = normaliser_tiers_du_plan(plan)

        assert plan[1].metadata["model_tier"] == "leger"
        assert rapport["reduce"] is None

    def test_tier_deja_conforme_ne_trace_pas_d_origine(self):
        plan = [_tache("a", 1, "leger"), _tache("b", 1, "leger")]

        normaliser_tiers_du_plan(plan)

        assert "model_tier_planner" not in plan[0].metadata
        assert plan[0].metadata["node_tier_role"] == "map"

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("MOTEUR_DAG_NODE_TIERS", "0")
        plan = [_tache("a", 1, "fort"), _tache("b", 1, "fort")]

        rapport = normaliser_tiers_du_plan(plan)

        assert [t.metadata["model_tier"] for t in plan] == ["fort", "fort"]
        assert rapport["inchangees"] == 2

    def test_desactivation_par_config(self):
        plan = [_tache("a", 1, "fort"), _tache("b", 1, "fort")]

        rapport = normaliser_tiers_du_plan(
            plan, {"dag_node_tiers": {"plan_normalization": False}}
        )

        assert [t.metadata["model_tier"] for t in plan] == ["fort", "fort"]
        assert rapport["inchangees"] == 2

    def test_plan_vide_sans_effet(self):
        assert normaliser_tiers_du_plan([]) == {"maps": [], "reduce": None, "inchangees": 0}
