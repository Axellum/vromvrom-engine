import json
import asyncio
from core.state import TaskPayload, StateUpdate
from agents.base_agent import BaseAgent
from core.llm_gateway import LLMGateway
from tools.tool_registry import ToolRegistry
from core.errors import classify_error
from tools.sanitizer import OutputSanitizer
import logging

logger = logging.getLogger(__name__)

# Nombre maximum de retries internes avant de remonter au DAG/Self-Healing
MAX_TOOL_RETRIES = 2
# Délai de base entre les retries (secondes)
BASE_RETRY_DELAY = 1.0

class ExecutorAgent(BaseAgent):
    """
    Agent autonome chargé d'exécuter des actions techniques en utilisant les outils enregistrés.
    Supporte le mode Sandbox (dry_run) pour les outils destructeurs .
    """
    def __init__(self, llm_gateway: LLMGateway, tool_registry: ToolRegistry, provider_name: str = "deepseek", sandbox_mode: bool = False):
        import sys
        import os
        is_windows = (sys.platform == 'win32' or os.name == 'nt')
        if is_windows:
            os_rules = """2. COMPATIBILITÉ WINDOWS : La machine hôte tourne sous Windows. N'utilise jamais de commandes terminal Unix comme 'ls', 'grep' ou 'cat' via 'run_terminal_command'. Utilise à la place les outils de manipulation de fichier de Python ('read_file', 'write_file') ou des commandes Windows natives ('dir', 'findstr', 'type').
3. SÉCURITÉ DU SYSTÈME : Ne modifie jamais de fichiers système Windows (comme C:\\Windows ou C:\\Windows\\System32). Tout fichier créé ou modifié doit l'être uniquement dans le répertoire de l'application ou du workspace de l'utilisateur (e:\\AuxFilsDesIdees).
4. CHEMINS : Le workspace principal est 'e:\\AuxFilsDesIdees'. Le tab5-engine (code Python) est dans 'e:\\AuxFilsDesIdees\\moteur_agents\\'. Les fichiers core/ sont donc dans 'e:\\AuxFilsDesIdees\\moteur_agents\\core\\'. Utilise TOUJOURS des chemins absolus."""
        else:
            os_rules = """2. COMPATIBILITÉ LINUX : La machine hôte tourne sous Linux (Alpine). N'exécute pas de commandes Windows comme 'dir', 'type', 'findstr' via 'run_terminal_command'. Utilise à la place des commandes Unix standard comme 'ls', 'cat', 'grep', ou de préférence les outils intégrés Python.
3. SÉCURITÉ DU SYSTÈME : Ne tente pas de modifier les répertoires système protégés. Tout fichier créé ou modifié doit l'être uniquement dans le workspace (/config).
4. CHEMINS : Le workspace principal est '/config'. Le tab5-engine (code Python) est dans '/config/moteur-master/'. Les fichiers core/ sont donc dans '/config/moteur-master/core/'. Utilise TOUJOURS des chemins absolus."""

        prompt = f"""Tu es l'ExecutorAgent. Ton but est d'accomplir la tâche technique demandée en utilisant tes outils.
CRITIQUE : Analyse attentivement la section 'RÉSULTATS PHASES PRÉCÉDENTES' dans le contexte.
Si la tâche consiste à écrire ou synthétiser des données (par exemple, fusionner des contenus de fichiers) et que les contenus de ces fichiers ont DÉJÀ été lus et figurent dans la section 'RÉSULTATS PHASES PRÉCÉDENTES', tu ne dois pas les relire.
Utilise DIRECTEMENT ces contenus du contexte et appelle uniquement 'write_file' pour enregistrer le résultat final. Ne fais aucun appel à 'read_file' dans ce cas.

CONSIGNES DE SÉCURITÉ ET DE COMPATIBILITÉ CRITIQUES :
1. PRIVILÉGIE LES OUTILS MCP : Si des outils MCP (commençant par 'mcp_') sont enregistrés et correspondent à ta tâche (par exemple pour Home Assistant ou SQLite), tu DOIS les utiliser en priorité absolue plutôt que de lancer des commandes shell ou de développer des scripts personnalisés.
{os_rules}
5. SOIS PÉDAGOGUE : Explique brièvement en français les opérations effectuées."""

        super().__init__(
            name="executor",
            system_prompt=prompt
        )
        self.gateway = llm_gateway
        self.tool_registry = tool_registry
        self.provider_name = provider_name
        self.sandbox_mode = sandbox_mode
        
        # Sanitizer de sorties d'outils (masquage des secrets)
        self._sanitizer = OutputSanitizer()
        
        # Encapsulation sandbox si le mode dry_run est actif
        self._sandbox = None
        if sandbox_mode:
            from tools.sandbox import SandboxWrapper
            self._sandbox = SandboxWrapper(tool_registry, dry_run=True)
            self.tool_registry = self._sandbox
            logger.info(f"[{self.name}] Mode Sandbox activé : les outils destructeurs généreront des diffs preview.")
        
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        # [Raccourci Déterministe V5.1 - Zero-LLM Latency]
        direct_call = payload.metadata.get("direct_tool_call")
        if direct_call:
            func_name = direct_call.get("name")
            kwargs = direct_call.get("arguments", {})
            logger.info(f"[{self.name}] Court-circuit déterministe détecté : exécution directe de '{func_name}' sans LLM.")
            try:
                res = await self.tool_registry.execute(func_name, kwargs)
                logger.info(f"[{self.name}] Résultat court-circuit direct : {res}")
                
                # Détecter une erreur de l'outil pour propager au Self-Healing
                if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                    return StateUpdate(
                        agent_name=self.name,
                        status="error",
                        result_data=res,
                        next_agent="END",
                        error_message=res
                    )
                return StateUpdate(
                    agent_name=self.name,
                    status="success",
                    result_data=f"Résultat '{func_name}' : {res}",
                    next_agent="END"
                )
            except Exception as e:
                err_msg = f"Exception lors du court-circuit de l'outil '{func_name}' : {e}"
                logger.error(f"[{self.name}] {err_msg}")
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    result_data=err_msg,
                    next_agent="END",
                    error_message=err_msg
                )

        from core.llm_gateway import load_config
        config = load_config()
        tier = payload.metadata.get("model_tier", self.provider_name)
        
        resolved_model_name, provider = self.gateway.get_provider_for_tier(tier, config)
        logger.info(f"[{self.name}] Résolution du modèle pour le Tier '{tier}' : {resolved_model_name}")
        
        user_prompt = f"Objectif : {payload.task_objective}\nContexte pertinent : {payload.relevant_context}"
        tools_schemas = self.tool_registry.get_all_schemas(payload.task_objective)
        
        logger.info(f"[{self.name}] Analyse de la requête et décision...")
        logger.info(f"[{self.name}] System Prompt: {self.system_prompt}")
        logger.info(f"[{self.name}] User Prompt: {user_prompt}")
        
        session_id = payload.metadata.get("session_id")
        
        # Initialisation de l'historique des messages pour la boucle ReAct multi-turn
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        max_turns = payload.metadata.get("max_turns", 10)
        use_search_grounding = payload.metadata.get("use_search_grounding", False)
        
        # Lancement de la boucle ReAct initiale
        last_results, final_text_response, tool_executed, last_tool_error = await self._execute_react_loop(
            messages=messages,
            user_prompt=user_prompt,
            provider=provider,
            tools_schemas=tools_schemas,
            max_turns=max_turns,
            session_id=session_id,
            use_search_grounding=use_search_grounding,
            is_correction=False
        )
        
        # Validation de sécurité : si aucun outil n'a été exécuté sur l'ensemble des tours
        # et que la tâche requiert une action, on lève une erreur.
        if not tool_executed:
            tool_keywords = ["créer", "écrire", "lire", "modifier", "supprimer", "exécuter", "run", "write", "read", "create", "delete", "execute", "call", "git", "file", "fichier", "dossier", "directory", "folder"]
            objective_lower = payload.task_objective.lower()
            needs_tool = any(kw in objective_lower for kw in tool_keywords)
            
            if needs_tool:
                logger.warning(f"[{self.name}] Aucun outil n'a été exécuté pour une tâche requérant une action. Retour d'une erreur.")
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    result_data=final_text_response or "Aucune réponse",
                    next_agent="END",
                    error_message="L'agent exécuteur n'a appelé aucun outil pour réaliser cette tâche. La réponse textuelle était : " + (final_text_response or "")
                )
                
        # Si un outil a échoué en dernier et n'a pas été corrigé
        if last_tool_error:
            logger.error(f"[{self.name}] Échec final de la tâche après auto-correction locale infructueuse. Dernière erreur : {last_tool_error}")
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data="\n".join(last_results) if last_results else last_tool_error,
                next_agent="END",
                error_message=last_tool_error
            )

        # Micro-cycle de revue locale ReAct-Review (Phase 4)
        is_healing_or_correction = (
            payload.metadata.get("is_healing", False) 
            or payload.metadata.get("is_review_correction", False) 
            or payload.metadata.get("is_local_review_correction", False)
        )
        
        max_local_review_rounds = 2
        local_round = 1
        
        while tool_executed and self.name != "ha_agent" and not last_tool_error and not is_healing_or_correction and local_round <= max_local_review_rounds:
            logger.info(f"[{self.name}] [LOCAL-REVIEW] Démarrage du round de revue locale {local_round}/{max_local_review_rounds}...")
            
            # Instanciation locale du ReviewerAgent
            from agents.reviewer import ReviewerAgent
            reviewer = ReviewerAgent(llm_gateway=self.gateway, provider_name="moyen")
            
            review_payload = TaskPayload(
                task_objective=payload.task_objective,
                relevant_context=(
                    f"Voici les modifications apportées par l'agent exécuteur :\n"
                    f"{chr(10).join(last_results) if last_results else final_text_response}"
                ),
                metadata={
                    "session_id": session_id,
                    "model_tier": "moyen"
                }
            )
            
            review_update = await reviewer.invoke(review_payload)
            
            if review_update.status == "success":
                logger.info(f"[{self.name}] [LOCAL-REVIEW] ✅ Verdict : Code validé et approuvé localement au round {local_round}.")
                local_review_feedback = review_update.result_data
                break
            else:
                severity = review_update.metadata.get("severity", "major")
                feedback = review_update.error_message or review_update.result_data
                logger.warning(f"[{self.name}] [LOCAL-REVIEW] ❌ Verdict : Code REJETÉ localement (round {local_round}, sévérité: {severity}).")
                
                if local_round >= max_local_review_rounds:
                    logger.error(f"[{self.name}] [LOCAL-REVIEW] Nombre maximum de rounds de revue locale atteint. Échec.")
                    return StateUpdate(
                        agent_name=self.name,
                        status="error",
                        result_data=feedback,
                        next_agent="END",
                        error_message=f"Le code a été rejeté par la revue locale au round {local_round} : {feedback}"
                    )
                
                # Relancer la boucle ReAct pour corriger localement
                logger.info(f"[{self.name}] [LOCAL-REVIEW] Relancement de la boucle ReAct pour appliquer les corrections demandées...")
                local_round += 1
                
                messages.append({
                    "role": "user",
                    "content": (
                        f"Ton travail précédent a été REJETÉ par le Reviewer local avec le retour suivant :\n"
                        f"{feedback}\n\n"
                        f"Corrige immédiatement ces points dans le code en utilisant tes outils (ex: ré-écriture de fichiers) "
                        f"puis termine pour soumettre les corrections."
                    )
                })
                
                # Relancer la boucle corrective unique
                last_results_corr, final_text_response_corr, tool_executed_corr, last_tool_error = await self._execute_react_loop(
                    messages=messages,
                    user_prompt=user_prompt,
                    provider=provider,
                    tools_schemas=tools_schemas,
                    max_turns=max_turns,
                    session_id=session_id,
                    use_search_grounding=use_search_grounding,
                    is_correction=True
                )
                
                if last_results_corr:
                    last_results.extend(last_results_corr)
                elif final_text_response_corr:
                    last_results.append(final_text_response_corr)
                    final_text_response = final_text_response_corr
                
                if last_tool_error:
                    logger.error(f"[{self.name}] [LOCAL-REVIEW-CORRECTION] Échec de la boucle corrective suite à erreur d'outil : {last_tool_error}")
                    break

        # Si des outils ont été exécutés, on retourne les résultats des outils combinés
        # Sinon on retourne la réponse texte finale.
        result_data = "\n".join(last_results) if last_results else final_text_response
        
        # Ajout des informations sandbox au résultat si actif
        sandbox_info = {}
        if self._sandbox:
            sandbox_info = self._sandbox.get_pending_summary()
            if sandbox_info.get("pending_writes", 0) > 0:
                result_data += f"\n\n[SANDBOX] {sandbox_info['pending_writes']} écriture(s) en attente de validation."
        
        metadata = {"sandbox": sandbox_info} if sandbox_info else {}
        if 'local_review_feedback' in locals() and local_review_feedback:
            metadata["local_review"] = local_review_feedback
            
        return StateUpdate(
            agent_name=self.name,
            status="success",
            result_data=result_data,
            next_agent="END",
            metadata=metadata
        )

    async def _execute_react_loop(
        self,
        messages: list,
        user_prompt: str,
        provider,
        tools_schemas: list,
        max_turns: int,
        session_id: str,
        use_search_grounding: bool,
        is_correction: bool = False
    ) -> tuple[list, str, bool, str | None]:
        """
        Boucle ReAct unique, réutilisée en mode initial et correction de revue.
        """
        tool_executed = False
        last_results = []
        final_text_response = ""
        last_tool_error = None
        prefix = "[LOCAL-REVIEW-CORRECTION]" if is_correction else ""

        for turn in range(max_turns):
            logger.info(f"[{self.name}]{prefix} Lancement du tour {turn + 1}/{max_turns} de la boucle ReAct...")
            
            response = await provider.generate_async(
                system_prompt=self.system_prompt,
                user_prompt=user_prompt,
                tools=tools_schemas,
                session_id=session_id,
                messages=messages,
                use_search_grounding=use_search_grounding,
            )
            
            # Étape 1 : Le LLM a-t-il décidé d'appeler un outil via l'API native (tool_calls) ?
            if isinstance(response, dict) and "tool_calls" in response and response["tool_calls"]:
                tool_calls = response["tool_calls"]
                assistant_message = {
                    "role": "assistant",
                    "content": response.get("content") or "",
                    "tool_calls": tool_calls
                }
                messages.append(assistant_message)
                
                turn_results = []
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    tool_call_id = tool_call.get("id", "call_123")
                    kwargs_str = tool_call["function"]["arguments"]
                    try:
                        kwargs = json.loads(kwargs_str)
                    except Exception as e:
                        res = f"Erreur de décodage des arguments JSON : {e}"
                        kwargs = {}
                        
                    logger.info(f"[{self.name}]{prefix} Appel d'outil détecté : {func_name}")
                    
                    res = await self._execute_tool_with_retry(func_name, kwargs)
                    tool_executed = True
                    
                    res_str = str(res)
                    res_str = self._sanitizer.sanitize(res_str, source=f"tool:{func_name}")
                    
                    if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                        agent_error = classify_error(res, source=f"tool:{func_name}")
                        logger.warning(f"[{self.name}]{prefix} Erreur d'outil détectée ({agent_error.category.value}) de {func_name}: {res}. Tentative d'auto-correction locale...")
                        last_tool_error = f"Erreur dans l'outil '{func_name}' : {res_str}"
                        
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": func_name,
                            "content": f"Erreur lors de l'exécution : {res_str}. Corrige cette erreur et réessaie."
                        }
                        messages.append(tool_message)
                    else:
                        last_tool_error = None
                        turn_results.append(f"Résultat '{func_name}' : {res_str}")
                        
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": func_name,
                            "content": res_str
                        }
                        messages.append(tool_message)
                    
                last_results.extend(turn_results)
                continue
                
            # Étape 2 : Fallback JSON
            text_response = ""
            if isinstance(response, str):
                text_response = response
            elif isinstance(response, dict) and "content" in response:
                text_response = response.get("content") or ""
                
            json_blocks = []
            if text_response:
                import re
                json_blocks = re.findall(r"```json\s*(.*?)\s*```", text_response, re.DOTALL)
                
            if json_blocks:
                messages.append({"role": "assistant", "content": text_response})
                
                turn_results = []
                for block in json_blocks:
                    try:
                        tool_call_data = json.loads(block.strip())
                        if isinstance(tool_call_data, dict) and "action" in tool_call_data:
                            func_name = tool_call_data["action"]
                            kwargs = tool_call_data.get("params", {})
                            
                            if func_name == "write_file":
                                if "file_path" in kwargs and "filepath" not in kwargs:
                                    kwargs["filepath"] = kwargs.pop("file_path")
                                if "contents" in kwargs and "content" not in kwargs:
                                    kwargs["content"] = kwargs.pop("contents")
                            elif func_name == "read_file":
                                if "file_path" in kwargs and "filepath" not in kwargs:
                                    kwargs["filepath"] = kwargs.pop("file_path")
                                    
                            logger.info(f"[{self.name}]{prefix} Appel d'outil extrait du texte (fallback JSON) : {func_name}")
                            res = await self._execute_tool_with_retry(func_name, kwargs)
                            tool_executed = True
                            
                            res_str = str(res)
                            res_str = self._sanitizer.sanitize(res_str, source=f"tool:{func_name}")
                            
                            if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                                agent_error = classify_error(res, source=f"tool:{func_name}")
                                logger.warning(f"[{self.name}]{prefix} Erreur d'outil détectée ({agent_error.category.value}) de {func_name} (fallback JSON): {res}. Tentative d'auto-correction locale...")
                                last_tool_error = f"Erreur dans l'outil '{func_name}' : {res_str}"
                                
                                messages.append({
                                    "role": "user", 
                                    "content": f"Erreur lors de l'exécution de '{func_name}' : {res_str}. Corrige cette erreur et réessaie."
                                })
                            else:
                                last_tool_error = None
                                turn_results.append(f"Résultat '{func_name}' : {res_str}")
                                messages.append({"role": "user", "content": f"Résultat '{func_name}' : {res_str}"})
                    except Exception as e:
                        logger.warning(f"Échec de l'analyse du bloc JSON extrait : {e}")
                        
                if turn_results:
                    last_results.extend(turn_results)
                    continue
            
            # Étape 3 : Réponse finale
            final_text_response = text_response
            break

        return last_results, final_text_response, tool_executed, last_tool_error

    async def _execute_tool_with_retry(
        self, func_name: str, kwargs: dict, max_retries: int = MAX_TOOL_RETRIES
    ) -> str:
        """
        Exécute un outil avec retry intelligent.
        
        Les erreurs retriables (réseau, timeout, rate_limit) déclenchent
        un retry automatique avec backoff exponentiel. Les erreurs non-retriables
        (logique, auth, permission) sont remontées immédiatement.
        
        Args:
            func_name: Nom de l'outil à exécuter
            kwargs: Arguments de l'outil
            max_retries: Nombre maximum de tentatives (défaut: MAX_TOOL_RETRIES)
            
        Returns:
            Le résultat de l'outil (string)
        """
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                res = await self.tool_registry.execute(func_name, kwargs)
                
                # Vérifier si le résultat est une erreur retriable
                if isinstance(res, str) and (res.startswith("Erreur") or res.lower().startswith("erreur")):
                    agent_error = classify_error(res, source=f"tool:{func_name}")
                    
                    if agent_error.is_retriable and attempt < max_retries:
                        delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))  # Backoff exponentiel
                        logger.warning(
                            f"[{self.name}] Erreur retriable ({agent_error.category.value}) "
                            f"de '{func_name}' (tentative {attempt}/{max_retries}). "
                            f"Retry dans {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        last_error = res
                        continue
                    else:
                        # Erreur non-retriable ou retries épuisés
                        return res
                
                # Succès — retourner le résultat
                return res
                
            except Exception as e:
                agent_error = classify_error(str(e), source=f"tool:{func_name}")
                
                if agent_error.is_retriable and attempt < max_retries:
                    delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"[{self.name}] Exception retriable ({agent_error.category.value}) "
                        f"de '{func_name}' (tentative {attempt}/{max_retries}). "
                        f"Retry dans {delay:.1f}s... Exception: {e}"
                    )
                    await asyncio.sleep(delay)
                    last_error = str(e)
                    continue
                else:
                    raise  # Remonter l'exception non-retriable
        
        # Retries épuisés — retourner la dernière erreur
        return last_error or f"Erreur : retries épuisés pour '{func_name}'"
