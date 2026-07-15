"""
mcp_server.py — Serveur MCP du Moteur Agents pour Antigravity IDE.

[T124] Point d'entrée mince : les 17 outils vivent désormais dans
core/mcp_tools/ (regroupés par domaine), tous enregistrés sur la même
instance FastMCP partagée (core/mcp_app.py) — un seul process/serveur MCP,
juste une meilleure organisation qu'un unique fichier de 1500+ lignes.

  core/mcp_tools/orchestrator.py  (8 outils LLM/routing) :
    run_tab5_agent, query_deepseek, get_engine_status,
    get_routing_recommendation, get_routing_matrix,
    delegate_complex_reasoning, query_llm_direct, delegate_to_gateway

  core/mcp_tools/memory.py        (6 outils mémoire/RAG) :
    get_models_catalog, rag_search, query_token_usage,
    list_available_models, query_runtime, search_memory

  core/mcp_tools/homeassistant.py (3 outils domotique) :
    search_ha_entities, validate_config_format, execute_ha_action

@version 4.0.0 — segmentation en 3 modules par domaine (#T124), sans changement d'API ni de comportement
"""
import logging
import os
from dotenv import load_dotenv

# Résolution absolue du fichier .env pour éviter les problèmes de CWD (répertoire de travail) de l'IDE
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Désactiver les logs bruyants qui pourraient perturber stdio (FastMCP gère ça en partie, mais prudence)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("mcp_server")

from core.mcp_app import mcp

# Import des 3 modules d'outils — déclenche l'enregistrement de leurs
# @mcp.tool() sur l'instance `mcp` partagée. Ordre : orchestrator avant
# memory (memory.list_available_models importe get_gateway depuis orchestrator).
import core.mcp_tools.orchestrator  # noqa: E402,F401
import core.mcp_tools.memory  # noqa: E402,F401
import core.mcp_tools.homeassistant  # noqa: E402,F401


if __name__ == "__main__":
    # Lancement du serveur MCP
    mcp.run()
