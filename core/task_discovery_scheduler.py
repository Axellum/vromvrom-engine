"""
core/task_discovery_scheduler.py — Planificateur d'auto-découverte et d'injection de tâches.

Ce module exécute en tâche de fond le scan de `TaskDiscoveryAgent`,
déduplique les tâches par rapport au backlog SQLite (`moteur_runtime.db`),
et injecte automatiquement les nouvelles tâches qualifiées par Tier LLM.

Garde-fous de la déduplication (correctif post-PR #374) :
- comparaison contre TOUT le backlog (`get_all_tasks(limit=None)`) — l'ancien
  plafond implicite de 50 lignes ré-injectait les tâches anciennes ;
- clé STABLE portée par le marqueur `[dedup:…]` du prompt (fichier + texte du
  TODO, sans numéro de ligne) — un TODO qui descend de trois lignes n'est pas
  une nouvelle tâche ;
- les tâches marquées « DÉCISION AXEL » ne sont jamais découvertes (filtre
  dans `TaskDiscoveryAgent.scan_board_tasks`) ;
- PLAFOND DUR d'injections par run : au-delà, le reste attend le prochain
  cycle — un scan ne doit pas noyer le backlog sous des dizaines de tâches.

Auteur : Antigravity IDE + Axel
Créé le : 2026-08-18 (Branche : feat/task-discovery-agent)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.task_discovery_agent import RE_MARQUEUR_DEDUP, TaskDiscoveryAgent
from core.backlog_db import add_task, get_all_tasks, get_discovery_keys

logger = logging.getLogger("task_discovery_scheduler")

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _ENGINE_ROOT / "data" / "task_discovery_scheduler_state.json"

DEFAULT_INTERVAL_S = 3600.0  # 1 heure par défaut

# Plafond dur : nombre maximum de tâches injectées par run. Dépassable via
# l'environnement, jamais via le code du scan. Un backlog qui déborde bloque
# les vraies tâches urgentes derrière une digue de TODO P3.
MAX_INJECTIONS_PAR_RUN = int(os.environ.get("TASK_DISCOVERY_MAX_INJECTIONS", "10"))


class TaskDiscoveryScheduler:
    """Planificateur autonome de découverte de tâches."""

    def __init__(self, interval_s: float = DEFAULT_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self.agent = TaskDiscoveryAgent(_ENGINE_ROOT)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def load_state(self) -> Dict[str, Any]:
        """Charge l'état d'exécution sauvegardé."""
        if not _STATE_FILE.exists():
            return {
                "last_run_at": None,
                "tasks_discovered_total": 0,
                "tasks_enqueued_total": 0,
                "last_enqueued_count": 0,
            }
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[TASK_SCHEDULER] Impossible de lire l'état: %s", exc)
            return {}

    def save_state(self, state: Dict[str, Any]) -> None:
        """Sauvegarde l'état du scheduler de manière sécurisée."""
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("[TASK_SCHEDULER] Erreur sauvegarde état: %s", exc)

    async def sync_discovered_tasks(self) -> Dict[str, Any]:
        """Scanne les tâches et injecte celles qui sont nouvelles dans le backlog SQLite."""
        state = self.load_state()
        discovered = self.agent.run_discovery()
        # TOUT le backlog, sans plafond : la dédup n'a de sens que complète.
        existing_tasks = await get_all_tasks(limit=None)

        # Index de déduplication à quatre niveaux :
        # 0. [#T397] colonne `discovery_key` en base — la seule source qui ne
        #    dépende pas d'un texte : un agent qui reformule ou tronque une
        #    description fait disparaître le marqueur `[dedup:…]`, pas la colonne ;
        # 1. clés stables `[dedup:…]` portées par les prompts déjà injectés ;
        # 2. titres existants (lignes historiques sans marqueur) ;
        # 3. descriptions existantes (idem).
        existing_dedup_keys: set[str] = {
            c.strip().lower() for c in await get_discovery_keys() if c
        }
        existing_titles: set[str] = set()
        existing_descs: set[str] = set()
        for t in existing_tasks:
            titre = t.get("title", "").strip().lower()
            desc = t.get("description", "") or ""
            if titre:
                existing_titles.add(titre)
            if desc.strip():
                existing_descs.add(desc.strip().lower())
            for m in RE_MARQUEUR_DEDUP.finditer(desc):
                existing_dedup_keys.add(m.group("cle").strip().lower())

        candidats: List[Dict[str, Any]] = []

        for task in discovered:
            # La clé vient du champ `dedup_key` ; à défaut, on la relit dans
            # le prompt lui-même (le marqueur y est toujours embarqué) : la
            # dédup ne doit pas dépendre du chemin qui a produit le payload.
            cle = task.get("dedup_key") or ""
            if not cle:
                m_cle = RE_MARQUEUR_DEDUP.search(task.get("prompt", ""))
                cle = m_cle.group("cle") if m_cle else ""
            cle = cle.strip().lower()
            t_title = task["title"].strip().lower()
            t_prompt = task["prompt"].strip().lower()

            deja_connu = (
                (cle and cle in existing_dedup_keys)
                or t_title in existing_titles
                or t_prompt in existing_descs
            )
            if deja_connu:
                continue

            # Conversion de priorité P1->1, P2->2, P3->3
            prio_int = 2
            if task.get("prio") == "P1":
                prio_int = 1
            elif task.get("prio") == "P3":
                prio_int = 3

            candidats.append({"payload": task, "prio_int": prio_int, "cle": cle})
            # Une même clé ne doit entrer qu'une fois, même découverte deux
            # fois dans le même run (ex. même TODO vu par deux scanners).
            if cle:
                existing_dedup_keys.add(cle)
            existing_titles.add(t_title)

        # Plafond dur : les plus prioritaires d'abord, le reste attend le
        # prochain cycle.
        candidats.sort(key=lambda c: (c["prio_int"], c["payload"]["title"]))
        retenus = candidats[:MAX_INJECTIONS_PAR_RUN]
        if len(candidats) > len(retenus):
            logger.info(
                "[TASK_SCHEDULER] Plafond d'injection atteint (%d) : %d tâche(s) "
                "reportée(s) au prochain cycle.",
                MAX_INJECTIONS_PAR_RUN, len(candidats) - len(retenus))

        enqueued: List[Dict[str, Any]] = []
        for c in retenus:
            task = c["payload"]
            try:
                # [#T397] La clé part EN BASE, où un index unique partiel la
                # refuse en double même si le filtrage applicatif ci-dessus se
                # trompe. La complexité était calculée puis jetée : elle sert au
                # routage par tier, elle a sa place dans la ligne.
                task_id = await add_task(
                    title=task["title"],
                    description=task["prompt"],
                    priority=c["prio_int"],
                    discovery_key=c["cle"] or None,
                    complexity=task.get("complexity"),
                    project_id=task.get("project_id"),
                )
                enqueued.append({"db_id": task_id, "task_id": task["task_id"], "title": task["title"]})
            except Exception as exc:
                logger.error("[TASK_SCHEDULER] Échec injection tâche %s: %s", task["task_id"], exc)

        state["last_run_at"] = time.time()
        state["tasks_discovered_total"] = state.get("tasks_discovered_total", 0) + len(discovered)
        state["tasks_enqueued_total"] = state.get("tasks_enqueued_total", 0) + len(enqueued)
        state["last_enqueued_count"] = len(enqueued)
        self.save_state(state)

        logger.info("[TASK_SCHEDULER] Synchronisation terminée : %d scannées, %d candidates, %d injectées (plafond %d).",
                    len(discovered), len(candidats), len(enqueued), MAX_INJECTIONS_PAR_RUN)

        return {
            "discovered_count": len(discovered),
            "candidates_count": len(candidats),
            "enqueued_count": len(enqueued),
            "enqueued_tasks": enqueued,
            "cap": MAX_INJECTIONS_PAR_RUN,
            "timestamp": state["last_run_at"],
        }

    async def _loop(self) -> None:
        """Boucle d'exécution continue."""
        logger.info("[TASK_SCHEDULER] Démarrage de la boucle planifiée (intervalle: %.0fs)...", self.interval_s)
        while self._running:
            try:
                await self.sync_discovered_tasks()
            except Exception as exc:
                logger.error("[TASK_SCHEDULER] Erreur dans la boucle: %s", exc)
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        """Démarre le scheduler en tâche de fond asyncio."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        """Arrête le scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
