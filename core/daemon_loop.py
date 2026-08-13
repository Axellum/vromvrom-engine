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

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée
from core.ha_token import get_ha_token  # [T239] lecture centralisée du token HA
from core.ha_url import get_ha_url  # [T330] lecture centralisée de l'URL HA

logger = logging.getLogger("daemon_loop")

# Répertoire racine du moteur (parent de core/)
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fichier de cache du contexte daemon (alimenté par les cycles de tick)
_DAEMON_CONTEXT_FILE = os.path.join(_ENGINE_ROOT, "daemon_context.json")


# ──────────────────────────────────────────────────────────────────
# État global du démon (exposé via l'API /api/daemon/status)
# ──────────────────────────────────────────────────────────────────

daemon_state: dict[str, Any] = {
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
    "last_ha_overload_check_at": None,
    "consecutive_high_cpu_ticks": 0,
}

# Historique des 50 derniers cycles (FIFO)
daemon_logs: list[dict[str, Any]] = []


def _load_persistent_config() -> dict[str, Any]:
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

def _check_git_status() -> dict[str, Any]:
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


async def _check_ha_health() -> dict[str, Any]:
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
        ha_token = get_ha_token()
        ha_url = get_ha_url()

        if not ha_token:
            result["status"] = "skipped"
            result["details"]["reason"] = "token Home Assistant non configuré (HASS_TOKEN/HA_TOKEN)"
            return result

        if not ha_url:
            result["status"] = "skipped"
            result["details"]["reason"] = "URL Home Assistant non configurée (HASS_URL/HA_URL)"
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


async def _get_ha_state(entity_id: str) -> dict[str, Any]:
    """Récupère l'état d'une entité HA via son API REST."""
    ha_token = get_ha_token()
    ha_url = get_ha_url()
    
    if not ha_token:
        raise ValueError("token Home Assistant non configuré (HASS_TOKEN/HA_TOKEN)")
    if not ha_url:
        raise ValueError("URL Home Assistant non configurée (HASS_URL/HA_URL)")
        
    import aiohttp
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{ha_url}/api/states/{entity_id}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
            ssl=ha_ssl_context(),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                raise RuntimeError(f"HTTP {resp.status}")


async def _send_ha_notification(title: str, message: str) -> bool:
    """Envoie une notification persistante à Home Assistant."""
    ha_token = get_ha_token()
    ha_url = get_ha_url()
    
    if not ha_token:
        return False
    if not ha_url:
        return False
        
    import aiohttp
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "title": title,
        "message": message
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ha_url}/api/services/persistent_notification/create",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
                ssl=ha_ssl_context(),
            ) as resp:
                return resp.status in (200, 201)
    except Exception as e:
        logger.warning(f"[DAEMON-HA] Échec d'envoi de notification HA : {e}")
        return False


# Codes de retour de _execute_freebox_ssh_command (#T266) : une dépendance
# absente est un état permanent du CONTRÔLE, pas une panne du système surveillé.
_SSH_OK = 0
_SSH_FAILURE = -1        # échec SSH réel (transitoire) → anomalie légitime
_SSH_UNAVAILABLE = -2    # dépendance optionnelle absente → contrôle indisponible

# Signature de la dernière cause d'échec SSH journalisée (#T266) : la même
# indisponibilité n'est rapportée qu'une fois, puis de nouveau si elle change.
_last_ssh_error_reported: str | None = None


def _reset_ssh_error_signature() -> None:
    """Réarme la journalisation d'échec SSH après une réussite (#T266)."""
    global _last_ssh_error_reported
    _last_ssh_error_reported = None


def _report_ssh_error_once(error_key: str, message: str) -> None:
    """Journalise un échec SSH une seule fois par signature d'erreur.

    Une même cause (dépendance absente, clé refusée…) ne doit pas inonder le
    journal à chaque cycle : une fois au premier constat suffit, puis de
    nouveau si la cause change (ou après une réussite). #T266
    """
    global _last_ssh_error_reported
    if error_key == _last_ssh_error_reported:
        return
    _last_ssh_error_reported = error_key
    logger.warning(f"[DAEMON-SSH] {message}")


