"""
core/review_loop.py — Boucle de Revue Automatique post-DAG.

Extrait de engine.py (Phase 1 Audit V5, Axe A1).
Après l'exécution du DAG, le ReviewerAgent évalue la qualité du code produit.
Si le Reviewer rejette, un plan correctif est généré et exécuté,
puis le code est re-soumis au Reviewer (max 2 rounds).

Historique :
- V5.2 : Logique inlinée dans engine.py (L579-L748)
- V5.5 : Extraction dans un module dédié (A1 Audit)
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from core.state import TaskPayload, ExecutionPhase

if TYPE_CHECKING:
    from core.engine import Engine

logger = logging.getLogger(__name__)

# Nombre maximum de rounds de review-correction
MAX_REVIEW_ROUNDS = 2


class ReviewLoop:
    """
    Boucle de revue automatique post-DAG.
    
    Workflow :
    1. Agrège les résultats du DAG (snippets des StateUpdates réussis)
    2. Soumet au ReviewerAgent pour évaluation
    3. Si rejeté → Planner génère un plan correctif → Exécution → Re-review
    4. Si approuvé ou max rounds atteint → Fin
    
    Le Reviewer fait un "soft-approve" pour les sévérités minor/info.
    """

    def __init__(self, engine: "Engine"):
        self._engine = engine
        self._last_visual_result = None

    async def run_review(
        self,
        initial_objective: str,
        max_rounds: int = MAX_REVIEW_ROUNDS,
        on_event=None,
    ) -> bool:
        """
        Exécute la boucle de revue post-DAG.
        
        Args:
            initial_objective: Objectif original de la requête utilisateur.
            max_rounds: Nombre maximum de rounds review-correction.
            on_event: Callback SSE asynchrone pour l'IHM.
            
        Returns:
            True si le code est validé, False si rejeté après tous les rounds.
        """
        reviewer_agent = self._engine.agents.get("reviewer")
        if not reviewer_agent:
            logger.info("[REVIEW] Reviewer non disponible, revue ignorée.")
            return True

        # Court-circuit : si aucun fichier de code ou de configuration n'a été modifié, pas besoin de revue
        if not self._has_modified_files():
            logger.info("[REVIEW] Aucun fichier de code ou de configuration n'a été créé ou modifié dans le workspace. Revue de code sautée avec succès.")
            if on_event:
                await on_event("review_completed", {
                    "session_id": self._engine.state.session_id,
                    "approved": True,
                    "visual_qa": None,
                    "verdict": "Auto-approuvé : Aucune modification de fichier détectée."
                })
            return True

        logger.info("[REVIEW] Démarrage de la revue automatique post-DAG...")
        if on_event:
            await on_event("review_started", {
                "session_id": self._engine.state.session_id,
            })

        approved = False

        for review_round in range(1, max_rounds + 1):
            # 1. Agrégation du contexte des résultats du DAG
            review_context = await self._build_review_context()

            review_payload = TaskPayload(
                task_objective=(
                    f"Revue automatique post-DAG (round {review_round}/{max_rounds}) "
                    f"du plan : {initial_objective}"
                ),
                relevant_context=review_context,
                metadata={
                    "session_id": self._engine.state.session_id,
                    "model_tier": "moyen",
                },
            )

            # 2. Invocation du Reviewer
            self._engine.state.current_phase = ExecutionPhase.REVIEWING
            if on_event:
                await on_event("agent_started", {
                    "agent_name": "reviewer",
                    "task_objective": review_payload.task_objective,
                })

            review_update = await reviewer_agent.invoke(review_payload)

            async with self._engine._history_lock:
                self._engine.state.history.append(review_update)

            if on_event:
                await on_event("agent_completed", {
                    "agent_name": "reviewer",
                    "status": review_update.status,
                    "result_data": review_update.result_data,
                    "error_message": review_update.error_message,
                })

            # 3. Évaluation du verdict
            if review_update.status == "success":
                logger.info(f"[REVIEW] ✅ Round {review_round} : code validé.")
                approved = True
                break
            else:
                # Reviewer a rejeté
                severity = review_update.metadata.get("severity", "?")
                logger.warning(
                    f"[REVIEW] ❌ Round {review_round} : code rejeté. Sévérité: {severity}"
                )

                if review_round >= max_rounds:
                    logger.error("[REVIEW] Limite de rounds de review atteinte. Marquage en erreur.")
                    break

                # 4. Tenter la correction via le Planner
                correction_ok = await self._apply_corrections(
                    review_update, initial_objective, review_round, on_event
                )
                if not correction_ok:
                    break
                
                logger.info(
                    f"[REVIEW] Corrections round {review_round} appliquées. "
                    f"Re-soumission au Reviewer..."
                )
                # La boucle for reprend → nouveau round de review

        if on_event:
            await on_event("review_completed", {
                "session_id": self._engine.state.session_id,
                "approved": approved,
                "visual_qa": self._last_visual_result,
                "verdict": review_update.result_data if 'review_update' in locals() else None
            })

        return approved

    async def _build_review_context(self) -> str:
        """
        Agrège les résultats réussis du DAG pour le Reviewer.
        
        Si les tâches produisent une interface (détecté via metadata
        'produces_ui' ou mots-clés dans l'objectif), un screenshot est capturé
        et analysé par le VisualQAService pour enrichir le contexte de review.
        """
        parts = []
        has_ui_tasks = False

        async with self._engine._history_lock:
            for u in self._engine.state.history:
                if u.status == "success" and u.metadata.get("task_id"):
                    snippet = str(u.result_data)[:500] if u.result_data else ""
                    parts.append(f"[{u.metadata.get('task_id', '?')}] {snippet}")
                    
                    # Détection des tâches produisant une UI
                    if u.metadata.get("produces_ui"):
                        has_ui_tasks = True
                    elif u.metadata.get("task_objective"):
                        obj_lower = u.metadata["task_objective"].lower()
                        ui_keywords = [
                            "interface", "ui", "ihm", "dashboard", "page",
                            "html", "css", "frontend", "bouton", "button",
                            "formulaire", "form", "onglet", "tab", "modal",
                        ]
                        if any(kw in obj_lower for kw in ui_keywords):
                            has_ui_tasks = True

        review_text = "\n---\n".join(parts)

        # Enrichissement visuel si des tâches UI sont détectées
        if has_ui_tasks:
            try:
                from core.visual_qa import VisualQAService
                visual_qa = VisualQAService()
                
                visual_result = await visual_qa.capture_and_analyze(
                    question=(
                        "Analyse cette capture d'écran de l'interface. "
                        "Identifie les problèmes visuels (alignement, couleurs, "
                        "contraste, ergonomie) et donne un score de qualité /10."
                    ),
                    session_id=self._engine.state.session_id,
                )
                
                if visual_result.get("success"):
                    self._last_visual_result = visual_result
                    visual_context = (
                        "\n\n--- ANALYSE VISUELLE (SCREENSHOT) ---\n"
                        f"Score visuel : {visual_result.get('score', '?')}/10\n"
                        f"Verdict : {visual_result.get('visual_verdict', 'N/A')}\n"
                    )
                    issues = visual_result.get("issues", [])
                    if issues:
                        visual_context += "Problèmes détectés :\n"
                        for issue in issues:
                            visual_context += f"  - {issue}\n"
                    
                    review_text += visual_context
                    logger.info(
                        f"[REVIEW] [VISUAL-QA] Contexte visuel injecté — "
                        f"score: {visual_result.get('score', '?')}/10"
                    )
                else:
                    self._last_visual_result = None
                    logger.info("[REVIEW] [VISUAL-QA] Capture échouée, review textuelle seule")
                    
            except Exception as vqa_err:
                logger.warning(
                    f"[REVIEW] [VISUAL-QA] Erreur (non bloquant) : {vqa_err}"
                )

        return review_text

    async def _apply_corrections(
        self,
        review_update,
        initial_objective: str,
        review_round: int,
        on_event=None,
    ) -> bool:
        """
        Génère et exécute un plan correctif basé sur les rejets du Reviewer.
        
        Returns:
            True si les corrections ont été appliquées avec succès.
        """
        planner_agent = self._engine.agents.get("planner")
        if not planner_agent:
            logger.error("[REVIEW] Planner non disponible pour la correction post-review.")
            return False

        corrections_list = review_update.metadata.get("corrections", [])
        correction_prompt = (
            f"CORRECTIONS REQUISES PAR LE REVIEWER (Round {review_round}) :\n"
            f"{review_update.error_message}\n\n"
            f"Génère un plan de correction pour résoudre ces problèmes. "
            f"Les corrections doivent cibler précisément les fichiers et lignes signalés."
        )

        correction_payload = TaskPayload(
            task_objective=correction_prompt,
            relevant_context=f"Plan d'origine : {initial_objective}",
            metadata={
                "session_id": self._engine.state.session_id,
                "is_review_correction": True,
            },
        )

        if on_event:
            await on_event("review_correction_started", {
                "round": review_round,
                "corrections_count": len(corrections_list),
            })

        # Invocation du Planner pour le plan correctif
        corr_plan_update = await planner_agent.invoke(correction_payload)
        async with self._engine._history_lock:
            self._engine.state.history.append(corr_plan_update)

        if corr_plan_update.status == "error" or not corr_plan_update.new_tasks:
            logger.error("[REVIEW] Planner a échoué à générer un plan correctif post-review.")
            return False

        # Exécution des tâches correctives par stage
        corr_stages = {}
        for ct in corr_plan_update.new_tasks:
            cs_id = ct.metadata.get("stage_id", 1)
            if cs_id not in corr_stages:
                corr_stages[cs_id] = []
            corr_stages[cs_id].append(ct)

        for cs_id in sorted(corr_stages.keys()):
            async def _run_corr_task(ct_payload: TaskPayload):
                ct_name = ct_payload.metadata.get("target_agent", "executor")
                ct_agent = self._engine.agents.get(ct_name)
                if not ct_agent:
                    raise ValueError(f"Agent cible inconnu pour correction: '{ct_name}'")
                return await ct_agent.invoke(ct_payload)

            corr_results = await asyncio.gather(
                *(_run_corr_task(ct) for ct in corr_stages[cs_id]),
                return_exceptions=True,
            )

            for cr in corr_results:
                if isinstance(cr, Exception):
                    logger.error(f"[REVIEW] Exception dans tâche corrective post-review : {cr}")
                    return False
                async with self._engine._history_lock:
                    self._engine.state.history.append(cr)
                if cr.status == "error":
                    logger.error(f"[REVIEW] Tâche corrective échouée : {cr.error_message}")
                    return False

        return True

    def _has_modified_files(self) -> bool:
        """Détecte s'il y a des fichiers de code ou config modifiés dans les workspaces Git."""
        import subprocess
        import os
        try:
            # Recherche des dépôts Git dans le workspace
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

            for git_dir in git_dirs:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=git_dir, encoding='utf-8', errors='ignore'
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) == 2:
                            filename = parts[1]
                            # Ignorer le log, les BDD du moteur, le stockage HA et config HA
                            if any(k in filename for k in ["moteur.log", "moteur_runtime.db", "models_registry.db", "memory.db", ".storage", "ServeurHA"]):
                                continue
                            # Ignorer le format JSON de la revue de code
                            if filename.endswith(('.yaml', '.yml', '.py', '.cpp', '.h', '.ino', '.html', '.css', '.js', '.md')):
                                logger.info(f"[REVIEW] Fichier modifié détecté dans Git : {filename}")
                                return True
        except Exception as e:
            logger.warning(f"[REVIEW] Exception durant la vérification des modifications Git : {e}")
        return False

