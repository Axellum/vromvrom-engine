"""
tests/test_dag_reactive.py — Tests unitaires pour l'ordonnanceur réactif de DAGRunner.

Vérifie :
- L'enregistrement correct du graphe et des tâches dans les tables `dag_tasks` et `dag_edges`.
- La mise à jour dynamique des statuts de tâches en BDD.
- L'exécution réactive asynchrone (PriorityQueue).
- L'intégrité de l'ordonnancement avec des dépendances complexes.
"""

import sys
import os
import tempfile
import pytest
import asyncio
import json
from unittest.mock import AsyncMock

# Ajouter le répertoire parent au path pour importer les modules du moteur
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import TaskPayload, StateUpdate, GlobalState
from core.dag_runner import DAGRunner
import core.runtime_db as db_mod


class MockAgent:
    def __init__(self, name: str, delay: float = 0.0):
        self.name = name
        self.delay = delay
        self.invoked_tasks = []

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        self.invoked_tasks.append(payload.task_id)
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=f"Success on {payload.task_id}",
            metadata={}
        )


class MockEngine:
    def __init__(self):
        self.state = GlobalState(session_id="test_dag_reactive_session")
        self._history_lock = asyncio.Lock()
        self.on_event = AsyncMock()
        self.context_manager = None
        self.agents = {
            "executor": MockAgent("executor"),
        }

    async def _validate_modified_yamls(self):
        return None


class TestDAGReactive:
    @pytest.fixture(autouse=True)
    def setup_and_teardown_db(self):
        # Setup base temporaire
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)
        
        yield
        
        # Teardown
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_dag_initialization_in_db(self):
        """Vérifie que les tâches et dépendances sont correctement enregistrées en base."""
        engine = MockEngine()
        runner = DAGRunner(engine)
        
        tasks = [
            TaskPayload(
                task_objective="Objective 1",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Objective 2",
                task_id="t2",
                depends_on=["t1"],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 2}
            )
        ]
        
        tasks_status, has_error = await runner.execute_dag(tasks, max_session_tokens=500_000)
        
        assert has_error is False
        assert tasks_status["t1"] == "success"
        assert tasks_status["t2"] == "success"

        # Vérifier en BDD
        conn = db_mod.get_connection()
        cursor = conn.execute(
            "SELECT task_id, status, depends_on_json FROM dag_tasks WHERE session_id = ?",
            (engine.state.session_id,)
        )
        db_tasks = cursor.fetchall()
        assert len(db_tasks) == 2
        
        task_map = {row[0]: (row[1], json.loads(row[2])) for row in db_tasks}
        assert "t1" in task_map
        assert task_map["t1"][0] == "success"
        assert task_map["t1"][1] == []
        
        assert "t2" in task_map
        assert task_map["t2"][0] == "success"
        assert task_map["t2"][1] == ["t1"]

        # Vérifier les edges
        cursor_edges = conn.execute(
            "SELECT parent_task_id, child_task_id FROM dag_edges WHERE session_id = ?",
            (engine.state.session_id,)
        )
        edges = cursor_edges.fetchall()
        assert len(edges) == 1
        assert edges[0] == ("t1", "t2")
        
        conn.close()

    @pytest.mark.asyncio
    async def test_complex_dependencies_and_priority(self):
        """Teste un DAG plus complexe pour s'assurer que l'ordre et la réactivité fonctionnent."""
        engine = MockEngine()
        # Mettre un agent avec un petit délai pour tester le parallélisme
        engine.agents["executor"] = MockAgent("executor", delay=0.01)
        runner = DAGRunner(engine)
        
        # t3 dépend de t1 et t2. t4 dépend de t3.
        tasks = [
            TaskPayload(
                task_objective="Tache 1",
                task_id="t1",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Tache 2",
                task_id="t2",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            ),
            TaskPayload(
                task_objective="Tache 3 (attend t1, t2)",
                task_id="t3",
                depends_on=["t1", "t2"],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 2}
            ),
            TaskPayload(
                task_objective="Tache 4 (attend t3)",
                task_id="t4",
                depends_on=["t3"],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 3}
            )
        ]
        
        tasks_status, has_error = await runner.execute_dag(tasks, max_session_tokens=500_000)
        assert has_error is False
        assert tasks_status["t1"] == "success"
        assert tasks_status["t2"] == "success"
        assert tasks_status["t3"] == "success"
        assert tasks_status["t4"] == "success"

        # L'ordre d'invocation : t1 et t2 doivent être invoqués avant t3, qui doit être invoqué avant t4.
        invocations = engine.agents["executor"].invoked_tasks
        assert invocations.index("t1") < invocations.index("t3")
        assert invocations.index("t2") < invocations.index("t3")
        assert invocations.index("t3") < invocations.index("t4")


