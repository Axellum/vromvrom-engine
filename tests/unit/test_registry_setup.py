"""
tests/unit/test_registry_setup.py — Registry d'outils factorisé (#T213).

Les 3 sites d'assemblage (core/factory.py, services/pipeline_service.py,
core/mcp_tools/orchestrator.py) passent désormais par tools/registry_setup :
ce test verrouille la convergence (même socle partout, plus de divergence
silencieuse des sets d'outils).
"""

from tools.registry_setup import register_base_tools, register_extended_tools
from tools.tool_registry import ToolRegistry

BASE_TOOLS = {
    "read_file", "write_file", "run_terminal_command",
    "call_api", "validate_config_yaml", "run_tests",
}
GIT_TOOLS = {"git_create_checkpoint", "git_rollback_checkpoint", "git_apply_checkpoint"}


def test_base_tools_complets_avec_git_safety():
    registry = ToolRegistry()
    register_base_tools(registry, git_safety=True)
    names = set(registry._tools)
    assert BASE_TOOLS <= names
    assert GIT_TOOLS <= names


def test_git_safety_desactivable():
    registry = ToolRegistry()
    register_base_tools(registry, git_safety=False)
    names = set(registry._tools)
    assert BASE_TOOLS <= names
    assert not (GIT_TOOLS & names)


def test_deux_sites_produisent_le_meme_set():
    """Deux registres construits via les fonctions partagées sont identiques —
    c'est exactement la garantie qui manquait aux 3 sites divergents."""
    r1, r2 = ToolRegistry(), ToolRegistry()
    for r in (r1, r2):
        register_base_tools(r)
        register_extended_tools(r)
    assert set(r1._tools) == set(r2._tools)


def test_les_trois_sites_importent_le_module_partage():
    """Vérifie statiquement que factory, pipeline_service et l'orchestrateur MCP
    passent tous par tools.registry_setup (anti-régression de la factorisation)."""
    import inspect

    import core.factory as factory
    import core.mcp_tools.orchestrator as orchestrator
    import services.pipeline_service as pipeline

    for module in (factory, pipeline, orchestrator):
        source = inspect.getsource(module)
        assert "registry_setup" in source, f"{module.__name__} ne passe plus par registry_setup"
