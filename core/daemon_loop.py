"""
core/daemon_loop.py — Boucle de fond asyncio pour le Démon Sentinelle 24/7.

Service asyncio autonome (PAS un BaseAgent) lancé au startup de gui_server.py.
Exécute un cycle de surveillance à intervalle paramétrable (config.json → persistent_agents).

Responsabilités par cycle :
1. Vérification Git — détecte les modifications non commitées
2. Santé Home Assistant — détecte les entités unavailable critiques
3. Pré-chargement Calendrier — cache JSON des événements du jour
4. Logging structuré — chaque cycle est enregistré dans session_history.db

La fréquence est relue à CHAQUE cycle depuis config.json, ce qui permet
le changement en live depuis l'IHM sans redémarrage.

Auteur : Antigravity IDE + Axel
Créé le : 2026-05-30
"""

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
from datetime import datetime
from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée
from typing import Dict, Any, List

logger = logging.getLogger("daemon_loop")

# Répertoire racine du moteur (parent de core/)
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fichier de cache du contexte daemon (alimenté par les cycles de tick)
_DAEMON_CONTEXT_FILE = os.path.join(_ENGINE_ROOT, "daemon_context.json")


# ──────────────────────────────────────────────────────────────────
# État global du démon (exposé via l'API /api/daemon/status)
# ──────────────────────────────────────────────────────────────────

daemon_state: Dict[str, Any] = {
    "running": False,
    "enabled": False,
    "last_cycle_at": None,
    "last_cycle_duration_ms": 0,
    "total_cycles": 0,
    "anomalies": [],         # Liste des anomalies détectées au dernier cycle
    "errors_count": 0,
    "last_error": None,
    "interval_minutes": 10,
    "started_at": None,
}

# Historique des 50 derniers cycles (FIFO)
daemon_logs: List[Dict[str, Any]] = []


def _load_persistent_config() -> Dict[str, Any]:
    """
    Charge la section persistent_agents depuis config.json.
    Relue à CHAQUE cycle pour permettre la modification en live.
    """
    try:
        from core.llm_gateway import load_config
        config = load_config()
        return config.get("persistent_agents", {})
    except Exception as e:
        logger.warning(f"[DAEMON] Impossible de lire persistent_agents: {e}")
        return {}


# ──────────────────────────────────────────────────────────────────
# Vérifications individuelles du cycle
# ──────────────────────────────────────────────────────────────────

def _check_git_status() -> Dict[str, Any]:
    """
    Vérifie le statut Git dans le workspace principal.
    Retourne le nombre de fichiers modifiés/non-trackés et une alerte si > 10.
    """
    result = {"check": "git_status", "status": "ok", "details": {}}
    
    try:
        # Vérifier dans le répertoire parent (e:\AuxFilsDesIdees)
        workspace = os.path.dirname(_ENGINE_ROOT)
        
        # Chercher les répertoires Git dans le workspace
        git_dirs = []
        if os.path.exists(os.path.join(_ENGINE_ROOT, ".git")):
            git_dirs.append(_ENGINE_ROOT)
        if os.path.exists(os.path.join(workspace, ".git")):
            git_dirs.append(workspace)
        
        total_modified = 0
        total_untracked = 0
        
        for git_dir in git_dirs:
            # Éviter le flash de fenêtre console
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=git_dir, encoding='utf-8', errors='ignore',
                timeout=10,
                creationflags=creationflags
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) < 2:
                        continue
                    status_flag, file_path = parts[0], parts[1]
                    
                    # Ignorer les extensions de base de données, cache, logs techniques et répertoire checkpoints
                    file_lower = file_path.lower()
                    if any(file_lower.endswith(ext) for ext in [".db", ".db-wal", ".db-shm", ".json", ".log", ".pyc", ".tmp", ".coverage"]) or "checkpoints/" in file_lower:
                        continue
                        
                    if status_flag == "??":
                        total_untracked += 1
                    else:
                        total_modified += 1
        
        result["details"] = {
            "modified": total_modified,
            "untracked": total_untracked,
            "repos_checked": len(git_dirs),
        }
        
        # Alerte si trop de fichiers non commitées
        if total_modified + total_untracked > 10:
            result["status"] = "warning"
            result["details"]["alert"] = (
                f"⚠️ {total_modified} modifiés + {total_untracked} non-trackés "
                f"dans {len(git_dirs)} dépôt(s)"
            )
        
    except subprocess.TimeoutExpired:
        result["status"] = "error"
        result["details"]["error"] = "Timeout Git (>10s)"
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)
    
    return result