class MockMapReduceAgent:
    def __init__(self, name: str, runner: DAGRunner):
        self.name = name
        self.runner = runner
        self.invoked_tasks = []

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        self.invoked_tasks.append(payload.task_id)
        # S'il s'agit de la tâche parent, on déclenche le MapReduce dynamique !
        if payload.task_id == "t_parent":
            chunks = ["chunk_A", "chunk_B", "chunk_C"]
            reduce_prompt = "Fusionner les chunks"
            return await self.runner.execute_map_reduce(
                payload, chunks, reduce_prompt=reduce_prompt
            )
        
        # S'il s'agit d'une tâche Map ou Reduce
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=f"Processed: {payload.task_objective}",
            metadata={}
        )


class TestDAGReactiveMapReduce:
    @pytest.fixture(autouse=True)
    def setup_and_teardown_db(self):
        # Setup base temporaire
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)
        
        yield
        
        # Teardown
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_reactive_map_reduce_execution(self):
        """Vérifie que MapReduce s'exécute dynamiquement et réactivement via la queue."""
        engine = MockEngine()
        runner = DAGRunner(engine)
        
        # Associer un agent qui sait faire du MapReduce
        map_reduce_agent = MockMapReduceAgent("executor", runner)
        engine.agents["executor"] = map_reduce_agent
        
        # Une tâche unique qui va se subdiviser en 3 Maps + 1 Reduce
        tasks = [
            TaskPayload(
                task_objective="Traitement global",
                task_id="t_parent",
                depends_on=[],
                metadata={"target_agent": "executor", "session_id": "test", "stage_id": 1}
            )
        ]
        
        tasks_status, has_error = await runner.execute_dag(tasks, max_session_tokens=500_000)
        
        assert has_error is False
        assert tasks_status["t_parent"] == "success"
        
        # Les tâches Map et Reduce doivent aussi être dans tasks_status car injectées dynamiquement !
        assert tasks_status["mapreduce_t_parent_map_0"] == "success"
        assert tasks_status["mapreduce_t_parent_map_1"] == "success"
        assert tasks_status["mapreduce_t_parent_map_2"] == "success"
        assert tasks_status["mapreduce_t_parent_reduce"] == "success"

        # L'ordre d'invocation :
        # 1. t_parent commence
        # 2. Les maps 0, 1, 2 s'exécutent (ordre non garanti pour le parallélisme, mais avant reduce)
        # 3. Le reduce s'exécute à la fin
        invocations = map_reduce_agent.invoked_tasks
        assert invocations[0] == "t_parent"
        
        maps = invocations[1:4]
        assert "mapreduce_t_parent_map_0" in maps
        assert "mapreduce_t_parent_map_1" in maps
        assert "mapreduce_t_parent_map_2" in maps
        
        assert invocations[4] == "mapreduce_t_parent_reduce"