async def _execute_freebox_ssh_command(command: str) -> tuple[int, str, str]:
    """
    Exécute une commande sur la VM Freebox via SSH.
    Tente par clé SSH en priorité, puis par Paramiko si configuré.

    Codes de retour : _SSH_OK (succès), _SSH_UNAVAILABLE (dépendance absente —
    contrôle indisponible, pas une anomalie), _SSH_FAILURE (échec SSH réel).
    Le message retourné contient la cause de l'échec du chemin par clé : un log
    en debug seul a masqué la vraie cause en production (#T266).
    """
    ssh_user = os.environ.get("SSH_USER", "axel")
    freebox_ip = "192.168.1.x"

    # 1. Tentative SSH système par clé — la raison d'échec est conservée pour
    #    être visible si tout le chemin échoue.
    key_error: str | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            f"{ssh_user}@{freebox_ip}", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            _reset_ssh_error_signature()
            return _SSH_OK, stdout.decode(errors='ignore').strip(), stderr.decode(errors='ignore').strip()
        key_error = f"code {proc.returncode} : {stderr.decode(errors='ignore').strip()}"
    except Exception as e:
        key_error = str(e)
    logger.debug(f"[DAEMON-SSH] Échec SSH système par clé : {key_error}")

    # 2. Tentative avec Paramiko — import isolé : une dépendance absente n'est
    #    pas un échec du système surveillé mais un contrôle indisponible.
    #    (ModuleNotFoundError est une sous-classe d'ImportError.)
    try:
        import paramiko
    except ImportError as e:
        msg = f"Contrôle indisponible : Paramiko absent (clé SSH : {key_error})"
        _report_ssh_error_once(f"unavailable:{key_error}", f"{msg} — {e}")
        return _SSH_UNAVAILABLE, "", f"{msg} — {e}"

    try:
        ssh_password = os.environ.get("SSH_PASSWORD", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": freebox_ip,
            "username": ssh_user,
            "timeout": 10
        }
        if ssh_password:
            connect_kwargs["password"] = ssh_password

        def _ssh_run():
            client.connect(**connect_kwargs)
            stdin, stdout, stderr = client.exec_command(command)
            return stdout.channel.recv_exit_status(), stdout.read().decode(errors='ignore').strip(), stderr.read().decode(errors='ignore').strip()

        code, out, err = await asyncio.to_thread(_ssh_run)
        if code == _SSH_OK:
            _reset_ssh_error_signature()
        return code, out, err
    except Exception as e:
        # Échec SSH réel : anomalie légitime — journalisée une fois par cause.
        msg = f"Échec SSH (clé : {key_error}) — Paramiko : {e}"
        _report_ssh_error_once(f"failure:{key_error}:{e}", msg)
        return _SSH_FAILURE, "", msg


