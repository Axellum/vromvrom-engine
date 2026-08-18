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
import os
from typing import TYPE_CHECKING

from core.acceptance_contract import (
    CLE_ETAT_INITIAL,
    CLE_SUBSTITUTIONS,
    cle_critere,
    normaliser_criteres,
    verifier_contrat,
)
from core.state import ExecutionPhase, StateUpdate, TaskPayload

if TYPE_CHECKING:
    from core.engine import Engine

logger = logging.getLogger(__name__)

# Nombre maximum de rounds de review-correction
MAX_REVIEW_ROUNDS = 2

# [#T117] Cascade routing qualité : si le Reviewer rejette avec un quality_score
# sous ce seuil sur un domaine "coûteux", la correction est forcée sur un tier
# plus puissant plutôt que de re-tenter au même tier. Configurable via
# `config.json` (clé `cascade_quality_escalation`) — valeurs ci-dessous = défauts.
DEFAULT_ESCALATION_ENABLED = True
DEFAULT_QUALITY_THRESHOLD = 6.0
DEFAULT_ELIGIBLE_DOMAINS = ["code_generation", "analysis", "sysadmin"]
DEFAULT_ESCALATED_TIER = "fort"


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
            # [#T253] …sauf si un contrat d'acceptation existe. Ce court-circuit
            # a été écrit pour la revue de CODE : sans fichier modifié, il n'y a
            # rien à relire. Mais un contrat, lui, vérifie l'OBJECTIF — qui peut
            # parfaitement être atteint sans toucher un fichier (lire, calculer,
            # répondre, interroger Home Assistant). Mesuré en prod le 10/08 :
            # ce raccourci auto-approuvait le plan et le contrat n'était jamais
            # évalué — c'était le dernier des trois verrous de #T253.
            criteres, etats_initiaux, substitutions = self._contrat_complet()
            verdict = None
            if criteres:
                rapport = await asyncio.to_thread(
                    verifier_contrat, criteres, etats_initiaux=etats_initiaux
                )
                if rapport.determinable:
                    verdict = rapport.satisfait
                    logger.info(
                        f"[REVIEW] [T253] Aucun fichier modifié, mais contrat évaluable : "
                        f"{'✅ satisfait' if verdict else '❌ en échec'} "
                        f"({len(rapport.verifiables)} critère(s) vérifiés)."
                    )
                    if on_event:
                        await on_event("contract_checked", {
                            "round": 0,
                            "satisfied": verdict,
                            "failures": len(rapport.echecs),
                            "summary": rapport.resume(),
                            "substitutions": substitutions,
                            "contexte": "sans_modification_de_fichier",
                        })

            if verdict is None:
                logger.info("[REVIEW] Aucun fichier de code ou de configuration n'a été créé ou modifié dans le workspace. Revue de code sautée avec succès.")
            if on_event:
                await on_event("review_completed", {
                    "session_id": self._engine.state.session_id,
                    "approved": True if verdict is None else verdict,
                    "visual_qa": None,
                    "verdict": (
                        "Auto-approuvé : Aucune modification de fichier détectée."
                        if verdict is None else
                        f"Contrat d'acceptation {'satisfait' if verdict else 'NON satisfait'} (aucun fichier modifié)."
                    ),
                })
            return True if verdict is None else verdict

        logger.info("[REVIEW] Démarrage de la revue automatique post-DAG...")
        if on_event:
            await on_event("review_started", {
                "session_id": self._engine.state.session_id,
            })

        approved = False

        for review_round in range(1, max_rounds + 1):
            # [#T253] Porte déterministe AVANT l'avis du Reviewer : quand le
            # Planner a posé un contrat vérifiable, ce sont les critères qui
            # tranchent, pas un score de complaisance. Contrat satisfait →
            # validation sans appel LLM ; contrat en échec → correction directe,
            # l'avis d'un modèle ne peut pas contredire un test qui échoue.
            #
            # [Incident du 17/08] Le contrat évalué est l'UNION de tous les
            # contrats de l'historique : un plan correctif peut enrichir le
            # contrat, jamais remplacer ni affaiblir celui qui a été posé (ou
            # approuvé) avant lui. Avant ce correctif, seul le plus récent
            # était lu — un lot en échec pouvait se réécrire un examen plus
            # facile à chaque replanification.
            criteres_contrat, etats_initiaux, substitutions = self._contrat_complet()
            if criteres_contrat:
                verdict_contrat = await asyncio.to_thread(
                    verifier_contrat, criteres_contrat, etats_initiaux=etats_initiaux
                )
                if verdict_contrat.determinable:
                    if on_event:
                        await on_event("contract_checked", {
                            "round": review_round,
                            "satisfied": verdict_contrat.satisfait,
                            "failures": len(verdict_contrat.echecs),
                            "summary": verdict_contrat.resume(),
                            "substitutions": substitutions,
                            "contexte": "revue_post_dag",
                        })
                    if verdict_contrat.satisfait:
                        logger.info(
                            f"[REVIEW] [T253] ✅ Contrat d'acceptation satisfait au round {review_round} "
                            f"({len(verdict_contrat.verifiables)} critère(s)) — revue LLM inutile."
                        )
                        approved = True
                        break

                    logger.warning(
                        f"[REVIEW] [T253] ❌ Contrat d'acceptation en échec au round {review_round} : "
                        f"{len(verdict_contrat.echecs)} critère(s) non satisfait(s)."
                    )
                    if review_round >= max_rounds:
                        logger.error("[REVIEW] [T253] Limite de rounds atteinte, contrat toujours en échec.")
                        break
                    if not await self._apply_corrections(
                        self._update_depuis_contrat(verdict_contrat),
                        initial_objective, review_round, on_event,
                    ):
                        break
                    continue
                logger.info(
                    "[REVIEW] [T253] Contrat non déterminable (aucun critère vérifiable) — "
                    "repli sur la revue LLM."
                )

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
                quality_score = review_update.metadata.get("quality_score")
                logger.warning(
                    f"[REVIEW] ❌ Round {review_round} : code rejeté. Sévérité: {severity}, "
                    f"Score: {quality_score}"
                )

                if review_round >= max_rounds:
                    logger.error("[REVIEW] Limite de rounds de review atteinte. Marquage en erreur.")
                    break

                # [#T117] Cascade routing qualité : escalader vers un tier plus fort
                # si le score est bas sur un domaine coûteux, plutôt que de re-tenter
                # au même tier en boucle.
                force_tier = self._escalation_tier(quality_score)
                if force_tier:
                    logger.warning(
                        f"[REVIEW] [T117] ⬆️ Escalade cascade qualité : quality_score={quality_score} "
                        f"sous seuil sur domaine coûteux → correction forcée au tier '{force_tier}'."
                    )
                    if on_event:
                        await on_event("review_escalation", {
                            "round": review_round,
                            "quality_score": quality_score,
                            "escalated_tier": force_tier,
                        })

                # 4. Tenter la correction via le Planner
                correction_ok = await self._apply_corrections(
                    review_update, initial_objective, review_round, on_event, force_tier=force_tier
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

    async def contrat_seul(self, on_event=None) -> bool | None:
        """
        [#T253] Évalue le contrat d'acceptation SANS revue LLM.

        Sert le cas que la boucle normale ne couvrait pas : le DAG s'est terminé
        en erreur. `core/engine.py` sautait alors toute vérification — or c'est
        exactement la situation où une preuve mécanique vaut mieux qu'un avis :
        une branche peut échouer alors que l'objectif est atteint par une autre,
        et seul le contrat peut trancher. Inversement, un contrat en échec
        confirme l'erreur au lieu de la deviner.

        Ne demande RIEN au LLM : après un DAG en échec, payer une revue de
        complaisance n'a pas de sens.

        Retourne `True` (objectif prouvé atteint), `False` (contrat en échec) ou
        `None` quand il n'y a rien de vérifiable — dans ce dernier cas l'appelant
        garde son comportement d'avant.
        """
        if os.getenv("MOTEUR_CONTRAT_SUR_ECHEC", "1").strip().lower() in ("0", "false", "off", "no"):
            return None

        criteres, etats_initiaux, substitutions = self._contrat_complet()
        if not criteres:
            return None

        verdict = await asyncio.to_thread(verifier_contrat, criteres, etats_initiaux=etats_initiaux)
        if not verdict.determinable:
            d_avance = len(verdict.resultats_satisfaits_d_avance)
            note = (
                f" — dont {d_avance} satisfait(s) d'avance, exclus du verdict : "
                "un contrat qui était déjà vrai avant le lot ne prouve rien."
                if d_avance else ""
            )
            logger.info(
                "[REVIEW] [T253] DAG en échec et contrat non déterminable "
                f"({len(verdict.non_verifiables)} critère(s) non vérifiable(s)){note} — verdict inchangé."
            )
            return None

        if on_event:
            await on_event("contract_checked", {
                "round": 0,
                "satisfied": verdict.satisfait,
                "failures": len(verdict.echecs),
                "summary": verdict.resume(),
                "substitutions": substitutions,
                "contexte": "dag_en_echec",
            })

        if verdict.satisfait:
            # [Incident du 17/08] Un DAG en erreur absous par contrat est un
            # événement rare et puissant : le journal doit dire EXACTEMENT ce
            # qui a été évalué et en quoi cela diffère du contrat approuvé.
            # Avant ce correctif, il fallait fouiller la base SQLite des heures
            # plus tard pour s'apercevoir que le contrat évalué n'était plus
            # celui qui avait été approuvé.
            logger.warning(
                "[REVIEW] [T253] ✅ Le DAG a signalé une erreur, mais le contrat d'acceptation "
                f"est SATISFAIT ({len(verdict.verifiables)} critère(s) vérifiés) : l'objectif est "
                f"prouvé atteint malgré l'échec d'une tâche.\n{self._trace_evaluation(verdict, substitutions)}"
            )
            return True

        logger.error(
            "[REVIEW] [T253] ❌ DAG en échec ET contrat non satisfait : "
            f"{len(verdict.echecs)} critère(s) en échec.\n{verdict.resume()}\n"
            f"{self._trace_evaluation(verdict, substitutions)}"
        )
        return False

    def _criteres_du_plan(self) -> list:
        """
        [#T253] Récupère le contrat d'acceptation évalué par la porte déterministe.

        [Incident du 17/08] Ce n'est PLUS « le plus récent de l'historique » :
        c'est l'UNION de tous les contrats posés, sans exception. Un plan
        correctif peut enrichir le contrat, jamais le remplacer
        ni l'affaiblir — avant ce correctif, chaque échec donnait au lot une
        occasion de se réécrire un examen plus facile, et le contrat approuvé
        disparaissait de l'évaluation (mesuré en prod le 17/08 : le fichier
        livrable exigé par le contrat approuvé n'a jamais été créé, et le lot
        a été déclaré satisfait sur deux critères portant sur des fichiers qui
        existaient déjà).

        Retourne des dicts bruts (format des metadata). L'ordre place le
        contrat le plus récent en tête — enrichissement oblige, c'est lui qui
        décrit le mieux l'état visé — puis les critères plus anciens jamais
        retirés : l'ordre ne change rien au verdict, tous sont évalués. Le
        plancher (contrat d'origine ou restauré) se reconnaît à sa position
        dans l'historique, pas à sa place dans cette liste.
        """
        criteres, _etats, _substitutions = self._contrat_complet()
        return criteres

    def _entrees_contrat(self) -> list[dict]:
        """
        Toutes les entrées de contrat de l'historique, en ordre chronologique.

        La PREMIÈRE entrée est le plancher : le contrat posé par le plan
        d'origine, ou — après une approbation humaine — celui restauré par
        `services/approval_resume_service.py` depuis `hitl_pending_approvals`,
        qui l'injecte en tête d'un historique neuf. Le service de reprise est
        donc couvert par construction : son contrat restauré est toujours le
        plancher, et aucun contrat correctif ultérieur ne peut l'évincer.
        """
        from core.acceptance_contract import CLE_CONTRAT
        entrees = []
        for update in self._engine.state.history:
            criteres = (update.metadata or {}).get(CLE_CONTRAT)
            if criteres:
                entrees.append({
                    "criteres": criteres,
                    "etat_initial": (update.metadata or {}).get(CLE_ETAT_INITIAL) or {},
                    "substitutions": (update.metadata or {}).get(CLE_SUBSTITUTIONS) or [],
                })
        return entrees

    def _contrat_complet(self) -> tuple[list, dict[int, bool], list]:
        """
        [Incident du 17/08] Construit le contrat effectif à évaluer.

        Retourne `(critères, états initiaux, substitutions appliquées)` :
        - les critères : union de tous les contrats de l'historique, après
          application des substitutions déclarées valides ;
        - les états initiaux : indexés sur la liste NORMALISÉE des critères
          (comme `verifier_contrat` les ré-indexe), le premier constat posé
          pour une même clé l'emportant toujours — c'est celui d'avant le
          travail ;
        - les substitutions appliquées, pour le journal et les événements.
        """
        from core.acceptance_contract import union_contrats
        entrees = self._entrees_contrat()
        if not entrees:
            return [], {}, []

        union = union_contrats([e["criteres"] for e in entrees])
        normalises = normaliser_criteres(union)

        plancher = normaliser_criteres(entrees[0]["criteres"])
        substitutions = self._appliquer_substitutions(entrees, union, plancher)
        if substitutions:
            # Les critères substitués sortent de l'évaluation — de façon tracée,
            # journalisée et réversible à la lecture : leurs remplaçants restent
            # dans l'union et seront évalués à leur place.
            union = self._retirer_substituees(union, substitutions)
        normalises = normaliser_criteres(union)

        ajoutes = max(len(normalises) - len(plancher) + len(substitutions), 0)
        if len(entrees) > 1:
            logger.info(
                f"[REVIEW] [CONTRAT] Union de {len(entrees)} contrat(s) de l'historique : "
                f"plancher approuvé/initial = {len(plancher)} critère(s), "
                f"contrat effectif = {len(normalises)} critère(s) "
                f"({ajoutes} ajouté(s) par les plans correctifs, "
                f"{len(substitutions)} substitution(s) déclarée(s) appliquée(s))."
            )

        etats = self._etats_initiaux_effectifs(entrees, normalises)
        return union, etats, substitutions

    @staticmethod
    def _retirer_substituees(union: list, substitutions: list) -> list:
        """Écarte de l'union les critères du plancher substitués (tracé, jamais silencieux)."""
        keys_a_retirer = set()
        for sub in substitutions:
            origine = normaliser_criteres(sub.get("origine"))
            if origine:
                keys_a_retirer.add(cle_critere(origine[0]))
        return [
            brut for brut in union
            if cle_critere(normaliser_criteres(brut)[0]) not in keys_a_retirer
        ]

    def _appliquer_substitutions(self, entrees: list, union: list, plancher: list) -> list:
        """
        Arbitrage de l'incident du 17/08 : un critère du plancher devenu
        IMPOSSIBLE (le plan correctif a légitimement changé d'approche) peut
        être remplacé — mais JAMAIS silencieusement. Chaque substitution doit
        être déclarée explicitement par le plan correctif, viser un critère du
        plancher, et son remplaçant doit être présent dans le contrat et ne pas
        être satisfait d'avance. Toute déclaration invalide est écartée et
        journalisée : le critère du plancher reste alors évalué tel quel.

        Seuls les plans CORRECTIFS (entrées d'index ≥ 1) peuvent déclarer une
        substitution : le plancher ne se substitue pas à lui-même.
        """
        keys_plancher = {cle_critere(c) for c in plancher}
        keys_union = {cle_critere(c) for c in normaliser_criteres(union)}
        etats = self._etats_initiaux_effectifs(entrees, normaliser_criteres(union))
        normalises_union = normaliser_criteres(union)

        appliquees = []
        for position, entree in enumerate(entrees):
            for declaration in entree["substitutions"]:
                if not isinstance(declaration, dict):
                    logger.warning("[REVIEW] [CONTRAT] Substitution ignorée : pas un objet.")
                    continue
                origine = normaliser_criteres(declaration.get("origine"))
                remplacant = normaliser_criteres(declaration.get("remplacant"))
                if not origine or not remplacant:
                    logger.warning(
                        f"[REVIEW] [CONTRAT] Substitution ignorée (origine ou remplaçant illisible) : {declaration!r}"
                    )
                    continue
                cle_origine, cle_remplacant = cle_critere(origine[0]), cle_critere(remplacant[0])
                if position == 0:
                    logger.warning(
                        "[REVIEW] [CONTRAT] Substitution ignorée : seul un plan correctif "
                        "peut déclarer une substitution, pas le contrat d'origine."
                    )
                    continue
                if cle_origine not in keys_plancher:
                    logger.warning(
                        "[REVIEW] [CONTRAT] Substitution ignorée : elle ne cible pas un "
                        f"critère du contrat approuvé — {origine[0].libelle()!r}."
                    )
                    continue
                if cle_remplacant not in keys_union:
                    logger.warning(
                        "[REVIEW] [CONTRAT] Substitution ignorée : le critère remplaçant "
                        f"est absent du contrat — {remplacant[0].libelle()!r}."
                    )
                    continue
                index_remplacant = next(
                    (i for i, c in enumerate(normalises_union) if cle_critere(c) == cle_remplacant),
                    None,
                )
                if etats.get(index_remplacant):
                    logger.warning(
                        "[REVIEW] [CONTRAT] Substitution ignorée : le critère remplaçant "
                        f"était satisfait d'avance — {remplacant[0].libelle()!r}."
                    )
                    continue
                appliquees.append({
                    "origine": dict(declaration["origine"]),
                    "remplacant": dict(declaration["remplacant"]),
                    "motif": str(declaration.get("motif") or "").strip(),
                })
                logger.warning(
                    "[REVIEW] [CONTRAT] SUBSTITUTION TRACÉE : le critère approuvé "
                    f"« {origine[0].libelle()} » est remplacé par « {remplacant[0].libelle()} » — "
                    f"motif : {declaration.get('motif') or 'non renseigné'}. "
                    "Le critère d'origine n'est plus évalué."
                )
        return appliquees

    @staticmethod
    def _etats_initiaux_effectifs(entrees: list, normalises: list) -> dict[int, bool]:
        """
        États initiaux indexés sur la liste normalisée du contrat effectif.

        Les constats des entrées sont posés par rapport à LEUR propre liste
        normalisée : on repasse par la clé de chaque critère pour ré-indexer.
        Le PREMIER constat posé pour une clé l'emporte — c'est le plus proche
        du début du travail. Une clé sans constat n'est jamais marquée
        « satisfaite d'avance » : le doute rend le jugement plus strict.
        """
        constats_par_cle: dict[tuple, bool] = {}
        for entree in entrees:
            normaux_entree = normaliser_criteres(entree["criteres"])
            for index_brut, valeur in (entree["etat_initial"] or {}).items():
                try:
                    index = int(index_brut)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(normaux_entree):
                    constats_par_cle.setdefault(cle_critere(normaux_entree[index]), bool(valeur))
        return {
            index: constats_par_cle[cle_critere(critere)]
            for index, critere in enumerate(normalises)
            if cle_critere(critere) in constats_par_cle
        }

    def _trace_evaluation(self, verdict, substitutions: list) -> str:
        """
        [Incident du 17/08] Trace d'audit du contrat évalué : provenance de
        chaque critère (plancher approuvé ou plan correctif), critères
        satisfaits d'avance, substitutions appliquées. Injectée dans le
        journal à chaque verdict de contrat, pour que la divergence entre
        contrat évalué et contrat approuvé soit visible sans fouiller la base.
        """
        entrees = self._entrees_contrat()
        lignes = ["[CONTRAT] Trace de l'évaluation :"]
        if not entrees:
            lignes.append("  - aucun contrat dans l'historique")
            return "\n".join(lignes)

        plancher = normaliser_criteres(entrees[0]["criteres"])
        keys_plancher = {cle_critere(c) for c in plancher}
        keys_substituees = set()
        for sub in substitutions:
            origine = normaliser_criteres(sub.get("origine"))
            if origine:
                keys_substituees.add(cle_critere(origine[0]))

        correctifs = 0
        for resultat in verdict.resultats:
            cle = cle_critere(resultat.critere)
            if cle in keys_plancher:
                provenance = "plancher (contrat d'origine/approuvé)"
            else:
                correctifs += 1
                provenance = "plan correctif (ajouté après le contrat d'origine)"
            note = " [satisfait d'avance — exclu du verdict]" if resultat.critere.satisfait_d_avance else ""
            # Les critères fichier montrent aussi leur cible : la description
            # seule peut masquer QUEL chemin a échoué — c'est précisément ce
            # qu'on reprochait au journal d'avant l'incident.
            cible = (
                f" — {resultat.critere.valeur}"
                if resultat.critere.type in ("fichier_contient", "fichier_existe") else ""
            )
            lignes.append(f"  - {resultat.critere.libelle()}{cible} ← {provenance}{note}")

        if correctifs:
            lignes.append(
                f"  ⚠️ {correctifs} critère(s) ne figurent PAS dans le contrat d'origine "
                f"({len(plancher)} critère(s)) : ils ont été posés par un plan correctif."
            )
        if keys_substituees:
            lignes.append(
                f"  ⚠️ {len(keys_substituees)} critère(s) du contrat d'origine substitué(s) "
                "(voir les lignes SUBSTITUTION TRACÉE ci-dessus)."
            )
        for sub in substitutions:
            lignes.append(
                f"  - substitution : « {sub['origine'].get('valeur')} » → "
                f"« {sub['remplacant'].get('valeur')} » ({sub.get('motif') or 'motif non renseigné'})"
            )
        return "\n".join(lignes)

    @staticmethod
    def _update_depuis_contrat(verdict) -> StateUpdate:
        """
        [#T253] Traduit un échec de contrat en rejet, dans la forme que
        `_apply_corrections` sait déjà consommer (`error_message` + `corrections`).
        """
        return StateUpdate(
            agent_name="contrat_acceptation",
            status="error",
            result_data=verdict.resume(),
            error_message=verdict.resume(),
            metadata={
                "corrections": [r.critere.libelle() for r in verdict.echecs],
                "severity": "bloquant",
                "source": "contrat_acceptation",
            },
        )

    def _escalation_tier(self, quality_score) -> str | None:
        """
        [#T117] Détermine si la correction doit être escaladée vers un tier plus
        puissant, et lequel. Retourne `None` si aucune escalade n'est nécessaire.

        Conditions cumulatives :
        - `cascade_quality_escalation.enabled` (config.json, défaut True)
        - quality_score fourni et strictement sous le seuil configuré
        - domaine dominant de la requête dans la liste des domaines éligibles
          (ex: code_generation, analysis, sysadmin — "tâches coûteuses")
        """
        if quality_score is None:
            return None

        try:
            from core.llm_gateway import load_config
            cfg = load_config().get("cascade_quality_escalation", {})
        except Exception:
            cfg = {}

        if not cfg.get("enabled", DEFAULT_ESCALATION_ENABLED):
            return None

        threshold = float(cfg.get("quality_threshold", DEFAULT_QUALITY_THRESHOLD))
        if quality_score >= threshold:
            return None

        eligible_domains = cfg.get("eligible_domains", DEFAULT_ELIGIBLE_DOMAINS)
        domain = self._get_dominant_category()
        if domain not in eligible_domains:
            return None

        return cfg.get("escalated_tier", DEFAULT_ESCALATED_TIER)

    def _get_dominant_category(self) -> str | None:
        """Retrouve le domaine dominant de la requête (posé par le Router en metadata)."""
        for u in self._engine.state.history:
            if u.metadata and u.metadata.get("dominant_category"):
                return u.metadata["dominant_category"]
        return None

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
        force_tier: str | None = None,
    ) -> bool:
        """
        Génère et exécute un plan correctif basé sur les rejets du Reviewer.

        Args:
            force_tier: [#T117] si fourni, écrase le `model_tier` de chaque tâche
                corrective générée par le Planner (cascade routing qualité).

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

        # [#T117] Cascade routing qualité : forcer le tier des tâches correctives
        # si une escalade a été décidée, quel que soit le tier choisi par le Planner.
        if force_tier:
            for ct in corr_plan_update.new_tasks:
                if not ct.metadata:
                    ct.metadata = {}
                ct.metadata["model_tier"] = force_tier

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
        import os
        import subprocess
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
                except Exception as err:
                    logger.debug(f"[REVIEW] Détection des dépôts Git impossible : {err}")

            moteur_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if moteur_dir not in git_dirs and os.path.exists(os.path.join(moteur_dir, ".git")):
                git_dirs.append(moteur_dir)

            for git_dir in git_dirs:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True,
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

