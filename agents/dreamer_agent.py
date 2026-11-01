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
import base64
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("dreamer_agent")

# Répertoire racine du moteur
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Répertoire des rapports du dreamer
_REPORTS_DIR = os.path.join(_ENGINE_ROOT, "checkpoints", "dreamer_reports")

# ── Gouverneur (planner + reviewer) d'une tâche autonome ────────────────────
# 23/08 Axel (D-21) : le GROS du travail = AJEAN. Le CERVEAU (planner +
# revue) dépend de l'hôte : PC = Gemini CLI abo `gemini-3.7-flash-medium`
# (`agy --model`) ; Deck = DeepSeek V4 Pro (CLI absents).
_GOUVERNEUR_ANTIGRAVITY = "gemini-3.5-flash-medium-cli"
_GOUVERNEUR_SANS_CLI = "deepseek-v4-pro"
# Alias historique : tests / logs qui lisent encore `_GOUVERNEUR_DEFAUT`.
# La valeur live passe par `_gouverneur_par_defaut()` (CLI ou pas).
_GOUVERNEUR_DEFAUT = _GOUVERNEUR_ANTIGRAVITY
_GOUVERNEURS_ESCALADE = ("glm-5", "deepseek-reasoner")
_GOUVERNEURS_ESCALADE_PC = ("glm-5", "deepseek-v4-pro", "deepseek-reasoner")
# Revue : même cerveau que le planner, par hôte.
_REVIEWER_DEFAUT = "deepseek-v4-pro"
_REVIEWER_REPLI = ("deepseek-reasoner", "deepseek-chat")
_SLUGS_GEMINI_CLI = (
    "gemini-3.5-flash-high-cli",
    "gemini-3.5-flash-medium-cli",
    "gemini-3.7-flash-high-cli",
    "gemini-3.7-flash-medium-cli",
    "gemini-cli",
    "antigravity",
)


def _gouverneur_par_defaut() -> str:
    """Gemini 3.7 Flash medium (agy) sur PC ; DeepSeek V4 Pro si les CLI sont coupés."""
    from core.budget_guard import _cli_providers_disabled
    if _cli_providers_disabled():
        return _GOUVERNEUR_SANS_CLI
    return _GOUVERNEUR_ANTIGRAVITY


def _gouverneurs_escalade() -> tuple[str, ...]:
    """Après un échec : pas le défaut (déjà tenté). PC peut monter vers V4 Pro."""
    from core.budget_guard import _cli_providers_disabled
    if _cli_providers_disabled():
        return _GOUVERNEURS_ESCALADE
    return _GOUVERNEURS_ESCALADE_PC


def _reviewer_par_defaut() -> str:
    """Cerveau de revue = même famille que le planner, par hôte."""
    if _gouverneur_par_defaut() == _GOUVERNEUR_ANTIGRAVITY:
        return _premier_gouverneur_vivant(
            (_GOUVERNEUR_ANTIGRAVITY, _REVIEWER_DEFAUT) + _REVIEWER_REPLI,
            _GOUVERNEUR_ANTIGRAVITY,
        )
    return _premier_gouverneur_vivant(
        (_REVIEWER_DEFAUT,) + _REVIEWER_REPLI, _REVIEWER_DEFAUT)


def _agy_disponible() -> bool:
    try:
        from core.llm.providers.gemini import chemin_agy
        return chemin_agy() is not None
    except Exception:
        return False


def _modele_eteint(model_id: str) -> bool:
    """
    Vrai si la sonde de santé a elle-même éteint ce modèle.

    Lecture tolérante : si la table de santé est illisible (base absente en
    test, migration en cours), on répond « pas éteint ». Un doute sur la santé
    ne doit jamais empêcher une tâche de partir — c'est la cascade qui tranche
    ensuite.

    Les slugs Gemini CLI éteints par l'ancien IPC (`antigravity-ide.cmd` +
    fichier d'échange) sont retentés dès que `agy` (vrai CLI abo) est là :
    sinon le gouverneur reste coincé sur Qwen à jamais.
    """
    if model_id in _SLUGS_GEMINI_CLI and _agy_disponible():
        return False
    try:
        from core.model_health_probe import lire_sante
        return bool(lire_sante(model_id).get("desactive_par_sonde"))
    except Exception as err:  # pragma: no cover — dépend de l'état de la base
        logger.debug(f"[DREAMER] [DreamCoder] Santé de '{model_id}' illisible : {err}")
        return False


def _premier_gouverneur_vivant(candidats: tuple[str, ...], repli: str) -> str:
    """
    Premier candidat que la sonde n'a pas éteint ; `repli` si aucun ne répond.

    Le repli lui-même est retourné tel quel : c'est le gouverneur par défaut,
    et s'il est mort aussi, l'appelant a déjà un chemin de dégradation vers le
    modèle de l'exécuteur. Les slugs `-cli` sont sautés quand l'hôte a coupé
    les providers CLI (Deck).
    """
    from core.budget_guard import _cli_providers_disabled
    skip_cli = _cli_providers_disabled()
    for modele in candidats:
        # Même convention que `core.llm_gateway._est_modele_cli` (suffixe `-cli`).
        # Recopiée ici : les tests de fenêtre stubbent `core.llm_gateway` et un
        # import ferait `ImportError: cannot import name '_est_modele_cli'`.
        if skip_cli and modele.lower().endswith("-cli"):
            continue
        if not _modele_eteint(modele):
            return modele
    return repli


# [#T420] Les chemins de slot sont MACHINE-LOCAUX (`H:\…` sur le PC,
# `/home/deck/…` sur le Deck) alors que `config.json` est suivi par git. Jusqu'ici
# chaque machine gardait donc un diff local sur un fichier suivi — le Deck est en
# ` M config.json` depuis toujours. Un `git pull`, un `reset --hard` ou un stash de
# `git_safety` efface ce diff, et le garde-fou #T419 rebascule alors en refus
# silencieux (`pas_de_clone_dedie`) : le cycle s'arrête sans que rien ne l'annonce.
# Mesuré le 20/08 en réconciliant le master du PC. `.env` est gitignoré, machine par
# machine : c'est là que ce réglage a sa place. Séparateur = `os.pathsep` (`;` sous
# Windows, `:` sous Linux), pour ne pas buter sur le `:` des lettres de lecteur.
CLE_ENV_SLOTS = "MOTEUR_DREAMCODER_REPO_PATHS"

# Un dépôt hors moteur (Tab5, écosystème) n'a qu'un index Git : deux tâches
# concurrentes s'y écraseraient. Verrou par projet, créé dans la boucle asyncio.
_verrous_projet: dict[str, asyncio.Lock] = {}


def _verrou_projet_async(projet_id: str) -> asyncio.Lock:
    lock = _verrous_projet.get(projet_id)
    if lock is None:
        lock = asyncio.Lock()
        _verrous_projet[projet_id] = lock
    return lock


