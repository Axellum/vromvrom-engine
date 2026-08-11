"""
core/routing_metrics.py — Persistance des métriques de routage dans SQLite.

Enregistre chaque décision de routage (catégorie, modèle choisi, latence, succès)
dans une base SQLite locale pour l'analyse a posteriori et l'auto-calibration
future du routeur.

Créé dans le cadre de l'audit V5.5 (Axe R2).
"""

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

from core.runtime_db import get_connection, get_db_path

_DB_PATH = get_db_path()

# Verrou global pour les écritures concurrentes (thread-safe)
_db_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Ouvre la base SQLite unifiée."""
    return get_connection()


def record_routing_decision(
    user_prompt: str,
    dominant_category: str | None,
    routing_type: str,
    target_agent: str,
    model_tier: str = "automatique",
    resolved_model: str = "",
    is_complex: bool = False,
    fast_path_used: bool = True,
    llm_classifier_used: bool = False,
    llm_confidence: float | None = None,
    context_categories: list | None = None,
    latency_ms: float | None = None,
    session_id: str = "",
) -> None:
    """
    Enregistre une décision de routage dans la base SQLite.

    Args:
        user_prompt: Le prompt utilisateur brut (hashé pour la confidentialité)
        dominant_category: La catégorie dominante détectée
        routing_type: Le type de routage (default, casual_chat, ha_direct, etc.)
        target_agent: L'agent cible choisi
        model_tier: Le tier de modèle sélectionné
        resolved_model: Le nom du modèle résolu — voir la note ci-dessous
        is_complex: Si la requête est jugée complexe
        fast_path_used: Si le fast-path par mots-clés a été utilisé
        llm_classifier_used: Si le LLM-classifier a été invoqué
        llm_confidence: La confiance du LLM-classifier (si utilisé)
        context_categories: Les catégories de contexte 3-Layers injectées
        latency_ms: La latence totale du routage en millisecondes
        session_id: L'identifiant de la session courante (#T296)

    [#T296] Pourquoi `resolved_model` reste vide, et pourquoi c'est voulu :
    le routage ne résout pas un modèle, il résout un TIER. Le modèle concret est
    choisi bien plus tard, à l'appel, par `FallbackProvider`, qui parcourt ses
    candidats jusqu'au premier qui aboutit (#T288). Remplir cette colonne au
    moment du routage reviendrait à écrire une prédiction en la présentant comme
    une mesure — précisément le défaut corrigé par #T294.

    Le lien routage → modèle réellement appelé passe donc par `session_id` :
    une jointure `routing_decisions ⋈ token_usage` rend le ou les modèles
    effectivement facturés pour cette décision, avec leur agent (#T296 volet A)
    et leur coût. L'information existe, elle n'est simplement pas dupliquée ici.
    """
    import hashlib
    prompt_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()[:16]

    try:
        with _db_lock:
            conn = _get_connection()
            conn.execute(
                """
                INSERT INTO routing_decisions (
                    timestamp, user_prompt_hash, prompt_length,
                    dominant_category, routing_type, target_agent,
                    model_tier, resolved_model, is_complex,
                    fast_path_used, llm_classifier_used, llm_confidence,
                    context_categories, latency_ms, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    prompt_hash,
                    len(user_prompt),
                    dominant_category,
                    routing_type,
                    target_agent,
                    model_tier,
                    resolved_model,
                    1 if is_complex else 0,
                    1 if fast_path_used else 0,
                    1 if llm_classifier_used else 0,
                    llm_confidence,
                    ",".join(context_categories) if context_categories else None,
                    latency_ms,
                    session_id,
                ),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[ROUTING METRICS] Erreur d'enregistrement : {e}")


def mark_routing_result(
    session_id: str,
    success: bool,
    error_category: str | None = None,
) -> None:
    """
    Met à jour la dernière décision de routage d'une session avec le résultat final.

    Args:
        session_id: L'identifiant de la session
        success: True si l'exécution a réussi, False sinon
        error_category: La catégorie d'erreur (si échec)
    """
    try:
        with _db_lock:
            conn = _get_connection()
            conn.execute(
                """
                UPDATE routing_decisions
                SET success = ?, error_category = ?
                WHERE session_id = ?
                AND id = (
                    SELECT id FROM routing_decisions
                    WHERE session_id = ?
                    ORDER BY timestamp DESC LIMIT 1
                )
                """,
                (1 if success else 0, error_category, session_id, session_id),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"[ROUTING METRICS] Erreur de mise à jour : {e}")


def get_routing_stats(last_n_hours: int = 24) -> dict[str, Any]:
    """
    Retourne les statistiques agrégées de routage des dernières N heures.

    Args:
        last_n_hours: Fenêtre temporelle en heures

    Returns:
        Dictionnaire avec les métriques agrégées (nb décisions, taux succès,
        répartition par catégorie, latence moyenne, etc.)
    """
    try:
        cutoff = time.time() - (last_n_hours * 3600)
        conn = _get_connection()

        # Nombre total de décisions
        total = conn.execute(
            "SELECT COUNT(*) FROM routing_decisions WHERE timestamp > ?", (cutoff,)
        ).fetchone()[0]

        if total == 0:
            conn.close()
            return {"total_decisions": 0, "period_hours": last_n_hours}

        # Taux de succès
        success_count = conn.execute(
            "SELECT COUNT(*) FROM routing_decisions WHERE timestamp > ? AND success = 1",
            (cutoff,),
        ).fetchone()[0]

        # Répartition par catégorie
        categories = conn.execute(
            """
            SELECT dominant_category, COUNT(*) as cnt
            FROM routing_decisions WHERE timestamp > ?
            GROUP BY dominant_category ORDER BY cnt DESC
            """,
            (cutoff,),
        ).fetchall()

        # Répartition par agent cible
        agents = conn.execute(
            """
            SELECT target_agent, COUNT(*) as cnt
            FROM routing_decisions WHERE timestamp > ?
            GROUP BY target_agent ORDER BY cnt DESC
            """,
            (cutoff,),
        ).fetchall()

        # Latence moyenne
        avg_latency = conn.execute(
            """
            SELECT AVG(latency_ms) FROM routing_decisions
            WHERE timestamp > ? AND latency_ms IS NOT NULL
            """,
            (cutoff,),
        ).fetchone()[0]

        # Usage du LLM classifier
        llm_used = conn.execute(
            "SELECT COUNT(*) FROM routing_decisions WHERE timestamp > ? AND llm_classifier_used = 1",
            (cutoff,),
        ).fetchone()[0]

        conn.close()

        return {
            "total_decisions": total,
            "period_hours": last_n_hours,
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
            "categories": {row[0] or "none": row[1] for row in categories},
            "agents": {row[0]: row[1] for row in agents},
            "avg_latency_ms": round(avg_latency, 2) if avg_latency else None,
            "llm_classifier_usage": llm_used,
            "fast_path_pct": round((total - llm_used) / total * 100, 1) if total > 0 else 100,
        }
    except Exception as e:
        logger.warning(f"[ROUTING METRICS] Erreur de lecture : {e}")
        return {"error": str(e)}
