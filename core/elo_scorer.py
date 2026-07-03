"""
core/elo_scorer.py — Système de scoring Elo pour le routage prédictif des LLM.

Dynamic Auto-Routing Elo.

Chaque modèle LLM reçoit un score Elo par domaine d'intention (code_generation,
home_assistant, analysis, etc.). Le score est mis à jour après chaque tâche DAG :
- Succès → le score monte
- Échec → le score descend

Le Router utilise ces scores pour trier dynamiquement les modèles d'un tier,
privilégiant le modèle le plus fiable ET le moins cher pour chaque type de tâche.

Algorithme Elo simplifié :
- Score initial : 1500.0 (référence universelle)
- K-factor adaptatif : K = 32 pour < 30 matchs, K = 16 après (stabilisation)
- "Match" : modèle vs difficulté de référence (Elo 1500)
- Succès = victoire, Échec = défaite

Persistance : Table SQLite `model_elo_scores` dans `routing_metrics.db`
(réutilise la BDD existante du Router pour simplifier l'architecture).
"""

import sqlite3
import time
import logging
import threading
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

from core.runtime_db import get_connection, get_db_path
from core.elo_core import expected_score, adaptive_k

_DB_PATH = get_db_path()

# Verrou global pour les écritures concurrentes (thread-safe DAG parallèle)
_elo_lock = threading.Lock()

# Score initial pour tout nouveau couple (modèle, domaine)
DEFAULT_ELO = 1500.0

# Score de référence de la "difficulté" d'une tâche (adversaire fixe)
REFERENCE_ELO = 1500.0

# K-factor adaptatif : plus élevé au début pour converger vite
K_FACTOR_INITIAL = 32.0   # < 30 matchs → apprentissage rapide
K_FACTOR_STABLE = 16.0    # >= 30 matchs → stabilisation progressive

# Seuil de matchs pour basculer en K stable
K_THRESHOLD = 30

# Bonus/malus de latence (optionnel) : pénaliser les modèles trop lents
LATENCY_PENALTY_THRESHOLD_MS = 10000  # Au-delà de 10s, légère pénalité
LATENCY_PENALTY_FACTOR = 0.5         # -0.5 Elo par seconde au-delà du seuil


def _get_connection() -> sqlite3.Connection:
    """Ouvre la base SQLite unifiée."""
    return get_connection()


