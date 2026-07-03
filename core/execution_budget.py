# -*- coding: utf-8 -*-
"""
core/execution_budget.py — Budget global d'exécution par requête (P2-3.4).

Plafonne une requête sur TROIS axes vérifiés au fil du DAG et de la boucle agent
(planner × stages × executor × revues × healing) :
  - tokens   : total de tokens consommés par la session ;
  - durée    : temps écoulé depuis le début de la requête ;
  - coût     : coût USD estimé cumulé de la session.

Une limite à 0 (ou absente) désactive l'axe correspondant. Par défaut, seul le
plafond de tokens est actif (rétro-compatibilité avec `max_session_tokens`) ;
durée et coût sont opt-in via `config.json` (`max_execution_seconds`,
`max_execution_cost_usd`).

Le même objet est partagé entre `engine` (boucle séquentielle) et `dag_runner`
(DAG réactif) pour que les plafonds temps/coût soient comptés sur TOUTE la requête
(et non réinitialisés par sous-étape).
"""

import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ExecutionBudget:
    """Plafond d'exécution (tokens / durée / coût) pour une requête."""

    def __init__(
        self,
        session_id: str,
        max_tokens: int = 0,
        max_duration_s: float = 0.0,
        max_cost_usd: float = 0.0,
        start_time: Optional[float] = None,
    ):
        self.session_id = session_id
        self.max_tokens = int(max_tokens or 0)
        self.max_duration_s = float(max_duration_s or 0.0)
        self.max_cost_usd = float(max_cost_usd or 0.0)
        self.start_time = start_time if start_time is not None else time.time()

    @classmethod
    def from_config(
        cls,
        session_id: str,
        config: Optional[dict] = None,
        start_time: Optional[float] = None,
    ) -> "ExecutionBudget":
        """Construit le budget depuis config.json (clés `max_session_tokens`,
        `max_execution_seconds`, `max_execution_cost_usd`)."""
        if config is None:
            try:
                from core.llm_gateway import load_config
                config = load_config()
            except Exception:
                config = {}
        return cls(
            session_id,
            max_tokens=int(config.get("max_session_tokens", 500_000) or 0),
            max_duration_s=float(config.get("max_execution_seconds", 0) or 0),
            max_cost_usd=float(config.get("max_execution_cost_usd", 0) or 0),
            start_time=start_time,
        )

    def elapsed(self) -> float:
        """Secondes écoulées depuis le début de la requête."""
        return time.time() - self.start_time

    def check(self) -> Optional[Dict[str, Any]]:
        """
        Vérifie les trois axes. Renvoie `None` si la requête est dans le budget,
        sinon un dict décrivant le premier dépassement constaté :
        `{"reason", "metric", "value", "limit"}`.
        """
        # ── Tokens ──
        if self.max_tokens > 0:
            try:
                from core.token_tracker import get_session_total_tokens
                tokens = get_session_total_tokens(self.session_id)
            except Exception:
                tokens = 0
            if tokens >= self.max_tokens:
                return {"reason": "tokens", "metric": "tokens",
                        "value": tokens, "limit": self.max_tokens}

        # ── Durée ──
        if self.max_duration_s > 0:
            el = self.elapsed()
            if el >= self.max_duration_s:
                return {"reason": "duration", "metric": "seconds",
                        "value": round(el, 1), "limit": self.max_duration_s}

        # ── Coût ──
        if self.max_cost_usd > 0:
            try:
                from core.token_tracker import get_session_total_cost
                cost = get_session_total_cost(self.session_id)
            except Exception:
                cost = 0.0
            if cost >= self.max_cost_usd:
                return {"reason": "cost", "metric": "usd",
                        "value": round(cost, 4), "limit": self.max_cost_usd}

        return None

    def event_payload(self, violation: Dict[str, Any], blocked: str = "") -> Dict[str, Any]:
        """Construit le payload SSE `budget_exceeded` à partir d'un dépassement."""
        return {
            "reason": violation["reason"],
            "metric": violation["metric"],
            "value": violation["value"],
            "limit": violation["limit"],
            "session_tokens": violation["value"] if violation["reason"] == "tokens" else None,
            "max_tokens": self.max_tokens,
            "blocked": blocked,
        }
