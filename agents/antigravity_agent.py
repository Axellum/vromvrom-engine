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

import logging
import os
import re

from agents.executor import ExecutorAgent
from core.llm_gateway import LLMGateway
from core.state import StateUpdate, TaskPayload
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# [#T317] Comparés en MOTS ENTIERS (cf. `_objectif_concerne_lvgl`). « ui » ne peut
# pas rester une sous-chaîne : il est présent dans « qui », « aujourd'hui », « celui »…
_MOTS_CLES_LVGL = frozenset({
    "lvgl", "ecran", "écran", "ui", "design", "layout", "widget", "dashboard",
})


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

        import os
        import sys
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
        if self._objectif_concerne_lvgl(payload.task_objective):
            lvgl_context = self._load_lvgl_templates()
            if lvgl_context:
                payload.relevant_context = (
                    payload.relevant_context + "\n\n" + lvgl_context
                ).strip()
                logger.info(f"[{self.name}] Directives LVGL Premium injectées dans le contexte.")

        # Délégation à la boucle ReAct d'ExecutorAgent
        return await super().invoke(payload)

    @staticmethod
    def _objectif_concerne_lvgl(objectif: str) -> bool:
        """
        [#T317] Détecte un objectif de design LVGL/UI — par MOT ENTIER, pas en sous-chaîne.

        La détection cherchait ses mots-clés avec `kw in objective_lower`. Or « ui » est
        une sous-chaîne d'une bonne partie du français courant : « qui », « aujourd'hui »,
        « celui », « lui », « puis », « suis », « depuis », « produit »… La quasi-totalité
        des objectifs déclenchaient donc l'injection des templates LVGL.

        Sans conséquence visible aujourd'hui — `docs/LVGL_PREMIUM_TEMPLATES.md` a été
        supprimé du dépôt, donc `_load_lvgl_templates()` rend une chaîne vide et
        l'injection est un no-op. Le jour où ce fichier revient, chaque tâche contenant
        « qui » se met à payer un contexte de design qu'elle n'a pas demandé. C'est
        exactement le mécanisme qui poussait la cascade vers le tier fort payant (#T295).

        Découvert le 12/08/2026 en cherchant l'origine d'un prompt de 289 966 tokens
        (ce n'était pas la cause — cf. #T314 — mais le défaut, lui, est réel).
        """
        mots = set(re.findall(r"[a-z0-9_àâéèêëîïôöùûüç]+", (objectif or "").lower()))
        return bool(mots & _MOTS_CLES_LVGL)

    def _load_lvgl_templates(self) -> str:
        """
        Charge les directives de design LVGL Premium depuis le fichier templates.
        Retourne une chaîne vide si le fichier n'existe pas.
        """
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            templates_path = os.path.join(project_root, "docs", "LVGL_PREMIUM_TEMPLATES.md")
            if os.path.exists(templates_path):
                with open(templates_path, encoding="utf-8") as f:
                    return "\n--- DIRECTIVES DESIGN LVGL PREMIUM ---\n" + f.read()
        except Exception as le:
            logger.warning(f"Impossible de charger les templates LVGL : {le}")
        return ""
