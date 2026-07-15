"""
core/mcp_tools/ — Outils du serveur MCP Tab5 Engine, groupés par domaine (#T124).

Segmentation d'un ex-mcp_server.py monolithique (17 outils, 1500+ lignes) en
3 modules, tous enregistrés sur la même instance FastMCP partagée
(core/mcp_app.py) — un seul process/serveur, juste une meilleure organisation :

- orchestrator.py    : LLM/routing (pipeline moteur, appels directs, routage coût/avantage)
- memory.py          : mémoire/RAG (catalogue modèles, ChromaDB, SQLite runtime/memory.db)
- homeassistant.py   : domotique (entités HA, actions HA, linter config ESPHome/YAML)
"""
