import os
import sys
import json
import logging
import asyncio
from contextlib import AsyncExitStack
from typing import Dict, Callable

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

class MCPBridge:
    """
    Pont permettant d'intégrer des serveurs MCP locaux configurés dans mcp_config.json
    en tant qu'outils dans le ToolRegistry du tab5-engine.
    """
    def __init__(self, config_path: str = None):
        self.config_path = config_path
        self.exit_stack = AsyncExitStack()
        self.sessions: Dict[str, ClientSession] = {}
        
    async def start(self, registry: ToolRegistry, user_prompt: str = "") -> None:
        """
        Lit mcp_config.json, détermine conditionnellement les serveurs à démarrer,
        démarre les processus serveurs en stdio, et enregistre les outils découverts.
        """
        paths_to_try = []
        if self.config_path:
            paths_to_try.append(self.config_path)
            
        paths_to_try.extend([
            os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-ide", "mcp_config.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp_config.json"),
            "/config/moteur-master/mcp_config.json"
        ])
        
        resolved_path = None
        for path in paths_to_try:
            if path and os.path.exists(path):
                resolved_path = path
                break
                
        if not resolved_path:
            logger.warning(f"[MCP Bridge] Fichier de config MCP introuvable. Tentés : {paths_to_try}")
            return
            
        logger.info(f"[MCP Bridge] Fichier de config MCP résolu : {resolved_path}")
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"[MCP Bridge] Erreur lors de la lecture de la config MCP : {e}")
            return
            
        mcp_servers = config.get("mcpServers", {})
        if not mcp_servers:
            logger.warning("[MCP Bridge] Aucun serveur MCP défini dans la configuration.")
            return
            
        # --- FILTRAGE CONDITIONNEL MCP (R2.1) ---
        servers_to_start = set()
        
        # Charger les règles d'activation
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp_activation_rules.json")
        rules = {}
        if os.path.exists(rules_path):
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                logger.info(f"[MCP Bridge] Règles d'activation chargées depuis : {rules_path}")
            except Exception as re:
                logger.warning(f"[MCP Bridge] Impossible de charger les règles d'activation : {re}")
                
        default_servers = rules.get("default", [])
        servers_to_start.update(default_servers)
        
        if user_prompt:
            logger.info(f"[MCP Bridge] Analyse du prompt pour l'activation conditionnelle MCP : '{user_prompt[:60]}...'")
            user_prompt_lower = user_prompt.lower()
            for rule in rules.get("rules", []):
                keywords = rule.get("keywords", [])
                activate = rule.get("activate", [])
                # Si un des mots-clés est présent dans le prompt
                if any(kw.lower() in user_prompt_lower for kw in keywords):
                    logger.info(f"[MCP Bridge] Match mot-clé RAG/MCP -> activation des serveurs : {activate}")
                    servers_to_start.update(activate)
        else:
            logger.info("[MCP Bridge] Aucun prompt fourni. Démarrage des serveurs MCP par défaut uniquement.")
            
        logger.info(f"[MCP Bridge] Liste des serveurs MCP cibles à démarrer : {list(servers_to_start)}")
        
        for server_name, server_config in mcp_servers.items():
            # Éviter de s'appeler de façon récursive
            if server_name in ["tab5-engine", "tab5_engine"]:
                continue
                
            # Filtrer selon l'activation conditionnelle
            if server_name not in servers_to_start:
                logger.debug(f"[MCP Bridge] Serveur '{server_name}' désactivé (non requis pour cette tâche).")
                continue
                
            command = server_config.get("command")
            args = server_config.get("args", [])
            env_override = server_config.get("env", {})
            
            if not command:
                logger.warning(f"[MCP Bridge] Commande absente pour le serveur '{server_name}', ignoré.")
                continue
                
            # --- ADAPTATION DES COMMANDES ET ARGUMENTS POUR LINUX ---
            is_windows = (sys.platform == 'win32' or os.name == 'nt')
            if not is_windows:
                # 1. Adapter la commande
                if command.endswith(".cmd"):
                    command = command[:-4]
                elif command.endswith(".exe"):
                    command = command[:-4]
                    
                # 2. Adapter les arguments
                new_args = []
                for arg in args:
                    if isinstance(arg, str):
                        # Remplacer les chemins réseau Windows par le chemin Linux local
                        arg_adapted = arg.replace("\\\\${HA_HOST:-192.168.1.x}\\config", "/config")
                        arg_adapted = arg_adapted.replace("e:\\AuxFilsDesIdees", "/config")
                        # Remplacer les antislashs par des slashs
                        arg_adapted = arg_adapted.replace("\\", "/")
                        new_args.append(arg_adapted)
                    else:
                        new_args.append(arg)
                args = new_args
                logger.info(f"[MCP Bridge] Linux Adaptation pour '{server_name}' -> command: '{command}', args: {args}")
                
            # Préparation des variables d'environnement système fusionnées
            env = os.environ.copy()
            if env_override:
                env.update(env_override)
                
            logger.info(f"[MCP Bridge] Tentative de connexion au serveur MCP '{server_name}'...")
            
            try:
                # Configuration des paramètres stdio pour le processus fils
                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env
                )
                
                # Entrer dans le gestionnaire de contexte stdio (lancement du processus) avec un timeout de 8 secondes
                try:
                    read_stream, write_stream = await asyncio.wait_for(
                        self.exit_stack.enter_async_context(stdio_client(server_params)),
                        timeout=8.0
                    )
                    
                    # Entrer dans la session client avec un timeout de 5 secondes
                    session = await asyncio.wait_for(
                        self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream)),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.error(f"[MCP Bridge] Timeout lors du lancement du processus ou de la session client pour '{server_name}'. Le serveur a été ignoré.")
                    continue
                
                # Initialiser le protocole MCP avec le serveur (avec timeout de 5 secondes, R2.2)
                try:
                    await asyncio.wait_for(session.initialize(), timeout=5.0)
                    self.sessions[server_name] = session
                    logger.info(f"[MCP Bridge] Serveur '{server_name}' connecté et initialisé.")
                except asyncio.TimeoutError:
                    logger.error(f"[MCP Bridge] Timeout (5s) lors du handshake d'initialisation avec '{server_name}'. Le serveur a été ignoré.")
                    continue
                
                # Récupérer les outils exposés par ce serveur
                tools_resp = await session.list_tools()
                tools = getattr(tools_resp, "tools", []) if hasattr(tools_resp, "tools") else tools_resp
                
                logger.info(f"[MCP Bridge] '{server_name}' expose {len(tools)} outil(s). Enregistrement...")
                
                for tool in tools:
                    # Génération d'un nom unique préfixé (ex: mcp_sqlite_ha_query)
                    registry_name = f"mcp_{server_name.replace('-', '_')}_{tool.name}"
                    
                    # Factory de wrapper pour capturer la bonne session et le nom d'outil d'origine
                    def make_wrapper(s: ClientSession, orig_name: str, s_name: str) -> Callable:
                        async def wrapper(**kwargs) -> str:
                            # Normalisation des chemins Windows/Linux (T104)
                            # On réécrit E:\ en H:\ pour tous les arguments de type string,
                            # très utile notamment pour le serveur 'filesystem' ou la CLI locale.
                            for k, v in kwargs.items():
                                if isinstance(v, str):
                                    if v.lower().startswith("e:\\"):
                                        kwargs[k] = "H:\\" + v[3:]
                                    elif v.lower().startswith("e:/"):
                                        kwargs[k] = "H:/" + v[3:]
                            
                            logger.info(f"[MCP Bridge] Exécution outil MCP '{orig_name}' sur le serveur '{s_name}' avec {kwargs}")
                            try:
                                result = await s.call_tool(orig_name, arguments=kwargs)
                                
                                # Concaténation propre du contenu de retour
                                content_parts = []
                                for content in result.content:
                                    if hasattr(content, "text"):
                                        content_parts.append(content.text)
                                    elif isinstance(content, dict) and "text" in content:
                                        content_parts.append(content["text"])
                                    else:
                                        content_parts.append(str(content))
                                        
                                return "\n".join(content_parts)
                            except Exception as ex:
                                logger.error(f"[MCP Bridge] Erreur lors de l'exécution de '{orig_name}' : {ex}")
                                return f"Erreur lors de l'appel à l'outil MCP '{orig_name}' : {str(ex)}"
                        return wrapper
                        
                    wrapper_func = make_wrapper(session, tool.name, server_name)
                    
                    # Enregistrement dans le ToolRegistry étendu
                    if hasattr(registry, "register_mcp_tool"):
                        registry.register_mcp_tool(
                            name=registry_name,
                            func=wrapper_func,
                            description=tool.description or f"Outil MCP issu du serveur {server_name}",
                            input_schema=tool.inputSchema
                        )
                    else:
                        registry.register(
                            name=registry_name,
                            func=wrapper_func,
                            description=tool.description or f"Outil MCP issu du serveur {server_name}"
                        )
                        
            except Exception as e:
                logger.error(f"[MCP Bridge] Impossible de démarrer le serveur MCP '{server_name}' : {e}")
                continue
                
    async def stop(self) -> None:
        """Arrête proprement tous les sous-processus et ferme les sessions."""
        logger.info("[MCP Bridge] Fermeture de toutes les sessions MCP...")
        try:
            await self.exit_stack.aclose()
            self.sessions.clear()
            logger.info("[MCP Bridge] Pont MCP arrêté avec succès.")
        except Exception as e:
            logger.error(f"[MCP Bridge] Erreur lors de la fermeture du stack MCP : {e}")