def update_elo(
    model_name: str,
    domain: str,
    success: bool,
    latency_ms: Optional[float] = None,
) -> float:
    """
    Met à jour le score Elo d'un modèle pour un domaine donné.

    Algorithme Elo :
        expected = 1 / (1 + 10^((R_ref - R_model) / 400))
        actual = 1.0 si succès, 0.0 si échec
        new_elo = old_elo + K * (actual - expected)

    Args:
        model_name: Nom du modèle LLM (ex: "gemini-3.5-flash-free")
        domain: Domaine d'intention (ex: "code_generation", "home_assistant")
        success: True si la tâche a réussi, False sinon
        latency_ms: Latence d'exécution en ms (optionnel, pour malus)

    Returns:
        Le nouveau score Elo après mise à jour.
    """
    if not model_name or not domain:
        return DEFAULT_ELO

    # Normaliser les noms (insensible à la casse)
    model_name = model_name.strip().lower()
    domain = domain.strip().lower()

    try:
        with _elo_lock:
            conn = _get_connection()

            # Récupérer le score actuel (ou créer l'entrée)
            row = conn.execute(
                "SELECT elo_score, total_matches, wins, losses, avg_latency_ms "
                "FROM model_elo_scores WHERE model_name = ? AND domain = ?",
                (model_name, domain),
            ).fetchone()

            if row:
                old_elo, total_matches, wins, losses, avg_lat = row
            else:
                old_elo = DEFAULT_ELO
                total_matches = 0
                wins = 0
                losses = 0
                avg_lat = None

            # K-factor adaptatif + formule Elo (cœur partagé core.elo_core)
            k = adaptive_k(total_matches, K_FACTOR_INITIAL, K_FACTOR_STABLE, K_THRESHOLD)
            expected = expected_score(old_elo, REFERENCE_ELO)
            actual = 1.0 if success else 0.0
            delta = k * (actual - expected)

            # Malus de latence optionnel (les modèles trop lents sont légèrement pénalisés)
            if latency_ms and latency_ms > LATENCY_PENALTY_THRESHOLD_MS:
                excess_seconds = (latency_ms - LATENCY_PENALTY_THRESHOLD_MS) / 1000.0
                latency_penalty = excess_seconds * LATENCY_PENALTY_FACTOR
                delta -= latency_penalty

            new_elo = old_elo + delta

            # Mise à jour des compteurs
            total_matches += 1
            if success:
                wins += 1
            else:
                losses += 1

            # Mise à jour de la latence moyenne (moyenne mobile exponentielle)
            if latency_ms:
                if avg_lat is None:
                    avg_lat = latency_ms
                else:
                    alpha = 0.3  # Poids du nouvel échantillon (réactivité)
                    avg_lat = alpha * latency_ms + (1 - alpha) * avg_lat

            # UPSERT dans SQLite
            conn.execute(
                """
                INSERT INTO model_elo_scores
                    (model_name, domain, elo_score, total_matches, wins, losses,
                     avg_latency_ms, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_name, domain) DO UPDATE SET
                    elo_score = excluded.elo_score,
                    total_matches = excluded.total_matches,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    avg_latency_ms = excluded.avg_latency_ms,
                    last_updated = excluded.last_updated
                """,
                (model_name, domain, new_elo, total_matches, wins, losses,
                 avg_lat, time.time()),
            )
            conn.commit()
            conn.close()

            direction = "↑" if success else "↓"
            logger.info(
                f"[ELO] {direction} {model_name} | {domain} : "
                f"{old_elo:.0f} → {new_elo:.0f} (Δ{delta:+.1f}, "
                f"K={k:.0f}, W/L={wins}/{losses})"
            )
            return new_elo

    except Exception as e:
        logger.warning(f"[ELO] Erreur de mise à jour pour {model_name}/{domain} : {e}")
        return DEFAULT_ELO


def get_ranked_models(
    domain: str,
    model_names: List[str],
) -> List[Tuple[str, float]]:
    """
    Retourne les modèles triés par score Elo décroissant pour un domaine donné.

    Les modèles sans historique Elo sont placés au milieu (score par défaut 1500),
    ce qui leur donne une chance d'être testés sans être prioritaires.

    Args:
        domain: Domaine d'intention (ex: "code_generation")
        model_names: Liste des noms de modèles disponibles dans le tier

    Returns:
        Liste de tuples (model_name, elo_score) triés par Elo décroissant.
    """
    if not model_names or not domain:
        return [(m, DEFAULT_ELO) for m in (model_names or [])]

    domain = domain.strip().lower()

    try:
        conn = _get_connection()
        # Récupérer les scores Elo pour ce domaine
        placeholders = ",".join(["?"] * len(model_names))
        rows = conn.execute(
            f"SELECT model_name, elo_score FROM model_elo_scores "
            f"WHERE domain = ? AND model_name IN ({placeholders})",
            [domain] + [m.strip().lower() for m in model_names],
        ).fetchall()
        conn.close()

        # Construire le mapping (nom normalisé → score)
        elo_map = {row[0]: row[1] for row in rows}

        # Associer chaque modèle à son score (DEFAULT_ELO si inconnu)
        scored = []
        for m in model_names:
            m_lower = m.strip().lower()
            score = elo_map.get(m_lower, DEFAULT_ELO)
            scored.append((m, score))

        # Tri par Elo décroissant
        scored.sort(key=lambda x: x[1], reverse=True)

        logger.debug(
            f"[ELO] Classement pour '{domain}' : "
            + " > ".join(f"{m}({s:.0f})" for m, s in scored)
        )
        return scored

    except Exception as e:
        logger.warning(f"[ELO] Erreur de ranking pour {domain} : {e}")
        return [(m, DEFAULT_ELO) for m in model_names]


