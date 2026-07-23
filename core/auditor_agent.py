"""
core/auditor_agent.py — Auditeur de code autonome, repeuple le backlog seul.

Service cron asyncio (même pattern que agents/dreamer_agent.py). Scanne le
code du dépôt lui-même (rotation d'une portée à la fois : core, agents, api,
memory, tools), envoie le corpus à un LLM via un prompt structuré forçant
un JSON de findings actionnables, dédoublonne contre le backlog actif
(core.backlog_db.get_active_task_titles), et insère les findings survivants
via core.backlog_db.add_task().

Réutilise la construction de corpus et l'hygiène de scan de secrets de
tools/vertex_audit.py (build_corpus/content_has_hardcoded_secret) plutôt que
de la dupliquer.

Ne modifie AUCUN fichier et n'a AUCUN accès Git — l'auditeur ne fait que
lire le code et proposer des tâches ; seul DreamCoder (agents/dreamer_agent.py)
modifie le dépôt, sur des branches Git isolées avec approbation humaine
obligatoire avant merge (PUT /api/backlog/tasks/{id}). Rien ici ne contourne
cette porte.
"""

import asyncio
import difflib
import glob
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("auditor_agent")

_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_PATH = os.path.join(_ENGINE_ROOT, "checkpoints", "auditor_state.json")
_REPORTS_DIR = os.path.join(_ENGINE_ROOT, "checkpoints", "auditor_reports")

_DEFAULT_SCOPES = ["core", "agents", "api", "memory", "tools"]

_SYSTEM_PROMPT = """Tu es un auditeur technique qui analyse un extrait de code source et en
extrait des tâches actionnables (bugs réels, dette technique concrète, incohérences).
Retourne UNIQUEMENT un JSON valide, sans balise markdown, avec cette structure exacte :
{
  "confidence": <entier 0-10, ta confiance dans la pertinence/actionabilité des findings>,
  "findings": [
    {"title": "...", "description": "...", "file": "chemin/relatif.py", "priority": 1|2|3}
  ]
}
priority: 1=urgent (bug réel/sécurité), 2=normal, 3=bas (cosmétique/mineur).
Ne propose que des findings concrets et actionnables, avec preuve dans le code fourni —
pas de généralités ni de suppositions sur du code que tu n'as pas vu. Si rien d'actionnable
n'est trouvé dans cet extrait, retourne findings=[]."""


# ──────────────────────────────────────────────────────────────────
# État persistant de l'auditeur (rotation de portée, dédup temporelle)
# ──────────────────────────────────────────────────────────────────

auditor_state: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_run_at": None,
    "last_report": None,
    "last_error": None,
    "total_runs": 0,
}


def _load_persistent_config() -> dict[str, Any]:
    """Charge la section persistent_agents depuis config.json (même pattern que dreamer_agent)."""
    try:
        from core.llm_gateway import load_config
        config = load_config()
        return config.get("persistent_agents", {})
    except Exception as e:
        logger.warning(f"[AUDITOR] Impossible de lire persistent_agents : {e}")
        return {}


def _load_state() -> dict[str, Any]:
    """État de rotation : {'scope_index': int, 'completed_count_baseline': int, 'last_run_at': float}."""
    if not os.path.exists(_STATE_PATH):
        return {"scope_index": 0, "completed_count_baseline": 0, "last_run_at": 0}
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[AUDITOR] État illisible, réinitialisation : {e}")
        return {"scope_index": 0, "completed_count_baseline": 0, "last_run_at": 0}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[AUDITOR] Erreur sauvegarde état : {e}")


# ──────────────────────────────────────────────────────────────────
# Estimation de coût (pas de tracking natif hors pipeline de session)
# ──────────────────────────────────────────────────────────────────

def _estimate_cost_usd(model_name: str, input_chars: int, output_chars: int) -> float:
    """
    Estimation grossière (chars/4 ≈ tokens) du coût d'un appel LLM ad-hoc,
    hors pipeline de session (donc non capté par core/token_tracker.py).
    Utilise les tarifs réels du catalogue si connus, sinon 0.0 (modèles
    locaux/gratuits ou non répertoriés).
    """
    try:
        from core.models_db import get_model_cost
        pricing = get_model_cost(model_name)
        cost_in = pricing.get("cost_input_per_m") or 0
        cost_out = pricing.get("cost_output_per_m") or 0
        input_tokens = input_chars / 4
        output_tokens = output_chars / 4
        return round((input_tokens / 1_000_000) * cost_in + (output_tokens / 1_000_000) * cost_out, 6)
    except Exception as e:
        logger.debug(f"[AUDITOR] Estimation de coût impossible pour '{model_name}' : {e}")
        return 0.0


# ──────────────────────────────────────────────────────────────────
# Extraction LLM structurée
# ──────────────────────────────────────────────────────────────────

