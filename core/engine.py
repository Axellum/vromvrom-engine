"""
core/engine.py — Moteur d'exécution central du tab5-engine.

Orchestrateur principal : reçoit un payload du Router, exécute l'agent approprié,
gère la file de tâches DAG via DAGRunner, la revue via ReviewLoop,
et les hooks post-exécution (Git, mémoire épisodique, Langfuse, consolidation).

Historique :
- V5.0  : Logique monolithique (~840 lignes)
- V5.5  : Refactoring A1 — extraction dag_runner.py, healing.py, review_loop.py
- V8    : Hook _consolidate_memory (decay, GC graphe, sync memory.db)
- V9    : Self-Healing du contexte (ContextSelfHealer), aiosqlite DAG
- V11   : Event Sourcing, Elo meta-learning, Workflow-as-Code

Modules extraits :
- dag_runner.py   : Exécution parallèle du DAG (ordonnancement réactif PriorityQueue)
- healing.py      : Self-Healing (re-planification + correction)
- review_loop.py  : Boucle Reviewer-Correction post-DAG
- hitl.py         : Human-In-The-Loop (pause/resume/approbation)

L'interface publique (Engine, __init__, register_agent, run) reste inchangée
pour garantir la rétrocompatibilité avec factory.py, gui_server.py, main.py, etc.
"""

import asyncio
import logging
import os
import subprocess
import time

from agents.base_agent import BaseAgent
from core.dag_runner import DAGRunner
from core.hitl import (
    DECISION_APPROUVE,
    DECISION_EN_ATTENTE,
    DECISION_REJETE,
    HITLManager,
)
from core.review_loop import ReviewLoop
from core.state import ExecutionPhase, GlobalState, StateUpdate, TaskPayload

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# [PHASE 1 - M5] Classification du risque HITL par OUTIL ciblé.
# La sévérité ne dépend plus seulement du libellé de la tâche (keyword matching
# contournable) mais aussi de l'outil/agent réellement visé par le plan.
# ──────────────────────────────────────────────────────────────────
HITL_CRITICAL_TOOLS = {"run_terminal_command", "git_rollback_checkpoint"}
HITL_HIGH_RISK_TOOLS = {"write_file", "git_apply_checkpoint", "edit_file"}
# Agents capables d'effets de bord sur le monde physique / l'état HA.
HITL_SIDE_EFFECT_AGENTS = {"ha_agent"}
# Préfixes de sessions AUTONOMES (aucun humain présent) → bypass d'office,
# sinon le flux stallerait jusqu'à l'auto-approbation au timeout (5 min).
# `task_` = tâche de backlog jouée par DreamCoder la nuit (`agents/dreamer_agent.py`,
# `session_id = f"task_{task_id}"`). Il manquait, et c'était le blocage de fond de
# la session autonome : DreamCoder est le SEUL de ces flux qui écrit vraiment du
# code, donc le seul dont tous les plans touchent write_file / run_terminal_command
# — c'est-à-dire dont tous les plans sont « à risque ». Mesuré le 18/08 sur le
# Deck : 4 tâches lancées, 4 plans corrects produits par le Planner (contrat
# d'acceptation compris), 4 sorties en `waiting_approval` après 6 s, 4 rollbacks.
# Personne ne pouvait approuver : le cycle tourne à 3 h du matin.
# Le garde-fou n'est pas le HITL ici, c'est la chaîne d'après : le travail se fait
# dans un clone dédié, sur une branche `task/*`, et n'entre dans `master` que par
# une PR relue à la main.
HITL_AUTONOMOUS_PREFIXES = ("daemon_", "dreamer_", "routine_", "maint_", "task_")
# Préfixes de sessions INTERACTIVES (un humain est présent, l'IHM gère l'approbation).
HITL_INTERACTIVE_PREFIXES = ("chat_", "stream_", "gui_session_")

_HITL_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# [#T111] Plafond de handoffs agent-à-agent (update.next_agent) dans la boucle
# séquentielle. Garde-fou anti-boucle inspiré du `recursion_limit` LangGraph :
# sans lui, un cycle A→B→A→B... déclenché par une logique d'agent buguée
# tournerait indéfiniment (aucun compteur n'existait auparavant sur ce chemin).
MAX_AGENT_HANDOFFS = 20

# COMMENTAIRE DE TEST POUR HOT-RELOAD AUTOMATIQUE DEPUIS WINDOWS


