import logging
import json
import asyncio
from core.state import TaskPayload, StateUpdate
from agents.base_agent import BaseAgent
from core.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

class ReviewerAgent(BaseAgent):
    """
    Agent spécialisé dans la revue de code et la validation sémantique (Checker/Reviewer).
    Analyse le code généré ou modifié et s'assure du respect des consignes de propreté,
    de sécurité (secrets), et des consignes utilisateur (commentaires en français, DAC, GPIO).
    """
    def __init__(self, llm_gateway: LLMGateway, provider_name: str = "deepseek"):
        super().__init__(
            name="reviewer",
            system_prompt="""Tu es le ReviewerAgent (Checker), un ingénieur expert en revue de code, architectures domotiques (Home Assistant, ESPHome C++) et de développement général.
Ton unique rôle est de relire et valider de façon critique le code proposé par les agents développeurs avant sa fusion.

DIRECTIVES DE REVUE ET CRITÈRES D'ACCEPTATION :
1. COMMENTAIRES EN FRANÇAIS : Tout le code modifié ou créé (C++, Python, YAML) doit comporter des commentaires explicatifs détaillés et rédigés en français. C'est une règle globale stricte de l'utilisateur.
2. SÉCURITÉ DES SECRETS : Aucun mot de passe, clé d'API, ou identifiant WiFi ne doit être écrit en clair. Ils doivent tous utiliser la syntaxe `!secret nom_du_secret` dans les fichiers Home Assistant et ESPHome.
3. CONFORMITÉ ESPHOME (C++ / GPIO) :
   - Vérifie la compatibilité des pins GPIO (pas de conflit sur les pins réservés au boot, comme GPIO12 sur l'ESP32).
   - Pour la puce audio ES8388 (DAC), assure-toi que le registre de puissance `DACPOWER` (0x04) est rallumé via l'écriture brute I2C `{0x04, 0x00}` dans le `on_boot`, pour compenser le bug officiel d'inversion d'ESPHome.
   - Vérifie que le `dac_output` de l'ES8388 est bien configuré sur `LINE1` (indispensable pour avoir du son).
4. QUALITÉ DU CODE : Recherche les erreurs d'indentation, les importations manquantes, et les fonctions/variables mal nommées.
5. RIGUEUR DU VERDICT : Si le code comporte une faille, un bug, ou une consigne non respectée, tu DOIS rejeter la modification et lister précisément les corrections requises. Sois très strict.
6. QUALITÉ VISUELLE (si un rapport d'analyse visuelle ou un screenshot est fourni dans le contexte) : Évalue l'harmonie des couleurs, la lisibilité des textes, l'alignement des éléments, la cohérence du design avec les standards premium (glassmorphism, coins arrondis, animations fluides). Un score visuel inférieur à 5/10 doit entraîner un rejet avec des recommandations précises.
7. SCORE DE QUALITÉ GRADUÉ : En plus du verdict binaire, attribue un `quality_score` de 0 (inacceptable) à 10 (parfait), cohérent avec la sévérité : critical≈0-3, major≈3-6, minor≈6-8, info≈8-10. Ce score sert à décider d'une éventuelle escalade vers un modèle plus puissant pour la correction — sois précis et non paresseux (n'attribue pas systématiquement 5)."""
        )
        self.gateway = llm_gateway
        self.provider_name = provider_name
        
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
                                logger.info(f"[REVIEWER] Fichier modifié détecté dans Git : {filename}")
                                return True
        except Exception as e:
            logger.warning(f"[REVIEWER] Exception durant la vérification des modifications Git : {e}")
        return False

    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        from core.llm_gateway import load_config
        config = load_config()
        session_id = payload.metadata.get("session_id")
        tier = payload.metadata.get("model_tier", self.provider_name)
        
        # Court-circuit : si aucun fichier de code ou de configuration n'a été modifié, pas besoin de revue
        if not self._has_modified_files():
            logger.info("[REVIEWER] Aucun fichier de code ou de configuration modifié détecté. Auto-approbation par défaut.")
            return StateUpdate(
                agent_name=self.name,
                status="success",
                result_data="Auto-approuvé : Aucune modification de fichier détectée.",
                next_agent="END",
                metadata={"severity": "info", "approved": True, "quality_score": 10.0}
            )

        # Détermination du fournisseur de modèle pour la relecture
        resolved_model_name, provider = self.gateway.get_provider_for_tier(tier, config)
        logger.info(f"[REVIEWER] Utilisation du modèle pour la relecture : {resolved_model_name}")
        
        user_prompt = (
            f"Tâche / Objectif d'origine : {payload.task_objective}\n"
            f"Code à relire et contexte de la modification : \n{payload.relevant_context}"
        )
        
        # Schéma JSON attendu de la relecture
        schema = {
          "type": "object",
          "properties": {
            "code_approved": {
              "type": "boolean",
              "description": "true si le code respecte toutes les consignes de sécurité, de propreté et les règles utilisateur, false sinon."
            },
            "severity": {
              "type": "string",
              "enum": ["critical", "major", "minor", "info"],
              "description": "Gravité de la plus haute correction requise. 'critical' = bug bloquant ou faille de sécurité, 'major' = non-respect de consigne (ex: commentaires manquants), 'minor' = style ou nommage, 'info' = suggestion d'amélioration optionnelle."
            },
            "review_feedback": {
              "type": "string",
              "description": "Ton analyse détaillée en français expliquant les points forts et faibles du code relu."
            },
            "target_corrections": {
              "type": "array",
              "items": {"type": "string"},
              "description": "Liste précise des lignes ou des logiques à corriger en cas de refus. Laisser vide si approuvé."
            },
            "quality_score": {
              "type": "number",
              "description": "Score global de qualité de 0 (inacceptable) à 10 (parfait), cohérent avec la sévérité (critical≈0-3, major≈3-6, minor≈6-8, info≈8-10)."
            }
          },
          "required": ["code_approved", "severity", "review_feedback", "target_corrections", "quality_score"]
        }
        
        try:
            response_json = await provider.generate_structured_async(
                system_prompt=self.system_prompt + "\nFormat de sortie OBLIGATORY (JSON): " + json.dumps(schema),
                user_prompt=user_prompt,
                schema=schema,
                session_id=session_id,
            )
            
            approved = response_json.get("code_approved", False)
            severity = response_json.get("severity", "major")
            feedback = response_json.get("review_feedback", "Aucun commentaire fourni.")
            corrections = response_json.get("target_corrections", [])
            # [#T117] Score gradué (0-10) — fallback sur la sévérité si le LLM l'omet
            _sev_scores = {"info": 9.0, "minor": 7.5, "major": 5.0, "critical": 2.0}
            quality_score = response_json.get("quality_score")
            if quality_score is None:
                quality_score = _sev_scores.get(severity, 5.0)
            quality_score = max(0.0, min(10.0, float(quality_score)))

            logger.info(f"[REVIEWER] Verdict : Approbation = {approved}, Sévérité = {severity}, Score = {quality_score:.1f}/10")
            logger.info(f"[REVIEWER] Commentaire : {feedback}")

            # Mise à jour Elo du routing_type depuis le verdict Reviewer (score gradué)
            try:
                _routing_type = payload.metadata.get("routing_type", "")
                if _routing_type:
                    from core.elo_router import get_elo_router
                    asyncio.create_task(
                        get_elo_router().update_score(_routing_type, quality_score)
                    )
            except Exception as _ee:
                logger.debug(f"[REVIEWER] EloRouter update skipped : {_ee}")

            # Auto-approbation douce : les remarques mineures/info ne bloquent pas
            if not approved and severity in ("info", "minor"):
                logger.info(f"[REVIEWER] Sévérité '{severity}' → auto-approbation douce (soft-approve). Suggestions notées mais non bloquantes.")
                approved = True
                feedback = f"[SOFT-APPROVE] {feedback}"

            
            if approved:
                return StateUpdate(
                    agent_name=self.name,
                    status="success",
                    result_data=f"Code validé et approuvé par le Reviewer. Sévérité: {severity}. Rapport : {feedback}",
                    next_agent="END",
                    metadata={"severity": severity, "approved": True, "quality_score": quality_score}
                )
            else:
                formatted_corrections = "\n".join(f"- {c}" for c in corrections)
                error_msg = f"Code rejeté par le Reviewer (sévérité: {severity}).\nRapport : {feedback}\nCorrections obligatoires :\n{formatted_corrections}"
                return StateUpdate(
                    agent_name=self.name,
                    status="error",
                    result_data=error_msg,
                    next_agent="END",
                    error_message=error_msg,
                    metadata={"severity": severity, "approved": False, "corrections": corrections, "quality_score": quality_score}
                )
                
        except Exception as e:
            logger.error(f"[REVIEWER] Échec de la session de relecture : {e}")
            # En cas de crash de l'API de relecture, on choisit de rejeter par sécurité
            return StateUpdate(
                agent_name=self.name,
                status="error",
                result_data=None,
                next_agent="END",
                error_message=f"Crash de l'API du Reviewer durant la relecture : {str(e)}"
            )
