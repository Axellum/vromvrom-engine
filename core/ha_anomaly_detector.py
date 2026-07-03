#!/usr/bin/env python3
"""
core/ha_anomaly_detector.py — Détecteur d'anomalies domotiques.

Analyse l'historique SQLite Home Assistant pour détecter :
- Dérives statistiques sur capteurs numériques (mean ± 2σ, fenêtre 24h vs 7j)
- Basculements anormaux sur entités binaires (fréquence > 2x la normale)

Appelé par DreamerAgent à chaque cycle nocturne (étape 2.6).

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import logging
import sqlite3
import statistics
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# Chemin par défaut sur HAOS VM
HA_DB_DEFAULT = "/homeassistant/home-assistant_v2.db"


class HAAnomalyDetector:
    """
    Détecteur d'anomalies domotiques à partir de la base SQLite HA.

    Thread-safe : toutes les opérations SQLite passent par asyncio.to_thread.
    La connexion est ouverte en mode read-only (isolation_level=None).
    """

    def __init__(self, db_path: str = HA_DB_DEFAULT):
        """
        Args:
            db_path: Chemin vers home-assistant_v2.db (HAOS: /homeassistant/)
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ──────────────────────────────────────────────────────────────────
    # Connexion SQLite
    # ──────────────────────────────────────────────────────────────────

    async def _get_conn(self) -> Optional[sqlite3.Connection]:
        """Retourne la connexion SQLite (lazy, read-only)."""
        if self._conn is None:
            try:
                conn = await asyncio.to_thread(
                    sqlite3.connect,
                    self.db_path,
                    isolation_level=None,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                self._conn = conn
                logger.info(f"[HA ANOMALY] Connexion SQLite : {self.db_path}")
            except Exception as e:
                logger.warning(f"[HA ANOMALY] Impossible de se connecter : {e}")
                return None
        return self._conn

    async def _query_states(self, entity_id: str, days: int = 7) -> List[dict]:
        """
        Récupère les états d'une entité depuis SQLite.

        Returns:
            Liste de {entity_id, state, last_changed}
        """
        conn = await self._get_conn()
        if not conn:
            return []
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            sql = """
                SELECT entity_id, state, last_changed
                FROM states
                WHERE entity_id = ?
                  AND last_changed >= ?
                ORDER BY last_changed ASC
            """
            cursor = conn.cursor()
            rows = await asyncio.to_thread(cursor.execute, sql, (entity_id, cutoff))
            results = await asyncio.to_thread(rows.fetchall)
            return [
                {"entity_id": r["entity_id"], "state": r["state"], "last_changed": r["last_changed"]}
                for r in results
            ]
        except Exception as e:
            logger.warning(f"[HA ANOMALY] Erreur requête {entity_id} : {e}")
            return []

    async def _get_top_entities(self, limit: int = 20, days: int = 7) -> List[str]:
        """Retourne les TOP N entités les plus actives (par nb de changements)."""
        conn = await self._get_conn()
        if not conn:
            return []
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            sql = """
                SELECT entity_id, COUNT(*) as cnt
                FROM states
                WHERE last_changed >= ?
                GROUP BY entity_id
                ORDER BY cnt DESC
                LIMIT ?
            """
            cursor = conn.cursor()
            rows = await asyncio.to_thread(cursor.execute, sql, (cutoff, limit))
            results = await asyncio.to_thread(rows.fetchall)
            return [r["entity_id"] for r in results]
        except Exception as e:
            logger.warning(f"[HA ANOMALY] Erreur TOP entités : {e}")
            return []

    # ──────────────────────────────────────────────────────────────────
    # Analyse
    # ──────────────────────────────────────────────────────────────────

    async def analyze_entity(self, entity_id: str, days: int = 7) -> dict:
        """
        Analyse une entité pour détecter des anomalies.

        Returns:
            {entity_id, anomaly: bool, severity: low|medium|high, details: str}
        """
        states = await self._query_states(entity_id, days)
        if not states:
            return {"entity_id": entity_id, "anomaly": False, "severity": "low",
                    "details": "Aucune donnée disponible"}

        entity_type = entity_id.split(".")[0] if "." in entity_id else ""

        if entity_type in ("sensor", "light", "climate", "number", "input_number"):
            return await self._analyze_numeric(entity_id, states)
        if entity_type in ("binary_sensor", "switch", "input_boolean", "cover"):
            return await self._analyze_binary(entity_id, states)

        return {"entity_id": entity_id, "anomaly": False, "severity": "low",
                "details": f"Type non analysable : {entity_type}"}

    async def _analyze_numeric(self, entity_id: str, states: List[dict]) -> dict:
        """
        Détecte les dérives sur capteurs numériques.
        Anomalie si mean 24h > mean 7j ± 2σ.
        """
        # Valeurs numériques valides sur 7j
        values = []
        for s in states:
            try:
                v = float(s["state"])
                if v == v:  # Exclure NaN (NaN != NaN)
                    values.append(v)
            except (ValueError, TypeError):
                continue

        if len(values) < 10:
            return {"entity_id": entity_id, "anomaly": False, "severity": "low",
                    "details": f"Données insuffisantes ({len(values)} valeurs numériques)"}

        mean_7j = statistics.mean(values)
        std_7j  = statistics.stdev(values) if len(values) > 1 else 0.0
        min_v, max_v = min(values), max(values)

        # Valeurs des dernières 24h
        cutoff_24h = datetime.now() - timedelta(hours=24)
        recent = []
        for s in states:
            try:
                t = datetime.fromisoformat(str(s["last_changed"])[:19])
                if t >= cutoff_24h:
                    v = float(s["state"])
                    if v == v:
                        recent.append(v)
            except (ValueError, TypeError):
                continue

        if not recent:
            return {"entity_id": entity_id, "anomaly": False, "severity": "low",
                    "details": f"Aucune donnée récente (24h). Moyenne 7j : {mean_7j:.1f}"}

        mean_24h = statistics.mean(recent)
        diff     = mean_24h - mean_7j
        seuil    = 2.0 * std_7j if std_7j > 0 else 0.5

        anomaly = abs(diff) > seuil
        if anomaly:
            if abs(diff) > 3.0 * std_7j:
                severity = "high"
            elif abs(diff) > 2.5 * std_7j:
                severity = "medium"
            else:
                severity = "low"
            direction = "au-dessus" if diff > 0 else "en dessous"
            details = (
                f"Moyenne 24h : {mean_24h:.1f} vs moy. 7j : {mean_7j:.1f} "
                f"({direction} de {abs(diff):.1f}) "
                f"[min={min_v:.1f}, max={max_v:.1f}, σ={std_7j:.1f}]"
            )
        else:
            severity = "low"
            details = (
                f"Normale — moy. 24h : {mean_24h:.1f} ≈ moy. 7j : {mean_7j:.1f} "
                f"(σ={std_7j:.1f})"
            )

        return {"entity_id": entity_id, "anomaly": anomaly, "severity": severity, "details": details}

    async def _analyze_binary(self, entity_id: str, states: List[dict]) -> dict:
        """
        Détecte les basculements anormaux sur entités binaires.
        Anomalie si transitions 24h > 2x la moyenne quotidienne 7j.
        """
        transitions_by_day: dict = {}
        cutoff_24h = datetime.now() - timedelta(hours=24)
        recent_transitions = 0
        total_transitions  = 0

        for i in range(1, len(states)):
            if states[i]["state"] != states[i-1]["state"]:
                total_transitions += 1
                try:
                    t = datetime.fromisoformat(str(states[i]["last_changed"])[:19])
                    day = t.strftime("%Y-%m-%d")
                    transitions_by_day[day] = transitions_by_day.get(day, 0) + 1
                    if t >= cutoff_24h:
                        recent_transitions += 1
                except (ValueError, TypeError):
                    continue

        if not transitions_by_day:
            return {"entity_id": entity_id, "anomaly": False, "severity": "low",
                    "details": "Aucune transition détectée"}

        avg_daily = total_transitions / len(transitions_by_day)
        anomaly   = avg_daily > 0 and recent_transitions > 2.0 * avg_daily

        if anomaly:
            ratio = recent_transitions / avg_daily if avg_daily > 0 else 999
            severity = "high" if ratio > 4.0 else ("medium" if ratio > 3.0 else "low")
            details = (
                f"Basculements anormaux : {recent_transitions} en 24h "
                f"(moyenne : {avg_daily:.1f}/j, ratio : {ratio:.1f}x)"
            )
        else:
            severity = "low"
            details = (
                f"Basculements normaux : {recent_transitions} en 24h "
                f"(moyenne : {avg_daily:.1f}/j)"
            )

        return {"entity_id": entity_id, "anomaly": anomaly, "severity": severity, "details": details}

    # ──────────────────────────────────────────────────────────────────
    # API publique
    # ──────────────────────────────────────────────────────────────────

    async def analyze_all(
        self,
        entity_ids: Optional[List[str]] = None,
        days: int = 7,
    ) -> List[dict]:
        """
        Analyse toutes les entités spécifiées ou les TOP 20 les plus actives.

        Returns:
            Liste des anomalies détectées uniquement (anomaly=True).
        """
        if entity_ids is None:
            entity_ids = await self._get_top_entities(limit=20, days=days)

        if not entity_ids:
            logger.warning("[HA ANOMALY] Aucune entité à analyser")
            return []

        logger.info(f"[HA ANOMALY] Analyse de {len(entity_ids)} entités...")
        anomalies = []
        for eid in entity_ids:
            try:
                result = await self.analyze_entity(eid, days)
                if result["anomaly"]:
                    anomalies.append(result)
                    logger.info(
                        f"[HA ANOMALY] ⚠️  Anomalie : {eid} "
                        f"(sévérité={result['severity']})"
                    )
            except Exception as e:
                logger.warning(f"[HA ANOMALY] Erreur analyse {eid} : {e}")

        logger.info(
            f"[HA ANOMALY] Analyse terminée : "
            f"{len(anomalies)}/{len(entity_ids)} anomalies détectées"
        )
        return anomalies

    async def format_suggestions(self, anomalies: List[dict]) -> str:
        """
        Formate les anomalies en texte lisible (TTS-friendly + Tab5).

        Returns:
            String formatée pour rapport DreamerAgent.
        """
        if not anomalies:
            return "✅ Aucune anomalie domotique détectée cette nuit."

        # Trier par sévérité décroissante
        order = {"high": 0, "medium": 1, "low": 2}
        sorted_a = sorted(anomalies, key=lambda x: order.get(x.get("severity", "low"), 9))

        emoji_map = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        lines = ["🔍 **Rapport d'anomalies domotiques**", ""]

        for a in sorted_a:
            em  = emoji_map.get(a.get("severity", "low"), "⚪")
            sev = a.get("severity", "low").upper()
            lines.append(f"{em} **{a['entity_id']}** [{sev}]")
            lines.append(f"   {a.get('details', '')}")
            lines.append("")

        high   = sum(1 for a in anomalies if a.get("severity") == "high")
        medium = sum(1 for a in anomalies if a.get("severity") == "medium")
        low    = sum(1 for a in anomalies if a.get("severity") == "low")

        lines.append("---")
        lines.append(
            f"📊 **Résumé** : {len(anomalies)} anomalie(s) "
            f"(🔴 {high} critique(s), 🟡 {medium} modérée(s), 🟢 {low} mineure(s))"
        )

        return "\n".join(lines)

    async def close(self):
        """Ferme la connexion SQLite proprement."""
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.close)
                logger.info("[HA ANOMALY] Connexion SQLite fermée")
            except Exception as e:
                logger.warning(f"[HA ANOMALY] Erreur fermeture : {e}")
            finally:
                self._conn = None
