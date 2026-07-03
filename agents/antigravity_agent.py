"""
agents/antigravity_agent.py — Agent Expert LVGL/Architecture du tab5-engine.

Refactorisé pour hériter d'ExecutorAgent au lieu de BaseAgent,
ce qui lui donne accès à la boucle ReAct et aux outils (read_file, write_file, etc.).

L'agent conserve :
- Son system prompt spécialisé LVGL/Architecture
- Le chargement dynamique des templates LVGL Premium
- Le tier fort par défaut

Héritage :
  BaseAgent → ExecutorAgent → AntigravityAgent
  (comme HACommandAgent)
"""

import os
import logging

from agents.executor import ExecutorAgent
from core.state import TaskPayload, StateUpdate
from core.llm_gateway import LLMGateway
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class AntigravityAgent(ExecutorAgent):
    """
    Agent expert spécialisé en raisonnement avancé, design LVGL et architecture.
    
    Hérite d'ExecutorAgent pour bénéficier de la boucle ReAct multi-tours
    avec accès complet aux outils (read_file, write_file, run_terminal_command, etc.).
    
    Passage de BaseAgent → ExecutorAgent (A4 Audit).
    """

    def __init__(self, llm_gateway: LLMGateway, tool_registry: ToolRegistry = None,
                 provider_name: str = "gemini", sandbox_mode: bool = False):
        # Initialisation via ExecutorAgent avec un ToolRegistry optionnel
        # Si pas de registry fourni, on en crée un vide (mode dégradé sans outils)
        if tool_registry is None:
            tool_registry = ToolRegistry()
        
        super().__init__(
            llm_gateway=llm_gateway,
            tool_registry=tool_registry,
            provider_name=provider_name,
            sandbox_mode=sandbox_mode
        )
        # Override du nom et du system prompt hérité d'ExecutorAgent
        self.name = "antigravity_agent"
        
        import sys
        import os
        is_windows = (sys.platform == 'win32' or os.name == 'nt')
        if is_windows:
            os_rule = "5. COMPATIBILITÉ WINDOWS : N'utilise jamais de commandes Unix (ls, grep, cat) via le terminal. Utilise à la place les outils de manipulation de fichiers de Python ('read_file', 'write_file') ou des commandes Windows natives."
        else:
            os_rule = "5. COMPATIBILITÉ LINUX : N'utilise pas de commandes Windows (dir, type, findstr) via le terminal. Utilise à la place des commandes Unix standard ou de préférence les outils de manipulation de fichiers de Python."

        self.system_prompt = f"""Tu es l'AntigravityAgent, un agent expert spécialisé en :
- Design LVGL premium (widgets, layouts, animations, thèmes)
- Architecture logicielle (patterns, refactoring, modularisation)
- Raisonnement avancé (analyse complexe, debugging multi-couches)

Tu as accès aux outils de l'ExecutorAgent (lecture/écriture de fichiers, commandes terminal, APIs).
Utilise-les pour exécuter concrètement tes recommandations au lieu de simplement générer du texte.

CONSIGNES :
1. Sois extrêmement rigoureux et méthodique
2. Explique brièvement chaque étape en français (pédagogie)
3. Pour les modifications de fichiers, utilise les outils read_file et write_file
4. Valide toujours le résultat final de tes modifications
{os_rule}"""

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        """
        Exécution de l'agent expert avec injection contextuelle des templates LVGL.
        
        Si la tâche concerne le design LVGL/UI, les directives Premium sont
        automatiquement injectées dans le contexte avant la boucle ReAct.
        """
        # Chargement conditionnel des directives LVGL Premium
        objective_lower = payload.task_objective.lower()
        lvgl_keywords = ["lvgl", "ecran", "écran", "ui", "design", "layout", "widget", "dashboard"]
        
        if any(kw in objective_lower for kw in lvgl_keywords):
            lvgl_context = self._load_lvgl_templates()
            if lvgl_context:
                payload.relevant_context = (
                    payload.relevant_context + "\n\n" + lvgl_context
                ).strip()
                logger.info(f"[{self.name}] Directives LVGL Premium injectées dans le contexte.")

        # Délégation à la boucle ReAct d'ExecutorAgent
        return await super().invoke(payload)

    def _load_lvgl_templates(self) -> str:
        """
        Charge les directives de design LVGL Premium depuis le fichier templates.
        Retourne une chaîne vide si le fichier n'existe pas.
        """
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            templates_path = os.path.join(project_root, "docs", "LVGL_PREMIUM_TEMPLATES.md")
            if os.path.exists(templates_path):
                with open(templates_path, "r", encoding="utf-8") as f:
                    return "\n--- DIRECTIVES DESIGN LVGL PREMIUM ---\n" + f.read()
        except Exception as le:
            logger.warning(f"Impossible de charger les templates LVGL : {le}")
        return ""
