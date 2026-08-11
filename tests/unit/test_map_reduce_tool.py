"""
test_map_reduce_tool.py — Outil `map_reduce` exposé aux agents (#T245).

Le pattern MapReduce du DAG n'avait aucun déclencheur en production ; cet outil
est ce déclencheur. Les tests couvrent surtout ses garde-fous, parce que c'est
le seul endroit du moteur où un agent peut, d'un appel, ouvrir 12 tâches
parallèles : validation des fragments, refus explicite (jamais de troncature
silencieuse), unicité du MapReduce en vol, et remise à zéro du verrou même
quand l'exécution échoue.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.state import GlobalState, StateUpdate
from tools.dag_map_reduce import MAX_CHUNKS, register_map_reduce_tool
from tools.tool_registry import ToolRegistry


class _RunnerFake:
    """DAGRunner minimal : mémorise l'appel et rend un résultat paramétrable."""

    def __init__(self, resultat=None, exception=None):
        self.appels = []
        self._resultat = resultat
        self._exception = exception

    async def execute_map_reduce(self, payload, chunks, reduce_prompt="", on_event=None):
        self.appels.append((payload, chunks, reduce_prompt))
        if self._exception:
            raise self._exception
        return self._resultat or StateUpdate(
            agent_name="executor", status="success", result_data="synthèse finale"
        )


class _EngineFake:
    def __init__(self, runner):
        self._dag_runner = runner
        self.state = GlobalState(session_id="sess_test")


def _outil(runner):
    registry = ToolRegistry()
    register_map_reduce_tool(registry, _EngineFake(runner))
    return registry


class TestEnregistrement:
    def test_schema_expose_une_liste(self):
        registry = _outil(_RunnerFake())
        schemas = {s["function"]["name"]: s for s in registry.get_all_schemas()}

        assert "map_reduce" in schemas
        params = schemas["map_reduce"]["function"]["parameters"]
        # Union array|string : la validation du registre passe avant la fonction,
        # une liste sérialisée en chaîne JSON doit pouvoir arriver jusqu'à elle.
        assert params["properties"]["chunks"]["type"] == ["array", "string"]
        assert params["required"] == ["objectif", "chunks"]

    def test_timeout_et_rate_limit_dedies(self):
        assert ToolRegistry._TOOL_TIMEOUTS["map_reduce"] > 60.0
        assert ToolRegistry._TOOL_RATE_LIMITS["map_reduce"] == 5


class TestGardeFous:
    def test_moins_de_deux_fragments_refuse(self):
        registry = _outil(_RunnerFake())
        res = asyncio.run(registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a"]}))
        assert "au moins 2" in res

    def test_trop_de_fragments_refuse_sans_tronquer(self):
        runner = _RunnerFake()
        registry = _outil(runner)
        trop = [f"f{i}" for i in range(MAX_CHUNKS + 3)]

        res = asyncio.run(registry.execute("map_reduce", {"objectif": "Analyser", "chunks": trop}))

        assert f"maximum {MAX_CHUNKS}" in res
        assert runner.appels == []  # rien n'a été lancé en douce

    def test_chaine_json_acceptee(self):
        runner = _RunnerFake()
        registry = _outil(runner)

        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": '["a", "b"]'})
        )

        assert res == "synthèse finale"
        assert runner.appels[0][1] == ["a", "b"]

    def test_chaine_non_json_refusee(self):
        registry = _outil(_RunnerFake())
        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": "a, b"})
        )
        assert "tableau JSON" in res

    def test_un_seul_mapreduce_en_vol(self):
        runner = _RunnerFake()
        runner._mapreduce_tool_en_vol = 1
        registry = _outil(runner)

        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a", "b"]})
        )

        assert "déjà en cours" in res
        assert runner.appels == []

    def test_sans_dag_runner(self):
        class _EngineSansRunner:
            state = GlobalState(session_id="s")

        registry = ToolRegistry()
        register_map_reduce_tool(registry, _EngineSansRunner())
        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a", "b"]})
        )
        assert "aucun DAGRunner" in res


