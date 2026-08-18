"""
agents/dreamer_agent.py — Agent autoDream de consolidation mémoire nocturne.

Service cron asyncio (PAS un BaseAgent classique) déclenché par :
1. Un horaire fixe configurable (défaut: 02h00)
2. Une période d'inactivité dépassée (défaut: 3h sans requête utilisateur)

Pipeline d'exécution :
1. Extraction — Requête SQLite sur session_history.db (sessions de la veille)
2. Analyse LLM — Prompt structuré envoyé au modèle dreamer_model (tier léger)
3. Consolidation — Appel des méthodes existantes de memory_db.py
4. Archivage — Compression des anciennes entrées token_usage
5. Rapport — Écriture d'un fichier JSON de synthèse dans checkpoints/

Auteur : Antigravity IDE + Axel
Créé le : 2026-05-30
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("dreamer_agent")

# Répertoire racine du moteur
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Répertoire des rapports du dreamer
_REPORTS_DIR = os.path.join(_ENGINE_ROOT, "checkpoints", "dreamer_reports")


def _dreamcoder_repo_root(pa_config: dict | None) -> str:
    """
    [#T202] Racine du dépôt de TRAVAIL DreamCoder (branches task/*, commits).

    Sur le Deck, la prod est déployée par overlay tar SANS dépôt Git légitime
    (décision Axel : jamais de `git init` dans le dossier de prod) : DreamCoder
    doit travailler dans un CLONE dédié (ex: /home/deck/dev_station/
    moteur_agents_dreamcoder/), configuré via
    `persistent_agents.dreamcoder_repo_path` dans config.json.

    Sans configuration explicite, repli sur _ENGINE_ROOT (poste de dev Windows,
    où le dossier du moteur EST un vrai clone) — comportement historique.
    Les rapports/checkpoints restent TOUJOURS dans _ENGINE_ROOT (runtime moteur).
    """
    configured = (pa_config or {}).get("dreamcoder_repo_path") or ""
    if configured:
        return os.path.abspath(configured)
    return _ENGINE_ROOT


# ──────────────────────────────────────────────────────────────────
# État global du dreamer (exposé via l'API)
# ──────────────────────────────────────────────────────────────────

dreamer_state: dict[str, Any] = {
    "running": False,
    "enabled": False,
    "last_run_at": None,
    "last_run_duration_ms": 0,
    "total_runs": 0,
    "last_report": None,       # Dernier rapport de consolidation
    "last_error": None,
    "next_scheduled": None,    # Prochain déclenchement prévu (ISO)
    "schedule": "02:00",
    "idle_trigger_hours": 3,
}


def _load_persistent_config() -> dict[str, Any]:
    """Charge la section persistent_agents depuis config.json."""
    try:
        from core.llm_gateway import load_config
        config = load_config()
        return config.get("persistent_agents", {})
    except Exception as e:
        logger.warning(f"[DREAMER] Impossible de lire persistent_agents: {e}")
        return {}


# ──────────────────────────────────────────────────────────────────
# Extraction des données de la journée
# ──────────────────────────────────────────────────────────────────

def _extract_daily_sessions() -> dict[str, Any]:
    """
    Extrait les sessions et appels LLM des dernières 24h depuis session_history.db.
    Retourne un résumé structuré pour le prompt du dreamer.
    """
    try:
        from core.session_history import get_sessions, get_token_stats

        # Récupérer les sessions des dernières 24h
        sessions = get_sessions(limit=50)
        cutoff = time.time() - 86400  # 24h

        recent_sessions = [
            s for s in sessions
            if s.get("started_at", 0) >= cutoff
        ]

        # Statistiques de tokens des dernières 24h
        token_stats = get_token_stats(since_hours=24)

        # Extraire les erreurs et corrections
        errors = []
        corrections = []
        for session in recent_sessions:
            if session.get("status") == "error" and session.get("error_message"):
                errors.append({
                    "objective": session.get("objective", "")[:100],
                    "error": session.get("error_message", "")[:200],
                })
            if session.get("result_summary") and "corrigé" in session.get("result_summary", "").lower():
                corrections.append({
                    "objective": session.get("objective", "")[:100],
                    "result": session.get("result_summary", "")[:200],
                })

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "sessions_count": len(recent_sessions),
            "success_count": sum(1 for s in recent_sessions if s.get("status") == "success"),
            "error_count": sum(1 for s in recent_sessions if s.get("status") == "error"),
            "token_stats": token_stats,
            "errors": errors[:10],
            "corrections": corrections[:10],
            "objectives": [s.get("objective", "")[:80] for s in recent_sessions[:20]],
        }
    except Exception as e:
        logger.error(f"[DREAMER] Erreur d'extraction des sessions: {e}")
        return {"error": str(e), "sessions_count": 0}


def _extract_existing_lessons() -> list[dict]:
    """
    Récupère les leçons existantes dans memory.db pour détecter les contradictions.
    """
    try:
        from memory.memory_db import MemoryDB
        db = MemoryDB.get_instance()

        # Récupérer les faits de chaque catégorie
        counts = db.get_all_facts_count()
        all_facts = []
        for category in counts.keys():
            facts = db.get_facts_by_category(category)
            for f in facts[:5]:  # Max 5 par catégorie pour limiter le prompt
                all_facts.append({
                    "category": f.get("category", ""),
                    "title": f.get("title", ""),
                    "content": f.get("content", "")[:150],
                    "score": f.get("relevance_score", 1.0),
                })

        return all_facts
    except Exception as e:
        logger.warning(f"[DREAMER] Erreur de lecture des leçons existantes: {e}")
        return []

async def _consolidate_memory() -> dict[str, int]:
    """
    Exécute la consolidation mémoire en appelant les méthodes existantes.
    Réutilise exactement le même code que engine.py::_consolidate_memory().
    Exécuté de manière non-bloquante pour la boucle d'événements.
    
    Retourne un dict avec les compteurs d'actions effectuées.
    """
    actions = {"decayed": 0, "gc_summarized": 0, "gc_archived": 0, "lessons_added": 0}

    try:
        from memory.memory_db import MemoryDB
        db = MemoryDB.get_instance()

        logger.info("[DREAMER] [Consolidation BDD] 1. Lancement du decay de pertinence...")
        # 1. Decay des scores de pertinence (faits non consultés > 7 jours)
        actions["decayed"] = await asyncio.to_thread(db.decay_relevance, 0.03)
        if actions["decayed"] > 0:
            logger.info(f"[DREAMER] [Consolidation BDD] Decay appliqué sur {actions['decayed']} faits")

        logger.info("[DREAMER] [Consolidation BDD] 2. Lancement du GC du graphe...")
        # 2. GC du graphe (observations > 15, entités temporaires > 30 jours)
        gc_result = await asyncio.to_thread(db.gc_graph_entities, 15, 30)
        actions["gc_summarized"] = gc_result.get("summarized", 0)
        actions["gc_archived"] = gc_result.get("archived", 0)

        if actions["gc_summarized"] > 0 or actions["gc_archived"] > 0:
            logger.info(
                f"[DREAMER] [Consolidation BDD] GC graphe: {actions['gc_summarized']} résumées, "
                f"{actions['gc_archived']} archivées"
            )

    except Exception as e:
        logger.error(f"[DREAMER] Erreur de consolidation mémoire: {e}", exc_info=True)

    return actions


async def _compress_old_episodes() -> dict:
    """
    [V9.2b] Compresse les épisodes anciens (> 7j, < 30j) en Faits mémoire.
    Appelé par le cycle dreamer après la consolidation SQLite.

    Workflow :
    1. Charger les épisodes de la fenêtre 7j-30j
    2. Pour chaque épisode non compressé, créer un Fact condensé dans memory.db
    3. Marquer l'épisode comme compressé (is_compressed=True)
    4. Purger les épisodes > 30j déjà compressés

    Returns:
        Dict {"compressed": N, "purged": M, "kept": K}
    """
    stats = {"compressed": 0, "purged": 0, "kept": 0, "error": None}
    try:
        from memory.episodes import EPISODE_TTL_DAYS, EpisodeStore
        from memory.memory_db import MemoryDB

        episode_store = EpisodeStore()
        now = datetime.now()
        cutoff_compress = now - timedelta(days=7)      # Épisodes > 7j = candidats
        cutoff_purge    = now - timedelta(days=EPISODE_TTL_DAYS)  # > 30j = à purger

        # Charger TOUS les épisodes (y compris les expirés pour la purge)
        all_episodes = await asyncio.to_thread(
            episode_store._load_all_episodes, True  # include_expired=True
        )

        db = MemoryDB.get_instance()
        to_compress = []
        to_purge    = []

        for ep in all_episodes:
            created_str = ep.get("created_at") or ep.get("timestamp", "")
            if not created_str:
                continue
            try:
                created_dt = datetime.fromisoformat(created_str[:19])
            except (ValueError, TypeError):
                continue

            is_compressed = ep.get("is_compressed", False)
            age_days = (now - created_dt).days

            if age_days > EPISODE_TTL_DAYS and is_compressed:
                to_purge.append(ep)
            elif age_days > 7 and not is_compressed:
                to_compress.append(ep)

        # ── Compression (max 20 par cycle) ──
        for ep in to_compress[:20]:
            try:
                objectif = ep.get("objective", "")[:60]
                date_str = ep.get("date", created_str[:10])
                result_summary = ep.get("result_summary", "")[:200]
                tokens = ep.get("total_tokens", 0)
                errors = ep.get("errors", [])

                await asyncio.to_thread(
                    db.record_learned_lesson,
                    "moteur",                        # category
                    f"Épisode {date_str}: {objectif}",  # title
                    (                                # content
                        f"Résultat: {result_summary}. "
                        f"Tokens: {tokens}. "
                        f"Erreurs: {', '.join(errors[:3]) if errors else 'aucune'}."
                    ),
                    "dreamer_agent",                 # source_file
                    "episode_compresse,dreamer,auto",# tags
                    "minor",                         # severity
                )

                # Rerouter le fichier JSON pour marquer is_compressed=True
                ep["is_compressed"] = True
                ep_path = os.path.join(
                    episode_store.episodes_dir,
                    _find_episode_file(episode_store.episodes_dir, ep.get("session_id", ""))
                )
                if ep_path and os.path.exists(ep_path):
                    import json as _json
                    with open(ep_path, "w", encoding="utf-8") as f:
                        _json.dump(ep, f, indent=2, ensure_ascii=False)

                stats["compressed"] += 1
            except Exception as ce:
                logger.warning(f"[DREAMER] Erreur compression épisode : {ce}")

        # ── Purge des épisodes expirés ──
        purge_stats = await asyncio.to_thread(episode_store.purge_old_episodes, False)
        stats["purged"] = purge_stats.get("deleted", 0)
        stats["kept"]   = purge_stats.get("kept", 0)

        logger.info(
            f"[DREAMER] [Episodes] {stats['compressed']} compressés → memory.db, "
            f"{stats['purged']} purgés, {stats['kept']} conservés"
        )

    except Exception as e:
        stats["error"] = str(e)
        logger.error(f"[DREAMER] Erreur compression épisodes : {e}", exc_info=True)

    return stats


def _find_episode_file(episodes_dir: str, session_id: str) -> str:
    """
    [V9.2b] Retrouve le fichier JSON d'un épisode par session_id (partiel).
    Retourne le chemin complet ou '' si non trouvé.
    """
    if not session_id or not os.path.exists(episodes_dir):
        return ""
    short_id = session_id[:16].replace(":", "_").replace("/", "_")
    for fname in os.listdir(episodes_dir):
        if fname.endswith(".json") and short_id in fname:
            return os.path.join(episodes_dir, fname)
    return ""


async def _analyze_with_llm(daily_data: dict, existing_lessons: list[dict],
                             pa_config: dict) -> dict | None:
    """
    Envoie un prompt structuré au LLM pour analyser la journée.
    
    Le LLM retourne :
    - Nouvelles leçons apprises
    - Faits obsolètes à marquer
    - Contradictions détectées
    """
    # Si aucune session à analyser, pas besoin du LLM
    if daily_data.get("sessions_count", 0) == 0:
        logger.info("[DREAMER] [Analyse LLM] Aucune session à analyser — LLM non appelé")
        return None

    try:
        from core.llm_gateway import LLMGateway, load_config

        gateway = LLMGateway()
        config = load_config()
        dreamer_tier = pa_config.get("dreamer_model", "leger")

        # [#T340] Prompt externalisé (prompts/agents/dreamer.md) ; repli ci-dessous.
        from core.prompt_loader import load_agent_prompt
        system_prompt = load_agent_prompt("dreamer", """Tu es un agent de consolidation mémoire pour un système multi-agents domotique.
Tu analyses les sessions de la journée écoulée et extrais les leçons apprises.

Tu devez retourner un JSON valide avec cette structure exacte :
{
    "new_lessons": [
        {"category": "esphome|moteur|gcp|hmi|infra", "title": "...", "content": "..."}
    ],
    "obsolete_facts": [
        {"title": "...", "reason": "..."}
    ],
    "contradictions": [
        {"existing_title": "...", "new_info": "...", "resolution": "..."}
    ],
    "summary": "Résumé de la consolidation en 2-3 phrases."
}

Catégories valides : esphome, moteur, gcp, hmi, infra.
Réponds UNIQUEMENT avec le JSON, sans commentaire.""")

        user_prompt = f"""## Sessions du {daily_data.get('date', 'N/A')}

**Statistiques :**
- Sessions : {daily_data.get('sessions_count', 0)} ({daily_data.get('success_count', 0)} succès, {daily_data.get('error_count', 0)} erreurs)
- Tokens consommés : {daily_data.get('token_stats', {}).get('total_tokens', 0):,}

**Objectifs traités :**
{chr(10).join('- ' + o for o in daily_data.get('objectives', [])[:15])}

**Erreurs rencontrées :**
{json.dumps(daily_data.get('errors', []), indent=2, ensure_ascii=False)}

**Corrections effectuées :**
{json.dumps(daily_data.get('corrections', []), indent=2, ensure_ascii=False)}

## Leçons existantes dans la base (pour détecter les contradictions)
{json.dumps(existing_lessons[:20], indent=2, ensure_ascii=False)}
"""

        # Résoudre le provider pour le tier du dreamer
        model_name, provider = gateway.get_provider_for_tier(dreamer_tier, config)
        logger.info(f"[DREAMER] [Analyse LLM] Modèle résolu pour consolidation : {model_name} (provider: {type(provider).__name__})")

        logger.info("[DREAMER] [Analyse LLM] Envoi de la requête au LLM (generate_async)...")
        # [#T308] La boucle autoDream tourne 24h/24 sur le Deck et consomme sans
        # être un agent : elle appelle le provider directement, donc l'enveloppe de
        # `BaseAgent.invoke()` ne s'applique pas et sa dépense restait anonyme.
        from core.agent_trace import agent_courant
        with agent_courant("dreamer"):
            response = await provider.generate_async(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=2000,
                temperature=0.3,
            )
        logger.info("[DREAMER] [Analyse LLM] Réponse reçue du LLM.")

        # Parser la réponse JSON
        response_text = response.strip()
        # Nettoyer les balises markdown ```json ... ``` si présentes
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        analysis = json.loads(response_text)
        return analysis

    except json.JSONDecodeError as je:
        logger.warning(f"[DREAMER] [Analyse LLM] Réponse LLM non-JSON: {je}. Réponse brute: {response[:200]}...")
        return None
    except Exception as e:
        logger.error(f"[DREAMER] [Analyse LLM] Erreur d'analyse LLM: {e}", exc_info=True)
        return None


def _apply_analysis(analysis: dict) -> dict[str, int]:
    """
    Applique les résultats de l'analyse LLM dans memory.db.
    Fonction synchrone exécutée dans un thread pour SQLite.
    """
    applied = {"lessons_added": 0, "facts_marked_obsolete": 0}

    try:
        from memory.memory_db import MemoryDB
        db = MemoryDB.get_instance()

        # 1. Ajouter les nouvelles leçons
        for lesson in analysis.get("new_lessons", []):
            try:
                db.record_learned_lesson(
                    category=lesson.get("category", "moteur"),
                    title=lesson.get("title", "Sans titre"),
                    content=lesson.get("content", ""),
                    source_file="dreamer_agent",
                    tags="dreamer,auto,nocturne",
                    severity="minor",
                )
                applied["lessons_added"] += 1
            except Exception as le:
                logger.warning(f"[DREAMER] Erreur d'ajout de leçon: {le}")

        # 2. Marquer les faits obsolètes (réduire leur score de pertinence)
        for obsolete in analysis.get("obsolete_facts", []):
            title = obsolete.get("title", "")
            if title:
                try:
                    # Chercher le fait par titre et réduire son score
                    facts = db.search_facts(title, limit=1)
                    if facts:
                        fact_id = facts[0].get("id")
                        if fact_id:
                            conn = db._get_conn()
                            try:
                                conn.execute(
                                    "UPDATE facts SET relevance_score = 0.1, "
                                    "updated_at = ? WHERE id = ?",
                                    (time.time(), fact_id)
                                )
                                conn.commit()
                                applied["facts_marked_obsolete"] += 1
                            finally:
                                conn.close()
                except Exception as oe:
                    logger.warning(f"[DREAMER] Erreur de marquage obsolète: {oe}")

        if applied["lessons_added"] > 0:
            logger.info(f"[DREAMER] [Appliquer Analyse] ✅ {applied['lessons_added']} leçon(s) ajoutée(s)")
        if applied["facts_marked_obsolete"] > 0:
            logger.info(f"[DREAMER] [Appliquer Analyse] 🗑️ {applied['facts_marked_obsolete']} fait(s) marqué(s) obsolètes")

    except Exception as e:
        logger.error(f"[DREAMER] Erreur d'application de l'analyse: {e}", exc_info=True)

    return applied


def _save_report(report: dict) -> str:
    """Sauvegarde le rapport de consolidation dans un fichier JSON."""
    os.makedirs(_REPORTS_DIR, exist_ok=True)

    filename = f"dreamer_report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    filepath = os.path.join(_REPORTS_DIR, filename)

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"[DREAMER] Rapport sauvegardé: {filename}")
        return filepath
    except Exception as e:
        logger.error(f"[DREAMER] Erreur de sauvegarde du rapport: {e}", exc_info=True)
        return ""


# ──────────────────────────────────────────────────────────────────
# DreamCoder — traitement d'une tâche + boucle de drainage à durée réglable
# ──────────────────────────────────────────────────────────────────

def _pousser_branche_tache(repo_root: str, branch_name: str,
                           timeout_s: float = 90.0) -> dict[str, Any]:
    """
    Pousse la branche 'task/*' vers `origin` — best effort, JAMAIS bloquant.

    Le commit local EST la valeur produite par la tâche. Un push impossible
    (clé de déploiement en lecture seule sur le Deck, remote volontairement
    neutralisé sur la prod, réseau coupé) ne doit pas retransformer une tâche
    réussie en échec : on journalise la raison et la branche reste récoltable
    par `scripts/recolte_dreamcoder.py` depuis un poste qui a le droit d'écrire.

    Deux garde-fous, tous deux nés d'un blocage réel :
      - `GIT_TERMINAL_PROMPT=0` — le clone dédié du Deck a un `origin` en HTTPS
        sans identifiants ; sans ça, git attendrait une saisie qui ne viendra
        jamais dans un service systemd.
      - timeout dur — le push est dans la fenêtre `dreamcoder_max_cycle_minutes`,
        qui est le budget de TOUTES les tâches du cycle : un push qui pend
        mangerait le temps des suivantes.

    :return: {"pushed": bool, "raison": str, "detail": str}
    """
    from tools.git_safety import _run_git

    # Le dépôt de PROD du Deck neutralise volontairement son push
    # (`no-push://le-deck-est-une-cible-de-deploiement`, garde-fou du 07/08 :
    # 23 clés en clair y étaient suivies). Détecté ici pour rendre une raison
    # lisible plutôt qu'une erreur de transport opaque.
    code, push_url, _ = _run_git(["remote", "get-url", "--push", "origin"], cwd=repo_root)
    if code != 0 or not push_url:
        return {"pushed": False, "raison": "remote_absent", "detail": push_url or ""}
    if push_url.startswith("no-push://"):
        return {"pushed": False, "raison": "remote_no_push", "detail": push_url}

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(
            ["git", "push", "--set-upstream", "origin", branch_name],
            cwd=repo_root, capture_output=True, text=True,
            timeout=timeout_s, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"pushed": False, "raison": "timeout", "detail": f"{timeout_s}s"}
    except Exception as e:  # transport cassé, git absent, permissions…
        return {"pushed": False, "raison": "erreur", "detail": str(e)[:300]}

    if res.returncode == 0:
        return {"pushed": True, "raison": "ok", "detail": ""}
    return {
        "pushed": False,
        "raison": "refuse",
        "detail": ((res.stderr or "") + (res.stdout or "")).strip()[:300],
    }


async def _process_one_dreamcoder_task(task: dict, pa_config: dict, provider: str,
                                        orig_branch: str, bg: Any) -> dict[str, Any]:
    """
    Traite UNE tâche du backlog : prépare une branche Git dédiée, exécute le
    pipeline complet dessus, commit si succès ou rollback+retry sinon, met à
    jour le statut de la tâche. Logique reprise à l'identique de l'ancien
    bloc inline de run_dreamer_cycle (étape 3.0) — extraite ici pour être
    appelable en boucle par _run_dreamcoder_drain_loop().

    :return: dict {"task_id", "title", "status", "branch", "cost_usd", "tokens_used", "error"}
    """
    import filelock

    from core.backlog_db import update_task_status

    task_id = task["id"]
    result: dict[str, Any] = {
        "task_id": task_id, "title": task["title"], "status": None,
        "branch": None, "cost_usd": 0.0, "tokens_used": 0, "error": None,
        "push": {"pushed": False, "raison": "non_tente", "detail": ""},
    }
    logger.info(f"[DREAMER] [DreamCoder] Tâche trouvée : ID {task_id} - '{task['title']}'")
    logger.info(f"[DREAMER] [DreamCoder] Provider alloué : {provider}")

    # Configurer temporairement les modèles selon le provider alloué.
    # 07/07 : rôles séparés — le provider de la cascade (gratuit/abonnement en
    # priorité) fait l'EXÉCUTION brute (dégrossir), tandis que le PLANNER et le
    # REVIEWER sont pilotés par un "gouverneur" fixe (DeepSeek par défaut,
    # escaladé vers zai-glm-4.7 — GLM/Z.ai hébergé gratuitement via la clé
    # Cerebras — sur une tâche déjà retentée au moins une fois, décision Axel
    # 07/07 : "gratuit pour dégrossir, DeepSeek/Z.ai pour gouverner"). Le
    # gouverneur ne passe PAS par la cascade BudgetGuard (appel direct par nom
    # de modèle) donc il est protégé séparément par le plafond quotidien
    # combiné (total_daily_budget_usd) : au-delà, on dégrade sur le modèle
    # gratuit de l'exécuteur plutôt que de continuer à consommer le budget
    # payant du gouverneur.
    from core.llm_gateway import load_config
    config = load_config()
    temp_config = config.copy()

    executor_model_map = {
        "ollama": "ollama_local",
        "lmstudio": "local",
        "gemini-cli-abo": "gemini-3.5-flash-high-cli",
        "claude-cli-abo": "claude-sonnet-4.6-thinking-cli",
        "cerebras-free": "gpt-oss-120b",
        "cohere-free": "command-r-plus-08-2024",
        "mistral-free": "open-mistral-nemo",
        "gemini-free": "gemini-3.5-flash-free",
        "deepseek-free": "deepseek-chat",
        "anthropic-claude-haiku": "claude-haiku-4-5",
    }
    executor_model = executor_model_map.get(provider, "deepseek-chat")
    temp_config["executor_model"] = executor_model

    dreamcoder_spent_today = await bg.get_scoped_spend_usd("dreamcoder", 86400.0)
    total_cap = bg.config.get("total_daily_budget_usd", 1.0)
    governor_within_budget = dreamcoder_spent_today < total_cap

    if not governor_within_budget:
        logger.warning(
            f"[DREAMER] [DreamCoder] Plafond quotidien combiné atteint "
            f"(${dreamcoder_spent_today:.4f}/{total_cap} USD) — gouverneur dégradé "
            f"sur le modèle de l'exécuteur ('{executor_model}') pour cette tâche."
        )
        governor_model = executor_model
    elif task.get("retries", 0) > 0:
        # Escalade qualité : la tâche a déjà échoué au moins une fois, on monte
        # en gamme sur un modèle GLM (gratuit via Cerebras) plutôt que de
        # retenter avec le même gouverneur.
        governor_model = "zai-glm-4.7"
    else:
        governor_model = "deepseek-chat"

    temp_config["planner_model"] = governor_model
    temp_config["reviewer_model"] = governor_model

    # Préparer la branche éphémère de l'agent
    from tools.git_safety import (
        _run_git,
        git_generate_semantic_commit_msg,
        git_prepare_agent_branch,
        git_rollback_checkpoint,
    )

    # [#T202] Toutes les opérations Git de la tâche ciblent le dépôt de travail
    # DreamCoder (clone dédié si configuré, _ENGINE_ROOT sinon).
    repo_root = _dreamcoder_repo_root(pa_config)

    branch_name = await asyncio.to_thread(
        git_prepare_agent_branch,
        session_id=str(task_id),
        repo_path=repo_root,
        prefix="task/"
    )

    if branch_name.startswith("Erreur"):
        logger.error(f"[DREAMER] [DreamCoder] Impossible de préparer la branche Git : {branch_name}")
        # `retries` était laissé à 0 sur ce chemin : la tâche n'atteignait
        # jamais 'abandoned' après 3 échecs, et comme 'failed' n'est pas repris
        # par get_next_task(), elle restait bloquée sans que rien ne le dise.
        # Constaté sur le Deck : deux tâches 'failed' / retries=0 depuis juillet.
        next_retries = task.get("retries", 0) + 1
        statut = "abandoned" if next_retries >= 3 else "failed"
        await update_task_status(task_id, statut, error_message=branch_name,
                                 retries=next_retries)
        result["status"] = statut
        result["error"] = branch_name
        return result

    logger.info(f"[DREAMER] [DreamCoder] Branche Git créée : {branch_name}")
    await update_task_status(task_id, 'running', git_branch=branch_name)
    result["branch"] = branch_name

    from core.app_state import get_app_state as _get_app_state
    from core.fenetres_execution import duree_enveloppe_dreamcoder_s, duree_tache_dreamcoder_s
    from services.pipeline_service import run_full_pipeline

    # [#T202] Propager la racine de travail au pipeline (hooks Git/YAML/doc de
    # l'Engine) et l'imposer dans le prompt : read_file/write_file résolvent les
    # chemins relatifs contre le CWD du process serveur, pas contre le clone.
    temp_config["repo_root"] = repo_root
    work_prompt = (
        f"[DÉPÔT DE TRAVAIL : {repo_root}]\n"
        f"Règle stricte : utilise EXCLUSIVEMENT des chemins ABSOLUS sous cette "
        f"racine pour read_file/write_file/run_terminal_command.\n\n"
        f"{task['description']}"
    )

    router = _get_app_state().get_shared_router()
    initial_payload, starting_agent = await router.analyze_request(
        task["description"], session_id=str(task_id)
    )

    async def sse_callback(event_type, data, engine_inst):
        pass

    session_id = f"task_{task_id}"

    try:
        logger.info("[DREAMER] [DreamCoder] Démarrage du pipeline de tâche...")
        # Les DEUX bornes viennent de core/fenetres_execution.py : sans
        # `timeout_seconds`, le pipeline reprenait son défaut de 120 s hérité du
        # chemin interactif, et l'enveloppe de 600 s ci-dessous n'était que
        # décorative — le vrai plafond d'une tâche de nuit était de 2 minutes.
        pipeline_result = await asyncio.wait_for(
            run_full_pipeline(
                user_prompt=work_prompt,
                session_id=session_id,
                initial_payload=initial_payload,
                starting_agent=starting_agent,
                on_event_callback=sse_callback,
                config=temp_config,
                timeout_seconds=duree_tache_dreamcoder_s(),
            ),
            timeout=duree_enveloppe_dreamcoder_s(),
        )

        logger.info(f"[DREAMER] [DreamCoder] Pipeline terminé avec statut: {pipeline_result.get('status')}")

        # Collecter les jetons utilisés
        from core.token_tracker import get_session_total_tokens
        tokens_used = get_session_total_tokens(session_id)

        # Déterminer le coût
        from core.runtime_db import get_connection
        cost = 0.0
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM token_usage WHERE session_id = ?",
                    (session_id,)
                ).fetchone()
                if row:
                    cost = row[0]
        except Exception as _ce:
            logger.warning(f"[DREAMER] [DreamCoder] Impossible de lire le coût réel : {_ce}")

        # Enregistrer la consommation
        window_type = "hourly" if provider == "gemini-free" else "daily"
        await bg.record_usage(
            provider=provider,
            tokens=tokens_used,
            cost=cost,
            model=temp_config["planner_model"],
            window_type=window_type
        )
        result["tokens_used"] = tokens_used
        result["cost_usd"] = cost

        if pipeline_result.get("status") == "completed" or pipeline_result.get("status") == "success":
            logger.info("[DREAMER] [DreamCoder] Tâche réussie. Enregistrement du commit...")
            await asyncio.to_thread(_run_git, ["add", "-A"], repo_root)
            commit_msg = await asyncio.to_thread(git_generate_semantic_commit_msg, repo_root, session_id)
            await asyncio.to_thread(_run_git, ["commit", "-m", commit_msg], repo_root)

            # Générer le diff
            _, diff_out, _ = await asyncio.to_thread(_run_git, ["diff", "HEAD~1..HEAD"], repo_root)

            # Sortir le travail du Deck : la branche est poussée vers origin dès
            # qu'elle porte un commit. Best effort — un refus (deploy key en
            # lecture seule) laisse la tâche 'completed' et la branche récoltable.
            push_info = await asyncio.to_thread(_pousser_branche_tache, repo_root, branch_name)
            result["push"] = push_info
            if push_info["pushed"]:
                logger.info(f"[DREAMER] [DreamCoder] Branche poussée sur origin : {branch_name}")
            else:
                logger.warning(
                    f"[DREAMER] [DreamCoder] Branche NON poussée ({push_info['raison']}) : "
                    f"{branch_name} — {push_info['detail'][:160]}"
                )

            # Créer le fichier de rapport de résultats
            results_dir = os.path.join(_ENGINE_ROOT, "checkpoints", "dreamcoder_results")
            os.makedirs(results_dir, exist_ok=True)
            result_file = os.path.join(results_dir, f"task_{task_id}.json")

            # Écriture sous verrou
            lock = filelock.FileLock(result_file + ".lock")
            with lock:
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "task_id": task_id,
                        "title": task["title"],
                        "branch": branch_name,
                        "diff": diff_out,
                        "summary": pipeline_result.get("response", ""),
                        "timestamp": time.time(),
                        "tokens_used": tokens_used,
                        "cost_usd": cost,
                        "push": push_info
                    }, f, indent=2, ensure_ascii=False)

            # Mettre à jour la base
            await update_task_status(
                task_id,
                'completed',
                result_summary=pipeline_result.get("response", "Succès sans résumé"),
                tokens_used=tokens_used
            )
            result["status"] = "success"
        else:
            err_msg = pipeline_result.get("error") or "Le pipeline s'est terminé sur un échec."
            raise RuntimeError(err_msg)

    except Exception as pipeline_err:
        logger.error(f"[DREAMER] [DreamCoder] Échec de l'exécution : {pipeline_err}")

        # Cooldown du provider d'exécution (voir core/budget_guard.py::mark_provider_failed) —
        # évite qu'un provider cassé (ex: CLI Gemini/Claude non trustée) soit
        # re-sélectionné sur la tâche suivante du même cycle de drainage.
        from core.budget_guard import mark_provider_failed
        mark_provider_failed(provider)

        # Rollback git
        await asyncio.to_thread(git_rollback_checkpoint, repo_root)

        # Supprimer la branche
        await asyncio.to_thread(_run_git, ["checkout", orig_branch], repo_root)
        await asyncio.to_thread(_run_git, ["branch", "-D", branch_name], repo_root)

        # Incrémenter les tentatives
        next_retries = task.get("retries", 0) + 1
        status = "failed"
        if next_retries >= 3:
            status = "abandoned"
            logger.warning(f"[DREAMER] [DreamCoder] Tâche {task_id} abandonnée après 3 échecs.")

        await update_task_status(
            task_id,
            status,
            error_message=str(pipeline_err),
            retries=next_retries
        )
        result["status"] = status
        result["error"] = str(pipeline_err)

    finally:
        # Retourner sur la branche d'origine
        await asyncio.to_thread(_run_git, ["checkout", orig_branch], repo_root)

    return result


async def _run_dreamcoder_drain_loop(pa_config: dict) -> dict[str, Any]:
    """
    Boucle bornée en durée (dreamcoder_max_cycle_minutes) qui draine le
    backlog tant qu'il reste du temps, du budget, et des tâches 'pending'.
    Remplace l'ancien traitement d'une tâche unique par cycle. Ne contourne
    JAMAIS la porte d'approbation Git humaine (PUT /api/backlog/tasks/{id}) :
    chaque tâche réussie reste sur sa branche 'task/*' en attente de revue.
    """
    from core.backlog_db import get_next_task, update_task_status
    from core.budget_guard import BudgetGuard
    from tools.git_safety import _run_git

    max_minutes = pa_config.get("dreamcoder_max_cycle_minutes", 12)
    deadline = time.time() + max_minutes * 60
    bg = BudgetGuard()

    results: dict[str, Any] = {
        "processed": 0, "success": 0, "failed": 0, "abandoned": 0, "paused": 0,
        "total_cost_usd": 0.0, "total_tokens": 0,
        "branches_awaiting_review": [], "branches_pushed": [],
        "tasks": [], "stopped_reason": None,
    }

    # [#T202] Dépôt de travail du cycle (clone dédié si configuré).
    repo_root = _dreamcoder_repo_root(pa_config)

    # [#T202] Rafraîchir le clone AVANT le cycle — UNIQUEMENT quand un clone
    # dédié est explicitement configuré : jamais de pull automatique sur le
    # dépôt de dev (_ENGINE_ROOT) sous les pieds d'Axel. --ff-only : un clone
    # divergent (branches task/* locales non mergées) n'est jamais écrasé,
    # le pull échoue proprement et le cycle continue sur l'état local.
    if (pa_config or {}).get("dreamcoder_repo_path"):
        pull_code, pull_out, pull_err = await asyncio.to_thread(
            _run_git, ["pull", "--ff-only"], repo_root)
        if pull_code == 0:
            logger.info(f"[DREAMER] [DreamCoder] Clone rafraîchi ({repo_root}) : {pull_out.strip()[:120]}")
        else:
            logger.warning(
                f"[DREAMER] [DreamCoder] pull --ff-only impossible sur {repo_root} "
                f"({(pull_err or pull_out).strip()[:160]}) — cycle sur l'état local."
            )

    code, orig_branch, _ = await asyncio.to_thread(
        _run_git, ["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    orig_branch = orig_branch.strip() if (code == 0 and orig_branch) else "master"
    logger.info(f"[DREAMER] [DreamCoder] Branche d'origine : {orig_branch}")

    while True:
        if time.time() >= deadline:
            results["stopped_reason"] = "max_cycle_minutes_reached"
            break

        task = await get_next_task()
        if not task:
            results["stopped_reason"] = "backlog_empty"
            break

        # Budget vérifié À CHAQUE tâche (pas une fois en tête de cycle)
        provider = await bg.get_available_provider()
        if not provider:
            logger.warning("[DREAMER] [DreamCoder] Aucun provider disponible (quotas épuisés).")
            await update_task_status(task["id"], 'paused', error_message='quota_exhausted')
            results["paused"] += 1
            results["stopped_reason"] = "budget_exhausted"
            break

        task_result = await _process_one_dreamcoder_task(task, pa_config, provider, orig_branch, bg)
        results["processed"] += 1
        results["tasks"].append(task_result)
        results["total_cost_usd"] += task_result.get("cost_usd", 0.0)
        results["total_tokens"] += task_result.get("tokens_used", 0)
        status = task_result.get("status")
        if status == "success":
            results["success"] += 1
            if task_result.get("branch"):
                results["branches_awaiting_review"].append(task_result["branch"])
                # Distinguer « travail fait » de « travail sorti du Deck » : une
                # branche non poussée demande une récolte manuelle, et le rapport
                # de cycle est le seul endroit où ça se voit.
                if (task_result.get("push") or {}).get("pushed"):
                    results["branches_pushed"].append(task_result["branch"])
        elif status in ("failed", "abandoned", "paused"):
            results[status] += 1
        # Les tâches 'failed'/'abandoned' sortent naturellement de 'pending' :
        # get_next_task() ne les re-sélectionnera pas au tour suivant, donc
        # "continuer au-delà d'un abandon" ne demande aucun code spécial.

    return results


def _save_dreamcoder_cycle_report(results: dict) -> str:
    """
    Rapport agrégé de fin de cycle (tâches traitées/réussies/échouées/
    abandonnées, coût total, branches en attente de revue). Vient EN PLUS
    des fichiers task_{id}.json déjà écrits par tâche
    (checkpoints/dreamcoder_results/), ne les remplace pas.
    """
    results_dir = os.path.join(_ENGINE_ROOT, "checkpoints", "dreamcoder_results")
    os.makedirs(results_dir, exist_ok=True)
    filepath = os.path.join(results_dir, f"cycle_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        return filepath
    except Exception as e:
        logger.error(f"[DREAMER] Erreur sauvegarde rapport de cycle DreamCoder : {e}")
        return ""


# ──────────────────────────────────────────────────────────────────
# Pipeline principal du dreamer
# ──────────────────────────────────────────────────────────────────

async def run_dreamer_cycle(pa_config: dict) -> dict[str, Any]:
    """
    Exécute un cycle complet de consolidation mémoire.
    
    Pipeline :
    1. Extraction des sessions de la veille (Thread)
    2. Consolidation automatique (decay, GC) (Thread)
    3. Analyse LLM (Thread)
    4. Application des résultats LLM (Thread)
    5. Génération du rapport (Thread)
    """
    cycle_start = time.time()

    logger.info("[DREAMER] 🌙 Démarrage du cycle de consolidation mémoire")
    dreamer_state["running"] = True

    report = {
        "timestamp": datetime.now().isoformat(),
        "pipeline": {},
        "actions": {},
        "duration_ms": 0,
    }

    try:
        anomalies = []
        # 1. Extraction (dans un thread)
        logger.info("[DREAMER] [Etape 1/5] Extraction des sessions de la journée (dans un thread séparé)...")
        daily_data = await asyncio.to_thread(_extract_daily_sessions)
        report["pipeline"]["extraction"] = {
            "sessions_count": daily_data.get("sessions_count", 0),
            "error_count": daily_data.get("error_count", 0),
        }
        logger.info(f"[DREAMER] [Etape 1/5] Sessions extraites : {daily_data.get('sessions_count', 0)}")

        # 2. Consolidation automatique (decay, GC)
        logger.info("[DREAMER] [Etape 2/5] Lancement de la consolidation SQLite (decay/GC)...")
        consolidation = await _consolidate_memory()
        report["actions"]["consolidation"] = consolidation
        logger.info("[DREAMER] [Etape 2/5] Consolidation automatique terminée.")

        # 2.5. [V9.2b] Compression des épisodes anciens en Faits mémoire
        logger.info("[DREAMER] [Etape 2.5] Compression des épisodes anciens (7j-30j)...")
        ep_stats = await _compress_old_episodes()
        report["actions"]["episodes_compression"] = ep_stats
        logger.info(
            f"[DREAMER] [Etape 2.5] Épisodes : {ep_stats['compressed']} compressés, "
            f"{ep_stats['purged']} purgés."
        )

        # 2.6. Détection d'anomalies domotiques (SQLite HA)
        logger.info("[DREAMER] [Etape 2.6] Analyse des anomalies domotiques (SQLite HA)...")
        try:
            from core.ha_anomaly_detector import HAAnomalyDetector
            detector = HAAnomalyDetector()
            anomalies = await detector.analyze_all(days=7)
            suggestions = await detector.format_suggestions(anomalies)
            await detector.close()
            report["actions"]["ha_anomalies"] = {
                "count": len(anomalies),
                "high": sum(1 for a in anomalies if a.get("severity") == "high"),
                "medium": sum(1 for a in anomalies if a.get("severity") == "medium"),
                "low": sum(1 for a in anomalies if a.get("severity") == "low"),
                "report": suggestions[:500],  # Limiter pour le rapport JSON
            }
            logger.info(
                f"[DREAMER] [Etape 2.6] {len(anomalies)} anomalie(s) détectée(s) "
                f"({report['actions']['ha_anomalies']['high']} critiques)"
            )
        except Exception as _ae:
            logger.warning(f"[DREAMER] [Etape 2.6] Analyse anomalies échouée (non bloquant) : {_ae}")
            report["actions"]["ha_anomalies"] = {"error": str(_ae)}

        # 2.7. Réentraînement MLRouter si suffisamment de sessions
        logger.info("[DREAMER] [Etape 2.7] Vérification réentraînement MLRouter...")
        try:
            from core.ml_router import get_ml_router
            _ml_r = get_ml_router()
            ml_result = await _ml_r.train(min_samples=50)
            if "error" in ml_result:
                logger.info(f"[DREAMER] [Etape 2.7] MLRouter : {ml_result['error']}")
            else:
                logger.info(
                    f"[DREAMER] [Etape 2.7] MLRouter réentraîné : "
                    f"accuracy={ml_result.get('accuracy', 0):.3f}, "
                    f"samples={ml_result.get('samples', 0)}"
                )
            report["actions"]["ml_router_training"] = ml_result
        except Exception as _mle:
            logger.warning(f"[DREAMER] [Etape 2.7] MLRouter train échoué (non bloquant) : {_mle}")
            report["actions"]["ml_router_training"] = {"error": str(_mle)}

        # 2.8. Migration ChromaDB (mémoire vectorielle persistante)
        logger.info("[DREAMER] [Etape 2.8] Migration vers ChromaDB (s'il est disponible)...")
        try:
            import os

            from memory.chroma_memory import get_chroma_memory
            _episodes_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "memory", "episodes"
            )
            _memory_db = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "memory.db"
            )
            _chroma = get_chroma_memory()
            ep_res   = await _chroma.migrate_compressed_episodes(_episodes_dir)
            fact_res = await _chroma.migrate_facts_from_sqlite(_memory_db)
            report["actions"]["chroma_migration"] = {
                "episodes": ep_res,
                "facts":    fact_res,
                "stats":    _chroma.get_stats(),
            }
            logger.info(
                f"[DREAMER] [Etape 2.8] ChromaDB : "
                f"{ep_res.get('migrated', 0)} épisodes + "
                f"{fact_res.get('migrated', 0)} faits migrés"
            )
        except ImportError:
            logger.info("[DREAMER] [Etape 2.8] chromadb non installé — migration ignorée (non bloquant)")
            report["actions"]["chroma_migration"] = {"skipped": True, "reason": "chromadb_absent"}
        except Exception as _che:
            # #T252 : ne plus masquer une vraie erreur de migration sous un faux
            # « ChromaDB non disponible » — on nomme la cause réelle.
            logger.warning(f"[DREAMER] [Etape 2.8] Erreur de migration ChromaDB (non bloquant) : {_che}")
            report["actions"]["chroma_migration"] = {"skipped": True, "error": str(_che)}

        # 2.9. Push du rapport d'anomalies vers le Tab5 M5Stack
        logger.info("[DREAMER] [Etape 2.9] Push du rapport d'anomalies vers le Tab5 M5Stack...")
        try:
            from core.tab5_pusher import get_tab5_pusher
            pusher = get_tab5_pusher()
            ha_anom_data = report["actions"].get("ha_anomalies", {})
            if "error" not in ha_anom_data and anomalies:
                push_ok = await pusher.push_anomaly_report(anomalies)
                logger.info(f"[DREAMER] [Etape 2.9] Push anomalies Tab5: {'succès' if push_ok else 'échec'}")
                report["actions"]["ha_anomalies"]["push_tab5"] = push_ok
            else:
                logger.info("[DREAMER] [Etape 2.9] Pas d'anomalies à pousser ou erreur précédente.")
        except Exception as _pe:
            logger.warning(f"[DREAMER] [Etape 2.9] Push anomalies Tab5 échoué (non bloquant) : {_pe}")

        # 3.0. DreamCoder
        logger.info("[DREAMER] [Etape 3.0] Vérification des tâches du backlog (DreamCoder)...")
        report["actions"]["dreamcoder"] = {"status": "skipped", "task_processed": False}
        try:
            if pa_config.get("dreamcoder_enabled", True):
                # Vérifier si l'utilisateur est inactif
                from core.app_state import get_app_state
                app_state = get_app_state()

                user_active = False
                if app_state.execution_state.get("status") == "running":
                    logger.info("[DREAMER] [DreamCoder] L'utilisateur est actif (moteur running). Etape sautée.")
                    user_active = True
                    report["actions"]["dreamcoder"]["reason"] = "user_active_running"

                if not user_active:
                    min_idle = pa_config.get("dreamcoder_min_idle_hours", 1.0)
                    last_user_activity = 0
                    try:
                        from core.runtime_db import get_connection
                        with get_connection() as conn:
                            row = conn.execute(
                                "SELECT MAX(started_at) FROM sessions WHERE session_id NOT LIKE 'task_%'"
                            ).fetchone()
                            if row and row[0]:
                                last_user_activity = row[0]
                    except Exception as _ie:
                        logger.warning(f"[DREAMER] [DreamCoder] Erreur lors de la lecture des sessions : {_ie}")

                    idle_hours = (time.time() - last_user_activity) / 3600
                    if idle_hours < min_idle:
                        logger.info(f"[DREAMER] [DreamCoder] L'utilisateur a été actif récemment ({idle_hours:.2f}h < {min_idle}h). Etape sautée.")
                        user_active = True
                        report["actions"]["dreamcoder"]["reason"] = "user_active_recent"
                        report["actions"]["dreamcoder"]["idle_hours"] = round(idle_hours, 2)

                if not user_active:
                    drain_results = await _run_dreamcoder_drain_loop(pa_config)
                    report["actions"]["dreamcoder"] = {
                        "status": "drained",
                        "tasks_processed": drain_results["processed"],
                        "tasks_succeeded": drain_results["success"],
                        "tasks_failed": drain_results["failed"],
                        "tasks_abandoned": drain_results["abandoned"],
                        "tasks_paused": drain_results["paused"],
                        "total_cost_usd": round(drain_results["total_cost_usd"], 6),
                        "total_tokens": drain_results["total_tokens"],
                        "branches_awaiting_review": drain_results["branches_awaiting_review"],
                        "branches_pushed": drain_results["branches_pushed"],
                        "stopped_reason": drain_results["stopped_reason"],
                    }
                    if drain_results["processed"] > 0:
                        await asyncio.to_thread(_save_dreamcoder_cycle_report, drain_results)
                    elif drain_results["stopped_reason"] == "backlog_empty":
                        logger.info("[DREAMER] [DreamCoder] Aucune tâche éligible dans le backlog.")
            else:
                logger.info("[DREAMER] [DreamCoder] DreamCoder désactivé par configuration.")
                report["actions"]["dreamcoder"]["reason"] = "disabled"

        except Exception as dreamcoder_err:
            logger.error(f"[DREAMER] [DreamCoder] Erreur générale DreamCoder : {dreamcoder_err}", exc_info=True)
            report["actions"]["dreamcoder"]["error"] = str(dreamcoder_err)
            report["actions"]["dreamcoder"]["status"] = "error"

        # 3. Analyse LLM
        logger.info("[DREAMER] [Etape 3/5] Extraction des leçons existantes (thread)...")
        existing_lessons = await asyncio.to_thread(_extract_existing_lessons)
        logger.info("[DREAMER] [Etape 3/5] Appel de l'analyse LLM...")
        analysis = await _analyze_with_llm(daily_data, existing_lessons, pa_config)

        if analysis:
            report["pipeline"]["llm_analysis"] = {
                "new_lessons": len(analysis.get("new_lessons", [])),
                "obsolete_facts": len(analysis.get("obsolete_facts", [])),
                "contradictions": len(analysis.get("contradictions", [])),
                "summary": analysis.get("summary", ""),
            }

            # 4. Appliquer les résultats de l'analyse
            logger.info("[DREAMER] [Etape 4/5] Application des conclusions LLM dans memory.db...")
            applied = await asyncio.to_thread(_apply_analysis, analysis)
            report["actions"]["applied"] = applied
            logger.info("[DREAMER] [Etape 4/5] Conclusions LLM appliquées.")
        else:
            logger.info("[DREAMER] [Etape 4/5] Pas d'analyse LLM nécessaire (ou sautée).")
            report["pipeline"]["llm_analysis"] = {"skipped": True}

        # 5. Sauvegarder le rapport
        logger.info("[DREAMER] [Etape 5/5] Enregistrement du rapport...")
        report["duration_ms"] = round((time.time() - cycle_start) * 1000, 1)
        report_path = await asyncio.to_thread(_save_report, report)

        # Mettre à jour l'état global
        dreamer_state["last_run_at"] = report["timestamp"]
        dreamer_state["last_run_duration_ms"] = report["duration_ms"]
        dreamer_state["total_runs"] += 1
        dreamer_state["last_report"] = {
            "path": report_path,
            "summary": analysis.get("summary", "Consolidation automatique uniquement") if analysis else "Aucune session à analyser",
            "actions": report["actions"],
        }

        logger.info(
            f"[DREAMER] 🌙 Cycle de consolidation complet terminé en {report['duration_ms']}ms !"
        )

    except Exception as e:
        logger.error(f"[DREAMER] ❌ Erreur critique dans le cycle: {e}", exc_info=True)
        dreamer_state["last_error"] = {
            "message": str(e),
            "timestamp": datetime.now().isoformat(),
        }
    finally:
        dreamer_state["running"] = False

    return report


# ──────────────────────────────────────────────────────────────────
# Boucle cron asyncio (déclenchement horaire ou par inactivité)
# ──────────────────────────────────────────────────────────────────

async def dreamer_main_loop():
    """
    Boucle cron asyncio pour le dreamer.
    
    Double déclenchement :
    1. Horaire fixe (dreamer_schedule, défaut 02:00)
    2. Période d'inactivité (dreamer_idle_trigger_hours, défaut 3h)
    
    Vérifie toutes les 5 minutes si l'une des conditions est remplie.
    """
    logger.info("[DREAMER] 🌙 Démarrage de la boucle autoDream")

    # Attendre que le système soit initialisé
    await asyncio.sleep(15)

    last_activity_check = time.time()

    while True:
        # Relire la config à chaque itération
        pa_config = _load_persistent_config()

        dreamer_state["enabled"] = pa_config.get("dreamer_enabled", True)
        dreamer_state["schedule"] = pa_config.get("dreamer_schedule", "02:00")
        dreamer_state["idle_trigger_hours"] = pa_config.get("dreamer_idle_trigger_hours", 3)

        if not pa_config.get("dreamer_enabled", True):
            await asyncio.sleep(300)  # Vérifier toutes les 5 min si réactivé
            continue

        should_run = False
        trigger_reason = ""

        # Vérification 1 : Horaire fixe
        schedule_time = pa_config.get("dreamer_schedule", "02:00")
        try:
            now = datetime.now()
            target_hour, target_minute = map(int, schedule_time.split(":"))

            # Calculer le prochain déclenchement
            target_dt = now.replace(hour=target_hour, minute=target_minute, second=0)
            if target_dt < now:
                target_dt += timedelta(days=1)
            dreamer_state["next_scheduled"] = target_dt.isoformat()

            # Déclencher si on est dans la fenêtre de 5 minutes autour de l'heure cible
            if now.hour == target_hour and target_minute <= now.minute < target_minute + 5:
                # Vérifier qu'on n'a pas déjà fait un run aujourd'hui
                last_run = dreamer_state.get("last_run_at", "")
                if not last_run or last_run[:10] != now.strftime("%Y-%m-%d"):
                    should_run = True
                    trigger_reason = f"Horaire fixe ({schedule_time})"
        except Exception:
            pass

        # Vérification 2 : Inactivité prolongée
        if not should_run:
            idle_hours = pa_config.get("dreamer_idle_trigger_hours", 3)
            try:
                from core.session_history import get_sessions
                recent = get_sessions(limit=1)
                if recent:
                    last_session_ts = recent[0].get("started_at", 0)
                    idle_since = time.time() - last_session_ts
                    idle_hours_actual = idle_since / 3600

                    if idle_hours_actual >= idle_hours:
                        # Vérifier qu'on n'a pas déjà consolidé dans les dernières idle_hours
                        last_run = dreamer_state.get("last_run_at", "")
                        if last_run:
                            try:
                                last_run_dt = datetime.fromisoformat(last_run)
                                hours_since_run = (datetime.now() - last_run_dt).total_seconds() / 3600
                                if hours_since_run >= idle_hours:
                                    should_run = True
                                    trigger_reason = f"Inactivité ({idle_hours_actual:.1f}h > {idle_hours}h)"
                            except Exception:
                                should_run = True
                                trigger_reason = f"Inactivité ({idle_hours_actual:.1f}h)"
                        else:
                            should_run = True
                            trigger_reason = f"Inactivité ({idle_hours_actual:.1f}h)"
            except Exception:
                pass

        # Exécuter le cycle si une condition est remplie
        if should_run:
            logger.info(f"[DREAMER] 🌙 Déclenchement: {trigger_reason}")
            try:
                await run_dreamer_cycle(pa_config)
            except Exception as e:
                logger.error(f"[DREAMER] ❌ Erreur dans le cycle: {e}")

        # Vérifier toutes les 5 minutes
        await asyncio.sleep(300)


# ──────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────

def get_dreamer_status() -> dict[str, Any]:
    """Retourne l'état complet du dreamer pour l'API."""
    return {**dreamer_state}


async def trigger_dreamer_manual() -> dict[str, Any]:
    """Déclenche manuellement un cycle du dreamer (depuis l'IHM)."""
    pa_config = _load_persistent_config()
    return await run_dreamer_cycle(pa_config)