async def _extract_findings(corpus: str, tier: str, max_output_tokens: int = 2000) -> tuple[dict | None, float]:
    """
    Appelle le LLM du tier donné (via ProviderScorer/LLMGateway, qualité
    d'abord) avec le corpus de code et le prompt structuré. Retourne
    (analyse JSON ou None, coût USD estimé).
    """
    from core.llm_gateway import LLMGateway, load_config

    gateway = LLMGateway()
    config = load_config()
    model_name, provider = gateway.get_provider_for_tier(tier, config)
    logger.info(f"[AUDITOR] Modèle résolu pour l'audit (tier={tier}) : {model_name}")

    user_prompt = f"## Extrait de code à auditer\n\n{corpus}"
    response = await provider.generate_async(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=max_output_tokens,
        temperature=0.2,
    )

    cost = _estimate_cost_usd(model_name, len(_SYSTEM_PROMPT) + len(user_prompt), len(response or ""))

    text = (response or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        return json.loads(text), cost
    except json.JSONDecodeError as je:
        logger.warning(f"[AUDITOR] Réponse LLM non-JSON ({je}). Réponse brute : {text[:200]}...")
        return None, cost


def _is_duplicate(title: str, active_titles: list[str], threshold: float) -> bool:
    """Similarité texte simple (difflib), volontairement sans embeddings pour cette itération."""
    norm = title.strip().lower()
    if not norm:
        return True
    return any(
        difflib.SequenceMatcher(None, norm, t.strip().lower()).ratio() >= threshold
        for t in active_titles
    )


# ──────────────────────────────────────────────────────────────────
# Cycle principal
# ──────────────────────────────────────────────────────────────────

async def run_auditor_cycle(pa_config: dict) -> dict[str, Any]:
    """
    Un cycle d'audit : construit le corpus de la portée courante (rotation),
    extrait des findings structurés via LLM, dédoublonne, insère dans le
    backlog. Avance la rotation de portée uniquement si le cycle aboutit
    (pas de findings extraits = portée à retenter plus tard, pas sautée).
    """
    from core.backlog_db import add_task, get_active_task_titles
    from core.budget_guard import BudgetGuard
    from tools.vertex_audit import build_corpus

    auditor_state["running"] = True
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "scope": None,
        "confidence": None,
        "findings_proposed": 0,
        "findings_skipped_duplicate": 0,
        "cost_usd": 0.0,
        "status": "ok",
    }

    try:
        bg = BudgetGuard()
        weekly_cap = bg.config.get("auditor_weekly_budget_usd", 1.0)
        spent = await bg.get_scoped_spend_usd("auditor", 7 * 86400)
        if spent >= weekly_cap:
            report["status"] = "budget_exhausted"
            logger.warning(f"[AUDITOR] Plafond hebdomadaire atteint ({spent:.4f}/{weekly_cap} USD) — cycle sauté.")
            return report

        state = _load_state()
        scopes = pa_config.get("auditor_scopes", _DEFAULT_SCOPES)
        if not scopes:
            report["status"] = "no_scopes_configured"
            return report

        scope_index = state.get("scope_index", 0) % len(scopes)
        scope = scopes[scope_index]
        report["scope"] = scope

        max_chars = pa_config.get("auditor_max_corpus_chars", 150_000)
        corpus, est_tokens = build_corpus([Path(_ENGINE_ROOT)], {scope}, set())
        if not corpus.strip():
            logger.info(f"[AUDITOR] Portée '{scope}' vide ou introuvable — rotation vers la suivante.")
            state["scope_index"] = (scope_index + 1) % len(scopes)
            _save_state(state)
            report["status"] = "empty_scope"
            return report

        corpus = corpus[:max_chars]
        logger.info(f"[AUDITOR] Portée '{scope}' — corpus {len(corpus):,} chars (~{est_tokens:,} tokens estimés avant troncature).")

        tier = pa_config.get("auditor_model_tier", "moyen")
        escalated_tier = pa_config.get("auditor_escalation_tier", "fort")
        confidence_threshold = pa_config.get("auditor_confidence_threshold", 6.0)
        dedup_threshold = pa_config.get("auditor_dedup_similarity_threshold", 0.75)

        analysis, cost = await _extract_findings(corpus, tier)
        report["cost_usd"] += cost

        if analysis and analysis.get("confidence", 10) < confidence_threshold:
            logger.info(
                f"[AUDITOR] Confiance basse ({analysis.get('confidence')}) sur '{scope}' — "
                f"un seul ré-essai sur tier '{escalated_tier}'."
            )
            escalated_analysis, escalated_cost = await _extract_findings(corpus, escalated_tier)
            report["cost_usd"] += escalated_cost
            if escalated_analysis:
                analysis = escalated_analysis

        report["confidence"] = analysis.get("confidence") if analysis else None

        if analysis:
            active_titles = await get_active_task_titles()
            for finding in analysis.get("findings", []):
                title = (finding.get("title") or "").strip()
                if not title:
                    continue
                if _is_duplicate(title, active_titles, dedup_threshold):
                    report["findings_skipped_duplicate"] += 1
                    continue

                priority = finding.get("priority", 3)
                priority = priority if priority in (1, 2, 3) else 3
                description = finding.get("description", "")
                file_ref = finding.get("file", "")
                if file_ref:
                    description = f"[{file_ref}] {description}"

                await add_task(title, description, priority=priority)
                active_titles.append(title)  # évite les doublons intra-cycle
                report["findings_proposed"] += 1

        # Enregistrer la dépense sous une source dédiée ('auditor'), séparée
        # du budget quotidien de DreamCoder — voir get_scoped_spend_usd().
        if report["cost_usd"] > 0:
            await bg.record_usage(
                provider=f"auditor-{tier}",
                tokens=0,
                cost=report["cost_usd"],
                model=tier,
                sync_source="auditor",
            )

        # Rotation de portée uniquement si le cycle a bien produit une analyse
        # (sinon on retente la même portée au prochain déclenchement).
        if analysis is not None:
            state["scope_index"] = (scope_index + 1) % len(scopes)
        state["last_run_at"] = time.time()
        _save_state(state)

        os.makedirs(_REPORTS_DIR, exist_ok=True)
        report_path = os.path.join(_REPORTS_DIR, f"auditor_report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        auditor_state["last_run_at"] = report["timestamp"]
        auditor_state["last_report"] = report
        auditor_state["total_runs"] += 1

    except Exception as e:
        logger.error(f"[AUDITOR] Erreur cycle : {e}", exc_info=True)
        auditor_state["last_error"] = {"message": str(e), "timestamp": datetime.now().isoformat()}
        report["status"] = "error"
        report["error"] = str(e)
    finally:
        auditor_state["running"] = False

    return report


# ──────────────────────────────────────────────────────────────────
# Boucle cron asyncio (déclenchement par compteur de tâches ou planning)
# ──────────────────────────────────────────────────────────────────

async def auditor_main_loop() -> None:
    """
    Boucle cron asyncio pour l'auditeur. Vérifie toutes les 15 min si l'une
    des conditions de déclenchement (auditor_trigger_mode) est remplie :
    - 'task_count' : N tâches DreamCoder complétées depuis le dernier passage
    - 'schedule' : créneau hebdomadaire fixe
    - 'both' : l'un ou l'autre
    Désactivé par défaut (auditor_enabled=false) — opt-in explicite requis.
    """
    logger.info("[AUDITOR] 🕵️ Démarrage de la boucle de l'auditeur autonome")
    await asyncio.sleep(20)  # laisser le dreamer démarrer en premier

    while True:
        pa_config = _load_persistent_config()
        auditor_state["enabled"] = pa_config.get("auditor_enabled", False)

        if not pa_config.get("auditor_enabled", False):
            await asyncio.sleep(900)
            continue

        state = _load_state()
        should_run = False
        mode = pa_config.get("auditor_trigger_mode", "task_count")

        if mode in ("schedule", "both"):
            now = datetime.now()
            weekday = pa_config.get("auditor_schedule_weekday", 6)  # 0=lundi..6=dimanche
            sched_time = pa_config.get("auditor_schedule_time", "03:00")
            try:
                target_hour, target_minute = map(int, sched_time.split(":"))
                in_window = (
                    now.weekday() == weekday
                    and now.hour == target_hour
                    and target_minute <= now.minute < target_minute + 15
                )
                if in_window and (time.time() - state.get("last_run_at", 0)) > 6 * 3600:
                    should_run = True
            except Exception as e:
                logger.warning(f"[AUDITOR] auditor_schedule_time invalide ('{sched_time}') : {e}")

        if not should_run and mode in ("task_count", "both"):
            try:
                from core.backlog_db import get_task_stats
                stats = await get_task_stats()
                baseline = state.get("completed_count_baseline", 0)
                threshold_n = pa_config.get("auditor_trigger_task_count", 10)
                if stats.get("completed", 0) - baseline >= threshold_n:
                    should_run = True
            except Exception as e:
                logger.warning(f"[AUDITOR] Erreur lecture des stats backlog : {e}")

        if should_run:
            logger.info("[AUDITOR] 🕵️ Déclenchement du cycle d'audit.")
            try:
                await run_auditor_cycle(pa_config)
            except Exception as e:
                logger.error(f"[AUDITOR] Erreur dans le cycle : {e}", exc_info=True)
            finally:
                # Reset du baseline même en cas de no-op, pour éviter un
                # re-déclenchement immédiat en boucle serrée.
                try:
                    from core.backlog_db import get_task_stats
                    stats = await get_task_stats()
                    st = _load_state()
                    st["completed_count_baseline"] = stats.get("completed", 0)
                    _save_state(st)
                except Exception as e:
                    logger.warning(f"[AUDITOR] Erreur mise à jour baseline : {e}")

        await asyncio.sleep(900)  # vérifie toutes les 15 min


def get_auditor_status() -> dict[str, Any]:
    """Retourne l'état complet de l'auditeur (pour une future route API de supervision)."""
    return {**auditor_state}


async def trigger_auditor_manual() -> dict[str, Any]:
    """Déclenche manuellement un cycle de l'auditeur (test/debug)."""
    pa_config = _load_persistent_config()
    return await run_auditor_cycle(pa_config)