def _slots_configures(pa_config: dict | None) -> list[str]:
    """
    Chemins de slot DÉCLARÉS, dans l'ordre de priorité, sans repli implicite.

    `MOTEUR_DREAMCODER_REPO_PATHS` (env) prime sur `dreamcoder_repo_paths`
    (config.json), qui prime sur `dreamcoder_repo_path` (clé historique).
    Liste vide = rien de déclaré : c'est ce que teste le garde-fou #T419 pour
    savoir s'il travaillerait dans le dépôt du moteur lui-même.
    """
    brut = os.environ.get(CLE_ENV_SLOTS) or ""
    if brut.strip():
        return [c.strip() for c in brut.split(os.pathsep) if c.strip()]

    chemins = (pa_config or {}).get("dreamcoder_repo_paths") or []
    if isinstance(chemins, str):
        chemins = [chemins]
    chemins = [c for c in chemins if c]
    if chemins:
        return chemins

    unique = (pa_config or {}).get("dreamcoder_repo_path") or ""
    return [unique] if unique else []


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

    [#T420] La déclaration peut venir de `.env` (voir `_slots_configures`) : le
    premier slot déclaré fait racine.
    """
    declares = _slots_configures(pa_config)
    if declares:
        return os.path.abspath(declares[0])
    return _ENGINE_ROOT


def _slots_dreamcoder(pa_config: dict | None) -> list[str]:
    """
    [#T387] Dépôts de travail du cycle — un par tâche exécutée en parallèle.

    Le parallélisme impose une isolation par CLONE, pas par branche : deux
    tâches concurrentes dans un même dépôt s'écrasent au `git checkout`, et le
    `git add -A` de la première ramasserait le travail en cours de la seconde.
    Les worktrees Git, qui seraient l'outil naturel, sont refusés en amont par
    `git_prepare_agent_branch` (#T318) — un worktree lié n'accepte ni stash ni
    checkout d'isolation. Donc : un clone complet par slot (19 Mo pièce).

    `MOTEUR_DREAMCODER_REPO_PATHS` (env) prime, puis `dreamcoder_repo_paths`
    (liste), puis le `dreamcoder_repo_path` unique — donc un seul slot, le
    comportement d'avant le parallélisme reste le défaut.
    """
    chemins = [os.path.abspath(c) for c in _slots_configures(pa_config)]
    # Un même clone deux fois dans la liste = deux tâches dans le même dépôt,
    # c'est-à-dire exactement ce que l'isolation cherche à éviter.
    uniques: list[str] = []
    for c in chemins:
        if c not in uniques:
            uniques.append(c)
    return uniques or [_dreamcoder_repo_root(pa_config)]


def _projet_et_depot_de_tache(task: dict, pa_config: dict | None, slot_moteur: str | None) -> tuple[str, str, str]:
    """
    (project_id, repo_root, orig_branch) pour UNE tâche.

    Le slot parallèle n'isole que les tâches *moteur* : deux clones, deux
    index Git. Une tâche Tab5 ne doit PAS écrire dans ces clones — elle vise
    `00ProjetTab` / `Axellum/M5-Tab5-ESPHome-LVGL`. La branche de base est
    relue sur CE dépôt (Tab5 est sur `main`, pas `master`).
    """
    from core.projects import resoudre_projet_pour_tache
    from tools.git_safety import _run_git

    projet = resoudre_projet_pour_tache(task)
    if projet.id == "moteur":
        repo_root = slot_moteur or _dreamcoder_repo_root(pa_config)
    else:
        cible = projet.chemin_travail_agent()
        if not cible:
            raise FileNotFoundError(
                f"Projet '{projet.id}' ({projet.label}) : aucun dossier Git local. "
                f"Attendu remote {projet.git_remote}. Pose "
                f"MOTEUR_PROJECT_{projet.id.upper()}_PATH ou projects.json."
            )
        repo_root = cible

    code, orig, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if code != 0 or not orig or orig == "HEAD":
        orig = projet.default_branch
    return projet.id, repo_root, orig


# [#T419] Clé d'échappement : autorise explicitement le drainage dans le dépôt
# d'où tourne le moteur. Absente = refus. Ce n'est pas une préférence de style,
# c'est le seul endroit où l'on déclare accepter qu'un agent commite, change de
# branche et pousse dans un dépôt qu'un humain est peut-être en train d'éditer.
CLE_DEPOT_COURANT = "dreamcoder_autoriser_depot_courant"


def _slots_dans_le_depot_courant(slots: list[str]) -> list[str]:
    """
    Slots qui pointent sur le dépôt d'où tourne le moteur lui-même.

    Mesuré les 18–19/08 : sur le PC, `dreamcoder_repo_path` était vide, donc
    `_dreamcoder_repo_root` retombait sur `_ENGINE_ROOT` — et le cycle autonome
    a travaillé **directement dans le dépôt de développement d'Axel**, pendant
    qu'il y codait. Il y a créé des branches, commité, et poussé 12 branches sur
    `origin` que la récolte (câblée sur le Deck) ne pouvait pas voir : 34,2 M de
    tokens jamais relus, dont deux branches destructrices et une backdoor
    d'authentification.

    Le danger ne commence même pas au commit : `_preparer_slot` fait un
    `git checkout master` sur le slot avant toute chose. Sur un dépôt de
    travail, ça arrache la branche courante sous les pieds de celui qui code.
    """
    racine = os.path.abspath(_ENGINE_ROOT)
    return [s for s in slots if os.path.abspath(s) == racine]


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

def _python_invalide_dans_index(repo_root: str) -> list[str]:
    """
    [#T394] Fichiers `.py` de l'index qui ne parsent pas.

    Contrôle volontairement minimal : on ne juge NI le style, NI la logique, NI
    la pertinence — seulement qu'un fichier Python reste du Python. C'est le
    plancher en dessous duquel un livrable n'a aucune valeur, quel qu'ait été
    l'avis du Reviewer.

    Lit l'index (`--cached`) et non le disque : ce sont exactement les fichiers
    qui partiraient dans le commit.

    :return: liste de « chemin: message », vide si tout parse.
    """
    import ast

    from tools.git_safety import _run_git

    code, sortie, _ = _run_git(["diff", "--cached", "--name-only"], cwd=repo_root)
    if code != 0 or not sortie:
        return []

    problemes: list[str] = []
    for chemin in sortie.splitlines():
        chemin = chemin.strip()
        if not chemin.endswith(".py"):
            continue
        absolu = os.path.join(repo_root, chemin)
        if not os.path.exists(absolu):   # fichier supprimé par la tâche
            continue
        try:
            with open(absolu, encoding="utf-8", errors="replace") as f:
                ast.parse(f.read())
        except SyntaxError as e:
            problemes.append(f"{chemin}: ligne {e.lineno} — {e.msg}")
        except Exception as e:  # lecture impossible, encodage exotique…
            problemes.append(f"{chemin}: illisible ({type(e).__name__})")
    return problemes


# [#T405] « completed » doit vouloir dire « les tests livres passent ».
#
# Mesure le 18/08 a 21h45, cycle autonome sur la production : trois taches
# declarees `completed` (#44, #48, #49 -- 1,17 M de tokens a elles trois) ont
# livre des branches dont **les tests ecrits par l agent echouent** : 2 echecs
# sur task/44, 3 sur task/48, et un TimeoutError sur task/49. Le contrat de
# delegation exigeait pourtant noir sur blanc « execute pytest et n annonce le
# succes que s il est vert ».
#
# Les garde-fous existants ferment d autres portes -- #T388 (branche vide),
# #T392 (branche identique a sa base), #T394 (code qui ne parse pas) -- mais
# aucun ne rejoue les tests. Le Reviewer, lui, est un LLM : il donne un avis,
# pas une preuve. Trois branches inutilisables sont ainsi parties en revue avec
# l etiquette « reussi ».
_TIMEOUT_TESTS_LIVRES_S = 420.0


def _fichiers_tests_modifies(repo_root: str, orig_branch: str) -> list[str]:
    """Fichiers de test ajoutes ou modifies par la branche de la tache."""
    # `_run_git` n'est pas importe au niveau module (il vient de tools.git_safety,
    # importe au plus pres de l'usage dans le reste de ce fichier).
    from tools.git_safety import _run_git
    _code, sortie, _err = _run_git(
        ["diff", "--name-only", f"{orig_branch}..HEAD"], repo_root)
    fichiers = []
    for ligne in (sortie or "").splitlines():
        chemin = ligne.strip().replace("\\", "/")
        if not chemin.endswith(".py"):
            continue
        nom = chemin.rsplit("/", 1)[-1]
        if chemin.startswith("tests/") or nom.startswith("test_"):
            fichiers.append(chemin)
    return fichiers


def _rejouer_tests_livres(repo_root: str, fichiers: list[str]) -> tuple[bool, str]:
    """
    Rejoue les tests livres. Rend (vert, compte-rendu).

    Une absence de pytest n est PAS un echec de la tache : on ne punit pas un
    agent pour un outil manquant sur la machine. En revanche un depassement de
    delai en est un -- un test qui pend est un livrable inutilisable.
    """
    import subprocess
    import sys

    if not fichiers:
        return True, "aucun fichier de test dans le diff"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", *fichiers],
            cwd=repo_root, capture_output=True, text=True,
            timeout=_TIMEOUT_TESTS_LIVRES_S,
        )
    except FileNotFoundError:
        return True, "pytest introuvable — porte ignoree"
    except subprocess.TimeoutExpired:
        return False, (
            f"les tests livres ne rendent pas la main apres "
            f"{_TIMEOUT_TESTS_LIVRES_S:.0f} s ({', '.join(fichiers[:3])})")
    if proc.returncode == 5:
        return True, "aucun test collecte"
    if proc.returncode == 0:
        return True, f"{len(fichiers)} fichier(s) de test au vert"
    sortie = ((proc.stdout or "") + (proc.stderr or "")).strip()
    sortie = sortie.replace(chr(10), " | ")
    return False, sortie[-600:] or f"pytest a rendu le code {proc.returncode}"


# [#T432] Un test qui ouvre `/home/deck/...` est vert sur le Deck et rouge
# en CI. Mesuré le 21/08 sur la PR #445 : `INDEX_PATH = "/home/deck/dev_station/
# moteur_agents_dc3/docs/wake_word_file_index.json"` → 5 FileNotFoundError
# GitHub, puis la CI de relance est morte en 5 s (quota). On refuse le
# motif AVANT le rejeu, sur la constante, pas sur un `open()` d'exemple.
_RE_CONSTANTE_CHEMIN_MACHINE = re.compile(
    r"""(?:PATH|RACINE|INDEX|CHEMIN|ROOT|DIR)\s*=\s*['"](?:/home/deck/|[A-Za-z]:\\)""",
    re.IGNORECASE,
)


def _tests_figent_un_chemin_machine(repo_root: str, fichiers: list[str]) -> list[str]:
    """Fichiers de test qui assignent un chemin d'hôte en constante."""
    fautifs: list[str] = []
    for rel in fichiers:
        chemin = os.path.join(repo_root, *rel.split("/"))
        try:
            with open(chemin, encoding="utf-8") as f:
                texte = f.read()
        except OSError:
            continue
        if _RE_CONSTANTE_CHEMIN_MACHINE.search(texte):
            fautifs.append(rel)
    return fautifs


# [#T405] Le meme controle, pour le frontend.
#
# Mesure le 18/08 : la tache #38 (TaskKanbanBoard, 430 990 tokens) est declaree
# `completed` et livre 384 lignes de TSX qui **ne compilent pas** -- trois
# erreurs TS6133 « declared but its value is never read », `noUnusedLocals`
# etant actif. C est le TROISIEME echec d affilee sur ce meme motif : la
# version de la veille avait deja ete rejetee pour un import inutilise.
#
# `pytest` ne voit rien du TypeScript. Sans cette porte, le frontend echappe
# entierement a la verification.
# Paquets de code de production : aucun fichier de test n'y a sa place. Verifie
# le 19/08 sur ce depot -- les seuls `test_*.py` hors de `tests/` vivent dans
# `scripts/` et `tools/`, jamais dans ceux-ci.
_PAQUETS_PRODUCTION = ("core/", "agents/", "api/", "services/")


def _livrables_hors_convention(repo_root: str, orig_branch: str) -> list[str]:
    """
    Fichiers AJOUTES par la branche qui violent l'arborescence du depot.

    Mesure le 19/08, trois taches d'affilee ont pris l'etiquette de zone du
    board pour un chemin :

      - T415 « Zone : runtime/llm » a livre un fichier nomme `runtime/llm`,
        contenant une ligne : la sortie d'un `find`. 815 690 tokens.
      - T414 « Zone : dreamcoder/decouverte » a cree
        `core/dreamcoder/decouverte/task_discovery_agent.py`, un doublon ecrit
        de zero du vrai agent, avec son test pose dans `core/`.
      - T416 « Zone : dreamcoder/metrics » a fait de meme.

    Les trois ont passe la porte « la branche differe de master » : elles
    ecrivaient bien des fichiers, simplement pas au bon endroit ni dans le bon
    dossier. Deux invariants suffisent a les arreter, et aucun fichier suivi du
    depot ne les viole aujourd'hui :

      1. un fichier livre porte une extension ;
      2. un fichier de test vit sous `tests/`, jamais dans un paquet de
         production.

    Seuls les fichiers AJOUTES sont examines : reorganiser l'existant reste
    possible, c'est la creation au petit bonheur qu'on ferme.
    """
    from tools.git_safety import _run_git
    _code, sortie, _err = _run_git(
        ["diff", "--name-only", "--diff-filter=A", f"{orig_branch}..HEAD"], repo_root)

    motifs: list[str] = []
    for ligne in (sortie or "").splitlines():
        chemin = ligne.strip().replace("\\", "/")
        if not chemin:
            continue
        nom = chemin.rsplit("/", 1)[-1]
        # Les fichiers caches (`.gitignore`, `.dockerignore`, `.pre-commit-config.yaml`)
        # sont nommes par convention, pas par extension : hors de portee de la
        # regle. Ce qu'on ferme, c'est le fichier ordinaire sans extension.
        if nom.startswith("."):
            continue
        if "." not in nom:
            motifs.append(
                f"'{chemin}' n'a pas d'extension -- un livrable en porte une. "
                f"Une etiquette de zone n'est pas un chemin de fichier."
            )
            continue
        est_test = nom.startswith("test_") or nom.endswith("_test.py")
        if est_test and chemin.startswith(_PAQUETS_PRODUCTION):
            motifs.append(
                f"'{chemin}' est un test pose dans un paquet de production -- "
                f"les tests vivent sous 'tests/'."
            )
            continue
        # Mesuré 23/08 : extract_git_files.py / get_git_file.py à la racine.
        if "/" not in chemin and nom.endswith(".py"):
            motifs.append(
                f"'{chemin}' est un script python posé à la racine — "
                f"un livrable vit sous agents/, api/, core/, scripts/ ou tests/."
            )
    return motifs


# ── [#T421] Le résumé d'une tâche : des faits d'abord, la prose ensuite ─────
#
# `result_summary` recevait `pipeline_result["response"]` brut — c'est-à-dire le
# TRANSCRIPT complet du DAG, pas une conclusion. Relevé sur la tâche #50 du
# 19/08, un « résumé » de 2 400 caractères qui contient, dans l'ordre : deux
# « Plan DAG généré avec N étapes », un bloc `<think>` de raisonnement interne,
# un plan d'action JSON, une section « ## Résumé » qui parle d'une erreur d'outil
# intermédiaire — et se termine sur un troisième « Plan DAG généré ». Rien
# là-dedans ne dit ce qui a été livré.
#
# Ce texte part tel quel dans le corps des PR autonomes (`corps_de_pr`), donc
# aucune PR de nuit n'est lisible sans ouvrir le diff.
#
# Le moteur connaît pourtant les faits : les fichiers du commit, le verdict du
# rejeu des tests, le provider, le coût. Ce sont des MESURES, pas un récit — on
# les met en tête. La prose de l'agent suit, nettoyée, et annoncée pour ce
# qu'elle est : son dernier mot, pas une conclusion garantie.

_BLOC_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LIGNE_PLAN_DAG = re.compile(r"^[ \t]*Plan DAG g[ée]n[ée]r[ée] avec .*$", re.MULTILINE)
_LIGNE_RESULTAT_OUTIL = re.compile(r"^[ \t]*R[ée]sultat '[^']*'[ \t]*:.*$", re.MULTILINE)
_LIGNES_VIDES_EN_TROP = re.compile(r"\n{3,}")

# Messages de dégradation qu'un transcript avale sans broncher : la tâche #52
# du 19/08 a été déclarée `completed` avec trois « Credit balance is too low »
# dans son résumé. Les compter, c'est dire qu'une réussite a coûté des essais.
_MARQUEURS_INCIDENT = (
    "Credit balance is too low",
    "Too Many Requests",
    "Service Unavailable",
    "Payment Required",
    "exécution de code inline refusée",
)


def _nettoyer_transcript(brut: str) -> str:
    """Retire du transcript ce qui n'est pas de la prose adressée au lecteur."""
    if not brut:
        return ""
    texte = _BLOC_THINK.sub("", brut)
    texte = _LIGNE_PLAN_DAG.sub("", texte)
    texte = _LIGNE_RESULTAT_OUTIL.sub("", texte)
    return _LIGNES_VIDES_EN_TROP.sub("\n\n", texte).strip()


def _dernier_mot_agent(brut: str, max_chars: int = 1200) -> str:
    """
    Fin de la prose nettoyée, coupée sur une frontière de paragraphe.

    On garde la FIN et non le début : le transcript se lit dans l'ordre, ce que
    l'agent a écrit en dernier est ce qui se rapproche le plus d'un bilan.
    """
    texte = _nettoyer_transcript(brut)
    if len(texte) <= max_chars:
        return texte
    queue = texte[-max_chars:]
    coupe = queue.find("\n\n")
    return "…\n\n" + (queue[coupe + 2:] if coupe != -1 else queue).strip()


def _incidents_transcript(brut: str) -> list[str]:
    """Marqueurs de dégradation présents dans le transcript, avec leur compte."""
    if not brut:
        return []
    return [f"{brut.count(m)} × « {m} »" for m in _MARQUEURS_INCIDENT if m in brut]


def _fichiers_du_commit(repo_root: str, orig_branch: str) -> tuple[list[str], int, int]:
    """Fichiers touchés par la branche et volume (+ajouts, -retraits)."""
    from tools.git_safety import _run_git
    _code, sortie, _err = _run_git(
        ["diff", "--numstat", f"{orig_branch}..HEAD"], repo_root)
    fichiers, ajouts, retraits = [], 0, 0
    for ligne in (sortie or "").splitlines():
        morceaux = ligne.split("\t")
        if len(morceaux) != 3:
            continue
        a, r, chemin = morceaux
        fichiers.append(chemin.strip())
        # '-' sur un binaire : compté comme fichier, pas comme volume.
        ajouts += int(a) if a.isdigit() else 0
        retraits += int(r) if r.isdigit() else 0
    return fichiers, ajouts, retraits


def _resume_de_tache(
    brut: str,
    *,
    fichiers: list[str],
    ajouts: int,
    retraits: int,
    verdict_tests: str | None,
    verdict_frontend: str | None,
    provider: str,
    gouverneur: str,
    tokens: int,
    cout: float,
) -> str:
    """
    Résumé lisible d'une tâche autonome : mesures en tête, prose ensuite.

    Tout ce qui précède le séparateur est constaté par le moteur (diff Git,
    rejeu des tests, compteurs de coût). Rien n'y vient du LLM.
    """
    lignes: list[str] = []

    if fichiers:
        entete = f"**Livré** — {len(fichiers)} fichier(s), +{ajouts} / −{retraits} :"
        lignes.append(entete)
        lignes += [f"- `{f}`" for f in fichiers[:12]]
        if len(fichiers) > 12:
            lignes.append(f"- … et {len(fichiers) - 12} autre(s)")
    else:
        lignes.append("**Livré** — aucun fichier (anomalie : le commit devrait en porter).")

    lignes.append("")
    lignes.append(f"- **Tests livrés** : {verdict_tests or 'aucun test dans le diff'}")
    if verdict_frontend:
        lignes.append(f"- **Frontend** : {verdict_frontend}")
    lignes.append(f"- **Modèles** : exécution `{provider}` · gouverneur `{gouverneur}`")
    lignes.append(f"- **Coût** : {tokens:,} jetons — ${cout:.4f}".replace(",", " "))

    incidents = _incidents_transcript(brut)
    if incidents:
        lignes.append(f"- **Incidents pendant l'exécution** : {', '.join(incidents)}")

    prose = _dernier_mot_agent(brut)
    if prose:
        lignes += [
            "",
            "---",
            "",
            "**Dernier mot de l'agent** — extrait de fin de transcript, ce n'est "
            "pas une conclusion garantie :",
            "",
            prose,
        ]
    return "\n".join(lignes)


def _consommation_de_la_tache(session_id: str, debut_tache: float) -> tuple[int, float]:
    """
    Jetons et coût réellement consommés par CE passage de la tâche (#T416).

    Lecture bornée dans le temps : le `session_id` d'une tâche est `task_<id>`,
    stable d'un rejeu à l'autre. Sans la borne, un quatrième passage ramasserait
    le coût des trois précédents (#T393, facteur 8 mesuré le 18/08).

    Fonction pure : elle LIT, elle n'impute rien. C'est l'appelant qui décide
    d'appeler `BudgetGuard.record_usage`, une fois et une seule.
    """
    tokens_used = 0
    cost = 0.0
    try:
        from core.token_tracker import get_session_total_tokens
        tokens_used = get_session_total_tokens(session_id)
    except Exception as err:
        logger.warning(f"[DREAMER] [DreamCoder] Jetons illisibles pour {session_id} : {err}")
    try:
        from core.runtime_db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM token_usage "
                "WHERE session_id = ? AND timestamp >= ?",
                (session_id, debut_tache),
            ).fetchone()
            if row:
                cost = float(row[0] or 0.0)
    except Exception as err:
        logger.warning(f"[DREAMER] [DreamCoder] Impossible de lire le coût réel : {err}")
    return tokens_used, cost


def _fichiers_frontend_modifies(repo_root: str, orig_branch: str) -> list[str]:
    """Fichiers TypeScript/TSX d'ihm-v2 touches par la branche de la tache."""
    from tools.git_safety import _run_git
    _code, sortie, _err = _run_git(
        ["diff", "--name-only", f"{orig_branch}..HEAD"], repo_root)
    return [
        chemin for chemin in (
            ligne.strip().replace(chr(92), "/") for ligne in (sortie or "").splitlines())
        if chemin.startswith("ihm-v2/") and chemin.endswith((".ts", ".tsx"))
    ]


def _verifier_frontend(repo_root: str) -> tuple[bool, str]:
    """
    Compile le frontend. Rend (vert, compte-rendu).

    `node_modules` absent n est PAS un echec de la tache : le clone d un slot
    n a pas forcement ses dependances installees, et on ne punit pas un agent
    pour l etat de la machine.
    """
    import subprocess

    ihm = os.path.join(repo_root, "ihm-v2")
    if not os.path.isdir(os.path.join(ihm, "node_modules")):
        return True, "node_modules absent — porte frontend ignoree"
    try:
        proc = subprocess.run(
            ["npx", "tsc", "-b"], cwd=ihm, capture_output=True, text=True,
            timeout=_TIMEOUT_TESTS_LIVRES_S, shell=(os.name == "nt"),
        )
    except FileNotFoundError:
        return True, "npx introuvable — porte frontend ignoree"
    except subprocess.TimeoutExpired:
        return False, "la compilation du frontend ne rend pas la main"
    if proc.returncode == 0:
        return True, "tsc -b au vert"
    sortie = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return False, sortie.replace(chr(10), " | ")[-600:]


def _delai_rejeu_echec_s() -> float:
    """
    [#T432] Garde entre deux tentatives d'une tâche `failed`.

    Plus long qu'un cycle (~30 min) : on ne rejoue pas dans la même fenêtre.
    Plus court qu'une journée : un échec de code transitoire revient le soir
    même, au lieu d'attendre 24 h d'inactivité. Plancher 60 s (les tests).
    Surcharge : `MOTEUR_REJEU_ECHEC_S`.
    """
    brut = os.environ.get("MOTEUR_REJEU_ECHEC_S", str(6 * 3600))
    try:
        val = float(brut)
    except (TypeError, ValueError):
        val = 6 * 3600.0
    return max(60.0, val)


# Portes de qualité : ce sont des échecs de la TÂCHE. `echec_imputable_au_provider`
# cherche des sous-chaînes trop larges (`timeout`, `401`) qui apparaissent aussi
# dans un nom de fichier de test — PR #445, `test_anthropic_timeout.py`. Sans
# ce filet, un échec pytest redeviendrait `pending` à l'infini.
_SIGNATURES_PORTE_QUALITE = (
    "tests livrés",
    "tests livres",
    "chemin de machine",
    "arborescence",
    "aucun agent",
    "identique à",
    "identique a",
    "ne parse pas",
    "frontend ne compile",
    "figent un chemin",
    "livrable est insuffisant",
    "titre de la tâche cite",
    "aucune modification n'était nécessaire",
    "script python posé à la racine",
    "déjà ✅",
)

# Un 429 / quota n'est pas un défaut du livrable : la tâche n'a pas démérité.
# Volontairement plus étroit que `echec_imputable_au_provider` (timeout, 401…).
_SIGNATURES_QUOTA_PROVIDER = (
    "429",
    "rate limit",
    "too many requests",
    "resource_exhausted",
    "resource exhausted",
    "quota",
)


def _est_porte_qualite(erreur: str) -> bool:
    """True si le message vient d'une porte du moteur, pas d'un appel LLM."""
    bas = str(erreur or "").lower()
    return any(s in bas for s in _SIGNATURES_PORTE_QUALITE)


def _faut_conserver_la_branche(erreur: str) -> bool:
    """
    True s'il y a un livrable à CORRIGER, pas seulement à recommencer.

    Une branche identique à master, ou un pipeline sans agent, n'a rien à
    garder : le retry part de zéro. Un livrable insuffisant (mauvais chemin,
    titre hors-sujet, « rien à faire » avec des fichiers jetables) a déjà du
    travail sur la branche — le détruire force l'agent à payer un second tour
    identique. Mesuré le 23/08 : tâche 64 `failed` après analyse, branche
    `task/64_*` détruite, `error_message` jamais réinjecté.
    """
    if not _est_porte_qualite(erreur):
        return False
    bas = str(erreur or "").lower()
    if "identique à" in bas or "identique a" in bas:
        return False
    if "aucun agent" in bas:
        return False
    return True


SORT_CONSERVER = "conserver"
SORT_SUPPRIMER = "supprimer"
SORT_ROLLBACK = "rollback"


def _sort_de_la_branche(erreur: str, statut: str) -> str:
    """
    Ce qu'il advient de la branche de tâche quand le pipeline a levé.

    Trois issues, pas deux — c'est la distinction qui manquait :

    * `conserver` — porte qualité ET rejeu à venir (`failed`) : la branche est
      poussée pour qu'un autre clone reprenne la correction.
    * `supprimer` — même travail, mais la tâche a brûlé ses tentatives
      (`abandoned`). Personne ne reprendra : pousser n'ajoute qu'une orpheline.
      Mesuré le 24/08 : 88 branches (74 moteur, 14 Tab5) nées exactement là.
    * `rollback` — rien à garder (branche identique à master, aucun agent) :
      nettoyage complet, comportement historique.

    `supprimer` nettoie SANS `git_rollback_checkpoint` : son `clean -fd`
    détruirait les fichiers non suivis d'un dépôt à politique humaine — le
    Tab5 travaille dans `00ProjetTab`, pas dans un clone jetable.
    """
    if not _faut_conserver_la_branche(erreur):
        return SORT_ROLLBACK
    return SORT_SUPPRIMER if statut == "abandoned" else SORT_CONSERVER


def _consigne_de_correction(tache: dict) -> str:
    """Critique du tour précédent, collée au prompt du retry."""
    retries = int(tache.get("retries") or 0)
    err = (tache.get("error_message") or "").strip()
    if retries <= 0 or not err:
        return ""
    return (
        "\n\n[CORRECTION — ne recommence PAS à zéro]\n"
        "Le moteur a REFUSÉ la tentative précédente. Le travail est déjà "
        "sur CETTE branche. Corrige précisément le refus suivant ; ne recrée "
        "pas des fichiers jetables à la racine :\n"
        f"{err[:2000]}\n"
        "Si c'est une analyse : un Markdown sous docs/analyses/. "
        "Si un fichier est mal placé : déplace-le, ne le duplique pas.\n"
    )


def _reprendre_branche_critique(repo_root: str, nom_branche: str) -> bool:
    """Checkout une branche de tâche déjà écrite (locale, sinon origin)."""
    from tools.git_safety import _run_git

    nom = (nom_branche or "").strip()
    if not nom or ".." in nom or nom.startswith("-"):
        return False
    code, _, _ = _run_git(["rev-parse", "--verify", f"refs/heads/{nom}"], repo_root)
    if code != 0:
        _run_git(["fetch", "origin", nom], repo_root)
        code, _, _ = _run_git(
            ["checkout", "-B", nom, f"origin/{nom}"], repo_root
        )
        return code == 0
    code, _, _ = _run_git(["checkout", nom], repo_root)
    return code == 0


def _est_quota_ou_429(erreur: str) -> bool:
    """True pour un plafonnement de provider, pas pour un timeout pytest."""
    if _est_porte_qualite(erreur):
        return False
    bas = str(erreur or "").lower()
    return any(s in bas for s in _SIGNATURES_QUOTA_PROVIDER)


def _verdict_apres_echec(erreur: str, retries: int) -> dict[str, Any]:
    """
    [#T432] Décide le sort d'une tâche dont le pipeline a levé.

    - infrastructure → pending, 0 tentative brûlée (`liberer_tache`)
    - 429 / quota → pending, 0 tentative, cooldown du provider
    - code (y compris un 401 LLM) → `failed` + horaire de rejeu, ou `abandoned`
    """
    from core.backlog_db import MAX_TENTATIVES_TACHE
    from core.budget_guard import echec_imputable_au_provider, panne_infrastructure

    retries_actuels = int(retries or 0)
    porte = _est_porte_qualite(erreur)
    if not porte and panne_infrastructure(erreur):
        return {
            "action": "liberer",
            "cause": "infra",
            "cooldown": False,
            "status": "infra_indisponible",
            "retries": retries_actuels,
            "scheduled_at": None,
        }
    if _est_quota_ou_429(erreur):
        return {
            "action": "liberer",
            "cause": "provider",
            "cooldown": True,
            "status": "provider_indisponible",
            "retries": retries_actuels,
            "scheduled_at": None,
        }
    suivants = retries_actuels + 1
    cooldown = (not porte) and echec_imputable_au_provider(erreur)
    if suivants >= MAX_TENTATIVES_TACHE:
        return {
            "action": "clore",
            "cause": "code",
            "cooldown": cooldown,
            "status": "abandoned",
            "retries": suivants,
            "scheduled_at": None,
        }
    return {
        "action": "clore",
        "cause": "code",
        "cooldown": cooldown,
        "status": "failed",
        "retries": suivants,
        "scheduled_at": time.time() + _delai_rejeu_echec_s(),
    }


def _proprietaire_depot_github(url_remote: str) -> tuple[str, str] | None:
    """(owner, repo) depuis une URL GitHub HTTPS, SSH, ou HTTPS avec identifiants."""
    if not url_remote:
        return None
    # `_url_https_github` refuse volontairement un HTTPS avec jeton dans l'URL
    # (ce n'est pas une URL de push). Pour *nommer* le dépôt, l'hôte suffit.
    source = _url_https_github(url_remote) or url_remote
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+)", source, re.IGNORECASE)
    if not m:
        return None
    owner = m.group(1)
    repo = m.group(2).rstrip("/").removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo


def _branche_par_defaut_depot(
    owner: str, repo: str, jeton: str, repo_root: str
) -> str:
    """
    La branche de base à viser pour une PR sur `owner/repo`.

    Mesuré le 24/08 : `base` valait `master` en dur. Le moteur est bien sur
    `master`, mais `Axellum/M5-Tab5-ESPHome-LVGL` est sur `main` — les 14
    branches Tab5 poussées n'ont jamais pu ouvrir une seule PR (HTTP 422).
    Trois sources, de la plus fiable à la plus disponible :
      1. l'API GitHub, qui fait autorité (même jeton que la PR) ;
      2. `origin/HEAD` du clone, quand il a été résolu au clonage ;
      3. `master`, l'ancien comportement.
    """
    import urllib.error
    import urllib.request

    from tools.git_safety import _run_git

    if jeton:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jeton}",
                "User-Agent": "tab5-engine-dreamcoder",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            defaut = (data.get("default_branch") or "").strip()
            if defaut:
                return defaut
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning(
                "[DREAMER] Branche par défaut de %s/%s illisible via l'API "
                "(%s) — repli local.", owner, repo, str(exc)[:120]
            )

    code, ref, _ = _run_git(
        ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo_root
    )
    if code == 0 and ref:
        # `origin/main` → `main`
        candidat = ref.strip().split("/", 1)[-1]
        if candidat:
            return candidat

    return "master"


def _ouvrir_pr_tache(
    repo_root: str, branch_name: str, titre: str, corps: str
) -> dict[str, Any]:
    """
    Ouvre une PR GitHub pour la branche poussée — best effort, JAMAIS bloquant.

    [#T432] Le moteur poussait la branche et s'arrêtait là. La récolte
    (`scripts/recolte_dreamcoder.py`) est un geste manuel depuis le PC.
    Mesuré le 21–22/08 : `task/70` et `task/84` sur origin, zéro PR.
    Le même jeton qui pousse (`MOTEUR_GITHUB_PUSH_TOKEN`) peut ouvrir la PR.
    """
    import urllib.error
    import urllib.request

    from tools.git_safety import _run_git

    jeton = (os.environ.get("MOTEUR_GITHUB_PUSH_TOKEN") or "").strip()
    if not jeton:
        return {"ouverte": False, "raison": "jeton_absent", "url": "", "detail": ""}

    code, remote, _ = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
    depot = _proprietaire_depot_github(remote) if code == 0 else None
    if depot is None:
        code_p, push_url, _ = _run_git(
            ["remote", "get-url", "--push", "origin"], cwd=repo_root
        )
        depot = _proprietaire_depot_github(push_url) if code_p == 0 else None
    if depot is None:
        return {
            "ouverte": False,
            "raison": "pas_github",
            "url": "",
            "detail": (remote or "")[:160],
        }

    owner, repo = depot
    base = _branche_par_defaut_depot(owner, repo, jeton, repo_root)
    payload = json.dumps({
        "title": (titre or f"Tâche {branch_name}")[:250],
        "head": branch_name,
        "base": base,
        "body": (corps or "")[:60000],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jeton}",
            "Content-Type": "application/json",
            "User-Agent": "tab5-engine-dreamcoder",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return {
            "ouverte": True,
            "raison": "ok",
            "url": data.get("html_url") or "",
            "detail": "",
        }
    except urllib.error.HTTPError as err:
        try:
            corps_err = err.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            corps_err = ""
        if err.code == 422 and "already exists" in corps_err.lower():
            return {
                "ouverte": True,
                "raison": "deja_ouverte",
                "url": "",
                "detail": corps_err[:160],
            }
        # La base visée fait partie du diagnostic : un 422 muet a caché
        # pendant des jours que `master` n'existait pas sur le dépôt Tab5.
        return {
            "ouverte": False,
            "raison": f"http_{err.code}",
            "url": "",
            "detail": f"base={base} head={branch_name} :: {corps_err[:200]}",
        }
    except Exception as err:
        return {
            "ouverte": False,
            "raison": "erreur",
            "url": "",
            "detail": str(err)[:300],
        }


def _url_https_github(url_remote: str) -> str | None:
    """
    [#T327] URL HTTPS d'un remote GitHub, ou None si ce n'en est pas un.

    Le clone du Deck a son `origin` en SSH, servi par une clé de déploiement en
    LECTURE SEULE : le push y est refusé. Le jeton, lui, ne vaut qu'en HTTPS —
    d'où cette conversion, faite à la volée sans jamais toucher la config du
    dépôt.
    """
    if url_remote.startswith("git@github.com:"):
        return "https://github.com/" + url_remote.split(":", 1)[1]
    if url_remote.startswith("https://github.com/"):
        return url_remote
    return None


def _entete_autorisation_git(jeton: str) -> dict[str, str]:
    """
    Variables d'environnement portant l'autorisation HTTP de `git push`.

    Le jeton ne doit apparaître NI dans `.git/config` (il survivrait au cycle et
    partirait dans une sauvegarde), NI dans la ligne de commande (visible par
    n'importe quel `ps` sur la machine). `GIT_CONFIG_*` l'injecte pour la durée
    du seul processus git — c'est la méthode qu'emploie GitHub Actions.
    """
    entete = base64.b64encode(f"x-access-token:{jeton}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {entete}",
    }


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
    commande = ["git", "push", "--set-upstream", "origin", branch_name]

    # [#T327] Jeton de poussée, opt-in explicite. Sans lui, on garde le chemin
    # historique : `origin` en SSH, refusé par la clé de déploiement en lecture
    # seule, et la branche attend la récolte depuis le PC.
    # Nom distinct de `GITHUB_TOKEN` à dessein : cette variable donne à un agent
    # tournant SANS surveillance le droit d'écrire sur GitHub. Elle doit être
    # posée en connaissance de cause, pas héritée d'un outil voisin.
    jeton = (os.environ.get("MOTEUR_GITHUB_PUSH_TOKEN") or "").strip()
    url_https = _url_https_github(push_url)
    if jeton and url_https:
        env.update(_entete_autorisation_git(jeton))
        # Refspec explicite : on pousse vers une URL, pas vers un remote nommé,
        # donc `--set-upstream` n'aurait rien à quoi se rattacher.
        commande = ["git", "push", url_https,
                    f"refs/heads/{branch_name}:refs/heads/{branch_name}"]

    try:
        res = subprocess.run(
            commande,
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


def _branche_courante(repo_root: str) -> str:
    """Branche réellement sortie dans le dépôt de travail (vide si détachée)."""
    from tools.git_safety import _run_git
    code, sortie, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    return sortie.strip() if code == 0 and sortie else ""


def _revenir_sur_la_branche_de_tache(repo_root: str, branch_name: str) -> str | None:
    """
    Ramène le dépôt sur la branche de la tâche avant de commiter.

    [#T432] L'agent dispose d'outils Git (`git_create_checkpoint` et consorts) et
    s'en sert : mesuré le 21/08 sur le Deck, la tâche 66 a fini avec le dépôt
    revenu sur `master`. Le `git commit` du harnais a donc écrit sur `master`, et
    la vérification `orig_branch..HEAD` — où `orig_branch` EST `master` — n'a vu
    aucune différence. Verdict rendu : « la branche task/66 est identique à
    master », alors que le travail existait bel et bien, à un commit de là.

    Trois tâches ont été déclarées en échec ce jour-là pour cette seule raison,
    et leur travail n'a été retrouvé qu'en inspectant le clone à la main.

    :return: un message d'erreur si la branche ne peut pas être rétablie, sinon None.
    """
    from tools.git_safety import _run_git
    courante = _branche_courante(repo_root)
    if courante == branch_name:
        return None
    logger.warning(
        f"[DREAMER] [DreamCoder] Le dépôt de travail est sur '{courante or '(détaché)'}' "
        f"et non sur '{branch_name}' : l'agent a changé de branche pendant la tâche. "
        f"Retour sur la branche de tâche avant le commit."
    )
    code, _, err = _run_git(["checkout", branch_name], repo_root)
    if code != 0:
        return (
            f"Le travail a été produit mais le dépôt est resté sur "
            f"'{courante or '(détaché)'}' au lieu de '{branch_name}', et le retour "
            f"a échoué : {err[:200]}. Le commit aurait atterri sur la mauvaise "
            f"branche — refus de commiter plutôt que de perdre le travail."
        )
    return None


async def _process_one_dreamcoder_task(task: dict, pa_config: dict, provider: str,
                                        orig_branch: str, bg: Any,
                                        repo_root: str | None = None) -> dict[str, Any]:
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
        "pr": {"ouverte": False, "raison": "non_tente", "url": "", "detail": ""},
    }
    # [#T393] Borne basse du coût de CETTE exécution : `session_id` est stable
    # d'un rejeu à l'autre, seul le temps distingue les passages.
    debut_tache = time.time()

    logger.info(f"[DREAMER] [DreamCoder] Tâche trouvée : ID {task_id} - '{task['title']}'")
    logger.info(f"[DREAMER] [DreamCoder] Provider alloué : {provider}")

    # Configurer temporairement les modèles selon le provider alloué.
    # D-21 : exécuteur = AJEAN ; cerveau PC = Gemini 3.7 Flash medium (agy) ;
    # cerveau Deck = DeepSeek V4 Pro. Escalade après échec via
    # `_gouverneurs_escalade()`. Le gouverneur ne passe PAS par BudgetGuard
    # (appel direct par nom de modèle) ; le plafond quotidien combiné le
    # dégrade sur le modèle de l'exécuteur.
    from core.llm_gateway import load_config
    config = load_config()
    temp_config = config.copy()

    # [#T387] Table déplacée dans core/sante_providers.py : la sonde de santé
    # et l'exécution DOIVENT viser le même modèle, sinon on déclare sain un
    # modèle et on en joue un autre.
    from core.sante_providers import MODELE_PAR_PROVIDER
    executor_model = MODELE_PAR_PROVIDER.get(provider, "deepseek-chat")
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
        # retenter avec le même gouverneur — mais seulement s'il répond encore
        # (cf. `_GOUVERNEURS_ESCALADE`). Sinon on redescend sur le gouverneur
        # par défaut : gouverner moins haut vaut mieux que gouverner dans le
        # vide.
        gouverneur_repli = _gouverneur_par_defaut()
        escalade = _gouverneurs_escalade()
        governor_model = _premier_gouverneur_vivant(escalade, gouverneur_repli)
        if governor_model != escalade[0]:
            logger.warning(
                f"[DREAMER] [DreamCoder] Escalade : "
                f"'{escalade[0]}' est éteint par la sonde — "
                f"gouverneur replié sur '{governor_model}'."
            )
    else:
        governor_model = _gouverneur_par_defaut()

    # Dernier filet planner : jamais Qwen. Repli = cerveau de l'hôte
    # (Gemini medium sur PC, V4 Pro sur Deck).
    if _modele_eteint(governor_model):
        repli_planner = _reviewer_par_defaut()
        logger.warning(
            f"[DREAMER] [DreamCoder] Gouverneur '{governor_model}' éteint par la "
            f"sonde — planner replié sur '{repli_planner}' (pas l'exécuteur)."
        )
        governor_model = repli_planner

    reviewer_model = _reviewer_par_defaut()
    temp_config["planner_model"] = governor_model
    temp_config["reviewer_model"] = reviewer_model

    # Préparer la branche éphémère de l'agent
    from tools.git_safety import (
        _run_git,
        git_generate_semantic_commit_msg,
        git_prepare_agent_branch,
        git_rollback_checkpoint,
    )

    # [#T202] Toutes les opérations Git de la tâche ciblent le dépôt du PROJET
    # visé (Tab5 → 00ProjetTab, moteur → clone DreamCoder). Le slot parallèle
    # n'est qu'un isolement d'index pour les tâches moteur.
    from core.projects import projet_par_id, resoudre_projet_pour_tache
    try:
        projet_id, repo_root, orig_branch = _projet_et_depot_de_tache(
            task, pa_config, repo_root,
        )
    except FileNotFoundError as exc:
        logger.error("[DREAMER] [DreamCoder] Dépôt projet introuvable : %s", exc)
        from core.backlog_db import MAX_TENTATIVES_TACHE
        next_retries = int(task.get("retries", 0) or 0) + 1
        statut = "abandoned" if next_retries >= MAX_TENTATIVES_TACHE else "failed"
        extra: dict[str, Any] = {}
        if statut == "failed":
            extra["scheduled_at"] = time.time() + _delai_rejeu_echec_s()
        await update_task_status(
            task_id, statut, error_message=str(exc), retries=next_retries, **extra,
        )
        result["status"] = statut
        result["error"] = str(exc)
        return result

    projet = projet_par_id(projet_id) or resoudre_projet_pour_tache(task)
    result["repo_root"] = repo_root
    result["project_id"] = projet_id
    await update_task_status(task_id, "running", project_id=projet_id)

    # Un clone dédié (dc3, 00ProjetTab_dc) peut être remis à zéro. La copie
    # humaine d'Axel, jamais : `reset --hard` + `clean -fd` y détruirait son
    # travail non commité (le motif exact de #T318 / #T435, mais sur Tab5).
    if projet.est_clone_agent():
        await asyncio.to_thread(assainir_slot, repo_root)

    branche_existante = (task.get("git_branch") or "").strip()
    reprendre = (
        bool(branche_existante)
        and int(task.get("retries") or 0) > 0
        and _faut_conserver_la_branche(task.get("error_message") or "")
    )
    if reprendre and await asyncio.to_thread(
        _reprendre_branche_critique, repo_root, branche_existante
    ):
        branch_name = branche_existante
        logger.info(
            f"[DREAMER] [DreamCoder] Retry dirigé : reprise de {branch_name} "
            f"(tâche {task_id}, tentative {int(task.get('retries') or 0) + 1})."
        )
    else:
        branch_name = await asyncio.to_thread(
            git_prepare_agent_branch,
            session_id=str(task_id),
            repo_path=repo_root,
            prefix="task/"
        )

    if branch_name.startswith("Erreur"):
        logger.error(f"[DREAMER] [DreamCoder] Impossible de préparer la branche Git : {branch_name}")
        # `retries` était laissé à 0 sur ce chemin : la tâche n'atteignait
        # jamais 'abandoned' après 3 échecs. Depuis #T432 un `failed` revient
        # après le délai de rejeu ; sans incrément elle bouclerait.
        # Constaté sur le Deck : deux tâches 'failed' / retries=0 depuis juillet.
        from core.backlog_db import MAX_TENTATIVES_TACHE
        next_retries = int(task.get("retries", 0) or 0) + 1
        statut = "abandoned" if next_retries >= MAX_TENTATIVES_TACHE else "failed"
        extra: dict[str, Any] = {}
        if statut == "failed":
            extra["scheduled_at"] = time.time() + _delai_rejeu_echec_s()
        await update_task_status(task_id, statut, error_message=branch_name,
                                 retries=next_retries, **extra)
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
    workspace_root = os.path.dirname(repo_root) or repo_root
    try:
        from core.projects import racine_espace_de_travail
        workspace_root = racine_espace_de_travail()
    except Exception:
        pass
    work_prompt = (
        f"[PROJET : {projet.id} — {projet.label}]\n"
        f"[DÉPÔT DE TRAVAIL : {repo_root}]\n"
        f"[GIT : {projet.git_remote}  branche de base : {orig_branch}]\n"
        f"Règle stricte : TOUT chemin doit être ABSOLU — jamais de chemin relatif, "
        f"qui se résoudrait contre le dossier du serveur et non contre ce dépôt.\n"
        f"ÉCRITURE (write_file) et commandes : uniquement sous {repo_root} "
        f"(c'est le git de CE projet, pas celui du moteur).\n"
        f"LECTURE (read_file) : tu peux lire ailleurs dans l'espace de travail "
        f"{workspace_root} — les autres projets y vivent. Une analyse qui porte "
        f"sur un autre projet se LIT là-bas ; le livrable s'ÉCRIT ICI, dans "
        f"{repo_root}.\n"
        f"IMPORTANT — RECHERCHES : ne lance JAMAIS une recherche depuis "
        f"{workspace_root} : cette racine contient des archives de plusieurs "
        f"gigaoctets et un partage réseau, chaque recherche y coûte ~40 secondes "
        f"et épuise ton budget de tours. Donne toujours un sous-dossier précis : "
        f"le dépôt de travail {repo_root}. Commence par "
        f"lister un dossier ciblé plutôt que par chercher largement.\n\n"
        f"{task['description']}"
        f"{consigne_de_livrable(task, repo_root)}"
        f"{_consigne_de_correction(task)}"
    )

    router = _get_app_state().get_shared_router()
    initial_payload, starting_agent = await router.analyze_request(
        task["description"], session_id=str(task_id)
    )

    # [#T441] La consigne de livrable doit atteindre le PLANNER, pas seulement
    # l'exécuteur. Le pipeline reçoit `work_prompt` (qui la porte), mais le
    # Planner, lui, reconstruit son prompt depuis le payload — bâti par le
    # routeur à partir de `task["description"]` seule. Le marqueur
    # `[LIVRABLE ATTENDU` n'y figurait donc pas, et le critère `fichier_existe`
    # de #T440 n'était jamais posé : mesuré le 21/08 sur la tâche 62, deux fois
    # de suite, aucune ligne « Livrable d'analyse ajouté au contrat » au journal.
    #
    # On l'ajoute au CONTEXTE du payload plutôt qu'à l'objectif : le routeur a
    # déjà classé la demande, sa décision ne doit pas changer parce qu'on
    # rallonge le texte.
    consigne = consigne_de_livrable(task, repo_root)
    correction = _consigne_de_correction(task)
    if consigne or correction:
        contexte = getattr(initial_payload, "relevant_context", "") or ""
        initial_payload.relevant_context = f"{contexte}{consigne}{correction}"

    # [#T426] Une analyse a besoin de plus de tours qu'un correctif ciblé : elle
    # doit d'abord PARCOURIR un dépôt qu'elle ne connaît pas, puis écrire son
    # rapport. Mesuré le 21/08 sur la tâche 49 (montée ESPHome du Tab5) : les 10
    # tours par défaut ont été consommés en lecture — search_files, list_directory,
    # read_text_file — et l'agent a atteint le plafond juste au moment où il
    # créait le dossier du rapport. Il avait compris quoi faire, il n'avait plus
    # de tours pour le faire.
    #
    # Réglage d'exploitation, pas constante de code : le budget d'une analyse se
    # règle sans redéploiement.
    jeton_tours = None
    if _est_une_tache_d_analyse(f"{task.get('title', '')} {task.get('description', '')}"):
        tours = int((pa_config or {}).get("dreamcoder_tours_react_analyse", 24) or 24)
        initial_payload.metadata["max_turns"] = tours
        # [#T426] Le payload initial n'atteint PAS les étapes du DAG, que le
        # planner construit sans rien hériter. Le plafond se pose donc sur la
        # SESSION, comme la racine de travail — sinon le journal continue
        # d'afficher « tour x/10 » sur une analyse censée en avoir 24.
        from agents.executor import definir_tours_react_session
        jeton_tours = definir_tours_react_session(tours)
        logger.info(
            f"[DREAMER] [DreamCoder] Tâche d'analyse : budget porté à {tours} tours "
            f"de boucle ReAct (le défaut de 10 sert à écrire, pas à explorer)."
        )

    async def sse_callback(event_type, data, engine_inst):
        pass

    # [#T438] UNE SESSION PAR TENTATIVE, et non par tâche.
    #
    # `task_<id>` était stable d'un rejeu à l'autre. Le budget d'exécution
    # (`max_session_tokens`, 500 000 par défaut) compte la consommation de la
    # SESSION : au bout de quelques tentatives, une tâche dépasse son plafond
    # avec la dépense de ses propres essais précédents, et toute nouvelle
    # tentative est refusée AVANT le moindre agent. Sans un mot dans le journal.
    #
    # Mesuré le 21/08 sur la tâche 57 : session `task_57`, 12 appels cumulés,
    # 2 192 330 jetons contre un plafond de 500 000. Le moteur routait vers le
    # Planner et terminait l'orchestration dans la seconde — « aucun agent n'a
    # tourné » — et chaque relance aggravait le blocage au lieu de le lever.
    #
    # L'horodatage de début borne la session à CETTE tentative. `_consommation_de_la_tache`
    # gardait déjà une borne temporelle pour la même raison (#T393/#T416) : le
    # défaut était connu côté mesure, pas côté budget.
    session_id = f"task_{task_id}_{int(debut_tache)}"

    # [#T388] Imposer la racine de résolution des chemins RELATIFS avant de
    # lancer le pipeline. Le prompt le demande déjà en toutes lettres, mais une
    # consigne textuelle demande au modèle de compenser un défaut d'outillage —
    # et le 18/08 il ne l'a pas fait : quatre livrables ont atterri dans le
    # dossier de PROD (CWD du serveur) pendant que les branches restaient vides.
    # Le `set` a lieu ici, dans la tâche asyncio du slot : chaque travailleur
    # parallèle a sa propre copie de contexte, donc sa propre racine.
    from tools.system import definir_racine_travail, reinitialiser_racine_travail
    jeton_racine = definir_racine_travail(repo_root)

    # [#T416] Déclarés AVANT le try. Ils étaient assignés après le retour du
    # pipeline : quand celui-ci lève (timeout d'enveloppe, provider mort), le
    # chemin d'échec les trouvait indéfinis — donc n'imputait rien du tout.
    tokens_used = 0
    cost = 0.0
    consommation_imputee = False

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

        # Collecter la consommation réelle de ce passage (jetons + coût).
        # [#T393] La lecture est BORNÉE AU TEMPS DE CETTE EXÉCUTION — voir
        # `_consommation_de_la_tache`. Mesuré le 18/08 sur un cycle complet :
        # 0,707 $ annoncés contre 0,0888 $ réellement dépensés, facteur 8.
        tokens_used, cost = await asyncio.to_thread(
            _consommation_de_la_tache, session_id, debut_tache)

        # Enregistrer la consommation
        window_type = "hourly" if provider == "gemini-free" else "daily"
        await bg.record_usage(
            provider=provider,
            tokens=tokens_used,
            cost=cost,
            model=temp_config["planner_model"],
            window_type=window_type
        )
        consommation_imputee = True
        result["tokens_used"] = tokens_used
        result["cost_usd"] = cost

        if pipeline_result.get("status") == "completed" or pipeline_result.get("status") == "success":
            logger.info("[DREAMER] [DreamCoder] Pipeline satisfait. Enregistrement du commit...")

            # [#T432] Vérifier SUR QUELLE BRANCHE on commite, avant de commiter.
            erreur_branche = await asyncio.to_thread(
                _revenir_sur_la_branche_de_tache, repo_root, branch_name)
            if erreur_branche:
                raise RuntimeError(erreur_branche)

            await asyncio.to_thread(_run_git, ["add", "-A"], repo_root)

            # [#T394] Refuser de commiter du Python qui ne parse pas.
            # Mesuré le 18/08 sur `task/18_1787055040` : l'agent a livré un
            # `services/ha_state_query.py` avec une chaîne non fermée
            # (« SyntaxError: unterminated string literal », ligne 279). Le
            # module ne s'importait plus — et ce fichier sert le VOCAL, le
            # chemin le plus utilisé de la prod. Le pipeline avait pourtant
            # déclaré la tâche réussie, et la branche est partie sur GitHub.
            # Un `ast.parse` coûte quelques millisecondes et ferme toute une
            # classe de livrables inutilisables : du code qui ne parse pas n'est
            # pas un succès, quoi qu'en dise le Reviewer.
            invalides = await asyncio.to_thread(_python_invalide_dans_index, repo_root)
            if invalides:
                raise RuntimeError(
                    "Le pipeline s'est déclaré terminé mais le code produit ne "
                    "parse pas : " + " | ".join(invalides[:3])
                )

            commit_msg = await asyncio.to_thread(git_generate_semantic_commit_msg, repo_root, session_id)
            await asyncio.to_thread(_run_git, ["commit", "-m", commit_msg], repo_root)

            # [#T388] Un pipeline « completed » ne prouve PAS qu'un fichier a
            # bougé. Le 18/08, quatre tâches ont été déclarées réussies avec
            # quatre branches vides : les livrables étaient partis dans le CWD
            # du serveur, `git add -A` n'avait rien trouvé, `git commit` avait
            # échoué sur « nothing to commit » — et personne ne lisait son code
            # de retour. Coût du mensonge : 1,13 M de tokens et une confiance
            # imméritée dans quatre branches à relire.
            # La preuve d'un travail, c'est un commit qui existe. Rien d'autre.
            code_diff, _, _ = await asyncio.to_thread(
                _run_git, ["diff", "--quiet", f"{orig_branch}..HEAD"], repo_root)
            if code_diff == 0:
                # [#T437] Dire ce que le pipeline A FAIT, pas seulement ce qu'il n'a
                # pas produit. « Aucun fichier n'a été écrit » envoie chercher un
                # problème de chemins — alors que le 21/08 à 10h53, la vraie cause
                # était qu'AUCUN agent n'avait tourné : la cascade de providers avait
                # rendu la main en 5 s (Cerebras 402, Cohere 429, CLI non approuvé),
                # le plan était vide, et l'exécuteur n'a jamais été appelé. Le
                # diagnostic a coûté trois cycles avant qu'on regarde ailleurs.
                agents = pipeline_result.get("agents_used") or []
                reponse = (pipeline_result.get("response") or "").strip()
                if not agents or agents == ["planner"]:
                    raise RuntimeError(
                        f"Le pipeline s'est déclaré terminé mais AUCUN agent d'exécution "
                        f"n'a tourné (agents : {agents or 'aucun'}). Le plan était vide ou "
                        f"le Planner n'a rien produit — regarder la cascade de providers, "
                        f"pas les chemins de fichiers."
                    )
                raise RuntimeError(
                    f"Le pipeline s'est déclaré terminé mais la branche {branch_name} "
                    f"est identique à {orig_branch} : aucun fichier n'a été écrit dans "
                    f"{repo_root}, alors que {len(agents)} agent(s) ont tourné "
                    f"({', '.join(str(a) for a in agents[:4])}) et rendu "
                    f"{len(reponse)} caractères de réponse. Vérifier que les chemins "
                    f"visent bien le dépôt de travail et non le répertoire courant."
                )

            # [#T418] Écrire des fichiers ne prouve pas les avoir écrits au bon
            # endroit. Le 19/08, trois tâches d'affilée ont pris l'étiquette de
            # zone du board pour un chemin — « Zone : runtime/llm » a produit un
            # fichier nommé `runtime/llm` — et ont franchi la porte précédente
            # sans encombre : la branche différait bien de master.
            hors_convention = await asyncio.to_thread(
                _livrables_hors_convention, repo_root, orig_branch)
            if hors_convention:
                raise RuntimeError(
                    "Le pipeline s'est déclaré terminé mais les fichiers livrés "
                    "ne respectent pas l'arborescence du dépôt : "
                    + " | ".join(hors_convention[:3])
                )

            # [#T421] Verdicts conservés : ils alimentent le résumé de la tâche.
            verdict_tests: str | None = None
            verdict_frontend: str | None = None

            # [#T405] La preuve, pas l'avis : les tests livres doivent passer.
            fichiers_tests = await asyncio.to_thread(
                _fichiers_tests_modifies, repo_root, orig_branch)
            if fichiers_tests:
                fautifs = await asyncio.to_thread(
                    _tests_figent_un_chemin_machine, repo_root, fichiers_tests)
                if fautifs:
                    raise RuntimeError(
                        "Les tests livrés figent un chemin de machine "
                        f"({', '.join(fautifs[:3])}) : un `/home/deck/` ou un "
                        "`H:\\` en constante ne survit pas à la CI. "
                        "Résoudre les chemins depuis `__file__`."
                    )
                logger.info(
                    f"[DREAMER] [DreamCoder] Rejeu des tests livres par la tache "
                    f"{task_id} : {', '.join(fichiers_tests[:3])}")
                vert, compte_rendu = await asyncio.to_thread(
                    _rejouer_tests_livres, repo_root, fichiers_tests)
                if not vert:
                    raise RuntimeError(
                        "Le pipeline s'est déclaré terminé mais les tests livrés "
                        f"échouent ({', '.join(fichiers_tests[:3])}) : {compte_rendu}"
                    )
                verdict_tests = compte_rendu
                logger.info(f"[DREAMER] [DreamCoder] Tests livres : {compte_rendu}")

            fichiers_front = await asyncio.to_thread(
                _fichiers_frontend_modifies, repo_root, orig_branch)
            if fichiers_front:
                logger.info(
                    f"[DREAMER] [DreamCoder] Compilation du frontend pour la tache "
                    f"{task_id} ({len(fichiers_front)} fichier(s) TS/TSX)")
                vert_front, cr_front = await asyncio.to_thread(_verifier_frontend, repo_root)
                if not vert_front:
                    raise RuntimeError(
                        "Le pipeline s'est déclaré terminé mais le frontend ne "
                        f"compile pas : {cr_front}"
                    )
                verdict_frontend = cr_front
                logger.info(f"[DREAMER] [DreamCoder] Frontend : {cr_front}")

            # Générer le diff
            _, diff_out, _ = await asyncio.to_thread(_run_git, ["diff", "HEAD~1..HEAD"], repo_root)

            # [#T421] Résumé lisible : les mesures du moteur d'abord, la prose
            # de l'agent ensuite. Le transcript brut est conservé à côté — on
            # nettoie pour lire, on ne jette rien.
            transcript_brut = pipeline_result.get("response", "") or ""
            fichiers_livres, ajouts, retraits = await asyncio.to_thread(
                _fichiers_du_commit, repo_root, orig_branch)
            # [#T436] Un fichier écrit n'est pas un livrable. Mesuré le 21/08 :
            # rejeu #63 `completed` avec `resultat_recherche.txt` à la racine
            # au lieu de `docs/analyses/*.md`. On refuse avant le push — sinon
            # la PR ouvre sur du jetable et la tâche ne revient jamais.
            refus_livrable = _livrable_insuffisant(
                f"{task.get('title', '')} {task.get('description', '')}",
                fichiers_livres,
            )
            if refus_livrable:
                raise RuntimeError(
                    "Le pipeline s'est déclaré terminé mais le livrable est "
                    f"insuffisant : {refus_livrable}"
                )
            hors_sujet = _titre_incoherent_avec_diff(
                f"{task.get('title', '')} {task.get('description', '')}",
                fichiers_livres,
            )
            if hors_sujet:
                raise RuntimeError(
                    "Le pipeline s'est déclaré terminé mais le "
                    f"{hors_sujet}"
                )
            if _agent_affirme_rien_a_faire(transcript_brut) and not any(
                not _est_fichier_jetable(f) for f in fichiers_livres
            ):
                raise RuntimeError(
                    "Le pipeline s'est déclaré terminé mais l'agent affirme "
                    "qu'aucune modification n'était nécessaire : pas de PR."
                )
            resume_lisible = _resume_de_tache(
                transcript_brut,
                fichiers=fichiers_livres,
                ajouts=ajouts,
                retraits=retraits,
                verdict_tests=verdict_tests,
                verdict_frontend=verdict_frontend,
                provider=provider,
                gouverneur=governor_model,
                tokens=tokens_used,
                cout=cost,
            )

            # Sortir le travail du Deck : la branche est poussée vers origin dès
            # qu'elle porte un commit. Best effort — un refus (deploy key en
            # lecture seule) laisse la tâche 'completed' et la branche récoltable.
            push_info = await asyncio.to_thread(_pousser_branche_tache, repo_root, branch_name)
            result["push"] = push_info
            if push_info["pushed"]:
                logger.info(f"[DREAMER] [DreamCoder] Branche poussée sur origin : {branch_name}")
                # [#T432] La récolte manuelle laissait des branches orphelines
                # (task/70, task/84 le 21–22/08). Même jeton, même geste.
                pr_info = await asyncio.to_thread(
                    _ouvrir_pr_tache,
                    repo_root,
                    branch_name,
                    f"[auto] {task.get('title') or f'Tâche #{task_id}'}",
                    resume_lisible or f"Tâche autonome #{task_id}",
                )
                result["pr"] = pr_info
                if pr_info.get("ouverte"):
                    logger.info(
                        f"[DREAMER] [DreamCoder] PR {pr_info['raison']} : "
                        f"{pr_info.get('url') or branch_name}"
                    )
                else:
                    logger.warning(
                        f"[DREAMER] [DreamCoder] PR non ouverte ({pr_info['raison']}) : "
                        f"{branch_name} — {str(pr_info.get('detail') or '')[:160]}"
                    )
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
                        "summary": resume_lisible,
                        # Le transcript intégral reste disponible pour l'autopsie
                        # d'un cycle : nettoyer sert à lire, pas à effacer.
                        "transcript_brut": transcript_brut,
                        "timestamp": time.time(),
                        "tokens_used": tokens_used,
                        "cost_usd": cost,
                        "push": push_info,
                        "pr": result.get("pr"),
                    }, f, indent=2, ensure_ascii=False)

            # Mettre à jour la base
            await update_task_status(
                task_id,
                'completed',
                result_summary=resume_lisible or "Succès sans résumé",
                tokens_used=tokens_used
            )
            result["status"] = "success"
        else:
            err_msg = pipeline_result.get("error") or "Le pipeline s'est terminé sur un échec."
            raise RuntimeError(err_msg)

    except Exception as pipeline_err:
        logger.error(f"[DREAMER] [DreamCoder] Échec de l'exécution : {pipeline_err}")

        # [#T416] Une tâche qui meurt en vol a quand même dépensé. Sur la base de
        # prod du Deck au 19/08, les 38 tâches en échec portaient toutes
        # `tokens_used = 0` — et le trou était plus large que la colonne : quand
        # le pipeline LÈVE (timeout d'enveloppe, provider mort), on saute
        # par-dessus le `record_usage`, donc **le BudgetGuard ne voyait rien non
        # plus**. Le plafond quotidien était aveugle aux échecs : une série de
        # timeouts pouvait brûler le budget sans jamais l'entamer sur le papier.
        # On relit la consommation à la même source et on l'impute — une seule
        # fois, `consommation_imputee` garde du double comptage quand l'échec
        # survient APRÈS l'imputation du chemin nominal (porte de qualité).
        if not consommation_imputee:
            tokens_used, cost = await asyncio.to_thread(
                _consommation_de_la_tache, session_id, debut_tache)
            if tokens_used or cost:
                try:
                    await bg.record_usage(
                        provider=provider,
                        tokens=tokens_used,
                        cost=cost,
                        model=temp_config["planner_model"],
                        window_type="hourly" if provider == "gemini-free" else "daily",
                    )
                    consommation_imputee = True
                except Exception as _bu:
                    logger.warning(
                        f"[DREAMER] [DreamCoder] Consommation d'un échec non imputée "
                        f"au budget : {_bu}")
            result["tokens_used"] = tokens_used
            result["cost_usd"] = cost
        if tokens_used or cost:
            logger.info(
                f"[DREAMER] [DreamCoder] Échec de la tâche {task_id} après "
                f"{tokens_used} jetons / ${cost:.4f} — coût imputé, pas perdu."
            )

        # Cooldown du provider d'exécution (voir core/budget_guard.py::mark_provider_failed) —
        # évite qu'un provider cassé (ex: CLI Gemini/Claude non trustée) soit
        # re-sélectionné sur la tâche suivante du même cycle de drainage.
        # [#T395] …mais SEULEMENT si la panne lui est imputable. Un code qui ne
        # compile pas ou une branche vide sont des échecs de la TÂCHE : punir le
        # provider prive le cycle d'un fournisseur sain pendant 30 minutes.
        # Mesuré le 18/08 : 3 échecs de qualité en 1,4 s → 3 providers en
        # cooldown → cycle arrêté sur « aucun provider libre », 4 tâches jamais
        # tentées.
        from core.budget_guard import mark_provider_failed

        verdict = _verdict_apres_echec(str(pipeline_err), task.get("retries", 0))
        if verdict["cause"] == "infra":
            logger.error(
                f"[DREAMER] [DreamCoder] Panne d'infrastructure sur la tâche {task_id} : "
                f"aucun provider joignable. La tâche repart en attente, "
                f"sans consommer de tentative.")
        elif verdict["cause"] == "provider":
            mark_provider_failed(provider)
            logger.warning(
                f"[DREAMER] [DreamCoder] Échec imputable au provider '{provider}' "
                f"sur la tâche {task_id} : tentative non brûlée, tâche remise "
                f"en attente pour un autre modèle.")
        elif verdict.get("cooldown"):
            # 401 / timeout LLM : on écarte le modèle 30 min, mais la tâche
            # a bien consommé une tentative — contrairement au 429.
            mark_provider_failed(provider)
            logger.warning(
                f"[DREAMER] [DreamCoder] Provider '{provider}' mis en cooldown "
                f"sur la tâche {task_id} ; tentative comptée (pas un 429).")
        else:
            logger.info(
                f"[DREAMER] [DreamCoder] Échec de la tâche, pas du provider "
                f"('{provider}' conservé pour la suite du cycle)."
            )

        # Sort de la branche : conserver / supprimer / rollback (cf.
        # `_sort_de_la_branche`, qui porte le raisonnement et ses tests).
        sort = _sort_de_la_branche(str(pipeline_err), verdict["status"])
        conserver = sort == SORT_CONSERVER
        if sort == SORT_SUPPRIMER:
            await asyncio.to_thread(
                _run_git, ["reset", "--hard", "HEAD"], repo_root)
            await asyncio.to_thread(
                _run_git, ["checkout", "-f", orig_branch], repo_root)
            await asyncio.to_thread(
                _run_git, ["branch", "-D", branch_name], repo_root)
            logger.info(
                f"[DREAMER] [DreamCoder] Branche {branch_name} supprimée : "
                f"tâche abandonnée après {verdict['retries']} échecs, "
                f"aucun rejeu ne la reprendra."
            )
        elif conserver:
            await asyncio.to_thread(
                _run_git, ["reset", "--hard", "HEAD"], repo_root)
            await asyncio.to_thread(
                _run_git, ["checkout", "-f", orig_branch], repo_root)
            push_info = await asyncio.to_thread(
                _pousser_branche_tache, repo_root, branch_name)
            logger.info(
                f"[DREAMER] [DreamCoder] Branche {branch_name} conservée pour "
                f"correction (push={push_info.get('pushed')})."
            )
        else:
            await asyncio.to_thread(git_rollback_checkpoint, repo_root)
            await asyncio.to_thread(_run_git, ["checkout", orig_branch], repo_root)
            await asyncio.to_thread(_run_git, ["branch", "-D", branch_name], repo_root)

        if verdict["action"] == "liberer":
            # [#T398] / [#T432] `liberer_tache` remet en 'pending' SANS toucher
            # aux compteurs : infra (clés absentes) comme provider (429) —
            # la tâche n'a pas démérité. Un autre modèle la reprendra.
            from core.backlog_db import liberer_tache
            await liberer_tache(task_id)
            result["status"] = verdict["status"]
            result["error"] = str(pipeline_err)
        else:
            if verdict["status"] == "abandoned":
                logger.warning(
                    f"[DREAMER] [DreamCoder] Tâche {task_id} abandonnée après "
                    f"{verdict['retries']} échecs."
                )

            # [#T416] `tokens_used` manquait ici : la ligne gardait 0 quoi qu'il
            # arrive. Un échec à 1,1 M de jetons se lisait « gratuit ».
            # [#T432] `scheduled_at` pose le délai avant rejeu d'un `failed`.
            extra: dict[str, Any] = {}
            if verdict["scheduled_at"] is not None:
                extra["scheduled_at"] = verdict["scheduled_at"]
            if conserver:
                extra["git_branch"] = branch_name
            await update_task_status(
                task_id,
                verdict["status"],
                error_message=str(pipeline_err),
                retries=verdict["retries"],
                tokens_used=tokens_used,
                **extra,
            )
            result["status"] = verdict["status"]
            result["error"] = str(pipeline_err)

    finally:
        # Retourner sur la branche d'origine
        await asyncio.to_thread(_run_git, ["checkout", orig_branch], repo_root)
        # [#T388] Rendre la racine de résolution d'avant la tâche : le serveur
        # continue de servir le vocal et le chat entre deux tâches de nuit.
        reinitialiser_racine_travail(jeton_racine)
        # [#T426] Le plafond de tours est propre à CETTE tâche : le rendre,
        # sinon une tâche de code exécutée ensuite dans le même contexte
        # hériterait du budget élargi d'une analyse.
        if jeton_tours is not None:
            from agents.executor import reinitialiser_tours_react_session
            reinitialiser_tours_react_session(jeton_tours)

    return result