class TestExecution:
    def test_succes_transmet_objectif_et_libere_le_verrou(self):
        runner = _RunnerFake()
        registry = _outil(runner)

        res = asyncio.run(
            registry.execute(
                "map_reduce",
                {"objectif": "Résumer le fichier", "chunks": ["a", "b"], "reduce_prompt": "Fusionne"},
            )
        )

        assert res == "synthèse finale"
        payload, chunks, reduce_prompt = runner.appels[0]
        assert payload.task_objective == "Résumer le fichier"
        assert payload.metadata["target_agent"] == "executor"
        assert payload.metadata["session_id"] == "sess_test"
        assert chunks == ["a", "b"]
        assert reduce_prompt == "Fusionne"
        assert runner._mapreduce_tool_en_vol == 0

    def test_echec_du_dag_remonte_et_libere_le_verrou(self):
        runner = _RunnerFake(
            resultat=StateUpdate(agent_name="executor", status="error", error_message="tout a cassé")
        )
        registry = _outil(runner)

        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a", "b"]})
        )

        assert "MapReduce échoué" in res and "tout a cassé" in res
        assert runner._mapreduce_tool_en_vol == 0

    def test_exception_libere_le_verrou(self):
        runner = _RunnerFake(exception=RuntimeError("boum"))
        registry = _outil(runner)

        res = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a", "b"]})
        )

        assert "boum" in res
        assert runner._mapreduce_tool_en_vol == 0
        # Un second appel doit repasser, le verrou n'est pas resté coincé
        runner._exception = None
        res2 = asyncio.run(
            registry.execute("map_reduce", {"objectif": "Analyser", "chunks": ["a", "b"]})
        )
        assert res2 == "synthèse finale"


@pytest.mark.parametrize("chunks", [None, 42, {"a": 1}])
def test_types_invalides_refuses(chunks):
    registry = _outil(_RunnerFake())
    res = asyncio.run(registry.execute("map_reduce", {"objectif": "Analyser", "chunks": chunks}))
    assert "Erreur" in res


class _AgentCapteur:
    """Agent mocké qui mémorise les payloads et répond en succès."""

    def __init__(self, name="executor"):
        self.name = name
        self.payloads = []

    async def invoke(self, payload):
        self.payloads.append(payload)
        return StateUpdate(
            agent_name=self.name, status="success", result_data=f"vu:{payload.task_id}", metadata={}
        )


def test_bout_en_bout_avec_un_vrai_dag_runner():
    """
    Câblage réel : outil → DAGRunner → agents. Sans DAG actif, le runner passe
    par son repli synchrone — c'est le chemin qu'emprunterait un appel depuis
    une session sans plan, et il n'avait jamais tourné en conditions réelles.
    """
    from core.dag_runner import DAGRunner

    agent = _AgentCapteur()

    class _EngineComplet:
        def __init__(self):
            self.state = GlobalState(session_id="sess_e2e")
            self.agents = {"executor": agent}
            self.context_manager = None
            self.on_event = None
            self._dag_runner = None

    engine = _EngineComplet()
    engine._dag_runner = DAGRunner(engine)

    registry = ToolRegistry()
    register_map_reduce_tool(registry, engine)

    res = asyncio.run(
        registry.execute(
            "map_reduce",
            {"objectif": "Résumer", "chunks": ["A", "B", "C"], "reduce_prompt": "Fusionne"},
        )
    )

    ids = [p.task_id for p in agent.payloads]
    tiers = {p.task_id: p.metadata.get("model_tier") for p in agent.payloads}
    maps = [i for i in ids if "_map_" in i]
    reduces = [i for i in ids if i.endswith("_reduce")]

    assert len(maps) == 3 and len(reduces) == 1
    assert all(tiers[m] == "leger" for m in maps)      # #T245 : maps en gratuit
    assert tiers[reduces[0]] == "fort"                  # …reduce sur le cerveau
    assert "vu:" in res
    assert engine._dag_runner._mapreduce_tool_en_vol == 0
