"""
tests/unit/test_agent_tool_permissions.py — Permissions d'outils par agent (#T233).

Un agent ne voit et ne peut appeler que les outils de sa liste `allowed_tools`
(config.json → core/custom_agents.py, agents_workflows.json →
core/workflow_bridge.py). Sémantique du champ : absent ou null = tous les
outils, [] = aucun outil, liste = ces outils uniquement.

Le registre étant partagé par tous les agents, le filtrage s'applique AU
MOMENT de fournir les schémas (get_all_schemas) et d'exécuter (execute), et le
refus nomme l'agent et l'outil — ni silence, ni « outil inexistant » qui
pousserait l'agent à contourner (cf. #T275).
"""
import asyncio

from agents.executor import ExecutorAgent
from core.custom_agents import build_custom_agent_prompt, register_config_custom_agents
from core.state import TaskPayload
from core.workflow_bridge import WorkflowBridge, _normalize_workflow
from tools.tool_registry import ToolRegistry


def _registre_deux_outils() -> ToolRegistry:
    """Registre minimal : read_file (autorisé) + write_file (souvent interdit)."""
    registry = ToolRegistry()
    registry.register("read_file", lambda filepath="": f"contenu de {filepath}", "Lit un fichier.")
    registry.register("write_file", lambda filepath="", content="": f"écrit {filepath}", "Écrit un fichier.")
    return registry


def _noms_schemas(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}


# ──────────────────────────────────────────────────────────────
# Registre : filtrage à la fourniture des schémas
# ──────────────────────────────────────────────────────────────

def test_agent_sans_allowed_tools_voit_les_memes_outils_qu_avant():
    """Non-régression : sans restriction (inconnu ou null), tous les outils."""
    registry = _registre_deux_outils()
    attendu = _noms_schemas(registry.get_all_schemas())

    # Agent jamais déclaré dans le registre des permissions
    assert _noms_schemas(registry.get_all_schemas(agent_name="agent_inconnu")) == attendu
    # Restriction explicitement levée (null = tous les outils)
    registry.set_agent_allowed_tools("agent_libre", None)
    assert _noms_schemas(registry.get_all_schemas(agent_name="agent_libre")) == attendu


def test_allowed_tools_filtre_les_schemas_exposes():
    """Agent restreint : seuls ses outils sont exposés, les autres inchangés."""
    registry = _registre_deux_outils()
    registry.set_agent_allowed_tools("agent_lecteur", ["read_file"])
    assert _noms_schemas(registry.get_all_schemas(agent_name="agent_lecteur")) == {"read_file"}
    # Registre partagé : les autres agents ne sont pas affectés
    assert len(registry.get_all_schemas(agent_name="agent_autre")) == 2


def test_liste_vide_signifie_aucun_outil_et_differe_de_null():
    """[] = aucun outil ; null (ou lever la restriction) = tous les outils."""
    registry = _registre_deux_outils()
    registry.set_agent_allowed_tools("agent_vide", [])
    assert registry.get_all_schemas(agent_name="agent_vide") == []

    # null ≠ [] : la restriction levée rend tous les outils
    registry.set_agent_allowed_tools("agent_vide", None)
    assert len(registry.get_all_schemas(agent_name="agent_vide")) == 2


def test_filtre_permission_compose_avec_le_jit_mcp():
    """Le filtre par agent s'applique en plus du filtrage JIT MCP (contexte)."""
    registry = _registre_deux_outils()
    registry.register_mcp_tool(
        "mcp_sqlite_ha_query", lambda: "ok", "Requête SQLite/HA", {"type": "object"}
    )
    registry.set_agent_allowed_tools("agent_lecteur", ["read_file", "mcp_sqlite_ha_query"])

    # L'objectif déclenche le JIT (mot-clé « sqlite ») ; la permission borne
    # ensuite l'ensemble exposé — déterministe même si le fichier de règles
    # JIT était absent (la permission coupe de toute façon).
    noms = _noms_schemas(registry.get_all_schemas("requete sqlite", agent_name="agent_lecteur"))
    assert noms == {"read_file", "mcp_sqlite_ha_query"}


# ──────────────────────────────────────────────────────────────
# Registre : refus explicite à l'exécution
# ──────────────────────────────────────────────────────────────

def test_appel_outil_non_autorise_refus_explicite_nommant_agent_et_outil():
    registry = _registre_deux_outils()
    registry.set_agent_allowed_tools("agent_lecteur", ["read_file"])

    res = asyncio.run(registry.execute(
        "write_file", {"filepath": "x", "content": "y"}, agent_name="agent_lecteur"
    ))
    assert "agent_lecteur" in res
    assert "write_file" in res
    assert "permission" in res.lower()
    assert "read_file" in res  # le refus rappelle les outils autorisés

    # L'outil autorisé fonctionne toujours
    res_ok = asyncio.run(registry.execute("read_file", {"filepath": "x"}, agent_name="agent_lecteur"))
    assert "contenu" in res_ok


def test_refus_non_retriable_et_sans_consommation_de_quota():
    """Le refus est classé PERMISSION (pas de retry auto) et ne consomme pas de quota."""
    from core.errors import classify_error

    registry = _registre_deux_outils()
    registry.set_agent_allowed_tools("agent_lecteur", ["read_file"])

    res = asyncio.run(registry.execute(
        "write_file", {"filepath": "x", "content": "y"}, agent_name="agent_lecteur"
    ))
    erreur = classify_error(res, source="tool:write_file")
    assert erreur.category.value == "permission"
    assert not erreur.is_retriable
    assert registry._call_counts.get("write_file", 0) == 0