class TestDAGScopedMemory:
    @pytest.fixture(autouse=True)
    def setup_and_teardown_db(self):
        # Setup base temporaire
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)
        
        yield
        
        # Teardown
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_scoped_memory_inheritance_and_override(self):
        """Valide l'écriture et la remontée récursive de variables avec surcharge."""
        session_id = "test_scoped_session"
        
        # global -> parent -> local
        db_mod.write_scoped_var(session_id, "global", None, "var_global_only", "val_global")
        db_mod.write_scoped_var(session_id, "global", None, "var_overridden", "val_global_override")
        
        db_mod.write_scoped_var(session_id, "parent", "global", "var_parent_only", "val_parent")
        db_mod.write_scoped_var(session_id, "parent", "global", "var_overridden", "val_parent_override")
        
        db_mod.write_scoped_var(session_id, "local", "parent", "var_local_only", "val_local")
        db_mod.write_scoped_var(session_id, "local", "parent", "var_overridden", "val_local_override")
        
        # Résoudre à partir de 'local'
        scoped_vars = db_mod.get_all_scoped_vars(session_id, "local")
        
        assert scoped_vars["var_global_only"] == "val_global"
        assert scoped_vars["var_parent_only"] == "val_parent"
        assert scoped_vars["var_local_only"] == "val_local"
        assert scoped_vars["var_overridden"] == "val_local_override"  # Surcharge de local sur parent et global

        # Résoudre à partir de 'parent'
        parent_vars = db_mod.get_all_scoped_vars(session_id, "parent")
        assert parent_vars["var_global_only"] == "val_global"
        assert parent_vars["var_parent_only"] == "val_parent"
        assert parent_vars["var_overridden"] == "val_parent_override"
        assert "var_local_only" not in parent_vars

    @pytest.mark.asyncio
    async def test_strict_isolation_level_3(self):
        """Valide le cloisonnement strict de Niveau 3 (isolation et compression globale)."""
        engine = MockEngine()
        runner = DAGRunner(engine)
        
        session_id = engine.state.session_id
        
        # Écriture dans global et local
        db_mod.write_scoped_var(session_id, "global", None, "secret_global", "mot_de_passe")
        db_mod.write_scoped_var(session_id, "local_scope", "global", "local_data", "information_locale")
        
        # Mock d'un agent pour voir le payload qu'il reçoit
        received_payloads = []
        class CaptureAgent:
            async def invoke(self, payload: TaskPayload) -> StateUpdate:
                received_payloads.append(payload)
                return StateUpdate(
                    agent_name="capture",
                    status="success",
                    result_data="Done",
                    metadata={}
                )
        
        engine.agents["capture_agent"] = CaptureAgent()
        
        # Tâche avec scope_level = 3
        tasks = [
            TaskPayload(
                task_objective="Objectif isole",
                task_id="t_strict",
                depends_on=[],
                metadata={
                    "target_agent": "capture_agent",
                    "session_id": session_id,
                    "stage_id": 1,
                    "scope_id": "local_scope",
                    "scope_level": 3
                }
            )
        ]
        
        status, has_error = await runner.execute_dag(tasks, max_session_tokens=500_000)
        assert has_error is False
        assert status["t_strict"] == "success"
        
        # Vérifier le payload reçu
        assert len(received_payloads) == 1
        payload = received_payloads[0]
        context = payload.relevant_context
        
        # Dans le niveau 3, le contexte global doit être compressé/compacté et non affiché textuellement
        # Le secret_global ("mot_de_passe") ne doit pas apparaître dans le contexte !
        assert "mot_de_passe" not in context
        assert "secret_global" in context  # La clé est listée dans les métadonnées globales compressées
        # [PHASE 2 D2-FIX] La valeur globale est désormais correctement décodée avant
        # compression : len("mot_de_passe") == 12. Auparavant, faute d'import json
        # accessible dans _run_single_task, json.loads échouait silencieusement et la
        # valeur JSON brute avec guillemets ('"mot_de_passe"', 14 car.) était mesurée.
        assert "[str (longueur: 12)]" in context  # Représentation compressée (valeur masquée)
        
        # Les données locales par contre doivent être en clair dans la mémoire de travail cloisonnée
        assert "information_locale" in context

    @pytest.mark.asyncio
    async def test_query_technical_knowledge_rag_tool(self):
        """Valide le bon enregistrement et fonctionnement de query_technical_knowledge."""
        from unittest.mock import patch
        
        # Importer factory et RAGEngine
        with patch('memory.rag.RAGEngine.query') as mock_query:
            mock_query.return_value = ["resultat RAG 1", "resultat RAG 2"]
            
            # Créer l'engine via factory pour tester l'enregistrement correct de l'outil
            from core.factory import create_engine
            # Mocker load_config pour éviter des problèmes de configuration
            with patch('core.factory.load_config', return_value={
                "planner_model": "fort", "executor_model": "automatique"
            }):
                # create_engine retourne (engine, router, config)
                engine, _, _ = create_engine(session_id="test_rag_session", register_git_tools=False)
                
                # Récupérer l'outil dans le registry
                registry = engine.agents["executor"].tool_registry
                assert "query_technical_knowledge" in registry._tools
                
                # Exécuter l'outil
                tool_func = registry._tools["query_technical_knowledge"]
                res = tool_func("comment configurer le Tab5 ?")
                
                # Vérifier que le RAGEngine a été interrogé
                mock_query.assert_called_once_with("comment configurer le Tab5 ?", top_n=3)
                assert res == ["resultat RAG 1", "resultat RAG 2"]