class Engine:
    """
    Moteur d'exécution central (Custom State Machine) orienté Task Queue.
    Responsable de l'orchestration, du dépilage des tâches et de la parallélisation.
    """

    def __init__(self, session_id: str, context_manager=None, repo_root: str | None = None):
        self.state = GlobalState(session_id=session_id)
        self.agents: dict[str, BaseAgent] = {}
        self.context_manager = context_manager
        # [#T202] Racine du dépôt de travail explicite (ex: clone dédié
        # DreamCoder). None = comportement historique (workspace découvert via
        # __file__ / CWD). Utilisée par les hooks Git/YAML/doc ci-dessous.
        self.repo_root = repo_root
        self.on_event = None  # Callback asynchrone pour diffuser les évènements
        # Verrou asyncio pour self.state.history — protège les écritures
        # concurrentes quand plusieurs branches du DAG s'exécutent en parallèle.
        self._history_lock = asyncio.Lock()
        # Gestionnaire de checkpoints pour persistance disque
        from core.checkpoint import CheckpointManager
        self._checkpoint_mgr = CheckpointManager()
        self._checkpoint_mgr.cleanup(max_age_hours=48)  # Nettoyage au démarrage
        # Mémoire procédurale (Skill Learning)
        from memory.skills import SkillStore
        self._skill_store = SkillStore()
        # Sous-modules extraits (A1 Audit)
        self._dag_runner = DAGRunner(self)
        self._review_loop = ReviewLoop(self)
        # Human-In-The-Loop Manager (pause/resume réel)
        self.hitl = HITLManager()
        # [#T267] Session suspendue en attente d'approbation : porte le request_id.
        # Tant qu'il est renseigné, la session ne doit ni fusionner ni annuler sa
        # branche éphémère — le travail approuvé y attend sa décision.
        self._suspension_request_id: str | None = None
        self._git_branch_courante: str | None = None
        # [#T379] Cause de l'échec décidée par le moteur (DAG en erreur, revue
        # rejetée, contrat non satisfait, rejet HITL, budget/plafond). Une fin
        # d'exécution doit dire POURQUOI elle a échoué : le booléen has_error
        # seul perd cette information. Les chemins qui terminent l'exécution
        # (run_full_pipeline en tête, approval_resume_service en reprise) la
        # lisent pour écrire le statut terminal avec un message qui nomme la
        # cause — incident du 17/08 : trois exécutions terminées sans que leur
        # session ne porte jamais ni statut terminal ni date de fin.
        self.cause_echec: str | None = None
        # Workflow-as-Code : transitions dynamiques depuis le graphe JSON
        from core.workflow_executor import WorkflowExecutor
        self._workflow_executor = WorkflowExecutor()

    def register_agent(self, agent: BaseAgent) -> None:
        self.agents[agent.name] = agent
        logger.debug(f"Agent '{agent.name}' enregistré.")

    async def _validate_modified_yamls(self) -> str | None:
        """
        Détecte via Git les fichiers YAML modifiés dans le workspace,
        et les valide à l'aide de l'outil validate_config_yaml de tools.system.
        Retourne un message d'erreur s'il y a un échec de validation, None sinon.
        """
        from tools.system import validate_config_yaml
        try:
            # [#T202] Racine explicite (clone DreamCoder) : ne scanner QUE ce
            # dépôt, pas tout le workspace découvert via __file__.
            if self.repo_root and os.path.exists(os.path.join(self.repo_root, ".git")):
                git_dirs = [self.repo_root]
            else:
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

                git_dirs = []
                if os.path.exists(os.path.join(project_root, ".git")):
                    git_dirs.append(project_root)
                else:
                    try:
                        for item in os.listdir(project_root):
                            item_path = os.path.join(project_root, item)
                            if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, ".git")):
                                git_dirs.append(item_path)
                    except Exception:
                        pass

                moteur_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if moteur_dir not in git_dirs and os.path.exists(os.path.join(moteur_dir, ".git")):
                    git_dirs.append(moteur_dir)

            modified_files = []
            for git_dir in git_dirs:
                # [T134] subprocess.run est synchrone → to_thread pour ne pas geler
                # le thread principal (Engine.run() tourne dans l'event loop FastAPI).
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "status", "--porcelain"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=git_dir, encoding='utf-8', errors='ignore'
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) == 2:
                            abs_path = os.path.abspath(os.path.join(git_dir, parts[1]))
                            if abs_path.endswith(('.yaml', '.yml')) and abs_path not in modified_files:
                                modified_files.append(abs_path)
                else:
                    logger.warning(f"[ENGINE] Impossible de récupérer le statut Git pour {git_dir} : {result.stderr}")

            if not modified_files:
                return None

            logger.info(f"[ENGINE] Validation de {len(modified_files)} fichier(s) YAML modifiés...")
            for yaml_file in modified_files:
                try:
                    with open(yaml_file, encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if "esphome:" not in content:
                        logger.info(f"[ENGINE] Fichier inclus détecté (pas de section 'esphome:'), validation ignorée pour: {yaml_file}")
                        continue
                except Exception as fe:
                    logger.warning(f"[ENGINE] Impossible de lire {yaml_file} pour vérifier 'esphome:': {fe}")
                    continue

                # [T134] esphome config (lancé par validate_config_yaml) est lent et synchrone.
                validation_res = await asyncio.to_thread(validate_config_yaml, yaml_file)
                if validation_res.startswith("Erreur"):
                    if "section missing" in validation_res or "esphome:" in validation_res:
                        logger.info(f"[ENGINE] Erreur de section manquante ignorée pour: {yaml_file}")
                        continue
                    return validation_res

            return None
        except Exception as e:
            logger.error(f"[ENGINE] Exception lors de la validation automatique des YAML modifiés : {e}")
            return None

    # ──────────────────────────────────────────────────────────────────
    # Méthode principale d'orchestration
    # ──────────────────────────────────────────────────────────────────

    async def run(self, initial_payload: TaskPayload, starting_agent: str) -> GlobalState:
        """
        Point d'entrée principal du moteur.

        Flux :
        1. Initialisation (Git branch, Langfuse, budget tokens)
        2. Boucle agent séquentiel (Router → Planner → ...) — voir _run_sequential_agents()
        3. Si le Planner produit un DAG → DAGRunner.execute_dag() — voir _handle_dag_execution()
        4. Review post-DAG → ReviewLoop.run_review() (dans _handle_dag_execution())
        5. Finalisation (Git merge, mémoire épisodique, checkpoint) — voir _finalize_session()

        [T137] Découpé en méthodes dédiées (250+ lignes → point d'entrée fin) : la
        logique DAG (HITL + DAGRunner + review + skills), auparavant dupliquée à
        l'identique sur les deux chemins possibles de la boucle séquentielle, est
        désormais unifiée dans _handle_dag_execution().
        """
        from tools.git_safety import git_finalize_agent_branch

        # Langfuse Tracing (optionnel)
        _lf = self._init_langfuse()

        if not initial_payload.metadata:
            initial_payload.metadata = {}
        initial_payload.metadata["session_id"] = self.state.session_id
        self.state.current_payload = initial_payload

        git_branch = None
        has_error = False
        tasks_status: dict = {}
        # [#T379] Nouvelle exécution : le verdict précédent ne doit pas survivre.
        self.cause_echec = None

        # [P2-3.4] Budget global d'exécution (tokens + durée + coût), partagé avec
        # le DAG runner pour plafonner TOUTE la requête. Durée/coût opt-in via config.
        from core.execution_budget import ExecutionBudget
        budget = ExecutionBudget.from_config(self.state.session_id)
        max_session_tokens = budget.max_tokens
        logger.info(
            f"[ENGINE] Budget exécution : {max_session_tokens:,} tokens, "
            f"{budget.max_duration_s or '∞'}s, {budget.max_cost_usd or '∞'}$ max"
        )

        # 1. Préparation de la branche Git éphémère
        git_branch = self._prepare_git_branch()
        # [#T267] Mémorisée sur l'instance : le point d'approbation en a besoin
        # pour la stocker avec la demande (la reprise doit retrouver la branche
        # sur laquelle le travail attend).
        self._git_branch_courante = git_branch
        self._suspension_request_id = None

        # Démarrer la trace Langfuse
        if _lf:
            try:
                _lf.start_trace(self.state.session_id, initial_payload.task_objective)
            except Exception:
                pass

        try:
            has_error, tasks_status = await self._run_sequential_agents(
                initial_payload, starting_agent, budget, max_session_tokens, _lf,
            )
        finally:
            # ── Hooks post-exécution ──
            self._run_doc_hook(has_error)

            # [#T267] ⚠️ POINT DANGEREUX — la finalisation Git est dans un `finally`,
            # donc une session SUSPENDUE en sortirait aussi par ici. Or _finalize_git
            # ne connaît que deux issues : merge (succès) ou rollback (échec). Sans
            # cette garde, une session en attente d'approbation verrait sa branche
            # éphémère détruite — donc le travail à approuver perdu — ou pire,
            # fusionnée alors que personne n'a rien approuvé. Une session suspendue
            # laisse sa branche EN PLACE : c'est la reprise qui la finalisera.
            if git_branch and not self.est_suspendue():
                self._finalize_git(git_branch, has_error, tasks_status, git_finalize_agent_branch)
            elif git_branch:
                logger.info(
                    f"[ENGINE] [T267] Session suspendue : branche '{git_branch}' laissée "
                    f"en place jusqu'à la décision ({self._suspension_request_id})."
                )

        return await self._finalize_session(initial_payload, has_error, _lf)

    def est_suspendue(self) -> bool:
        """[#T267] La session attend-elle une approbation humaine ?"""
        return self._suspension_request_id is not None

    async def _run_sequential_agents(
        self,
        initial_payload: TaskPayload,
        starting_agent: str,
        budget,
        max_session_tokens: int,
        _lf,
    ) -> tuple[bool, dict]:
        """
        [T137] Boucle agent séquentielle (Router → Planner → ... → DAG optionnel
        via la file de tâches). Extraite de run().

        Returns:
            (has_error, tasks_status)
        """
        current_agent_name: str | None = starting_agent
        has_error = False
        tasks_status: dict = {}
        agent_handoffs = 0

        # ── Boucle agent séquentiel ──
        while current_agent_name and current_agent_name.upper() != "END":
            agent_handoffs += 1
            if agent_handoffs > MAX_AGENT_HANDOFFS:
                logger.error(
                    f"[ENGINE] ⛔ Plafond de boucle atteint ({MAX_AGENT_HANDOFFS} handoffs "
                    f"agent-à-agent) : arrêt anti-boucle (agent courant: {current_agent_name})."
                )
                if self.on_event:
                    await self.on_event("loop_limit_exceeded", {
                        "kind": "agent_handoffs",
                        "limit": MAX_AGENT_HANDOFFS,
                        "current_agent": current_agent_name,
                    })
                has_error = True
                # [#T379] Nommer la cause : la session doit pouvoir dire
                # pourquoi elle s'est arrêtée.
                self.cause_echec = (
                    f"Plafond de boucle atteint ({MAX_AGENT_HANDOFFS} handoffs "
                    f"agent-à-agent) — arrêt anti-boucle."
                )
                break

            agent = self.agents.get(current_agent_name)
            if not agent:
                raise ValueError(f"Agent cible inconnu: '{current_agent_name}'")

            print(f"\n[ENGINE] -> Routage vers l'agent: {current_agent_name.upper()}")

            # [P2-3.4] Garde-fou budget global (tokens + durée + coût)
            violation = budget.check()
            if violation:
                logger.warning(
                    f"[ENGINE] ⛔ BUDGET DÉPASSÉ ({violation['reason']}) : "
                    f"{violation['value']} / {violation['limit']} {violation['metric']}. "
                    f"Arrêt propre de l'exécution."
                )
                if self.on_event:
                    await self.on_event(
                        "budget_exceeded",
                        budget.event_payload(violation, blocked=current_agent_name),
                    )
                has_error = True
                # [#T379] Nommer la cause : la session doit pouvoir dire
                # pourquoi elle s'est arrêtée.
                self.cause_echec = (
                    f"Budget d'exécution dépassé ({violation['reason']}) — arrêt propre."
                )
                break

            # Mise à jour de la phase
            if current_agent_name == "planner":
                self.state.current_phase = ExecutionPhase.PLANNING
            elif current_agent_name == "reviewer":
                self.state.current_phase = ExecutionPhase.REVIEWING
            else:
                self.state.current_phase = ExecutionPhase.EXECUTING

            if self.on_event:
                await self.on_event("agent_started", {
                    "agent_name": current_agent_name,
                    "task_objective": self.state.current_payload.task_objective,
                })

            # Langfuse : span pour cet agent
            if _lf:
                try:
                    _lf.start_span(self.state.session_id, current_agent_name)
                except Exception:
                    pass

            # ── Exécution de l'agent actif ──
            update: StateUpdate = await agent.invoke(self.state.current_payload)

            # Validation YAML post-exécution si succès
            if update.status == "success":
                yaml_err = await self._validate_modified_yamls()
                if yaml_err:
                    update.status = "error"
                    update.error_message = yaml_err
                    update.result_data = f"Erreur de validation de configuration YAML : {yaml_err}"

            if self.on_event:
                await self.on_event("agent_completed", {
                    "agent_name": current_agent_name,
                    "status": update.status,
                    "result_data": update.result_data,
                    "error_message": update.error_message,
                })

            # Langfuse : fermer le span
            if _lf:
                try:
                    _lf.end_span(
                        self.state.session_id, current_agent_name,
                        status=update.status,
                        output=str(update.result_data)[:300] if update.result_data else None,
                    )
                except Exception:
                    pass

            # Checkpoint disque après chaque transition
            try:
                self._checkpoint_mgr.save(self.state)
            except Exception as _cp_err:
                logger.warning(f"[ENGINE] Checkpoint échoué : {_cp_err}")

            # Gestion des nouvelles tâches (Planner → DAG)
            if update.new_tasks:
                print(f"[ENGINE] -> L'agent {current_agent_name} a planifié {len(update.new_tasks)} nouvelle(s) tâche(s).")
                for task in update.new_tasks:
                    if not task.metadata:
                        task.metadata = {}
                    if "session_id" not in task.metadata:
                        task.metadata["session_id"] = self.state.session_id
                self.state.task_queue.extend(update.new_tasks)

            # Historisation thread-safe
            async with self._history_lock:
                self.state.history.append(update)

            if update.status == "error":
                logger.error(f"Erreur depuis {current_agent_name}: {update.error_message}")
                # [#T219] Branche d'échec du Workflow-as-Code. La résolution des
                # nœuds condition sait router un status="error" (out-false), mais
                # aucun appelant ne l'atteignait : ce `break` sortait de la boucle
                # AVANT le bloc de transitions ci-dessous, rendant toute branche
                # d'erreur dessinée dans l'éditeur HMI inatteignable en prod.
                # On ne dévie du fail-fast que si l'auteur du graphe a réellement
                # câblé une sortie conditionnée par l'échec (require_explicit_
                # condition) : une arête « toujours active » ne suffit pas, sinon
                # le moteur enchaînerait sur l'agent suivant à chaque erreur.
                # Contexte transmis à la branche de récupération : sur un échec,
                # l'information utile est dans error_message (result_data est le
                # plus souvent None) — sans quoi l'agent cible corrigerait à
                # l'aveugle.
                contexte_echec = "\n".join(
                    part for part in (
                        (update.error_message or "").strip(),
                        str(update.result_data).strip() if update.result_data else "",
                    ) if part
                )
                wf_error_tasks = self._workflow_executor.resolve_next_tasks(
                    current_agent=current_agent_name,
                    status="error",
                    result_data=contexte_echec,
                    session_id=self.state.session_id,
                    require_explicit_condition=True,
                )
                if not wf_error_tasks:
                    has_error = True
                    # [#T379] Nommer la cause : la session doit pouvoir dire
                    # pourquoi elle s'est arrêtée.
                    self.cause_echec = (
                        f"Erreur de l'agent '{current_agent_name}' sans branche de "
                        f"récupération : {(update.error_message or 'détail indisponible')[:200]}"
                    )
                    break

                logger.info(
                    f"[ENGINE] [#T219] Branche d'échec du workflow : "
                    f"{len(wf_error_tasks)} transition(s) injectée(s) depuis le "
                    f"graphe pour '{current_agent_name}' (statut error)."
                )
                if self.on_event:
                    await self.on_event("workflow_error_branch", {
                        "source_agent": current_agent_name,
                        "targets": [
                            t.metadata.get("target_agent") for t in wf_error_tasks
                        ],
                    })

                self.state.task_queue.extend(wf_error_tasks)
                dag_tasks = list(self.state.task_queue)
                self.state.task_queue.clear()
                new_tasks_status, has_error = await self._handle_dag_execution(
                    dag_tasks, initial_payload, max_session_tokens, budget,
                )
                if new_tasks_status is not None:
                    tasks_status = new_tasks_status
                break

            # ── Détermination du prochain agent ──
            if update.next_agent and update.next_agent.upper() != "END":
                self.state.current_payload = TaskPayload(
                    task_objective=f"Poursuite après exécution de {current_agent_name}",
                    relevant_context=str(update.result_data),
                    metadata={"previous_agent": current_agent_name, "session_id": self.state.session_id},
                )
                current_agent_name = update.next_agent
            elif self._workflow_executor.has_transitions(current_agent_name) and not update.new_tasks:
                # Transitions dynamiques depuis le workflow JSON
                wf_tasks = self._workflow_executor.resolve_next_tasks(
                    current_agent=current_agent_name,
                    status=update.status,
                    result_data=str(update.result_data)[:500],
                    session_id=self.state.session_id,
                )
                if wf_tasks:
                    self.state.task_queue.extend(wf_tasks)
                    logger.info(
                        f"[ENGINE] [A11] Workflow-as-Code : {len(wf_tasks)} transition(s) "
                        f"injectée(s) depuis le graphe pour '{current_agent_name}'"
                    )
                # [FIX] Ne pas sortir du while — laisser le bloc suivant (task_queue)
                # traiter les tâches DAG via DAGRunner. Avant ce fix, current_agent_name=None
                # provoquait une sortie prématurée SANS exécuter le DAG.
                current_agent_name = None
                # Forcer le passage au traitement de la file DAG ci-dessous
                if self.state.task_queue:
                    dag_tasks = list(self.state.task_queue)
                    self.state.task_queue.clear()
                    new_tasks_status, has_error = await self._handle_dag_execution(
                        dag_tasks, initial_payload, max_session_tokens, budget,
                    )
                    if new_tasks_status is not None:
                        tasks_status = new_tasks_status
            else:
                # ── Traitement de la file DAG ──
                if self.state.task_queue:
                    dag_tasks = list(self.state.task_queue)
                    self.state.task_queue.clear()
                    new_tasks_status, has_error = await self._handle_dag_execution(
                        dag_tasks, initial_payload, max_session_tokens, budget,
                    )
                    if new_tasks_status is not None:
                        tasks_status = new_tasks_status

                current_agent_name = None  # Fin du traitement

        return has_error, tasks_status

    async def _handle_dag_execution(
        self,
        dag_tasks: list,
        initial_payload: TaskPayload,
        max_session_tokens: int,
        budget,
    ) -> tuple:
        """
        [T137] Exécute un lot de tâches DAG : approbation HITL, DAGRunner.execute_dag,
        review post-DAG, puis enregistrement des skills. Factorise un bloc auparavant
        dupliqué à l'identique dans run() (chemin "transitions workflow" ET chemin
        "file DAG standard").

        Returns:
            (tasks_status, has_error) — tasks_status vaut None si le plan a été
            rejeté via HITL avant que DAGRunner ne tourne (l'appelant doit alors
            laisser sa propre variable tasks_status inchangée, pour préserver le
            comportement d'origine).
        """
        # Point d'approbation HITL avant le DAG. Les plans contenant des tâches à
        # risque (write_file, run_terminal, delete) déclenchent une approbation humaine.
        decision = await self._check_hitl_before_dag(
            dag_tasks, initial_payload.task_objective
        )

        if decision == DECISION_EN_ATTENTE:
            # [#T267] Ni approuvé ni rejeté : le plan attend une décision humaine
            # HORS de la fenêtre de session. On ne bloque pas, on ne joue pas le
            # DAG, et surtout on ne remonte PAS d'erreur — une session suspendue
            # n'est pas une session en échec. `execute_dag` sera appelé plus tard,
            # à la reprise, avec le DAG stocké tel quel.
            logger.info(
                f"[ENGINE] [T267] Plan mis en attente d'approbation "
                f"({self._suspension_request_id}) — la session se termine sans exécuter le DAG."
            )
            if self.on_event:
                await self.on_event("plan_waiting_approval", {
                    "request_id": self._suspension_request_id,
                    "task_count": len(dag_tasks),
                })
            return None, False

        if decision == DECISION_REJETE:
            logger.warning("[ENGINE] Plan rejeté par l'utilisateur (HITL).")
            # [#T379] Le rejet humain est une fin d'exécution à part entière :
            # nommer la cause pour que la session close dise ce qui s'est passé.
            self.cause_echec = "Plan rejeté par l'utilisateur (HITL)."
            if self.on_event:
                await self.on_event("plan_rejected", {
                    "reason": "Rejeté par l'utilisateur via HITL"
                })
            return None, True

        return await self.executer_dag_et_controles(
            dag_tasks, initial_payload.task_objective, max_session_tokens, budget,
        )

    async def executer_dag_et_controles(
        self,
        dag_tasks: list,
        objectif: str,
        max_session_tokens: int,
        budget=None,
    ) -> tuple:
        """
        [#T253] Exécute le DAG **et tout ce qui doit le suivre** : revue post-DAG,
        contrat d'acceptation, enregistrement des skills.

        Extraite de `_handle_dag_execution` parce que la reprise après approbation
        humaine (#T267) appelait `execute_dag()` directement et **sautait donc tous
        ces contrôles**. Mesuré en production le 11/08 sur la première chaîne
        complète (`bg_6148ea67`) : le Planner avait bien émis un contrat de
        2 critères (`[PLANNER] [T253] Contrat d'acceptation : 2 critère(s)`), le DAG
        a réussi et fusionné — mais **aucune ligne de revue**, donc le contrat n'a
        jamais été évalué. Un plan approuvé par un humain est précisément celui qui
        mérite le plus d'être vérifié : c'est le seul qui touche à des fichiers.

        Returns:
            (tasks_status, has_error), comme `execute_dag`, mais après contrôles.

        [#T379] Chaque porte de sortie qui décide has_error nomme aussi sa
        cause dans `self.cause_echec` : le chemin de reprise après approbation
        n'a QUE ce retour pour écrire le statut terminal de la session, et un
        booléen ne distingue pas un DAG en échec d'une revue rejetée ni d'un
        contrat non satisfait.
        """
        # [#T379] Nouveau verdict : remise à plat de la cause (une exécution
        # passée par une branche d'erreur #T219 peut très bien réussir ici).
        self.cause_echec = None
        tasks_status, has_error = await self._dag_runner.execute_dag(
            tasks=dag_tasks,
            max_session_tokens=max_session_tokens,
            on_event=self.on_event,
            budget=budget,  # [P2-3.4] budget partagé (tokens+durée+coût)
        )
        if has_error:
            n_echecs = sum(
                1 for s in (tasks_status or {}).values() if s in ("error", "blocked")
            )
            self.cause_echec = f"Échec d'exécution du DAG : {n_echecs} tâche(s) en erreur."

        # ── Review post-DAG ──
        if not has_error and tasks_status:
            review_enabled = self._is_review_enabled()
            if review_enabled and self.agents.get("reviewer"):
                approved = await self._review_loop.run_review(
                    initial_objective=objectif,
                    on_event=self.on_event,
                )
                if not approved:
                    has_error = True
                    # [#T379] Rejet du Reviewer : fin d'exécution négative, nommée.
                    self.cause_echec = (
                        "Revue post-DAG rejetée par le Reviewer après épuisement "
                        "des rounds de correction."
                    )
        elif has_error and tasks_status:
            # [#T253] DAG en échec : la revue LLM est sautée (payer un avis de
            # complaisance après un échec n'a pas de sens), mais le contrat
            # d'acceptation, lui, doit être évalué — c'est le seul mécanisme
            # capable de PROUVER que l'objectif est atteint malgré l'échec d'une
            # branche, ou de confirmer qu'il ne l'est pas. Mesuré le 10/08 :
            # sans cette branche, une seule tâche en erreur suffisait à sauter
            # toute vérification du plan.
            verdict = await self._review_loop.contrat_seul(on_event=self.on_event)
            if verdict is True:
                has_error = False
                # [#T379] Le contrat a prouvé l'objectif atteint : plus d'échec,
                # donc plus de cause d'échec.
                self.cause_echec = None
                # Absoudre aussi les statuts d'erreur pour la finalisation Git :
                # `_finalize_git` exige que TOUTES les tâches soient « success »,
                # sinon rollback même quand le contrat a prouvé l'objectif atteint.
                for tid, statut in list(tasks_status.items()):
                    if statut in ("error", "blocked"):
                        tasks_status[tid] = "success"
                logger.info(
                    "[ENGINE] [T253] Contrat satisfait malgré DAG en erreur — "
                    "session et branche Git traitées comme succès."
                )
            elif verdict is False:
                # [#T379] Le contrat CONFIRME l'échec : c'est lui la cause
                # décisive (plus précise que « une tâche est en erreur »).
                self.cause_echec = (
                    "Contrat d'acceptation non satisfait après échec du DAG."
                )
            # verdict None (rien de vérifiable) : la cause du DAG est conservée.

        # Enregistrement des skills après un DAG réussi
        if not has_error:
            self._record_skills_from_dag(dag_tasks, objectif)

        return tasks_status, has_error

    async def _finalize_session(
        self, initial_payload: TaskPayload, has_error: bool, _lf,
    ) -> GlobalState:
        """
        [T137] Finalisation post-boucle : phase finale, mémoire épisodique,
        consolidation mémoire, checkpoint final, fermeture trace Langfuse, event.
        Extraite de run().
        """
        # [#T267] Une session suspendue n'est ni réussie ni échouée : elle attend.
        # Sa phase reste WAITING_APPROVAL — c'est ce que le checkpoint doit porter,
        # et ce que l'IHM doit afficher au lieu d'un « échec » trompeur.
        suspendue = self.est_suspendue()

        # Phase finale
        if not suspendue:
            self.state.current_phase = ExecutionPhase.FAILED if has_error else ExecutionPhase.COMPLETED

        if suspendue:
            # Ni épisode ni consolidation : la session n'a pas produit son résultat,
            # l'écrire en mémoire épisodique apprendrait une conclusion fausse.
            logger.info(
                f"[ENGINE] [T267] Session {self.state.session_id} suspendue en attente "
                f"d'approbation — mémoire épisodique et consolidation reportées à la reprise."
            )
        else:
            # Sauvegarde mémoire épisodique
            self._save_episode(initial_payload)

            # Hook de consolidation mémoire (decay, GC graphe, sync memory.db)
            await self._consolidate_memory(initial_payload, has_error)

        # Checkpoint final
        try:
            self._checkpoint_mgr.save(self.state)
        except Exception as _e:
            from core.error_reporter import report_swallowed
            report_swallowed("engine.checkpoint_final", _e, level="debug")

        # Langfuse : fermer la trace
        if _lf:
            try:
                _lf.end_trace(
                    self.state.session_id,
                    status=(
                        "waiting_approval" if suspendue else ("error" if has_error else "success")
                    ),
                )
                _lf.flush()
            except Exception as _e:
                from core.error_reporter import report_swallowed
                report_swallowed("engine.langfuse_end_trace", _e, level="debug")

        if suspendue:
            print(f"\n[ENGINE] Orchestration SUSPENDUE (Session: {self.state.session_id})")
        else:
            print(f"\n[ENGINE] Orchestration terminée (Session: {self.state.session_id})")
        if self.on_event:
            await self.on_event("orchestration_completed", {
                "session_id": self.state.session_id,
                "status": (
                    "waiting_approval" if suspendue else ("error" if has_error else "success")
                ),
                "request_id": self._suspension_request_id,
            })
        return self.state

    # ──────────────────────────────────────────────────────────────────
    # Méthodes privées d'initialisation et de finalisation
    # ──────────────────────────────────────────────────────────────────

    def _init_langfuse(self):
        """Initialise le bridge Langfuse (optionnel, no-op si absent)."""
        try:
            from core.langfuse_bridge import LangfuseBridge
            return LangfuseBridge.get_instance()
        except Exception:
            return None

    def _load_max_session_tokens(self) -> int:
        """Charge le budget maximum de tokens depuis config.json (cache D1)."""
        try:
            from core.llm_gateway import load_config
            return load_config().get("max_session_tokens", 500_000)
        except Exception:
            return 500_000

    def _prepare_git_branch(self) -> str | None:
        """Prépare la branche Git éphémère pour l'isolation du workspace."""
        try:
            from tools.git_safety import git_prepare_agent_branch
            # [#T202] Cibler le dépôt de travail explicite s'il est fourni.
            branch = git_prepare_agent_branch(
                self.state.session_id, repo_path=self.repo_root or "."
            )
            if branch.startswith("Erreur"):
                logger.warning(f"[ENGINE] Impossible de préparer la branche Git : {branch}")
                return None
            logger.info(f"[ENGINE] Espace de travail isolé sur la branche éphémère : {branch}")
            return branch
        except Exception as ge:
            logger.warning(f"[ENGINE] Exception lors de la préparation de la branche Git : {ge}")
            return None

    def _is_review_enabled(self) -> bool:
        """Vérifie si la revue automatique est activée dans config.json (cache D1)."""
        try:
            from core.llm_gateway import load_config
            return load_config().get("auto_review", True)
        except Exception:
            return True

    def _run_doc_hook(self, has_error: bool) -> None:
        """Hook non-bloquant de génération automatique de documentation."""
        if not has_error:
            try:
                from tools.doc_generator import DocGenerator
                doc_gen = DocGenerator()
                doc_gen.update_docs(
                    # [#T202] Racine de travail explicite si fournie (clone DreamCoder)
                    repo_path=self.repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    session_id=self.state.session_id,
                )
            except Exception as doc_err:
                logger.warning(f"[ENGINE] Doc auto-gen échoué (non bloquant) : {doc_err}")

    def _finalize_git(self, git_branch: str, has_error: bool, tasks_status: dict, git_finalize_fn) -> None:
        """Finalise la branche Git (merge ou rollback)."""
        try:
            execution_success = not has_error
            if tasks_status:
                execution_success = execution_success and all(
                    s == "success" for s in tasks_status.values()
                )
            # [#T202] Finaliser dans le dépôt de travail explicite s'il est fourni.
            merge_msg = git_finalize_fn(
                git_branch, execution_success, self.state.session_id,
                repo_path=self.repo_root or ".",
            )
            logger.info(f"[ENGINE] Finalisation Git : {merge_msg}")
        except Exception as gfe:
            logger.error(f"[ENGINE] Exception lors de la finalisation de la branche Git : {gfe}")

    def _save_episode(self, initial_payload: TaskPayload) -> None:
        """Sauvegarde l'épisode dans la mémoire épisodique."""
        try:
            from memory.episodes import EpisodeStore
            episode_store = EpisodeStore()
            errors = [u.error_message for u in self.state.history if u.error_message]
            lessons = [
                f"Tâche '{u.agent_name}' corrigée : {str(u.result_data)[:200]}"
                for u in self.state.history
                if u.metadata.get("is_healing") or u.metadata.get("is_correction")
            ]
            result_summary = ""
            for u in reversed(self.state.history):
                if u.status == "success" and u.result_data:
                    result_summary = str(u.result_data)[:500]
                    break
            episode_store.save_episode(
                session_id=self.state.session_id,
                objective=initial_payload.task_objective,
                result_summary=result_summary,
                errors=errors[:10],
                lessons=lessons[:5],
                entities_touched=self.state.entity_memory,
                execution_phase=self.state.current_phase.value,
            )
        except Exception as ep_err:
            logger.warning(f"[ENGINE] Échec de sauvegarde d'épisode : {ep_err}")

    async def _consolidate_memory(self, initial_payload: TaskPayload,
                                  has_error: bool) -> None:
        """
        Hook post-exécution de consolidation mémoire bidirectionnelle.
        
        Appelé après chaque orchestration (succès ou échec) pour :
        1. Décrémenter le score de pertinence des faits anciens (decay)
        2. Nettoyer le graphe de connaissances (GC observations)
        3. Enregistrer les leçons apprises (self-healing/corrections) dans memory.db
        4. Synchroniser l'épisode dans memory.db
        """
        try:
            from memory.memory_db import MemoryDB
            db = MemoryDB.get_instance()

            # 1. Decay des scores de pertinence (faits non consultés > 7 jours)
            decayed = await db.decay_relevance_async(decay_rate=0.03)
            if decayed > 0:
                logger.info(f"[CONSOLIDATION] {decayed} faits ont subi un decay de pertinence.")

            # 2. GC du graphe (observations > 15, entités temporaires > 30 jours)
            gc_result = await db.gc_graph_entities_async(max_observations=15, max_age_days=30)
            if gc_result["summarized"] > 0 or gc_result["archived"] > 0:
                logger.info(
                     f"[CONSOLIDATION] GC graphe : {gc_result['summarized']} résumées, "
                     f"{gc_result['archived']} archivées"
                )

            # 2bis. Self-Healing du contexte : vérifier la cohérence
            # entre la documentation Markdown et le code réel
            try:
                from tools.context_self_healing import ContextSelfHealer
                healer = ContextSelfHealer()
                diagnostics = healer.validate_all()
                if diagnostics:
                    logger.warning(
                        f"[CONSOLIDATION] [SELF-HEALING] {len(diagnostics)} "
                        f"incohérence(s) détectée(s) dans le contexte :"
                    )
                    for diag in diagnostics[:5]:  # Limiter le log à 5 diagnostics
                        logger.warning(f"  ⚠️ [{diag['type']}] {diag['message']}")
                else:
                    logger.info("[CONSOLIDATION] [SELF-HEALING] Contexte validé — aucune incohérence.")
            except Exception as sh_err:
                logger.debug(f"[CONSOLIDATION] Self-healing du contexte ignoré : {sh_err}")

            # 3. Enregistrer les leçons apprises (corrections/self-healing)
            for update in self.state.history:
                is_healing = update.metadata.get("is_healing", False)
                is_correction = update.metadata.get("is_correction", False)

                if (is_healing or is_correction) and update.result_data:
                    # Déterminer la catégorie depuis le contexte
                    category = self._infer_lesson_category(
                        str(update.result_data), update.agent_name
                    )
                    severity = "major" if is_healing else "minor"

                    await db.record_learned_lesson_async(
                        category=category,
                        title=f"Auto-correction {update.agent_name}",
                        content=str(update.result_data)[:500],
                        source_file=f"session_{self.state.session_id}",
                        tags=f"{update.agent_name},auto,{severity}",
                        severity=severity,
                    )

            # 4. Synchroniser l'épisode dans memory.db
            result_summary = ""
            for u in reversed(self.state.history):
                if u.status == "success" and u.result_data:
                    result_summary = str(u.result_data)[:300]
                    break

            await db.upsert_episode_async(
                session_date=time.strftime("%Y-%m-%d"),
                session_folder=f"session_{self.state.session_id[:8]}",
                summary=f"{initial_payload.task_objective[:200]} → "
                        f"{'ERREUR' if has_error else 'OK'}: {result_summary[:100]}",
                category=self._infer_lesson_category(
                    initial_payload.task_objective, ""
                ),
                tags="auto,engine",
                source_file="engine.py",
            )

            logger.info("[CONSOLIDATION] Mémoire consolidée avec succès.")

        except Exception as mem_err:
            logger.warning(f"[ENGINE] Consolidation mémoire échouée (non bloquant) : {mem_err}")

    def _infer_lesson_category(self, text: str, agent_name: str) -> str:
        """
        Infère la catégorie d'une leçon apprise depuis le texte et l'agent.
        Retourne : esphome, moteur, gcp, hmi, infra, ou 'moteur' par défaut.
        """
        text_lower = (text + " " + agent_name).lower()

        if any(kw in text_lower for kw in ["esphome", "lvgl", "tab5", "esp32", "gpio", "ota"]):
            return "esphome"
        elif any(kw in text_lower for kw in ["dashboard", "ihm", "hmi", "ui", "css", "frontend"]):
            return "hmi"
        elif any(kw in text_lower for kw in ["gcp", "oauth", "gemini", "api", "cloud", "quota"]):
            return "gcp"
        elif any(kw in text_lower for kw in ["windows", "powershell", "git", "docker", "infra"]):
            return "infra"
        else:
            return "moteur"

    def _record_skills_from_dag(self, dag_tasks: list, objective: str) -> None:
        """
        Extrait les séquences d'outils réussies du DAG et les enregistre
        comme skills réutilisables dans le SkillStore.
        """
        try:
            # Extraire les outils utilisés depuis l'historique des tâches réussies
            tools_used = []
            for update in self.state.history:
                if update.status == "success" and update.metadata:
                    # Les outils sont enregistrés dans metadata["tools_used"] par les agents
                    if "tools_used" in update.metadata:
                        tools_used.extend(update.metadata["tools_used"])
                    elif update.agent_name and update.agent_name not in ("planner", "reviewer"):
                        tools_used.append(update.agent_name)

            if len(tools_used) >= 2:
                # Générer un pattern descriptif à partir de l'objectif
                pattern = objective[:80] if len(objective) > 80 else objective
                # Extraire les tags depuis les mots clés de l'objectif
                tags = [
                    word.lower() for word in objective.split()
                    if len(word) > 4 and word.isalpha()
                ][:5]

                self._skill_store.record_skill(
                    pattern=pattern,
                    tools_sequence=tools_used[:10],  # Limiter à 10 outils
                    tags=tags,
                    objective=objective
                )
        except Exception as sk_err:
            logger.warning(f"[ENGINE] Erreur d'enregistrement de skill : {sk_err}")

    def _assess_dag_risk(self, dag_tasks: list) -> tuple[str, list]:
        """
        [PHASE 1 - M5] Évalue le risque d'un DAG à partir de l'OUTIL/agent ciblé
        (signal primaire) ET des mots-clés du libellé (signal de repli).

        Méthode pure (sans effet de bord) pour être testable isolément.

        Returns:
            (max_risk, risky_tasks) où max_risk ∈ {low, medium, high, critical}.
        """
        # Signal de repli : mots-clés dans l'objectif (conservé mais plus décisif seul).
        risk_keywords = {
            "high": ["supprimer", "delete", "remove", "drop", "format", "reset", "rm ", "rmdir"],
            "medium": ["écrire", "modifier", "créer", "write", "modify", "create",
                       "install", "exécuter", "terminal", "run_terminal"],
        }

        max_risk = "low"
        risky_tasks = []

        def _bump(level: str, reason: str) -> None:
            nonlocal max_risk
            if _HITL_RISK_ORDER[level] > _HITL_RISK_ORDER[max_risk]:
                max_risk = level
            risky_tasks.append(f"[{level.upper()}] {reason}")

        for task in dag_tasks:
            obj = task.task_objective or ""
            obj_lower = obj.lower()
            meta = task.metadata or {}
            target = meta.get("target_agent", "")

            # 1. Signal PRIMAIRE : outil explicitement ciblé (court-circuit déterministe).
            direct_tool = (meta.get("direct_tool_call") or {}).get("name", "")
            if direct_tool in HITL_CRITICAL_TOOLS:
                _bump("critical", f"outil {direct_tool} — {obj[:60]}")
            elif direct_tool in HITL_HIGH_RISK_TOOLS:
                _bump("high", f"outil {direct_tool} — {obj[:60]}")

            # 2. Signal PRIMAIRE : agent à effets de bord.
            if target in HITL_SIDE_EFFECT_AGENTS:
                _bump("medium", f"agent {target} — {obj[:60]}")

            # 3. Signal de repli : mots-clés du libellé.
            for level in ("high", "medium"):
                if any(kw in obj_lower for kw in risk_keywords[level]):
                    _bump(level, obj[:80])
                    break

        return max_risk, risky_tasks

    def _hitl_bypass_reason(self, session_id: str, max_risk: str) -> str | None:
        """
        [PHASE 1 - M5] Décide si l'approbation HITL peut être contournée.

        Retourne une raison (str) si bypass autorisé, None si l'approbation est requise.
        - Sessions autonomes (daemon/dreamer/...) : bypass (aucun humain présent).
        - Risque faible : bypass (lecture seule, analyse).
        - Sessions interactives à risque : approbation REQUISE par défaut, sauf si
          config.json → hitl.interactive_auto_approve == true (opt-in explicite).
        """
        if session_id.startswith(HITL_AUTONOMOUS_PREFIXES):
            return "session autonome (aucun humain présent)"
        if max_risk == "low":
            return "risque faible (lecture seule / analyse)"
        try:
            from core.llm_gateway import load_config
            hitl_cfg = load_config().get("hitl", {})
        except Exception:
            hitl_cfg = {}
        if hitl_cfg.get("interactive_auto_approve", False) and session_id.startswith(HITL_INTERACTIVE_PREFIXES):
            return "auto-approbation interactive activée par config (hitl.interactive_auto_approve)"
        return None

    async def _check_hitl_before_dag(
        self, dag_tasks: list, objective: str
    ) -> str:
        """
        Vérifie si le DAG nécessite une approbation humaine.

        Le niveau de risque est calculé par `_assess_dag_risk` (outil ciblé +
        mots-clés), et la politique de bypass par `_hitl_bypass_reason`. Les plans
        interactifs à risque ne sont PAS contournés d'office (correctif M5 de l'audit).

        [#T267] Retourne désormais TROIS états et non plus un booléen :
            DECISION_APPROUVE   — exécuter le DAG maintenant (approuvé ou bypass)
            DECISION_REJETE     — abandonner le plan
            DECISION_EN_ATTENTE — plan mis en attente, session suspendue sans erreur

        Le mode non bloquant est le défaut. Le comportement d'avant (attente
        bloquante de 300 s) reste accessible via `MOTEUR_HITL_NON_BLOQUANT=0`,
        mais il faut savoir ce qu'on réactive : il est incompatible avec le
        `wait_for` de 120 s de `services/pipeline_service.py`, ce qui rendait
        tout plan à risque inexécutable en production.
        """
        # Analyser le niveau de risque du plan (outil ciblé + libellé).
        max_risk, risky_tasks = self._assess_dag_risk(dag_tasks)

        # Politique de bypass (autonome / faible risque / opt-in config).
        bypass_reason = self._hitl_bypass_reason(self.state.session_id, max_risk)
        if bypass_reason:
            if max_risk != "low":
                logger.info(f"[ENGINE] ⚡ HITL contourné ({bypass_reason}) malgré risque '{max_risk}'.")
            return DECISION_APPROUVE

        # Construire le résumé du plan pour l'IHM
        plan_summary = f"Plan : {objective[:120]}\n"
        plan_summary += f"Nombre de tâches : {len(dag_tasks)}\n"
        plan_summary += f"Risque détecté : {max_risk.upper()}\n\n"
        plan_summary += "Tâches à risque :\n"
        for rt in risky_tasks[:10]:
            plan_summary += f"  • {rt}\n"

        # Demander l'approbation humaine via le HITLManager
        self.state.current_phase = ExecutionPhase.WAITING_APPROVAL

        if self.on_event:
            await self.on_event("phase_changed", {
                "phase": "waiting_approval",
            })

        request_id = f"dag_{self.state.session_id}_{len(dag_tasks)}"
        description = (
            f"Le plan contient {len(risky_tasks)} tâche(s) à risque ({max_risk}). "
            f"Approuvez-vous l'exécution ?"
        )

        # [#T267] Chemin non bloquant (défaut) : on persiste la demande AVEC le DAG
        # à rejouer, on notifie l'IHM, et on rend la main immédiatement. La phase
        # reste WAITING_APPROVAL — c'est elle qui sera lue au checkpoint.
        if self._hitl_non_bloquant():
            await self.hitl.demander_sans_bloquer(
                request_id=request_id,
                session_id=self.state.session_id,
                dag_tasks=self._serialiser_dag(dag_tasks),
                description=description,
                objective=objective,
                plan_summary=plan_summary,
                risk_level=max_risk,
                git_branch=self._git_branch_courante,
                criteres=self._criteres_acceptation_courants(),
                on_event=self.on_event,
            )
            self._suspension_request_id = request_id
            return DECISION_EN_ATTENTE

        # Chemin historique (bloquant), conservé derrière le kill-switch.
        decision = await self.hitl.request_approval(
            request_id=request_id,
            description=description,
            plan_summary=plan_summary,
            risk_level=max_risk,
            on_event=self.on_event,
        )

        # Restaurer la phase d'exécution
        self.state.current_phase = ExecutionPhase.EXECUTING

        if self.on_event:
            await self.on_event("approval_received", {
                "request_id": decision.request_id,
                "approved": decision.approved,
                "feedback": decision.feedback,
            })

        return DECISION_APPROUVE if decision.approved else DECISION_REJETE

    def _criteres_acceptation_courants(self) -> list:
        """
        [#T253] Contrat d'acceptation posé par le Planner, lu dans l'historique.

        Il doit être persisté AVEC la demande d'approbation : à la reprise, le
        moteur est neuf et son `state.history` est vide — le contrat mourrait
        donc avec la session suspendue, et ne serait jamais évalué. Mesuré en
        prod le 11/08 : un plan approuvé fusionnait sans qu'aucun de ses
        2 critères ne soit vérifié.

        [Incident du 17/08] On lit le PREMIER contrat de l'historique, pas le
        plus récent : c'est celui posé par le plan soumis à approbation, et
        c'est lui qui deviendra le PLANCHER à la reprise (injecté en tête de
        l'historique par `approval_resume_service`). Le HITL ne suspend et ne
        rejoue que le plan d'origine — jamais un plan correctif — donc le
        contrat persisté doit être celui qui est approuvé. (Avant ce
        correctif, la docstring renvoyait vers « le plus récent gagne », la
        règle même qui a laissé un plan correctif évincer le contrat approuvé.)
        """
        from core.acceptance_contract import CLE_CONTRAT
        for update in self.state.history:
            criteres = (update.metadata or {}).get(CLE_CONTRAT)
            if criteres:
                return criteres
        return []

    @staticmethod
    def _hitl_non_bloquant() -> bool:
        """
        [#T267] Le point d'approbation suspend-il la session au lieu de bloquer ?

        Non bloquant par défaut. `MOTEUR_HITL_NON_BLOQUANT=0` restaure l'attente
        bloquante d'avant #T267 — utile pour un test ou un usage hors pipeline,
        jamais en production derrière le `wait_for` de 120 s.
        """
        return os.environ.get("MOTEUR_HITL_NON_BLOQUANT", "1").strip().lower() not in ("0", "false", "off")

    @staticmethod
    def _serialiser_dag(dag_tasks: list) -> list[dict]:
        """
        [#T267] Sérialise le DAG pour qu'il soit rejoué À L'IDENTIQUE à la reprise.

        `model_dump(mode="json")` et non `core.serializers.task_payload_to_dict` :
        ce dernier est volontairement partiel (il perd `status`, `assigned_agent`,
        `result_summary`) parce qu'il sert l'affichage. Ici la fidélité prime —
        ce qui est approuvé doit être exactement ce qui s'exécute.
        """
        sortie = []
        for t in dag_tasks:
            if hasattr(t, "model_dump"):
                sortie.append(t.model_dump(mode="json"))
            elif isinstance(t, dict):
                sortie.append(t)
            else:
                logger.warning(f"[ENGINE] [T267] Tâche non sérialisable ignorée : {type(t)}")
        return sortie
