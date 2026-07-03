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

import os
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger("dreamer_agent")

# Répertoire racine du moteur
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Répertoire des rapports du dreamer
_REPORTS_DIR = os.path.join(_ENGINE_ROOT, "checkpoints", "dreamer_reports")


# ──────────────────────────────────────────────────────────────────
# État global du dreamer (exposé via l'API)
# ──────────────────────────────────────────────────────────────────

dreamer_state: Dict[str, Any] = {
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


def _load_persistent_config() -> Dict[str, Any]:
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

def _extract_daily_sessions() -> Dict[str, Any]:
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


def _extract_existing_lessons() -> List[Dict]:
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

async def _consolidate_memory() -> Dict[str, int]:
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
        from memory.episodes import EpisodeStore, EPISODE_TTL_DAYS
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


async def _analyze_with_llm(daily_data: Dict, existing_lessons: List[Dict],
                             pa_config: Dict) -> Optional[Dict]:
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
        
        # Construire le prompt d'analyse
        system_prompt = """Tu es un agent de consolidation mémoire pour un système multi-agents domotique.
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
Réponds UNIQUEMENT avec le JSON, sans commentaire."""

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


def _apply_analysis(analysis: Dict) -> Dict[str, int]:
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


def _save_report(report: Dict) -> str:
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
# Pipeline principal du dreamer
# ──────────────────────────────────────────────────────────────────

async def run_dreamer_cycle(pa_config: Dict) -> Dict[str, Any]:
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
            from memory.chroma_memory import get_chroma_memory
            import os
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
        except Exception as _che:
            logger.info(f"[DREAMER] [Etape 2.8] ChromaDB non disponible (non bloquant) : {_che}")
            report["actions"]["chroma_migration"] = {"skipped": True}

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
                    from core.backlog_db import get_next_task, update_task_status
                    from core.budget_guard import BudgetGuard
                    import filelock
                    
                    task = await get_next_task()
                    if task:
                        task_id = task["id"]
                        logger.info(f"[DREAMER] [DreamCoder] Tâche trouvée : ID {task_id} - '{task['title']}'")
                        report["actions"]["dreamcoder"]["task_processed"] = True
                        report["actions"]["dreamcoder"]["task_id"] = task_id
                        report["actions"]["dreamcoder"]["task_title"] = task["title"]
                        
                        # Vérifier le budget/provider disponible
                        bg = BudgetGuard()
                        provider = await bg.get_available_provider()
                        if not provider:
                            logger.warning(f"[DREAMER] [DreamCoder] Aucun provider disponible (quotas épuisés).")
                            await update_task_status(task_id, 'paused', error_message='quota_exhausted')
                            report["actions"]["dreamcoder"]["status"] = "paused"
                            report["actions"]["dreamcoder"]["error"] = "quota_exhausted"
                        else:
                            logger.info(f"[DREAMER] [DreamCoder] Provider alloué : {provider}")
                            report["actions"]["dreamcoder"]["allocated_provider"] = provider
                            
                            # Configurer temporairement les modèles selon le provider alloué
                            from core.llm_gateway import load_config
                            config = load_config()
                            temp_config = config.copy()
                            
                            if provider == "ollama":
                                # Runtime local préféré (cf. budget_guard) — clé gateway ollama_local
                                temp_config["planner_model"] = "ollama_local"
                                temp_config["executor_model"] = "ollama_local"
                                temp_config["reviewer_model"] = "ollama_local"
                            elif provider == "lmstudio":
                                temp_config["planner_model"] = "local"
                                temp_config["executor_model"] = "local"
                                temp_config["reviewer_model"] = "local"
                            elif provider == "gemini-free":
                                temp_config["planner_model"] = "gemini-3.5-flash-free"
                                temp_config["executor_model"] = "gemini-3.5-flash-free"
                                temp_config["reviewer_model"] = "gemini-3.5-flash-free"
                            elif provider == "deepseek-free":
                                temp_config["planner_model"] = "deepseek-chat"
                                temp_config["executor_model"] = "deepseek-chat"
                                temp_config["reviewer_model"] = "deepseek-chat"
                            elif provider == "anthropic-claude-haiku":
                                temp_config["planner_model"] = "claude-haiku-4-5"
                                temp_config["executor_model"] = "claude-haiku-4-5"
                                temp_config["reviewer_model"] = "claude-haiku-4-5"
                                
                            # Préparer la branche éphémère de l'agent
                            from tools.git_safety import git_prepare_agent_branch, git_rollback_checkpoint, git_generate_semantic_commit_msg, _run_git
                            
                            # Récupérer la branche d'origine
                            code_branch, orig_branch, _ = await asyncio.to_thread(_run_git, ["rev-parse", "--abbrev-ref", "HEAD"], _ENGINE_ROOT)
                            orig_branch = orig_branch.strip() if (code_branch == 0 and orig_branch) else "master"
                            
                            logger.info(f"[DREAMER] [DreamCoder] Branche d'origine : {orig_branch}")
                            
                            branch_name = await asyncio.to_thread(
                                git_prepare_agent_branch,
                                session_id=str(task_id),
                                repo_path=_ENGINE_ROOT,
                                prefix="task/"
                            )
                            
                            if branch_name.startswith("Erreur"):
                                logger.error(f"[DREAMER] [DreamCoder] Impossible de préparer la branche Git : {branch_name}")
                                await update_task_status(task_id, 'failed', error_message=branch_name)
                                report["actions"]["dreamcoder"]["status"] = "failed"
                                report["actions"]["dreamcoder"]["error"] = branch_name
                            else:
                                logger.info(f"[DREAMER] [DreamCoder] Branche Git créée : {branch_name}")
                                await update_task_status(task_id, 'running', git_branch=branch_name)
                                
                                # Lancer le pipeline complet
                                from services.pipeline_service import run_full_pipeline
                                from core.app_state import get_app_state as _get_app_state

                                # [P1-2.1] Router canonique partagé (gateway/RAG/config câblés).
                                router = _get_app_state().get_shared_router()
                                initial_payload, starting_agent = await router.analyze_request(task["description"])
                                
                                async def sse_callback(event_type, data, engine_inst):
                                    pass
                                    
                                session_id = f"task_{task_id}"
                                
                                try:
                                    logger.info(f"[DREAMER] [DreamCoder] Démarrage du pipeline de tâche...")
                                    result = await asyncio.wait_for(
                                        run_full_pipeline(
                                            user_prompt=task["description"],
                                            session_id=session_id,
                                            initial_payload=initial_payload,
                                            starting_agent=starting_agent,
                                            on_event_callback=sse_callback,
                                            config=temp_config
                                        ),
                                        timeout=600.0 # 10 minutes timeout
                                    )
                                    
                                    logger.info(f"[DREAMER] [DreamCoder] Pipeline terminé avec statut: {result.get('status')}")
                                    
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
                                    
                                    if result.get("status") == "completed" or result.get("status") == "success":
                                        logger.info(f"[DREAMER] [DreamCoder] Tâche réussie. Enregistrement du commit...")
                                        await asyncio.to_thread(_run_git, ["add", "-A"], _ENGINE_ROOT)
                                        commit_msg = await asyncio.to_thread(git_generate_semantic_commit_msg, _ENGINE_ROOT, session_id)
                                        await asyncio.to_thread(_run_git, ["commit", "-m", commit_msg], _ENGINE_ROOT)
                                        
                                        # Générer le diff
                                        _, diff_out, _ = await asyncio.to_thread(_run_git, ["diff", "HEAD~1..HEAD"], _ENGINE_ROOT)
                                        
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
                                                    "summary": result.get("response", ""),
                                                    "timestamp": time.time(),
                                                    "tokens_used": tokens_used,
                                                    "cost_usd": cost
                                                }, f, indent=2, ensure_ascii=False)
                                                
                                        # Mettre à jour la base
                                        await update_task_status(
                                            task_id,
                                            'completed',
                                            result_summary=result.get("response", "Succès sans résumé"),
                                            tokens_used=tokens_used
                                        )
                                        report["actions"]["dreamcoder"]["status"] = "success"
                                    else:
                                        err_msg = result.get("error") or "Le pipeline s'est terminé sur un échec."
                                        raise RuntimeError(err_msg)
                                        
                                except Exception as pipeline_err:
                                    logger.error(f"[DREAMER] [DreamCoder] Échec de l'exécution : {pipeline_err}")
                                    
                                    # Rollback git
                                    await asyncio.to_thread(git_rollback_checkpoint, _ENGINE_ROOT)
                                    
                                    # Supprimer la branche
                                    await asyncio.to_thread(_run_git, ["checkout", orig_branch], _ENGINE_ROOT)
                                    await asyncio.to_thread(_run_git, ["branch", "-D", branch_name], _ENGINE_ROOT)
                                    
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
                                    report["actions"]["dreamcoder"]["status"] = status
                                    report["actions"]["dreamcoder"]["error"] = str(pipeline_err)
                                    
                                finally:
                                    # Retourner sur la branche d'origine
                                    await asyncio.to_thread(_run_git, ["checkout", orig_branch], _ENGINE_ROOT)
                    else:
                        logger.info("[DREAMER] [DreamCoder] Aucune tâche éligible dans le backlog.")
                        report["actions"]["dreamcoder"]["reason"] = "no_eligible_task"
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

def get_dreamer_status() -> Dict[str, Any]:
    """Retourne l'état complet du dreamer pour l'API."""
    return {**dreamer_state}


async def trigger_dreamer_manual() -> Dict[str, Any]:
    """Déclenche manuellement un cycle du dreamer (depuis l'IHM)."""
    pa_config = _load_persistent_config()
    return await run_dreamer_cycle(pa_config)