async def _check_ha_health() -> Dict[str, Any]:
    """
    Vérifie la santé de Home Assistant via l'API REST.
    Détecte les entités critiques en état 'unavailable' ou 'unknown'.
    """
    result = {"check": "ha_health", "status": "ok", "details": {}}
    
    # Entités critiques à surveiller (Tab5, DAC, Voice Assistant)
    critical_entities = [
        "switch.m5stack_tab5_home_assistant_hmi_tab5_wake_word_active",
        "sensor.m5stack_tab5_home_assistant_hmi_tab5_core_temp",
        "media_player.m5stack_tab5_home_assistant_hmi_tab5_media_player",
    ]
    
    try:
        # Récupérer le token HA depuis les variables d'environnement
        ha_token = os.environ.get("HASS_TOKEN", "")
        ha_url = os.environ.get("HASS_URL", "http://${HA_HOST:-192.168.1.x}:8123")
        
        if not ha_token:
            result["status"] = "skipped"
            result["details"]["reason"] = "HASS_TOKEN non configuré"
            return result
        
        import aiohttp
        headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        
        unavailable_entities = []
        
        async with aiohttp.ClientSession() as session:
            for entity_id in critical_entities:
                try:
                    async with session.get(
                        f"{ha_url}/api/states/{entity_id}",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5),
                        ssl=ha_ssl_context(),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            state = data.get("state", "")
                            if state in ("unavailable", "unknown"):
                                unavailable_entities.append({
                                    "entity_id": entity_id,
                                    "state": state,
                                })
                        else:
                            unavailable_entities.append({
                                "entity_id": entity_id,
                                "state": f"HTTP {resp.status}",
                            })
                except Exception:
                    unavailable_entities.append({
                        "entity_id": entity_id,
                        "state": "unreachable",
                    })
        
        result["details"]["entities_checked"] = len(critical_entities)
        result["details"]["unavailable"] = unavailable_entities
        
        if unavailable_entities:
            result["status"] = "warning"
            result["details"]["alert"] = (
                f"⚠️ {len(unavailable_entities)}/{len(critical_entities)} "
                f"entité(s) critique(s) indisponible(s)"
            )
        
    except ImportError:
        result["status"] = "skipped"
        result["details"]["reason"] = "aiohttp non installé"
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)
    
    return result


async def _prefetch_calendar() -> Dict[str, Any]:
    """
    Pré-charge les événements du calendrier Google pour la journée.
    Écrit le résultat dans daemon_context.json pour les futurs prompts.
    """
    result = {"check": "calendar_prefetch", "status": "ok", "details": {}}
    
    try:
        from tools.google_workspace import get_calendar_events
        
        events_raw = get_calendar_events(calendar_id="primary", max_results="10")
        
        # Parser la réponse (c'est une string JSON)
        if isinstance(events_raw, str):
            try:
                events_data = json.loads(events_raw)
            except json.JSONDecodeError:
                events_data = {"raw": events_raw}
        else:
            events_data = events_raw
        
        # Écrire le cache pour les agents
        context = {
            "calendar_events": events_data,
            "fetched_at": datetime.now().isoformat(),
            "source": "daemon_loop",
        }
        
        # Charger le contexte existant pour ne pas écraser les autres données
        existing_context = {}
        if os.path.exists(_DAEMON_CONTEXT_FILE):
            try:
                with open(_DAEMON_CONTEXT_FILE, 'r', encoding='utf-8') as f:
                    existing_context = json.load(f)
            except Exception:
                pass
        
        existing_context.update(context)
        
        with open(_DAEMON_CONTEXT_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_context, f, indent=2, ensure_ascii=False)
        
        event_count = len(events_data) if isinstance(events_data, list) else 0
        result["details"]["events_cached"] = event_count
        
    except ImportError:
        result["status"] = "skipped"
        result["details"]["reason"] = "Module google_workspace non disponible"
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)[:200]
    
    return result


def _check_memory_health() -> Dict[str, Any]:
    """
    Vérifie la santé de la base mémoire (memory.db).
    Retourne les statistiques de la base et détecte les faits obsolètes.
    """
    result = {"check": "memory_health", "status": "ok", "details": {}}
    
    try:
        from memory.memory_db import MemoryDB
        db = MemoryDB.get_instance()
        
        stats = db.get_stats()
        stale_facts = db.get_stale_facts(threshold=0.3)
        
        result["details"] = {
            "facts_count": stats.get("facts", 0),
            "episodes_count": stats.get("episodes", 0),
            "graph_entities": stats.get("graph_entities", 0),
            "db_size_kb": stats.get("db_size_kb", 0),
            "stale_facts_count": len(stale_facts),
        }
        
        if len(stale_facts) > 5:
            result["status"] = "info"
            result["details"]["alert"] = (
                f"ℹ️ {len(stale_facts)} faits avec un score de pertinence < 0.3"
            )
        
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)
    
    return result


# ──────────────────────────────────────────────────────────────────
# Cycle principal du démon
# ──────────────────────────────────────────────────────────────────