class TestLocalHealingAndReview:
    @pytest.fixture(autouse=True)
    def setup_and_teardown_db(self):
        # Setup base temporaire
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db_mod.get_db_path()
        db_mod.override_db_path(self._tmp.name)
        
        yield
        
        # Teardown
        db_mod.override_db_path(self._original_db_path)
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_local_tool_auto_correction(self):
        """Vérifie que l'agent technique s'auto-corrige localement après une erreur d'outil."""
        from unittest.mock import MagicMock, patch
        from tools.tool_registry import ToolRegistry
        from core.llm_gateway import LLMGateway
        from agents.executor import ExecutorAgent
        
        gateway = MagicMock(spec=LLMGateway)
        mock_provider = MagicMock()
        gateway.get_provider_for_tier.return_value = ("mock_model", mock_provider)
        
        # ToolRegistry avec un outil qui échoue puis réussit
        registry = ToolRegistry()
        tool_calls_count = 0
        
        def mock_tool(arg: str):
            nonlocal tool_calls_count
            tool_calls_count += 1
            if arg == "invalide":
                return "Erreur : argument invalide"
            return f"Succès avec {arg}"
            
        registry.register("test_tool", mock_tool, "Outil de test.")
        
        agent = ExecutorAgent(llm_gateway=gateway, tool_registry=registry, provider_name="leger")
        
        # Simuler les réponses du LLM pour les différents tours
        responses = [
            {
                "content": "Je vais appeler test_tool avec un mauvais argument.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": '{"arg": "invalide"}'}
                }]
            },
            {
                "content": "L'outil a échoué. Je vais corriger en appelant avec un bon argument.",
                "tool_calls": [{
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": '{"arg": "valide"}'}
                }]
            },
            {
                "content": "C'est bon, l'opération a réussi.",
                "tool_calls": []
            }
        ]
        
        call_index = 0
        def side_effect(*args, **kwargs):
            nonlocal call_index
            res = responses[min(call_index, len(responses) - 1)]
            call_index += 1
            return res
            
        mock_provider.generate_async = AsyncMock(side_effect=side_effect)
        mock_provider.generate_structured_async = AsyncMock(return_value={
            "code_approved": True, "severity": "info",
            "review_feedback": "OK", "target_corrections": []
        })

        payload = TaskPayload(
            task_objective="Faire une action corrigible",
            relevant_context="",
            metadata={"session_id": "test_local_healing", "model_tier": "leger"}
        )
        
        with patch('core.llm_gateway.load_config', return_value={}):
            update = await agent.invoke(payload)
            
        assert update.status == "success"
        assert tool_calls_count == 2
        assert "Succès avec valide" in update.result_data

    @pytest.mark.asyncio
    async def test_local_react_review_cycle(self):
        """Vérifie le micro-cycle de revue locale ReAct-Review avec correction et validation."""
        from unittest.mock import MagicMock, patch
        from tools.tool_registry import ToolRegistry
        from core.llm_gateway import LLMGateway
        from agents.executor import ExecutorAgent
        
        gateway = MagicMock(spec=LLMGateway)
        mock_provider = MagicMock()
        gateway.get_provider_for_tier.return_value = ("mock_model", mock_provider)
        
        registry = ToolRegistry()
        # Outil de modification fictif pour déclencher la revue
        registry.register("write_file", lambda filepath, content: "Fichier écrit.", "Écrit un fichier.")
        
        agent = ExecutorAgent(llm_gateway=gateway, tool_registry=registry, provider_name="leger")
        
        # Simuler les réponses du LLM pour l'agent
        agent_responses = [
            {
                "content": "J'écris le fichier.",
                "tool_calls": [{
                    "id": "call_w1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": '{"filepath": "test.txt", "content": "hello"}'}
                }]
            },
            {
                "content": "J'ai fini d'écrire le fichier.",
                "tool_calls": []
            },
            {
                "content": "Le reviewer a rejeté. Je vais corriger le fichier en ajoutant des commentaires.",
                "tool_calls": [{
                    "id": "call_w2",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": '{"filepath": "test.txt", "content": "hello # commentaire en francais"}'}
                }]
            },
            {
                "content": "J'ai corrigé le fichier.",
                "tool_calls": []
            }
        ]
        
        agent_call_index = 0
        def agent_generate_side_effect(*args, **kwargs):
            nonlocal agent_call_index
            res = agent_responses[min(agent_call_index, len(agent_responses) - 1)]
            agent_call_index += 1
            return res
            
        mock_provider.generate_async = AsyncMock(side_effect=agent_generate_side_effect)
        
        reviewer_responses = [
            {
                "code_approved": False,
                "severity": "major",
                "review_feedback": "Le code manque de commentaires explicatifs rédigés en français.",
                "target_corrections": ["Ajouter des commentaires en français dans test.txt"]
            },
            {
                "code_approved": True,
                "severity": "info",
                "review_feedback": "Parfait, le code est bien commenté en français.",
                "target_corrections": []
            }
        ]
        
        reviewer_call_index = 0
        def reviewer_generate_side_effect(*args, **kwargs):
            nonlocal reviewer_call_index
            res = reviewer_responses[min(reviewer_call_index, len(reviewer_responses) - 1)]
            reviewer_call_index += 1
            return res
            
        mock_provider.generate_structured_async = AsyncMock(side_effect=reviewer_generate_side_effect)
        
        payload = TaskPayload(
            task_objective="Écrire un fichier de configuration",
            relevant_context="",
            metadata={"session_id": "test_react_review", "model_tier": "leger"}
        )
        
        # [CI-fix] ReviewerAgent._has_modified_files() fait un vrai `git status` →
        # résultat dépendant de l'état du working tree (flaky : `test.txt` n'est même
        # pas une extension de code détectée). On force la détection pour exercer
        # réellement le cycle de revue locale (rejet → correction → approbation).
        with patch('core.llm_gateway.load_config', return_value={}), \
             patch('agents.reviewer.ReviewerAgent._has_modified_files', return_value=True):
            update = await agent.invoke(payload)

        assert update.status == "success"
        assert "Code validé et approuvé" in update.metadata.get("local_review", "")
        assert reviewer_call_index == 2