# [#T392] Branche de référence d'un slot : celle dont part chaque tâche et
# contre laquelle son diff est calculé. Le drainage la suppose toujours
# atteinte — d'où le retour forcé en tête de `_preparer_slot`.
BASE_SLOT = "master"


# [#T424] Une tâche d'ANALYSE n'a pas de livrable en code — et le harnais, lui,
# exige une branche non vide pour valider. Les entrées de board « **À ANALYSER** »
# étaient donc condamnées d'avance : l'agent réfléchissait, répondait en texte, et
# le cycle constatait une branche vide. Mesuré le 21/08 : les tâches 49, 50 et 51
# (T421, T422, T423) mortes ainsi, alors que le raisonnement avait bien eu lieu.
#
# On ne relâche PAS la porte du harnais — une branche vide reste un échec, c'est
# ce qui empêche le moteur de mentir. On donne un livrable à l'analyse : un
# rapport Markdown écrit dans le dépôt. Le travail devient relisible en PR, et la
# branche cesse d'être vide parce qu'elle contient quelque chose de réel.
_MARQUEURS_ANALYSE = ("a analyser", "a etudier", "etude de faisabilite", "audit de")


def _sans_accents_minuscule(texte: str) -> str:
    """Comparaison insensible à la casse ET aux accents (piège #T383)."""
    import unicodedata
    decompose = unicodedata.normalize("NFD", (texte or "").lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _est_une_tache_d_analyse(texte: str) -> bool:
    """Tâche dont le livrable attendu est un raisonnement, pas du code."""
    normalise = _sans_accents_minuscule(texte)
    return any(marqueur in normalise for marqueur in _MARQUEURS_ANALYSE)


# Fichiers que le harnais a déjà vu passer pour « un fichier écrit » (#T436).
# Un dump grep, un extracteur one-shot ou `resultat_recherche.txt` à la racine
# ne sont pas un livrable : la tâche doit revenir en `failed` / `pending`.
_NOMS_JTABLES = {
    "resultat_recherche.txt",
    "resultats_recherche.txt",
    "search_results.txt",
    "plan_correctif.json",
    "plan_action.json",
}
_PREFIXES_NOMS_JTABLES = ("search_", "extract_", "inspect_", "grep_", "_tmp")


def _est_fichier_jetable(chemin: str) -> bool:
    """True si le chemin ne peut pas compter comme livrable de tâche."""
    c = (chemin or "").replace("\\", "/").lstrip("./")
    if not c:
        return True
    nom = c.rsplit("/", 1)[-1].lower()
    if nom in _NOMS_JTABLES:
        return True
    if c.startswith("tmp/") or "/tmp/" in f"/{c}":
        return True
    if nom.startswith(_PREFIXES_NOMS_JTABLES):
        # Un `search_ha.py` sous core/ est du produit. Le jetable, c'est
        # l'extracteur one-shot posé à la racine ou dans scripts/.
        if "/" not in c or c.startswith(("scripts/", "tmp/")):
            return True
    # Dump texte / JSON / script jetable posé à la racine du dépôt (pas sous docs/).
    if "/" not in c and nom.endswith((".txt", ".json", ".py", ".sh")):
        return True
    if "/" not in c and nom.endswith(".md") and nom.startswith(("plan_", "rapport_")):
        return True
    return False


def _livrable_insuffisant(texte: str, fichiers: list[str]) -> str | None:
    """
    None si le diff contient un livrable acceptable, sinon la raison du refus.

    Analyse → au moins un Markdown sous `docs/analyses/`.
    Code → au moins un fichier non jetable.
    """
    chemins = [(f or "").replace("\\", "/").lstrip("./") for f in fichiers if f]
    if not chemins:
        return "aucun fichier dans le commit"
    utiles = [c for c in chemins if not _est_fichier_jetable(c)]
    if _est_une_tache_d_analyse(texte):
        # [#T479] `startswith("docs/analyses/")` imposait la convention du
        # dépôt moteur, où ce dossier est à la racine. Le Tab5 n'a pas de
        # `docs/analyses/` mais il a un `Tab5/` et un `docs/` : l'agent y a
        # rangé son rapport sous `Tab5/docs/analyses/`, un chemin cohérent
        # avec ce qu'il voyait. La porte l'a refusé pour sa POSITION, pas pour
        # son contenu. Mesuré deux fois le 25/08, sur deux hôtes et deux
        # modèles — tâche 122 (Deck) et tâche 78 (PC) : ~1,94 M de jetons.
        # Ce qui compte est la convention `docs/analyses/…md`, pas la
        # profondeur à laquelle le dépôt la place.
        rapports = [
            c for c in utiles
            if ("docs/analyses/" in f"/{c}") and c.endswith(".md")
        ]
        if not rapports:
            apercu = ", ".join(chemins[:5])
            return (
                "tâche d'analyse sans rapport dans docs/analyses/*.md "
                f"(livré : {apercu})"
            )
        return None
    if not utiles:
        apercu = ", ".join(chemins[:5])
        return f"seulement des fichiers jetables ({apercu})"
    return None


_RE_FICHIER_CITE_DANS_TITRE = re.compile(
    r"(?:agents|api|core|scripts|tests|services|tools)/[\w./-]+\.py",
    re.IGNORECASE,
)
_RE_ID_TACHE_BOARD = re.compile(r"#T(\d+)\b", re.IGNORECASE)


def _titre_incoherent_avec_diff(titre: str, fichiers: list[str]) -> str | None:
    """Le titre nomme un fichier précis que le diff n'a pas touché → PR hors-sujet."""
    cites = [m.group(0).replace("\\", "/") for m in _RE_FICHIER_CITE_DANS_TITRE.finditer(titre or "")]
    if not cites:
        return None
    livres = {(f or "").replace("\\", "/").lstrip("./") for f in fichiers if f}
    manquants = [c for c in cites if c not in livres]
    if not manquants:
        return None
    apercu = ", ".join(sorted(livres)[:5]) or "(vide)"
    return (
        f"titre de la tâche cite {', '.join(manquants)} "
        f"mais le diff ne les contient pas (livré : {apercu})"
    )


def _agent_affirme_rien_a_faire(transcript: str) -> bool:
    """True si l'agent conclut « rien à modifier » — souvent suivi d'un commit poubelle."""
    t = _sans_accents_minuscule(transcript or "")
    return any(
        p in t
        for p in (
            "aucune modification necessaire",
            "aucune modification n'est necessaire",
            "je ne touche a rien",
            "tache deja accomplie",
            "rien a modifier",
            "aucune action requise",
            "critere de succes pleinement atteint",
        )
    )


def _id_tache_board_deja_livree(titre: str, board: str) -> str | None:
    """Si le titre cite #T414 déjà ✅ sur le board, renvoyer 'T414'."""
    for num in _RE_ID_TACHE_BOARD.findall(titre or ""):
        if re.search(rf"✅[^\n]*#T{num}\b", board or "", re.IGNORECASE):
            return f"T{num}"
    return None


def _nom_de_rapport(task_id: int, titre: str) -> str:
    """Chemin RELATIF du rapport d'analyse, stable et lisible."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", _sans_accents_minuscule(titre))
    slug = slug.strip("_")[:48].strip("_")
    return f"docs/analyses/tache_{task_id}_{slug or 'analyse'}.md"


def consigne_de_livrable(task: dict, repo_root: str) -> str:
    """
    Consigne ajoutée au prompt quand la tâche est une analyse.

    Rend explicite ce que le harnais exige de toute façon : un fichier écrit.
    Sans elle, l'agent rend son analyse dans son texte de réponse — perdue à la
    seconde où le cycle se termine, et la branche reste vide.
    """
    texte = f"{task.get('title', "")} {task.get('description', "")}"
    if not _est_une_tache_d_analyse(texte):
        return ""
    chemin = _nom_de_rapport(int(task.get("id", 0) or 0), task.get("title", ""))
    chemin_absolu = os.path.join(repo_root, chemin.replace("/", os.sep))
    return (
        "\n\n[LIVRABLE ATTENDU — TÂCHE D'ANALYSE]\n"
        "Cette tâche ne demande PAS de modifier du code. Ton livrable est un "
        "rapport Markdown, que tu dois écrire avec `write_file` dans :\n"
        f"    {chemin_absolu}\n"
        "Il doit contenir, dans cet ordre : (1) ce que tu as CONSTATÉ, avec les "
        "chemins de fichiers et les valeurs réelles que tu as lues ; (2) les options "
        "envisageables et leurs contreparties ; (3) une recommandation avec son coût "
        "estimé ; (4) ce que tu n'as PAS pu vérifier. N'invente aucun chiffre ni "
        "aucun chemin : ce que tu n'as pas lu, écris que tu ne l'as pas lu. "
        "Une analyse qui ne se termine pas par un fichier écrit compte pour un échec."
    )

def assainir_slot(repo_root: str) -> bool:
    """
    Rend un clone de slot à un état neuf : arbre propre, aucun stash en attente.

    [#T435] Le nettoyage posé en préparation de CYCLE (#T434) ne suffisait pas :
    il tourne une fois par cycle, alors que `git_safety` refabrique l'état à
    chaque TÂCHE. Mesuré le 21/08 à 10h27 sur `dc3` — le slot nettoyé à 10h27:2x
    était de nouveau en conflit à 10h27:38, avec dans le journal :

        [GIT SAFETY] Échec du stash des modifs utilisateur :
        scripts/recolte_dreamcoder.py: needs merge

    Un `stash` d'agent (`agent_checkpoint_…`) laissé par une tâche précédente est
    réappliqué sur un master qui a bougé, remet des marqueurs de conflit dans un
    fichier Python, et la porte #T394 refuse alors TOUTES les tâches suivantes —
    y compris celles qui n'ont jamais touché ce fichier.

    D'où la purge des stashs : dans un clone de slot il n'y a pas d'utilisateur à
    protéger. Le travail retenu d'une tâche vit sur sa branche ; un stash n'y a
    aucune valeur, et il en a une négative dès que la base bouge.

    :return: True si quelque chose a été nettoyé.
    """
    from tools.git_safety import _run_git

    nettoye = False

    code_sale, sale, _ = _run_git(["status", "--porcelain"], repo_root)
    if code_sale == 0 and (sale or "").strip():
        lignes = [l for l in (sale or "").splitlines() if l.strip()]
        logger.warning(
            f"[DREAMER] [DreamCoder] Slot {repo_root} sale ({len(lignes)} entrée(s)) : "
            f"remise à zéro. Exemples : {', '.join(l.strip()[:50] for l in lignes[:3])}"
        )
        # Une fusion ou un stash interrompu laisse un index en conflit ; l'abandon
        # échoue sans opération en cours, c'est sans conséquence.
        _run_git(["merge", "--abort"], repo_root)
        _run_git(["reset", "--hard"], repo_root)
        _run_git(["clean", "-fd"], repo_root)
        nettoye = True

    code_stash, stashs, _ = _run_git(["stash", "list"], repo_root)
    if code_stash == 0 and (stashs or "").strip():
        n = len([l for l in (stashs or "").splitlines() if l.strip()])
        logger.warning(
            f"[DREAMER] [DreamCoder] Slot {repo_root} : {n} stash(s) en attente "
            f"purgé(s). Un clone de slot n'a pas d'utilisateur à protéger, et un "
            f"stash réappliqué sur une base qui a bougé casse les tâches suivantes."
        )
        _run_git(["stash", "clear"], repo_root)
        nettoye = True

    return nettoye


async def _preparer_slot(repo_root: str, rafraichir: bool) -> str:
    """
    [#T387] Met un clone de slot en état de travailler et rend sa branche d'origine.

    `--ff-only` : un clone qui aurait divergé (branche `task/*` locale non
    mergée) n'est jamais écrasé — le pull échoue proprement et le slot part de
    son état local. Le pull n'a lieu que sur un clone explicitement configuré :
    jamais de `git pull` automatique sur le dépôt de dev sous les pieds d'Axel.
    """
    from tools.git_safety import _run_git

    # [#T434] Un slot doit repartir PROPRE. Il est jetable par construction : rien
    # de ce qu'une tâche y laisse n'a de valeur une fois la tâche finie — son
    # travail retenu vit sur sa branche, et son travail non retenu n'a pas à
    # survivre.
    #
    # Mesuré le 21/08 sur `dc3` : un `stash` réappliqué par `git_safety` sur un
    # master qui avait bougé a laissé des marqueurs `<<<<<<< Updated upstream`
    # dans `scripts/recolte_dreamcoder.py`. Le fichier ne parsait plus. Les TROIS
    # tâches suivantes — dont deux analyses qui n'y touchaient pas — ont été
    # refusées par la porte #T394 (« le code produit ne parse pas »), sur les
    # dégâts d'une autre. Un slot sale contamine tout ce qui passe après lui.
    #
    # `reset --hard` + `clean -fd` : c'est exactement ce que le déploiement
    # s'interdit sur la prod (bases .db non suivies), et exactement ce qu'il faut
    # ici. Le garde-fou #T419 a déjà refusé de travailler ailleurs que dans un
    # clone dédié — on ne nettoie donc jamais un dépôt habité.
    await asyncio.to_thread(assainir_slot, repo_root)

    # [#T392] Ramener le slot sur sa base AVANT tout. Il prenait sa branche
    # courante pour base, quelle qu'elle soit : un slot resté sur une `task/*`
    # d'un cycle précédent — parce que le cycle a été tué, ou qu'un humain a
    # inspecté le clone — faisait partir toutes les tâches suivantes de ce
    # travail-là. Mesuré le 18/08 : « la branche task/17_… est identique à
    # task/13_… », un diff calculé contre la mauvaise référence.
    # Une branche de travail non mergée n'est jamais perdue pour autant : elle
    # reste dans le clone, `checkout` ne fait que la quitter.
    code, courante, _ = await asyncio.to_thread(
        _run_git, ["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    courante = courante.strip() if (code == 0 and courante) else ""
    if courante != BASE_SLOT:
        code_base, _, err_base = await asyncio.to_thread(
            _run_git, ["checkout", BASE_SLOT], repo_root)
        if code_base == 0:
            logger.warning(
                f"[DREAMER] [DreamCoder] Slot {repo_root} était resté sur "
                f"'{courante or '(inconnue)'}' — ramené sur '{BASE_SLOT}'."
            )
        else:
            logger.error(
                f"[DREAMER] [DreamCoder] Slot {repo_root} bloqué sur "
                f"'{courante}' : {(err_base or '').strip()[:140]}. Les tâches de "
                f"ce slot partiront d'une base inattendue."
            )

    if rafraichir:
        code, sortie, err = await asyncio.to_thread(_run_git, ["pull", "--ff-only"], repo_root)
        if code == 0:
            logger.info(f"[DREAMER] [DreamCoder] Slot rafraîchi ({repo_root}) : {sortie.strip()[:100]}")
        else:
            logger.warning(
                f"[DREAMER] [DreamCoder] pull --ff-only impossible sur {repo_root} "
                f"({(err or sortie).strip()[:140]}) — slot sur son état local."
            )

    code, branche, _ = await asyncio.to_thread(
        _run_git, ["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    return branche.strip() if (code == 0 and branche) else BASE_SLOT


_RE_ZONE_BOARD = re.compile(r"^\[?T\d+\]?\s*([\w\-/.]+)")

# Borne du nombre de tâches qu'un slot peut mettre de suite de côté dans un
# même drainage (réservées puis libérées faute de provider d'après
# l'historique). Si tout le backlog restant est bloqué, on arrête le slot
# plutôt que de le laisser tourner jusqu'au deadline.
_MAX_TACHES_BLOQUEES_PAR_SLOT = 25


def _domaine_de_tache(task: dict) -> str:
    """
    Domaine de routage d'une tâche du backlog — la clé de l'historique de
    réussite (type de tâche × provider) : c'est lui qui alimente et qui lit
    `model_elo_scores`.

    - Tâche du board (« [T381] archi/ihm-v2 - … ») → la zone du board ;
    - TODO de code (« TODO dans core/x.py — … ») → 'code_generation', pour
      concentrer l'historique au lieu de l'émietter par fichier ;
    - Sinon → 'general'.
    """
    titre = (task.get("title") or "").strip()
    m = _RE_ZONE_BOARD.match(titre)
    if m:
        return m.group(1).lower()
    if titre.startswith("TODO "):
        return "code_generation"
    return "general"


# [#T406] `dreamcoder_max_cycle_minutes` ne bornait QUE l'intervalle entre deux
# taches : `if time.time() >= deadline` est teste en tete de boucle du
# travailleur, jamais pendant. Une tache longue depasse donc la fenetre sans
# aucune limite.
#
# Mesure le 18/08 : fenetre reglee a 22 minutes, cycle encore en cours a 30, sur
# une seule tache (#42) qui enchainait les rondes de revue locale. Il a fallu
# l'arreter a la main. Sur un cycle de nuit, personne n'est la pour le faire.
_PLANCHER_TACHE_S = 60.0


async def _executer_tache_bornee(fabrique_coroutine, temps_restant_s: float,
                                 task_id: int):
    """
    Execute la tache sans jamais deborder la fenetre du cycle.

    Rend le resultat de la tache, ou None si la fenetre l'a interrompue.
    Un plancher d'une minute evite d'annuler une tache a peine commencee quand
    la fenetre est deja presque ecoulee.
    """
    borne = max(_PLANCHER_TACHE_S, temps_restant_s)
    try:
        return await asyncio.wait_for(fabrique_coroutine(), timeout=borne)
    except asyncio.TimeoutError:
        logger.warning(
            f"[DREAMER] [DreamCoder] Tache {task_id} interrompue : la fenetre du "
            f"cycle est ecoulee ({borne:.0f} s). La tache repart en attente."
        )
        return None


async def _choisir_provider_tache(domaine: str, bg, sains: set[str] | None,
                                  en_vol: set[str]) -> tuple[str | None, str, dict]:
    """
    Choisit le provider d'UNE tâche : historique de réussite du domaine
    d'abord (`classer_providers_pour_tache`), cascade de coût en secours.

    :return: (provider ou None, raison du choix, exclusions par historique)
    """
    from core.llm.ajean_adapter import (
        PROVIDERS_LOCAUX_HORS_ELECTION,
        PROVIDERS_WORKFLOW,
        ajean_http_ok,
        premier_eligible as _premier_ajean,
        providers_workflow_actifs,
    )
    from core.provider_scorer import classer_providers_pour_tache
    from core.sante_providers import MODELE_PAR_PROVIDER

    # Hors sonde (sains=None), le payant reste hors de l'élection par
    # historique : il est le dernier recours de la cascade de coût, pas un
    # candidat libre. Sous sonde, il n'est de toute façon pas dans `sains`.
    base = sorted(sains) if sains is not None else sorted(
        p for p in MODELE_PAR_PROVIDER if p != "anthropic-claude-haiku")
    # 23/08 : Ollama / LM Studio ne sont plus élus, même sains.
    base = [p for p in base if p not in PROVIDERS_LOCAUX_HORS_ELECTION]
    # 06/10 : un AJEAN retiré du réglage (providers_sondes) n'est plus candidat,
    # même hors sonde où `base` vient du registre.
    ajean_actifs = providers_workflow_actifs()
    base = [p for p in base if p not in PROVIDERS_WORKFLOW or p in ajean_actifs]
    # [#T403] Le cooldown vaut AUSSI pour l'election par historique. Sans ce
    # filtre, un provider qui vient de rendre un 429 est reelu a la tache
    # suivante : seule la cascade de cout consultait le cooldown, et elle n'est
    # que le secours. Mesure le 18/08 : 429 sur gemini-free a la tache #43,
    # gemini-free reattribue a la #44 dans la foulee, cycle mort avec quatre
    # providers sains disponibles.
    from core.budget_guard import provider_en_cooldown
    candidats = [p for p in base
                 if p not in en_vol and not provider_en_cooldown(p)]

    # [#T409] Le QUOTA aussi. #T404 a corrige le plafond Gemini (20 requetes par
    # jour) mais seulement dans la cascade de cout ; l'election par historique
    # l'ignorait. Mesure le 19/08 a 22h18 : les deux taches du cycle recoivent
    # gemini-free, quota du jour deja consomme. Meme oubli que #T403.
    epuises = {}
    for candidat in list(candidats):
        if await bg.quota_gratuit_epuise(candidat):
            epuises[candidat] = "quota gratuit epuise"
    if epuises:
        candidats = [p for p in candidats if p not in epuises]
        logger.info(
            f"[DREAMER] [DreamCoder] Ecartes pour quota : {', '.join(sorted(epuises))}")

    # 23/08 : AJEAN vivant = executor des workflows, avant l'Elo.
    # Sous sonde : s'il est dans `sains`, on le prend. S'il en est absent
    # (sonde PRET ratée sur un Qwen thinking à content vide), GET /v1/models
    # tranche : le Deck doit quand même prendre le Qwen du PC — sauf si le
    # réglage l'a retiré (06/10, `providers_workflow_actifs`).
    if sains is not None:
        ajean = _premier_ajean(candidats)
        if ajean:
            return ajean, "ajean_local", epuises
        for pid in ajean_actifs:
            if pid in en_vol or provider_en_cooldown(pid):
                continue
            if await ajean_http_ok(pid):
                return pid, "ajean_local_lan", epuises

    ordonnes, exclusions = classer_providers_pour_tache(candidats, domaine)
    exclusions = {**epuises, **exclusions}
    if ordonnes:
        return ordonnes[0], "historique_reussite", exclusions
    if candidats and len(exclusions) == len(candidats):
        # Tous les candidats sont écartés PAR L'HISTORIQUE : rejouer la
        # cascade de coût n'y changerait rien, elle ignore ce motif.
        return None, "historique:aucun_candidat", exclusions
    provider = await bg.get_available_provider(sains=sains, exclus=en_vol | set(exclusions))
    return provider, "cascade_cout", exclusions


async def _run_dreamcoder_drain_loop(pa_config: dict) -> dict[str, Any]:
    """
    Draine le backlog en PARALLÈLE sur N slots isolés, borné par
    `dreamcoder_max_cycle_minutes`. Ne contourne JAMAIS la porte d'approbation
    Git humaine (PUT /api/backlog/tasks/{id}) : chaque tâche réussie reste sur
    sa branche 'task/*' en attente de revue.

    [#T387] Trois garde-fous portent le parallélisme :
      1. un CLONE par slot — deux checkout concurrents dans un même dépôt se
         marchent dessus (`_slots_dreamcoder`) ;
      2. une RÉSERVATION atomique de la tâche — sinon les N travailleurs lisent
         la même ligne 'pending' et jouent la même tâche (`reserver_prochaine_tache`) ;
      3. un PROVIDER distinct par tâche en vol — sinon un même quota est
         consommé N fois plus vite, et ses 429 arrivent N fois plus tôt.

    Le routage est de plus CONSCIENT DE LA TÂCHE : la tâche est réservée
    d'abord, puis le provider est choisi par taux de réussite historique sur
    son domaine (`_choisir_provider_tache`), la cascade de coût restant le
    secours. Chaque verdict de fin de tâche alimente cet historique (Elo).
    """
    from core.backlog_db import liberer_tache, reserver_prochaine_tache, update_task_status
    from core.budget_guard import BudgetGuard
    from core.sante_providers import candidats_sondes, providers_sains, sonder_providers

    max_minutes = pa_config.get("dreamcoder_max_cycle_minutes", 12)
    deadline = time.time() + max_minutes * 60
    bg = BudgetGuard()

    results: dict[str, Any] = {
        "processed": 0, "success": 0, "failed": 0, "abandoned": 0, "paused": 0,
        "infra_indisponible": 0,
        "interrompues_fenetre": 0,
        "total_cost_usd": 0.0, "total_tokens": 0,
        "branches_awaiting_review": [], "branches_pushed": [],
        "tasks": [], "stopped_reason": None,
        "slots": [], "providers_sains": [], "providers_ecartes": {},
        "providers_ecartes_historique": {},
    }

    slots = _slots_dreamcoder(pa_config)
    # [#T420] Même source que les slots : sans cela un chemin déclaré dans `.env`
    # aurait bien donné un clone, mais `_preparer_slot` ne l'aurait pas rafraîchi.
    clone_dedie = bool(_slots_configures(pa_config))
    results["slots"] = slots

    # [#T419] Pas de clone dédié, pas de drainage. Le refus vient AVANT
    # `_preparer_slot`, qui commence par un `git checkout master` : sur un dépôt
    # où quelqu'un travaille, ce seul geste arrache sa branche courante.
    # Le repli historique sur `_ENGINE_ROOT` supposait un poste de dev où le
    # dossier du moteur est un vrai clone — c'est vrai, et c'est justement le
    # problème : les 18–19/08, le cycle y a commité et poussé 12 branches que
    # personne ne pouvait récolter. Ce chemin reste ouvert, mais il se déclare.
    en_place = _slots_dans_le_depot_courant(slots)
    if en_place and not pa_config.get(CLE_DEPOT_COURANT, False):
        logger.error(
            "[DREAMER] [DreamCoder] Cycle annulé : aucun clone dédié configuré — "
            f"le drainage viserait le dépôt du moteur lui-même ({en_place[0]}). "
            "Renseigner `persistent_agents.dreamcoder_repo_paths` dans config.json, "
            f"ou assumer explicitement le risque avec `persistent_agents.{CLE_DEPOT_COURANT}: true`."
        )
        results["stopped_reason"] = "pas_de_clone_dedie"
        results["slots_en_place"] = en_place
        return results

    # [#T478] Le droit d'ouvrir une PR se constate AVANT de dépenser, pas après.
    # Sans `MOTEUR_GITHUB_PUSH_TOKEN`, `_ouvrir_pr_tache` rend `jeton_absent` —
    # mais tout au bout du cycle, une fois les jetons consommés, et dans un
    # `logger.warning` noyé parmi les autres. Le push, lui, réussit quand même
    # via le gestionnaire d'identifiants du poste : on obtient donc des branches
    # sur origin et **aucune PR en face**, sans que rien ne l'annonce.
    # Mesuré le 24/08 : le Deck avait le jeton, le PC non. 14 branches Tab5,
    # zéro PR, et le seul indice était à la fin d'un journal de tâche.
    results["peut_ouvrir_pr"] = bool(
        (os.environ.get("MOTEUR_GITHUB_PUSH_TOKEN") or "").strip())
    if not results["peut_ouvrir_pr"]:
        from core.env_bootstrap import missing_key_message
        logger.warning(
            "[DREAMER] [DreamCoder] Ce cycle POUSSERA ses branches mais "
            "n'ouvrira AUCUNE PR : %s. Les livrables resteront sur origin, à "
            "récolter à la main (`scripts/recolte_dreamcoder.py`).",
            missing_key_message("MOTEUR_GITHUB_PUSH_TOKEN"),
        )

    branches_origine = await asyncio.gather(
        *[_preparer_slot(s, clone_dedie) for s in slots])

    # ── Sonde de santé : ce qui répond est constaté, pas décrété ──
    # « La clé est présente » ne veut pas dire « le provider peut inférer » :
    # cerebras-free rend 402, claude-cli-abo rend « Credit balance is too low »
    # comme s'il s'agissait d'une réponse normale. Sans cette sonde, la rotation
    # les élit et leur confie des tâches qu'ils perdent.
    sains: set[str] | None = None
    if pa_config.get("dreamcoder_sonde_sante", True):
        # Le payant reste hors sonde : il est le dernier recours de la cascade,
        # et le sonder coûterait de l'argent à chaque cycle pour rien.
        # [#20/08] La liste est un réglage d'exploitation (config.json →
        # routing_policy.providers_sondes) : on retire un hôte mort sans code.
        candidats = candidats_sondes()
        detail = await sonder_providers(candidats)
        sains = providers_sains(detail)
        results["providers_sains"] = sorted(sains)
        results["providers_ecartes"] = {
            p: r["motif"] for p, r in detail.items() if not r["sain"]}
        if not sains:
            # Mieux vaut ne rien tenter que brûler un `retries` sur chaque tâche
            # du backlog parce que le réseau est tombé.
            logger.error("[DREAMER] [DreamCoder] Aucun provider ne répond — cycle annulé.")
            results["stopped_reason"] = "aucun_provider_sain"
            return results

    # Un travailleur de plus que de providers sains ne ferait qu'attendre.
    nb_travailleurs = min(len(slots), len(sains)) if sains else len(slots)
    logger.info(
        f"[DREAMER] [DreamCoder] Drainage sur {nb_travailleurs} slot(s) parallèle(s) "
        f"— providers sains : {', '.join(results['providers_sains']) or 'non sondés'}"
    )

    # `en_vol` = providers actuellement attribués à une tâche en cours. Le verrou
    # couvre à la fois la sélection et l'agrégation : tout tient dans une seule
    # boucle d'événements, donc un asyncio.Lock suffit.
    en_vol: set[str] = set()
    verrou = asyncio.Lock()
    ecartes_historique: dict[str, str] = {}

    async def _travailleur(idx: int, repo_root: str, orig_branch: str) -> str:
        """Pioche et exécute des tâches jusqu'à épuisement du temps ou du backlog."""
        # Tâches réservées puis LIBÉRÉES parce que l'historique écartait tous
        # les providers pour leur domaine : elles sont exclues des réservations
        # suivantes du slot, sinon la pioche reprendrait la même en boucle.
        bloquees: set[int] = set()
        while True:
            if time.time() >= deadline:
                return "max_cycle_minutes_reached"

            # Réserver la tâche D'ABORD, choisir le provider ENSUITE : le
            # routage se fait par historique (type de tâche × provider), donc
            # il faut connaître la tâche avant d'élire le modèle. L'ordre
            # inverse (provider puis tâche) ne permettait que la cascade de
            # coût, aveugle au contenu du backlog.
            task = await reserver_prochaine_tache(exclure_ids=bloquees)
            if not task:
                # Plus rien de jouable : soit le backlog est vide, soit tout
                # ce qui reste est bloqué par l'historique sur ce slot.
                return "aucun_provider_pour_domaine" if bloquees else "backlog_empty"

            # #T414 a été rejouée le 23/08 alors que le board la marquait ✅.
            # On abandonne sans brûler de jeton : git > backlog stale.
            try:
                board_path = os.path.join(_ENGINE_ROOT, "docs", "travail", "TACHES.md")
                with open(board_path, encoding="utf-8") as fh:
                    board_txt = fh.read()
            except OSError:
                board_txt = ""
            deja = _id_tache_board_deja_livree(
                f"{task.get('title', '')} {task.get('description', '')}", board_txt
            )
            if deja:
                logger.warning(
                    "[DREAMER] [DreamCoder] Tâche %s abandonnée : %s déjà ✅ sur le board.",
                    task["id"], deja,
                )
                await update_task_status(
                    task["id"],
                    "abandoned",
                    error_message=f"{deja} est déjà ✅ sur le board : pas de second cycle.",
                )
                async with verrou:
                    results["processed"] += 1
                    results["abandoned"] += 1
                    results["tasks"].append({
                        "id": task["id"],
                        "status": "abandoned",
                        "error": f"{deja} déjà livré",
                    })
                continue

            domaine = _domaine_de_tache(task)
            async with verrou:
                provider, raison, exclusions = await _choisir_provider_tache(
                    domaine, bg, sains, set(en_vol))
                if provider:
                    en_vol.add(provider)
                ecartes_historique.update(exclusions)

            if not provider:
                # La tâche repart en attente : faute de provider, elle ne doit
                # ni brûler un `retries`, ni rester bloquée en 'running'.
                await liberer_tache(task["id"])
                if raison.startswith("historique"):
                    # Ce n'est PAS la fin du slot : la tâche suivante du
                    # backlog vise peut-être un domaine où un provider peut
                    # jouer. La tâche bloquée est mise de côté pour le reste
                    # du drainage — la ré-réserverait sans fin sinon.
                    bloquees.add(task["id"])
                    logger.warning(
                        f"[DREAMER] [DreamCoder] Slot {idx} : tâche {task['id']} "
                        f"('{task['title'][:40]}') sans provider pour le domaine "
                        f"'{domaine}' d'après l'historique — libérée, mise de côté "
                        f"({len(bloquees)}/{_MAX_TACHES_BLOQUEES_PAR_SLOT}), "
                        f"essai de la suivante.")
                    if len(bloquees) >= _MAX_TACHES_BLOQUEES_PAR_SLOT:
                        return "aucun_provider_pour_domaine"
                    continue
                # Les autres slots tiennent les providers disponibles : ce n'est
                # pas un épuisement de quota, juste un slot de trop. Il rend la
                # main sans toucher au backlog.
                logger.info(
                    f"[DREAMER] [DreamCoder] Slot {idx} : aucun provider libre, arrêt du slot.")
                return "aucun_provider_libre"

            try:
                logger.info(
                    f"[DREAMER] [DreamCoder] Slot {idx} ← tâche {task['id']} "
                    f"'{task['title'][:45]}' via {provider} "
                    f"[{domaine}, {raison}] ({repo_root})")
                from core.projects import resoudre_projet_pour_tache as _resoudre_p
                projet_tache = _resoudre_p(task)
                verrou_projet = None
                if projet_tache.id != "moteur":
                    # Deux tâches Tab5 ne peuvent pas checkout en parallèle
                    # dans le même dépôt humain.
                    verrou_projet = _verrou_projet_async(projet_tache.id)
                    await verrou_projet.acquire()
                try:
                    task_result = await _executer_tache_bornee(
                        lambda: _process_one_dreamcoder_task(
                            task, pa_config, provider, orig_branch, bg, repo_root=repo_root),
                        deadline - time.time(), task["id"])
                finally:
                    if verrou_projet is not None and verrou_projet.locked():
                        verrou_projet.release()
                if task_result is None:
                    # Interrompue par la fenetre : elle n'a pas demerite, elle
                    # repart en attente sans consommer de tentative.
                    await liberer_tache(task["id"])
                    async with verrou:
                        results["interrompues_fenetre"] += 1
                    return "max_cycle_minutes_reached"
                task_result["slot"] = idx
                task_result["repo_root"] = task_result.get("repo_root") or repo_root
                task_result["domaine"] = domaine
                task_result["provider"] = provider
                task_result["raison_routage"] = raison
            finally:
                async with verrou:
                    en_vol.discard(provider)

            # [#T398] Panne d'infrastructure : rien ne sert d'enchaîner. La
            # tâche suivante tomberait de la même façon, et le cycle défilerait
            # le backlog entier en pure perte. Le slot rend la main.
            if task_result.get("status") == "infra_indisponible":
                async with verrou:
                    results["processed"] += 1
                    results["tasks"].append(task_result)
                    results["infra_indisponible"] += 1
                logger.error(
                    f"[DREAMER] [DreamCoder] Slot {idx} arrêté : plus aucun "
                    f"provider joignable (panne d'infrastructure).")
                return "infra_indisponible"

            # [#T432] Un 429 ne doit pas faire re-piocher LA MÊME tâche en
            # boucle dans ce slot (retries non incrémentés). On la met de
            # côté : un autre slot, ou le prochain cycle, la reprendra avec
            # un autre modèle.
            if task_result.get("status") == "provider_indisponible":
                bloquees.add(task["id"])
                async with verrou:
                    results["processed"] += 1
                    results["tasks"].append(task_result)
                if len(bloquees) >= _MAX_TACHES_BLOQUEES_PAR_SLOT:
                    return "aucun_provider_pour_domaine"
                continue

            # Nourrir l'historique : le verdict de la tâche met à jour l'Elo du
            # modèle d'exécution sur ce domaine — c'est ce que lira le routage
            # des nuits suivantes. Un modèle qui échoue 3 fois sur du vocal
            # sans aucun succès n'en recevra plus, quel que soit son prix.
            statut = task_result.get("status")
            if statut in ("success", "failed", "abandoned"):
                try:
                    from core.elo_scorer import update_elo
                    await asyncio.to_thread(
                        update_elo,
                        MODELE_PAR_PROVIDER.get(provider, provider),
                        domaine, statut == "success")
                except Exception as _elo_err:
                    logger.debug(
                        f"[DREAMER] [DreamCoder] Mise à jour Elo échouée (non bloquant) : {_elo_err}")

            async with verrou:
                results["processed"] += 1
                results["tasks"].append(task_result)
                results["total_cost_usd"] += task_result.get("cost_usd", 0.0)
                results["total_tokens"] += task_result.get("tokens_used", 0)
                statut = task_result.get("status")
                if statut == "success":
                    results["success"] += 1
                    if task_result.get("branch"):
                        results["branches_awaiting_review"].append(task_result["branch"])
                        # Distinguer « travail fait » de « travail sorti du Deck » :
                        # une branche non poussée demande une récolte manuelle.
                        if (task_result.get("push") or {}).get("pushed"):
                            results["branches_pushed"].append(task_result["branch"])
                elif statut in ("failed", "abandoned", "paused"):
                    results[statut] += 1

    motifs = await asyncio.gather(
        *[_travailleur(i, slots[i], branches_origine[i]) for i in range(nb_travailleurs)],
        return_exceptions=True,
    )

    # Un slot qui explose ne doit pas emporter le rapport des autres.
    propres: list[str] = []
    for i, m in enumerate(motifs):
        if isinstance(m, BaseException):
            logger.error(f"[DREAMER] [DreamCoder] Slot {i} en erreur : {m}", exc_info=m)
            propres.append("slot_en_erreur")
        else:
            propres.append(m)

    # Le motif d'arrêt du cycle est le plus « bloquant » rencontré : un backlog
    # vide n'a pas le même sens qu'une fenêtre écoulée avec des tâches en attente.
    for prioritaire in ("infra_indisponible", "slot_en_erreur", "max_cycle_minutes_reached",
                        "aucun_provider_pour_domaine", "aucun_provider_libre",
                        "backlog_empty"):
        if prioritaire in propres:
            results["stopped_reason"] = prioritaire
            break

    results["providers_ecartes_historique"] = ecartes_historique

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
                    min_idle = pa_config.get("dreamcoder_min_idle_hours", 0.0)
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
                        # [#T387] Ce que le cycle a réellement eu sous la main :
                        # sans ça, un cycle maigre est indistinguable d'un cycle
                        # qui a tourné avec un seul provider vivant sur six.
                        "slots": drain_results.get("slots", []),
                        "providers_sains": drain_results.get("providers_sains", []),
                        "providers_ecartes": drain_results.get("providers_ecartes", {}),
                        "stopped_reason": drain_results["stopped_reason"],
                    }
                    if drain_results["processed"] > 0:
                        chemin_rapport = await asyncio.to_thread(
                            _save_dreamcoder_cycle_report, drain_results)
                        # Compteur de rendement : l'entonnoir tenté → poussé →
                        # récolté → mergé n'existe que si chaque cycle alimente
                        # ses deux premiers stades ici, au moment où ils arrivent.
                        # [#T421] Le nom du rapport part AVEC le décompte : c'est
                        # lui qui empêche `reconstruire_depuis_rapports` de
                        # recompter ce même cycle et de doubler tout l'entonnoir.
                        try:
                            from core.rendement_dreamcoder import enregistrer_cycle
                            await asyncio.to_thread(
                                enregistrer_cycle, drain_results,
                                os.path.basename(chemin_rapport) if chemin_rapport else None)
                        except Exception as _rend_err:
                            logger.warning(
                                f"[DREAMER] [DreamCoder] Compteur de rendement non alimenté "
                                f"(non bloquant) : {_rend_err}")
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

def _declenchement_horaire(
    maintenant: datetime,
    minute_cible: object,
    dernier_iso: str,
) -> tuple[bool, str]:
    """Fenêtre de 5 min à :MM de chaque heure, au plus une fois par heure civile.

    `minute_cible` None / absent → inactif (l'horaire quotidien reste le maître).
    """
    if minute_cible is None or minute_cible == "":
        return False, ""
    try:
        minute = int(minute_cible)
    except (TypeError, ValueError):
        return False, ""
    if not 0 <= minute <= 59:
        return False, ""
    if not (minute <= maintenant.minute < minute + 5):
        return False, ""
    creneau = maintenant.strftime("%Y-%m-%dT%H")
    if dernier_iso:
        try:
            dernier = datetime.fromisoformat(str(dernier_iso))
            if dernier.strftime("%Y-%m-%dT%H") == creneau:
                return False, ""
        except ValueError:
            pass
    return True, f"Cadence horaire (:{minute:02d})"


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

        # Vérification 0 : cadence horaire (minute dans l'heure, décalage PC/Deck).
        # Axel 23/08 : un cycle / heure, Deck décalé de 30 min pour ne pas
        # saturer AJEAN en même temps. `dreamer_hourly_minute` absent → inactif.
        ok_horaire, raison_horaire = _declenchement_horaire(
            datetime.now(),
            pa_config.get("dreamer_hourly_minute"),
            dreamer_state.get("last_run_at", ""),
        )
        if ok_horaire:
            should_run = True
            trigger_reason = raison_horaire

        # Vérification 1 : Horaire fixe quotidien — sauté si cadence horaire.
        schedule_time = pa_config.get("dreamer_schedule", "02:00")
        try:
            now = datetime.now()
            target_hour, target_minute = map(int, schedule_time.split(":"))
            if pa_config.get("dreamer_hourly_minute") is not None:
                raise ValueError("cadence horaire active")

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

        # Vérification 2 : Inactivité — seulement sans cadence horaire
        # (sinon on doublerait le cycle : horaire + idle).
        if not should_run and pa_config.get("dreamer_hourly_minute") is None:
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