async def _check_ha_overload() -> dict[str, Any]:
    """
    Vérifie si Home Assistant ou la VM Freebox est surchargée.
    Si le CPU global est > 80% pendant 2 cycles consécutifs, ou si le check de 6h est dû :
    - Fait un diagnostic précis par SSH
    - Redémarre les conteneurs fautifs si besoin (Samba, etc.)
    """
    result = {"check": "ha_overload", "status": "ok", "details": {}}
    
    # 1. Vérifier la CPU globale via l'API REST de HA
    cpu_usage = 0.0
    try:
        state_data = await _get_ha_state("sensor.processor_use")
        cpu_usage = float(state_data.get("state", "0.0"))
        result["details"]["global_cpu_percent"] = cpu_usage
    except Exception as e:
        logger.warning(f"[DAEMON-OVERLOAD] Impossible de lire sensor.processor_use: {e}")
        result["details"]["global_cpu_percent"] = None
        # En cas d'erreur de lecture, on n'arrête pas, on continue pour le check périodique
        
    # 2. Déterminer si un check SSH complet est nécessaire
    now = time.time()
    last_check = daemon_state.get("last_ha_overload_check_at")
    
    # Check toutes les 6 heures (21600 secondes)
    due_by_time = (last_check is None) or (now - last_check >= 21600)
    
    # Check réactif : si CPU > 80%
    is_high_cpu = cpu_usage >= 80.0
    if is_high_cpu:
        daemon_state["consecutive_high_cpu_ticks"] += 1
    else:
        daemon_state["consecutive_high_cpu_ticks"] = 0
        
    due_by_cpu = daemon_state["consecutive_high_cpu_ticks"] >= 2
    
    if not (due_by_time or due_by_cpu):
        result["details"]["ssh_check_executed"] = False
        result["details"]["consecutive_high_cpu_ticks"] = daemon_state["consecutive_high_cpu_ticks"]
        return result
        
    # 3. Exécuter le diagnostic SSH complet
    result["details"]["ssh_check_executed"] = True
    result["details"]["trigger_reason"] = "time" if due_by_time else "cpu_surcharge"
    daemon_state["last_ha_overload_check_at"] = now
    
    logger.info(f"[DAEMON-OVERLOAD] Lancement du diagnostic SSH Freebox VM (Raison: {result['details']['trigger_reason']})")
    
    # Récupérer les stats docker
    code, stdout, stderr = await _execute_freebox_ssh_command("sudo docker stats --no-stream --format '{{.Name}}:{{.CPUPerc}}'")
    if code == _SSH_UNAVAILABLE:
        # Dépendance optionnelle absente : contrôle indisponible, PAS une anomalie
        # du système surveillé. « skipped » est le vocabulaire existant du daemon
        # pour « contrôle non effectué » (token absent, aiohttp absent…) et ne
        # tombe pas dans le filtre warning/error de la collecte (#T266).
        result["status"] = "skipped"
        result["details"]["reason"] = f"Contrôle indisponible : {stderr}"
        return result
    if code != _SSH_OK:
        result["status"] = "error"
        result["details"]["error"] = f"Échec SSH : {stderr}"
        return result
        
    # Parser les stats docker
    containers_cpu = {}
    overloaded_containers = []
    
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        name, cpu_str = line.split(":", 1)
        try:
            cpu_val = float(cpu_str.replace("%", "").strip())
            containers_cpu[name] = cpu_val
            
            # Seuils de surcharge
            if name == "addon_core_samba":
                limit = 50.0  # Seuil plus bas pour Samba
            else:
                limit = 80.0  # Seuil standard pour les autres conteneurs
                
            if cpu_val >= limit:
                overloaded_containers.append((name, cpu_val))
        except ValueError:
            pass
            
    result["details"]["containers_cpu"] = containers_cpu
    
    # Sauvegarder dans daemon_context.json
    try:
        existing_context = {}
        if os.path.exists(_DAEMON_CONTEXT_FILE):
            with open(_DAEMON_CONTEXT_FILE, encoding='utf-8') as f:
                existing_context = json.load(f)
        existing_context["last_ha_overload_check_at"] = now
        existing_context["last_containers_cpu"] = containers_cpu
        with open(_DAEMON_CONTEXT_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_context, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[DAEMON-OVERLOAD] Impossible d'écrire daemon_context.json: {e}")
        
    # 4. Auto-healing : Redémarrer les conteneurs fautifs
    restarted_containers = []
    for name, cpu_val in overloaded_containers:
        logger.warning(f"[DAEMON-OVERLOAD] Surcharge détectée : {name} consomme {cpu_val}% CPU. Tentative d'auto-healing...")
        
        # Commande de redémarrage
        restart_code, r_stdout, r_stderr = await _execute_freebox_ssh_command(f"sudo docker restart {name}")
        if restart_code == 0:
            restarted_containers.append(name)
            msg = f"Le conteneur {name} a été redémarré car sa consommation CPU était de {cpu_val}%."
            logger.info(f"[DAEMON-OVERLOAD] Auto-healing réussi : {msg}")
            
            # Notification HA
            await _send_ha_notification(
                title="⚠️ Moteur Sentinelle : Auto-healing CPU",
                message=msg
            )
            
            # Enregistrer un épisode de mémoire
            try:
                from memory.memory_db import MemoryDB
                db = MemoryDB.get_instance()
                db.add_episode(
                    summary=f"Auto-healing CPU Freebox VM : Redémarrage de {name} ({cpu_val}% CPU)",
                    category="system_health"
                )
            except Exception:
                pass
        else:
            logger.error(f"[DAEMON-OVERLOAD] Échec auto-healing pour {name} : {r_stderr}")
            
    result["details"]["overloaded_containers"] = overloaded_containers
    result["details"]["restarted_containers"] = restarted_containers
    
    if restarted_containers:
        result["status"] = "warning"
        result["details"]["alert"] = f"⚠️ Auto-healing appliqué : Redémarrage de {', '.join(restarted_containers)}"
        
    return result


async def _prefetch_calendar() -> dict[str, Any]:
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
                with open(_DAEMON_CONTEXT_FILE, encoding='utf-8') as f:
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


def _check_memory_health() -> dict[str, Any]:
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

async def run_tick_cycle(config: dict[str, Any]) -> dict[str, Any]:
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
    git_result, ha_result, calendar_result, memory_result, ha_overload_result = await asyncio.gather(
        asyncio.to_thread(_check_git_status),
        _check_ha_health(),
        _prefetch_calendar(),
        asyncio.to_thread(_check_memory_health),
        _check_ha_overload(),
    )

    # Collecter les anomalies
    all_checks = [git_result, ha_result, calendar_result, memory_result, ha_overload_result]
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

    # Restaurer l'état persistant
    if os.path.exists(_DAEMON_CONTEXT_FILE):
        try:
            with open(_DAEMON_CONTEXT_FILE, encoding='utf-8') as f:
                ctx = json.load(f)
                daemon_state["last_ha_overload_check_at"] = ctx.get("last_ha_overload_check_at")
                logger.info(f"[DAEMON] 🧭 État persistant restauré : dernier check overload à {daemon_state['last_ha_overload_check_at']}")
        except Exception as e:
            logger.warning(f"[DAEMON] Impossible de charger l'état persistant : {e}")

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

def get_daemon_status() -> dict[str, Any]:
    """Retourne l'état complet du démon pour l'API."""
    return {**daemon_state}


def get_daemon_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Retourne les N derniers logs du démon."""
    return list(reversed(daemon_logs[:limit]))
