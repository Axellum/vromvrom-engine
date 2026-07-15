"""
core/mcp_app.py — Instance FastMCP partagée du serveur MCP Tab5 Engine (#T124).

Un seul objet `mcp`, importé par mcp_server.py (point d'entrée) et par les
modules d'outils (core/mcp_tools/*) qui l'utilisent pour décorer leurs
fonctions avec @mcp.tool(). Un seul process/serveur MCP au final — juste le
code réorganisé par domaine (LLM/routing, mémoire/RAG, domotique) au lieu
d'un unique fichier de 1500+ lignes.
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Tab5 Engine")