def get_all_scores() -> Dict[str, Dict[str, dict]]:
    """
    Retourne tous les scores Elo, structurés par modèle puis par domaine.

    Format retourné :
    {
        "gemini-3.5-flash-free": {
            "code_generation": {"elo": 1520.3, "matches": 12, "wins": 9, ...},
            "home_assistant": {"elo": 1480.7, "matches": 5, "wins": 3, ...},
        },
        ...
    }

    Utilisé par le dashboard métriques (Acte 5) et l'endpoint API.
    """
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT model_name, domain, elo_score, total_matches, wins, losses, "
            "avg_latency_ms, last_updated FROM model_elo_scores "
            "ORDER BY model_name, domain"
        ).fetchall()
        conn.close()

        result = {}
        for (model, domain, elo, matches, wins, losses, avg_lat, updated) in rows:
            if model not in result:
                result[model] = {}
            result[model][domain] = {
                "elo": round(elo, 1),
                "matches": matches,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / max(1, matches) * 100, 1),
                "avg_latency_ms": round(avg_lat, 1) if avg_lat else None,
                "last_updated": updated,
            }
        return result

    except Exception as e:
        logger.warning(f"[ELO] Erreur de lecture globale : {e}")
        return {}


def get_model_profile(model_name: str) -> Dict[str, dict]:
    """
    Retourne le profil complet d'un modèle (forces/faiblesses par domaine).

    Format retourné :
    {
        "code_generation": {"elo": 1520.3, "matches": 12, "rank": "expert"},
        "analysis": {"elo": 1480.7, "matches": 5, "rank": "competent"},
        ...
    }

    Le champ "rank" est dérivé du score Elo :
    - >= 1600 : "expert"
    - >= 1500 : "competent"
    - >= 1400 : "novice"
    - < 1400 : "unreliable"
    """
    model_name = model_name.strip().lower()

    def _rank(elo: float) -> str:
        if elo >= 1600:
            return "expert"
        elif elo >= 1500:
            return "competent"
        elif elo >= 1400:
            return "novice"
        else:
            return "unreliable"

    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT domain, elo_score, total_matches, wins, losses, avg_latency_ms "
            "FROM model_elo_scores WHERE model_name = ? ORDER BY elo_score DESC",
            (model_name,),
        ).fetchall()
        conn.close()

        profile = {}
        for (domain, elo, matches, wins, losses, avg_lat) in rows:
            profile[domain] = {
                "elo": round(elo, 1),
                "matches": matches,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / max(1, matches) * 100, 1),
                "rank": _rank(elo),
                "avg_latency_ms": round(avg_lat, 1) if avg_lat else None,
            }
        return profile

    except Exception as e:
        logger.warning(f"[ELO] Erreur de profil pour {model_name} : {e}")
        return {}


def get_domain_leaderboard(domain: str, top_n: int = 10) -> List[dict]:
    """
    Retourne le classement des modèles pour un domaine spécifique.

    Args:
        domain: Le domaine d'intention
        top_n: Nombre maximum de modèles à retourner

    Returns:
        Liste de dicts triés par Elo décroissant.
    """
    domain = domain.strip().lower()
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT model_name, elo_score, total_matches, wins, losses "
            "FROM model_elo_scores WHERE domain = ? "
            "ORDER BY elo_score DESC LIMIT ?",
            (domain, top_n),
        ).fetchall()
        conn.close()

        return [
            {
                "model": row[0],
                "elo": round(row[1], 1),
                "matches": row[2],
                "wins": row[3],
                "losses": row[4],
                "win_rate": round(row[3] / max(1, row[2]) * 100, 1),
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning(f"[ELO] Erreur de leaderboard pour {domain} : {e}")
        return []
