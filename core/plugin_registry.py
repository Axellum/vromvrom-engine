"""
core/plugin_registry.py — Découverte et chargement automatique de plugins.

Scanne le dossier plugins/ au démarrage du moteur et charge les agents
custom définis dans chaque sous-dossier. Chaque plugin est un dossier
contenant un fichier agent.py avec une classe héritant de BaseAgent.

Structure attendue :
    plugins/
    ├── mon_plugin/
    │   ├── plugin.json        # Métadonnées (nom, version, description)
    │   └── agent.py           # Classe d'agent custom
    └── autre_plugin/
        ├── plugin.json
        └── agent.py

Créé dans le cadre de l'audit V5.5 (Axe A2 — PluginRegistry).
"""

import os
import json
import importlib
import importlib.util
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Dossier racine des plugins (relatif au moteur_agents/)
_PLUGINS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins",
)


class PluginInfo:
    """Métadonnées d'un plugin chargé."""

    def __init__(
        self,
        name: str,
        version: str = "0.1.0",
        description: str = "",
        author: str = "",
        agent_class: Optional[type] = None,
        enabled: bool = True,
        path: str = "",
    ):
        self.name = name
        self.version = version
        self.description = description
        self.author = author
        self.agent_class = agent_class
        self.enabled = enabled
        self.path = path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "enabled": self.enabled,
            "has_agent": self.agent_class is not None,
            "path": self.path,
        }


class PluginRegistry:
    """
    Registre de plugins pour la découverte et le chargement automatique.

    Usage:
        registry = PluginRegistry()
        plugins = registry.discover()
        for plugin in plugins:
            agent = registry.create_agent(plugin.name, llm_gateway=gw)
            engine.register_agent(agent)
    """

    def __init__(self, plugins_dir: str = None):
        """
        Args:
            plugins_dir: Chemin du dossier de plugins. Par défaut : moteur_agents/plugins/
        """
        self._plugins_dir = plugins_dir or _PLUGINS_DIR
        self._plugins: Dict[str, PluginInfo] = {}
        self._loaded = False

    def discover(self) -> List[PluginInfo]:
        """
        Scanne le dossier plugins/ et charge les métadonnées de chaque plugin.

        Returns:
            Liste des PluginInfo trouvés.
        """
        if not os.path.isdir(self._plugins_dir):
            logger.info(
                f"[PLUGIN REGISTRY] Dossier plugins non trouvé : {self._plugins_dir}. "
                "Créez-le pour activer les plugins."
            )
            return []

        discovered = []

        for entry in os.listdir(self._plugins_dir):
            plugin_path = os.path.join(self._plugins_dir, entry)
            if not os.path.isdir(plugin_path):
                continue

            # Ignorer les dossiers spéciaux
            if entry.startswith((".", "_", "__")):
                continue

            # Charger plugin.json si présent
            meta_file = os.path.join(plugin_path, "plugin.json")
            meta = {}
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception as e:
                    logger.warning(f"[PLUGIN REGISTRY] Erreur lecture {meta_file} : {e}")

            # Charger la classe d'agent si agent.py existe.
            # SÉCURITÉ : charger un agent.py exécute son code (exec_module). Pour
            # éviter toute exécution de code arbitraire au démarrage, le chargement
            # n'est effectué que si MOTEUR_ENABLE_PLUGINS=1 est explicitement défini.
            agent_class = None
            agent_file = os.path.join(plugin_path, "agent.py")
            if os.path.exists(agent_file):
                if os.environ.get("MOTEUR_ENABLE_PLUGINS", "").strip() in ("1", "true", "True"):
                    agent_class = self._load_agent_class(entry, agent_file)
                else:
                    logger.warning(
                        f"[PLUGIN REGISTRY] 🔒 agent.py de '{entry}' NON chargé "
                        "(MOTEUR_ENABLE_PLUGINS désactivé). Métadonnées seules."
                    )

            plugin_info = PluginInfo(
                name=meta.get("name", entry),
                version=meta.get("version", "0.1.0"),
                description=meta.get("description", ""),
                author=meta.get("author", ""),
                agent_class=agent_class,
                enabled=meta.get("enabled", True),
                path=plugin_path,
            )

            self._plugins[plugin_info.name] = plugin_info
            discovered.append(plugin_info)

            status = "✅" if agent_class else "📦"
            logger.info(
                f"[PLUGIN REGISTRY] {status} Plugin '{plugin_info.name}' v{plugin_info.version} "
                f"découvert ({plugin_path})"
            )

        self._loaded = True
        logger.info(
            f"[PLUGIN REGISTRY] Découverte terminée : {len(discovered)} plugin(s) trouvé(s)."
        )

        return discovered

    def _load_agent_class(self, plugin_name: str, agent_file: str) -> Optional[type]:
        """
        Charge dynamiquement la classe d'agent depuis un fichier agent.py.

        La convention est que la classe doit hériter de BaseAgent et être
        la première classe définie dans le fichier.
        """
        try:
            spec = importlib.util.spec_from_file_location(
                f"plugins.{plugin_name}.agent", agent_file
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Chercher la première classe qui hérite de BaseAgent
            from agents.base_agent import BaseAgent

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseAgent)
                    and attr is not BaseAgent
                ):
                    logger.info(
                        f"[PLUGIN REGISTRY] Agent '{attr_name}' chargé depuis "
                        f"plugins/{plugin_name}/agent.py"
                    )
                    return attr

            logger.warning(
                f"[PLUGIN REGISTRY] Aucune classe BaseAgent trouvée dans "
                f"plugins/{plugin_name}/agent.py"
            )
            return None

        except Exception as e:
            logger.error(
                f"[PLUGIN REGISTRY] Erreur de chargement de "
                f"plugins/{plugin_name}/agent.py : {e}"
            )
            return None

    def create_agent(self, plugin_name: str, **kwargs) -> Optional[Any]:
        """
        Instancie un agent à partir d'un plugin chargé.

        Args:
            plugin_name: Nom du plugin
            **kwargs: Arguments passés au constructeur de l'agent
                      (llm_gateway, tool_registry, etc.)

        Returns:
            Instance de l'agent, ou None si le plugin n'a pas d'agent.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            logger.warning(f"[PLUGIN REGISTRY] Plugin '{plugin_name}' non trouvé.")
            return None

        if not plugin.agent_class:
            logger.warning(
                f"[PLUGIN REGISTRY] Plugin '{plugin_name}' n'a pas d'agent."
            )
            return None

        if not plugin.enabled:
            logger.info(
                f"[PLUGIN REGISTRY] Plugin '{plugin_name}' désactivé (enabled=false)."
            )
            return None

        try:
            agent = plugin.agent_class(**kwargs)
            logger.info(
                f"[PLUGIN REGISTRY] Agent '{agent.name}' instancié "
                f"depuis le plugin '{plugin_name}'."
            )
            return agent
        except Exception as e:
            logger.error(
                f"[PLUGIN REGISTRY] Erreur d'instanciation de l'agent "
                f"du plugin '{plugin_name}' : {e}"
            )
            return None

    def get_all_plugins(self) -> List[Dict[str, Any]]:
        """Retourne les métadonnées de tous les plugins découverts."""
        return [p.to_dict() for p in self._plugins.values()]

    def get_enabled_plugins(self) -> List[PluginInfo]:
        """Retourne uniquement les plugins activés ayant un agent."""
        return [
            p for p in self._plugins.values()
            if p.enabled and p.agent_class is not None
        ]

    @property
    def plugins_dir(self) -> str:
        return self._plugins_dir

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)
