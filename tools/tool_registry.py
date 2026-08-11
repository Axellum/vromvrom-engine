"""
tools/tool_registry.py — Registre central des outils exposés aux agents LLM.

Expose les fonctions Python (tools/api.py, system.py, terminal.py, sandbox.py...)
aux agents via un schéma JSON (tool calling), avec rate limiting. Utilisé par
tous les agents héritant d'ExecutorAgent et par core/mcp_bridge.py pour
enregistrer les outils MCP tiers découverts.
"""
import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# [#T273] Outils capables de modifier le système ou le dépôt. Sert à tracer
# combien d'actions s'exécutent hors du point d'approbation humaine, qui ne
# couvre aujourd'hui que le chemin DAG (cf. core/router.py, garde de routage).
# Noms relevés dans `tools/registry_setup.py`, pas devinés : `git_rollback_checkpoint`
# y figure parce qu'il ANNULE des modifications (il détruit du travail), et
# `git_apply_checkpoint` parce qu'il fusionne.
OUTILS_A_RISQUE = frozenset({
    "write_file",
    "run_terminal_command",
    "git_rollback_checkpoint",
    "git_apply_checkpoint",
})

class ToolRegistry:
    """
    Gestionnaire centralisé des outils (Tool Calling).
    Expose des fonctions Python de manière sécurisée aux agents LLM.
    """
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        # Compteur d'appels par outil pour rate limiting et observabilité
        self._call_counts: dict[str, int] = {}  # nom_outil -> nb appels dans la session
        self._call_timestamps: dict[str, list[float]] = {}  # nom_outil -> [timestamps]
        self._blocked_tools: dict[str, str] = {}  # nom_outil -> raison du blocage
        # [#T233] Permissions par agent : nom_agent -> liste des outils autorisés.
        # Un agent ABSENT du dictionnaire (ou dont la valeur est None) voit tous
        # les outils ; [] signifie « aucun outil ». Le registre étant partagé par
        # tous les agents, le filtrage s'applique à la fourniture des schémas et
        # à l'exécution, jamais à l'enregistrement.
        self._agent_allowed_tools: dict[str, list[str]] = {}

    def register(self, name: str, func: Callable, description: str) -> None:
        """
        Enregistre une fonction Python et génère automatiquement son schéma JSON
        (format OpenAI function calling).
        """
        self._tools[name] = func

        # Introspection basique pour l'MVP (Génération automatique des paramètres)
        sig = inspect.signature(func)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            # Simplification: tout est considéré comme string dans le MVP
            properties[param_name] = {"type": "string"}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        self._schemas[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
        logger.debug(f"Outil enregistré : {name}")

    def register_mcp_tool(self, name: str, func: Callable, description: str, input_schema: dict[str, Any]) -> None:
        """
        Enregistre un outil MCP avec son schéma d'entrée déjà défini.
        """
        self._tools[name] = func
        self._schemas[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": input_schema
            }
        }
        logger.debug(f"Outil MCP enregistré : {name}")

    def set_agent_allowed_tools(self, agent_name: str, allowed_tools: list[str] | None) -> None:
        """
        [#T233] Déclare les outils autorisés d'un agent (ou lève sa restriction).

        Sémantique du champ `allowed_tools` (config.json / agents_workflows.json) :
        - absent ou null → tous les outils (aucune restriction) ;
        - [] → aucun outil ;
        - liste de noms → seuls ces outils, à l'exclusion de tous les autres.

        Le registre est partagé par tous les agents : cette déclaration ne touche
        pas à l'enregistrement des outils, elle n'est consultée qu'au moment de
        fournir les schémas (get_all_schemas) et d'exécuter (execute).
        """
        if allowed_tools is None:
            self._agent_allowed_tools.pop(agent_name, None)
            return
        if isinstance(allowed_tools, str) or not isinstance(allowed_tools, (list, tuple, set)):
            logger.warning(
                f"[T233] allowed_tools invalide pour l'agent '{agent_name}' "
                f"({allowed_tools!r}) : traité comme « tous les outils »."
            )
            self._agent_allowed_tools.pop(agent_name, None)
            return
        self._agent_allowed_tools[agent_name] = [str(t) for t in allowed_tools]

    def _filtrer_par_permissions(
        self, schemas: list[dict[str, Any]], agent_name: str | None
    ) -> list[dict[str, Any]]:
        """[#T233] Restreint une liste de schémas aux outils autorisés de l'agent.

        Agent inconnu ou sans restriction → liste inchangée (non-régression).
        """
        if agent_name is None:
            return schemas
        allowed = self._agent_allowed_tools.get(agent_name)
        if allowed is None:
            return schemas
        return [s for s in schemas if s["function"]["name"] in allowed]

    def get_all_schemas(self, task_objective: str = None, agent_name: str = None) -> list[dict[str, Any]]:
        """
        Retourne la liste des définitions JSON pour l'injecter dans la requête LLM.

        Deux filtres successifs, appliqués AU MOMENT de fournir les schémas (le
        registre est partagé par tous les agents) :
        1. Si task_objective est fourni, filtrage dynamique (JIT) des outils MCP
           par mots-clés pour éviter de surcharger la fenêtre de contexte ;
        2. [#T233] Si agent_name a une restriction déclarée, seuls les outils de
           sa liste `allowed_tools` sont exposés (absent/null = tous, [] = aucun).
        """
        if not task_objective:
            return self._filtrer_par_permissions(list(self._schemas.values()), agent_name)

        import json
        import os

        # Charger les règles de filtrage MCP
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp_activation_rules.json")
        active_mcp_servers = set()

        if os.path.exists(rules_path):
            try:
                with open(rules_path, encoding="utf-8") as f:
                    rules_data = json.load(f)

                obj_lower = task_objective.lower()
                for rule in rules_data.get("rules", []):
                    # Si un mot clé de la règle est trouvé dans l'objectif, on active ces serveurs
                    if any(kw in obj_lower for kw in rule.get("keywords", [])):
                        for srv in rule.get("activate", []):
                            active_mcp_servers.add(srv.replace("-", "_"))

                # Toujours ajouter les serveurs par défaut
                for srv in rules_data.get("default", []):
                    active_mcp_servers.add(srv.replace("-", "_"))
            except Exception as e:
                logger.error(f"Erreur lors du traitement de mcp_activation_rules.json : {e}")

        # Si aucune règle n'a été lue ou activée, on n'applique pas de filtre
        if not active_mcp_servers:
            return self._filtrer_par_permissions(list(self._schemas.values()), agent_name)

        filtered_schemas = []
        for name, schema in self._schemas.items():
            # Les outils MCP sont préfixés par 'mcp_'
            if name.startswith("mcp_"):
                # Extraire le nom du serveur du préfixe, ex: mcp_sqlite_ha_query -> sqlite_ha
                is_active = False
                for srv in active_mcp_servers:
                    if name.startswith(f"mcp_{srv}_"):
                        is_active = True
                        break
                if is_active:
                    filtered_schemas.append(schema)
            else:
                # Conserver les outils natifs non-MCP
                filtered_schemas.append(schema)

        logger.info(f"[JIT MCP] {len(filtered_schemas)} outil(s) exposé(s) sur {len(self._schemas)} (Serveurs actifs: {list(active_mcp_servers)})")
        return self._filtrer_par_permissions(filtered_schemas, agent_name)

    # [AUDIT QW3] Patterns de secrets à masquer dans les sorties d'outils
    # Empêche la fuite de clés API, tokens Bearer, mots de passe vers les LLM cloud
    _SECRET_PATTERNS = None  # Compilé à la première utilisation (lazy)

    @staticmethod
    def _sanitize_output(result: Any) -> Any:
        """Masque les patterns de secrets dans les sorties d'outils avant renvoi au LLM."""
        if not isinstance(result, str):
            return result
        import re
        # Compilation lazy des patterns (une seule fois)
        if ToolRegistry._SECRET_PATTERNS is None:
            ToolRegistry._SECRET_PATTERNS = [
                # Clés API (sk-..., key-..., etc.)
                (re.compile(r'(sk-[a-zA-Z0-9]{10,})'), r'sk-***MASKED***'),
                (re.compile(r'(key-[a-zA-Z0-9]{10,})'), r'key-***MASKED***'),
                # Tokens Bearer
                (re.compile(r'(Bearer\s+)[a-zA-Z0-9._\-]{20,}', re.IGNORECASE), r'\1***MASKED***'),
                # Clés API génériques (longues chaînes hexadécimales précédées de "api_key=", "token=", etc.)
                (re.compile(r'((?:api[_-]?key|token|secret|password|passwd|pwd)\s*[=:]\s*)["\']?[a-zA-Z0-9._\-]{16,}["\']?', re.IGNORECASE), r'\1***MASKED***'),
                # Variables d'environnement sensibles dans les dumps (.env, export, set)
                (re.compile(r'((?:DEEPSEEK|GEMINI|OPENAI|ANTHROPIC|CLAUDE)[_A-Z]*(?:KEY|TOKEN|SECRET)\s*=\s*)[^\s\r\n]+', re.IGNORECASE), r'\1***MASKED***'),
            ]
        for pattern, replacement in ToolRegistry._SECRET_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    # Timeouts configurables par catégorie d'outil (en secondes)
    _TOOL_TIMEOUTS = {
        "run_terminal_command": 30.0,
        "read_file": 10.0,
        "write_file": 10.0,
        "call_api": 20.0,
        # [#T245] Un MapReduce enchaîne N générations LLM parallèles puis une
        # agrégation : il se compte en minutes, pas en secondes.
        "map_reduce": 900.0,
        "default": 60.0,  # Pour les outils MCP et autres
    }

    # Limites d'appels par session par catégorie d'outil
    _TOOL_RATE_LIMITS: dict[str, int] = {
        "run_terminal_command": 50,    # Commandes terminal : max 50 par session
        "write_file": 100,             # Écritures fichier : max 100 par session
        "read_file": 200,              # Lectures fichier : max 200 par session
        "map_reduce": 5,               # Fan-out DAG : max 5 par session (#T245)
        "mcp_default": 80,             # Outils MCP par défaut : max 80 par session
        "default": 150,                # Tout autre outil : max 150 par session
    }

    async def execute(self, name: str, kwargs: dict[str, Any], agent_name: str = None) -> Any:
        """Exécute l'outil demandé de manière asynchrone avec timeout, rate limiting et sanitization."""
        # [#T233] Refus explicite des outils hors périmètre : ni silence, ni
        # « outil inexistant » (qui pousserait l'agent à contourner, cf. #T275).
        # Le refus nomme l'agent et l'outil, rappelle les outils autorisés, et
        # est journalisé (trace d'audit). Vérifié avant le rate limiting : une
        # tentative refusée ne consomme pas de quota de session.
        if agent_name is not None:
            allowed = self._agent_allowed_tools.get(agent_name)
            if allowed is not None and name not in allowed:
                refus = (
                    f"Erreur permission : l'agent '{agent_name}' n'est pas autorisé à "
                    f"utiliser l'outil '{name}'. Outils autorisés : "
                    f"{', '.join(allowed) if allowed else 'aucun'}. "
                    f"Cette tentative est journalisée ; utilise un outil de ta liste, "
                    f"tout contournement sera refusé de la même façon."
                )
                logger.warning(f"[T233-PERMISSION] {refus}")
                return refus

        if name not in self._tools:
            return f"Erreur critique: Tentative d'appel d'un outil non autorisé ou inexistant '{name}'."

        # Vérification du rate limit avant exécution
        rate_limit_error = self._check_rate_limit(name)
        if rate_limit_error:
            return rate_limit_error

        # Validation des arguments par rapport au schéma JSON enregistré (notamment pour les outils MCP)
        if name in self._schemas:
            schema = self._schemas[name]["function"].get("parameters")
            if schema:
                try:
                    import jsonschema
                    # s'assurer que kwargs est bien un dictionnaire pour la validation
                    if not isinstance(kwargs, dict):
                        return f"Erreur de validation : les arguments pour l'outil '{name}' doivent être fournis sous forme de dictionnaire JSON."
                    jsonschema.validate(instance=kwargs, schema=schema)
                except jsonschema.ValidationError as ve:
                    error_msg = f"Erreur de validation des arguments pour l'outil '{name}' : {ve.message}. Schéma attendu : {schema}"
                    logger.warning(error_msg)
                    return error_msg
                except Exception as ve_err:
                    logger.error(f"Erreur interne lors de la validation du schéma pour '{name}' : {ve_err}")

        # [#T273] Trace d'audit des actions à risque. Le point d'approbation
        # humaine (#T267) est posé sur le chemin DAG uniquement : tout outil qui
        # écrit ou exécute par un autre chemin agit sans supervision possible.
        # Cette ligne ne bloque rien — elle rend la surface MESURABLE, faute de
        # quoi on ne saurait pas ce qui reste à couvrir. Volontairement en `info`
        # et non en `warning` : une alerte permanente et sans objet apprend à
        # ignorer le journal (c'est le mécanisme de #T266).
        if name in OUTILS_A_RISQUE:
            logger.info(f"[T273-AUDIT] Outil à risque exécuté : {name}")

        func = self._tools[name]
        # Timeout configurable par outil
        timeout = self._TOOL_TIMEOUTS.get(name, self._TOOL_TIMEOUTS["default"])
        try:
            logger.info(f"Exécution de l'outil : {name} (timeout: {timeout}s)")
            if inspect.iscoroutinefunction(func):
                coro = func(**kwargs)
            else:
                coro = asyncio.to_thread(func, **kwargs)
            # Exécution avec garde-fou de timeout
            result = await asyncio.wait_for(coro, timeout=timeout)
            # [AUDIT QW3] Sanitization des secrets avant renvoi au LLM
            return self._sanitize_output(result)
        except TimeoutError:
            logger.error(f"Outil '{name}' a dépassé le timeout de {timeout}s")
            return f"Erreur : l'outil '{name}' a dépassé le timeout de {timeout}s. Vérifiez l'état de la ressource ou simplifiez la requête."
        except Exception as e:
            logger.error(f"Outil '{name}' a échoué: {e}")
            return f"Erreur interne de l'outil {name}: {str(e)}"

    def _check_rate_limit(self, name: str) -> str | None:
        """
        Vérifie si un outil a dépassé sa limite d'appels par session.
        
        Returns:
            Message d'erreur si la limite est atteinte, None sinon.
        """
        # Déterminer la limite applicable
        if name in self._TOOL_RATE_LIMITS:
            limit = self._TOOL_RATE_LIMITS[name]
        elif name.startswith("mcp_"):
            limit = self._TOOL_RATE_LIMITS.get("mcp_default", 80)
        else:
            limit = self._TOOL_RATE_LIMITS.get("default", 150)

        current_count = self._call_counts.get(name, 0)

        if current_count >= limit:
            msg = (
                f"Erreur rate_limit : l'outil '{name}' a atteint sa limite de "
                f"{limit} appels par session ({current_count} appels effectués). "
                f"Utilisez un autre outil ou attendez la prochaine session."
            )
            logger.warning(f"[RATE LIMIT] {msg}")
            self._blocked_tools[name] = msg
            return msg

        # Incrémenter le compteur et enregistrer le timestamp
        self._call_counts[name] = current_count + 1
        if name not in self._call_timestamps:
            self._call_timestamps[name] = []
        self._call_timestamps[name].append(time.time())

        return None

    def get_usage_stats(self) -> dict[str, Any]:
        """
        Retourne les statistiques d'utilisation des outils.
        
        Returns:
            Dictionnaire avec le nombre d'appels par outil, les outils bloqués,
            et le top 5 des outils les plus utilisés.
        """
        sorted_tools = sorted(
            self._call_counts.items(), key=lambda x: x[1], reverse=True
        )
        return {
            "total_calls": sum(self._call_counts.values()),
            "calls_per_tool": dict(self._call_counts),
            "blocked_tools": dict(self._blocked_tools),
            "top_5": dict(sorted_tools[:5]),
        }

    def reset_counters(self) -> None:
        """
        Réinitialise les compteurs d'appels.
        Appelé au début de chaque nouvelle session.
        """
        self._call_counts.clear()
        self._call_timestamps.clear()
        self._blocked_tools.clear()
        logger.info("[TOOL REGISTRY] Compteurs d'appels réinitialisés.")