async def run_tick_cycle(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Exécute un cycle complet du démon sentinelle.
    
    Retourne un rapport structuré avec les résultats de chaque vérification.
    Aucun appel LLM n'est fait — le LLM serait appelé uniquement
    en cas d'anomalie nécessitant un diagnostic (TODO: phase future).
    """
    cycle_start = time.time()
    cycle_id = f"tick_{int(cycle_start)}"
    
    logger.info(f"[DAEMON] ▶ Début du cycle {cycle_id}")
    
    # Exécuter TOUS les checks en parallèle via asyncio.gather()
    # Les fonctions synchrones (git, memory) sont enveloppées dans to_thread()
    # pour ne pas bloquer la boucle asyncio. Durée de cycle = max(latences)
    # au lieu de sum(latences).
    git_result, ha_result, calendar_result, memory_result = await asyncio.gather(
        asyncio.to_thread(_check_git_status),
        _check_ha_health(),
        _prefetch_calendar(),
        asyncio.to_thread(_check_memory_health),
    )
    
    # Collecter les anomalies
    all_checks = [git_result, ha_result, calendar_result, memory_result]
    anomalies = [c for c in all_checks if c["status"] in ("warning", "error")]
    
    cycle_duration_ms = round((time.time() - cycle_start) * 1000, 1)
    
    # Construire le rapport du cycle
    report = {
        "cycle_id": cycle_id,
        "timestamp": datetime.now().isoformat(),
        "duration_ms": cycle_duration_ms,
        "checks": {c["check"]: c for c in all_checks},
        "anomalies_count": len(anomalies),
        "anomalies": [
            {"check": a["check"], "status": a["status"],
             "alert": a["details"].get("alert", a["details"].get("error", ""))}
            for a in anomalies
        ],
    }
    
    # Mettre à jour l'état global
    daemon_state["last_cycle_at"] = report["timestamp"]
    daemon_state["last_cycle_duration_ms"] = cycle_duration_ms
    daemon_state["total_cycles"] += 1
    daemon_state["anomalies"] = report["anomalies"]
    
    # Ajouter au log FIFO (max 50 entrées)
    daemon_logs.append({
        "cycle_id": cycle_id,
        "timestamp": report["timestamp"],
        "duration_ms": cycle_duration_ms,
        "anomalies_count": len(anomalies),
        "checks_summary": {c["check"]: c["status"] for c in all_checks},
    })
    if len(daemon_logs) > 50:
        daemon_logs.pop(0)
    
    # Log console
    status_emoji = "✅" if not anomalies else "⚠️"
    logger.info(
        f"[DAEMON] {status_emoji} Cycle {cycle_id} terminé en {cycle_duration_ms}ms "
        f"({len(anomalies)} anomalie(s))"
    )
    
    return report


# ──────────────────────────────────────────────────────────────────
# Boucle principale asyncio (lancée au startup de gui_server.py)
# ──────────────────────────────────────────────────────────────────

async def daemon_main_loop():
    """
    Boucle de fond asyncio paramétrable.
    
    Lit la fréquence depuis config.json à CHAQUE cycle pour permettre
    la modification en live depuis l'IHM sans redémarrage.
    """
    logger.info("[DAEMON] 🚀 Démarrage du Démon Sentinelle 24/7")
    daemon_state["started_at"] = datetime.now().isoformat()
    daemon_state["running"] = True
    
    # Attendre quelques secondes au démarrage pour laisser FastAPI s'initialiser
    await asyncio.sleep(5)
    
    while True:
        # Relire la config à CHAQUE itération (modification en live)
        pa_config = _load_persistent_config()
        
        daemon_state["enabled"] = pa_config.get("daemon_enabled", True)
        daemon_state["interval_minutes"] = pa_config.get("daemon_interval_minutes", 10)
        
        if not pa_config.get("daemon_enabled", True):
            # Démon désactivé — vérifier toutes les 60s si réactivé
            logger.debug("[DAEMON] Démon désactivé, vérification dans 60s...")
            await asyncio.sleep(60)
            continue
        
        # Calculer l'intervalle depuis la config
        interval_seconds = pa_config.get("daemon_interval_minutes", 10) * 60
        
        try:
            await run_tick_cycle(pa_config)
        except Exception as e:
            logger.error(f"[DAEMON] ❌ Erreur critique dans le cycle: {e}")
            daemon_state["errors_count"] += 1
            daemon_state["last_error"] = {
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
            }
        
        # Attendre l'intervalle configuré avant le prochain cycle
        await asyncio.sleep(interval_seconds)


# ──────────────────────────────────────────────────────────────────
# API publique (utilisée par gui_server.py pour les routes /api/daemon/*)
# ──────────────────────────────────────────────────────────────────

def get_daemon_status() -> Dict[str, Any]:
    """Retourne l'état complet du démon pour l'API."""
    return {**daemon_state}


def get_daemon_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retourne les N derniers logs du démon."""
    return list(reversed(daemon_logs[:limit]))
