"""
core/elo_router.py — Système Elo de meta-learning pour le routing des requêtes.

Attribue un score Elo à chaque routing_type (home_assistant, casual_chat,
analysis, code_generation) en fonction des évaluations du ReviewerAgent (1-10).

Complémentaire au MLRouter  :
  - MLRouter : prédit depuis l'historique (statistique, off-line)
  - EloRouter : ajuste en temps réel selon la qualité perçue (on-line)

Formule Elo classique (K=32, adversaire théorique = 1200) :
  expected = 1 / (1 + 10^((1200 - elo) / 400))
  new_elo  = elo + 32 * (outcome - expected)

ReviewerAgent score → outcome :
  >= 7.0 → victoire (1.0)
  4.0-6.9 → draw (0.5)
  < 4.0   → défaite (0.0)

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.elo_core import expected_score, updated_elo, reviewer_score_to_outcome

logger = logging.getLogger(__name__)

# Configuration
_ENGINE_DIR  = Path(__file__).resolve().parent.parent
DEFAULT_DB   = str(_ENGINE_DIR / "moteur_runtime.db")
ROUTING_TYPES = ["home_assistant", "casual_chat", "analysis", "code_generation"]
INITIAL_ELO   = 1200.0
K_FACTOR      = 32
OPPONENT_ELO  = 1200.0  # Joueur théorique moyen (baseline)


class EloRouter:
    """
    Gestionnaire de scores Elo pour le meta-learning du routing.

    Thread-safe : connexion SQLite thread-local + threading.Lock pour les écritures.
    """

    def __init__(self, db_path: Optional[str] = None, k_factor: int = K_FACTOR):
        """
        Args:
            db_path:  Chemin vers moteur_runtime.db
            k_factor: Facteur K Elo (défaut: 32 comme aux échecs)
        """
        self.db_path  = db_path or DEFAULT_DB
        self.k_factor = k_factor
        self._lock    = threading.Lock()
        self._local   = threading.local()

        self._init_database()
        self._init_scores()
        logger.info(
            f"[ELO ROUTER] Initialisé — k={k_factor}, "
            f"types={ROUTING_TYPES}"
        )

    # ──────────────────────────────────────────────────────────────
    # Connexion SQLite thread-local
    # ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Retourne une connexion SQLite locale au thread courant."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                timeout=30,
                isolation_level=None,  # autocommit
            )
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_database(self) -> None:
        """Crée la table elo_routing si elle n'existe pas encore."""
        with self._lock:
            c = self._conn()
            c.execute("""
                CREATE TABLE IF NOT EXISTS elo_routing (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    routing_type TEXT NOT NULL,
                    elo_score    REAL DEFAULT 1200,
                    wins         INTEGER DEFAULT 0,
                    losses       INTEGER DEFAULT 0,
                    draws        INTEGER DEFAULT 0,
                    last_updated TEXT
                )
            """)
            c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_elo_routing_type
                ON elo_routing(routing_type)
            """)

    def _init_scores(self) -> None:
        """Initialise les 4 routing_types avec Elo=1200 si absents."""
        with self._lock:
            now = datetime.now().isoformat()
            for rt in ROUTING_TYPES:
                self._conn().execute(
                    "INSERT OR IGNORE INTO elo_routing "
                    "(routing_type, elo_score, wins, losses, draws, last_updated) "
                    "VALUES (?, ?, 0, 0, 0, ?)",
                    (rt, INITIAL_ELO, now),
                )

    # ──────────────────────────────────────────────────────────────
    # Mise à jour
    # ──────────────────────────────────────────────────────────────

    async def update_score(
        self, routing_type: str, reviewer_score: float
    ) -> Dict:
        """
        Met à jour le score Elo depuis l'évaluation ReviewerAgent.

        Args:
            routing_type:   Type de routing évalué
            reviewer_score: Score ReviewerAgent 1-10

        Returns:
            {routing_type, old_elo, new_elo, delta, outcome: win|draw|loss}
        """
        # Convertir score → outcome Elo (cœur partagé core.elo_core)
        outcome, outcome_str = reviewer_score_to_outcome(reviewer_score)

        def _update():
            with self._lock:
                c   = self._conn()
                row = c.execute(
                    "SELECT elo_score, wins, losses, draws "
                    "FROM elo_routing WHERE routing_type = ?",
                    (routing_type,),
                ).fetchone()

                if not row:
                    raise ValueError(f"[ELO ROUTER] routing_type inconnu : {routing_type}")

                old_elo = float(row["elo_score"])
                wins    = int(row["wins"])
                losses  = int(row["losses"])
                draws   = int(row["draws"])

                # Formule Elo classique (cœur partagé core.elo_core)
                expected = expected_score(old_elo, OPPONENT_ELO)
                new_elo  = round(updated_elo(old_elo, outcome, expected, self.k_factor), 2)

                # Mise à jour compteurs
                if outcome == 1.0:
                    wins += 1
                elif outcome == 0.0:
                    losses += 1
                else:
                    draws += 1

                c.execute(
                    "UPDATE elo_routing SET elo_score=?, wins=?, losses=?, draws=?, "
                    "last_updated=? WHERE routing_type=?",
                    (new_elo, wins, losses, draws, datetime.now().isoformat(), routing_type),
                )

                delta = round(new_elo - old_elo, 2)
                logger.info(
                    f"[ELO ROUTER] {routing_type} : "
                    f"{old_elo:.1f} → {new_elo:.1f} "
                    f"(Δ={delta:+.1f}, {outcome_str}, reviewer={reviewer_score}/10)"
                )
                return {
                    "routing_type": routing_type,
                    "old_elo":      old_elo,
                    "new_elo":      new_elo,
                    "delta":        delta,
                    "outcome":      outcome_str,
                }

        return await asyncio.to_thread(_update)

    # ──────────────────────────────────────────────────────────────
    # Lecture
    # ──────────────────────────────────────────────────────────────

    def get_best_routing(self, candidates: Optional[List[str]] = None) -> str:
        """
        Retourne le routing_type avec le meilleur Elo parmi les candidats.

        Args:
            candidates: Sous-ensemble à considérer (défaut: tous les 4)

        Returns:
            routing_type gagnant
        """
        targets = candidates or ROUTING_TYPES
        placeholders = ",".join("?" * len(targets))
        rows = self._conn().execute(
            f"SELECT routing_type, elo_score FROM elo_routing "
            f"WHERE routing_type IN ({placeholders}) ORDER BY elo_score DESC LIMIT 1",
            targets,
        ).fetchall()

        if not rows:
            logger.warning("[ELO ROUTER] Aucun candidat — retour défaut")
            return targets[0]

        best = rows[0]["routing_type"]
        logger.debug(f"[ELO ROUTER] Meilleur routing : {best}")
        return best

    def get_all_scores(self) -> Dict[str, Dict]:
        """
        Retourne tous les scores Elo avec statistiques.

        Returns:
            {routing_type: {elo, wins, losses, draws, win_rate}}
        """
        rows = self._conn().execute(
            "SELECT * FROM elo_routing ORDER BY elo_score DESC"
        ).fetchall()

        result = {}
        for r in rows:
            total = (r["wins"] or 0) + (r["losses"] or 0) + (r["draws"] or 0)
            win_rate = round(r["wins"] / total * 100, 1) if total > 0 else 0.0
            result[r["routing_type"]] = {
                "elo":      round(float(r["elo_score"]), 1),
                "wins":     r["wins"],
                "losses":   r["losses"],
                "draws":    r["draws"],
                "win_rate": win_rate,
            }
        return result

    async def get_recommendation(
        self,
        dominant_category: str,
        ml_prediction: Optional[str] = None,
        ml_confidence: float = 0.0,
    ) -> Dict:
        """
        Recommandation combinée Elo + MLRouter.

        Règle :
          - Si MLRouter conf >= 0.75 ET elo[ml_prediction] >= 1150 → suivre ML
          - Sinon → suivre le meilleur Elo

        Args:
            dominant_category: Catégorie router actuel
            ml_prediction:     Prédiction MLRouter (optionnel)
            ml_confidence:     Confiance MLRouter (0-1)

        Returns:
            {recommended, reason, elo, ml_used}
        """
        scores = await asyncio.to_thread(self.get_all_scores)
        best_elo_type  = max(scores, key=lambda t: scores[t]["elo"])
        best_elo_score = scores[best_elo_type]["elo"]

        ml_used = False
        reason  = ""

        if ml_prediction and ml_confidence >= 0.75:
            ml_elo = scores.get(ml_prediction, {}).get("elo", 0)
            if ml_elo >= 1150:
                recommended = ml_prediction
                ml_used     = True
                reason = (
                    f"ML prédit {ml_prediction} (conf={ml_confidence:.0%}) "
                    f"avec Elo={ml_elo:.0f} ≥ 1150"
                )
            else:
                recommended = best_elo_type
                reason = (
                    f"ML prédit {ml_prediction} mais Elo={ml_elo:.0f} < 1150 "
                    f"→ meilleur Elo : {best_elo_type} ({best_elo_score:.0f})"
                )
        else:
            recommended = best_elo_type
            reason = f"Meilleur Elo : {best_elo_type} ({best_elo_score:.0f})"
            if ml_prediction:
                reason += f" | ML conf trop faible ({ml_confidence:.0%})"

        logger.info(f"[ELO ROUTER] Recommandation → {recommended} ({reason})")

        return {
            "recommended": recommended,
            "reason":      reason,
            "elo":         scores.get(recommended, {}).get("elo", INITIAL_ELO),
            "ml_used":     ml_used,
        }


# ──────────────────────────────────────────────────────────────────
# Singleton thread-safe
# ──────────────────────────────────────────────────────────────────

_elo_instance: Optional[EloRouter] = None
_elo_lock = threading.Lock()


def get_elo_router() -> EloRouter:
    """Retourne le singleton EloRouter (double-check locking)."""
    global _elo_instance
    if _elo_instance is None:
        with _elo_lock:
            if _elo_instance is None:
                _elo_instance = EloRouter()
    return _elo_instance