def test_appel_sans_agent_nomme_ignore_les_permissions():
    """Appelants sans identité (API, sandbox) : comportement historique intact."""
    registry = _registre_deux_outils()
    registry.set_agent_allowed_tools("agent_lecteur", ["read_file"])
    res = asyncio.run(registry.execute("write_file", {"filepath": "x", "content": "y"}))
    assert res == "écrit x"


# ──────────────────────────────────────────────────────────────
# Câblage : ExecutorAgent → registre (chemin zéro-LLM)
# ──────────────────────────────────────────────────────────────

def test_executor_agent_synchronise_ses_permissions_a_l_invoke():
    """Le refus traverse le vrai chemin d'exécution (direct_tool_call, sans LLM)."""
    registry = _registre_deux_outils()
    agent = ExecutorAgent(
        llm_gateway=object(), tool_registry=registry,
        provider_name="leger", allowed_tools=["read_file"],
    )
    agent.name = "agent_restreint"  # renommé après construction, comme en prod

    update = asyncio.run(agent.invoke(TaskPayload(
        task_objective="écrire un fichier",
        relevant_context="",
        metadata={"direct_tool_call": {"name": "write_file", "arguments": {"filepath": "x", "content": "y"}}},
    )))
    assert update.status == "error"
    assert "agent_restreint" in (update.error_message or "")
    assert "write_file" in (update.error_message or "")

    # Sans restriction, le même agent exécute l'outil
    agent.allowed_tools = None
    update_ok = asyncio.run(agent.invoke(TaskPayload(
        task_objective="écrire un fichier",
        relevant_context="",
        metadata={"direct_tool_call": {"name": "write_file", "arguments": {"filepath": "x", "content": "y"}}},
    )))
    assert update_ok.status == "success"


# ──────────────────────────────────────────────────────────────
# Câblage : config.json (custom_agents) et agents_workflows.json
# ──────────────────────────────────────────────────────────────

def test_agent_custom_config_json_transmet_allowed_tools():
    """core/custom_agents.py : l'entrée config.json est câblée jusqu'à l'exécution."""

    class _FakeEngine:
        agents = {}

        def register_agent(self, agent):
            self.agents[agent.name] = agent

    registry = _registre_deux_outils()
    config = {
        "custom_agents": [
            {"name": "agent_lecture_seule", "label": "Lecture seule", "tier": "leger",
             "allowed_tools": ["read_file"]},
        ]
    }
    engine = _FakeEngine()
    added = register_config_custom_agents(engine, object(), registry, config)
    assert added == 1

    agent = engine.agents["agent_lecture_seule"]
    assert agent.allowed_tools == ["read_file"]
    # Le prompt ne prétend plus « accès à tous les outils » à un agent restreint
    assert "read_file" in agent.system_prompt
    assert "tous les outils" not in agent.system_prompt

    # Refus effectif via le chemin d'exécution complet
    update = asyncio.run(agent.invoke(TaskPayload(
        task_objective="écrire un fichier",
        relevant_context="",
        metadata={"direct_tool_call": {"name": "write_file", "arguments": {"filepath": "x", "content": "y"}}},
    )))
    assert update.status == "error"
    assert "agent_lecture_seule" in (update.error_message or "")


def test_build_custom_agent_prompt_reflete_la_restriction():
    assert "tous les outils" in build_custom_agent_prompt("agent_a")
    assert "tous les outils" in build_custom_agent_prompt("agent_a", allowed_tools=None)
    prompt = build_custom_agent_prompt("agent_a", "Agent A", ["read_file"])
    assert "read_file" in prompt
    assert "tous les outils" not in prompt


def test_workflow_bridge_normalise_allowed_tools_des_deux_formats():
    """agents_workflows.json : champ lu dans data (React Flow) et en racine (legacy)."""
    data = {
        "nodes": [
            # Schéma React Flow de l'éditeur ihm-v2
            {"id": "n1", "type": "agent",
             "data": {"agent": "agent_wf", "tier": "leger", "allowed_tools": ["read_file"]}},
            # Schéma legacy v1
            {"id": "n2", "type": "agent", "agentName": "agent_wf2", "tier": "moyen",
             "allowed_tools": ["read_file", "write_file"]},
            # Sans restriction : absent (doit rester absent/None)
            {"id": "n3", "type": "agent", "data": {"agent": "agent_wf3", "tier": "leger"}},
        ],
        "edges": [],
    }
    normalise = _normalize_workflow(data)
    par_id = {n["id"]: n for n in normalise["nodes"]}
    assert par_id["n1"]["allowed_tools"] == ["read_file"]
    assert par_id["n2"]["allowed_tools"] == ["read_file", "write_file"]
    assert par_id["n3"]["allowed_tools"] is None

    bridge = WorkflowBridge(workflow_path="/nonexistent/for-tests.json")
    bridge._cache = normalise
    configs = {c["name"]: c for c in bridge.get_custom_agents_config()}
    assert configs["agent_wf"]["allowed_tools"] == ["read_file"]
    assert configs["agent_wf2"]["allowed_tools"] == ["read_file", "write_file"]
    assert configs["agent_wf3"]["allowed_tools"] is None
