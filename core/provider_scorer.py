"""
core/provider_scorer.py — Scoring de routage coût/quota/latence par modèle (#T133).

Extrait de LLMGateway.get_provider_for_tier() (150+ lignes, mélangeait résolution
de tier + pénalités quota + pénalités financières + Elo + tri) — un God Object
identifié par l'audit core architecture du 04/07/2026. get_provider_for_tier()
ne fait plus que résoudre le tier, instancier les providers et trier via cette
classe ; toute la logique de scoring vit ici.
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ProviderScorer:
    """
    Calcule un score de priorité par modèle candidat (plus bas = plus prioritaire),
    combinant coût de base (models_registry.db ou heuristique), pénalités de
    saturation de quota, pénalité de solde DeepSeek critique, et latence live
    (tie-breaker plafonné au-delà du cascade_priority statique).
    """

    # channel → (clé dans get_quotas_status(), sous-clés de quota à vérifier)
    _QUOTA_KEYS = {
        "gemini-free-flash": ("gemini_free_flash", ("rpm", "tpm", "rpd")),
        "gemini-free-pro": ("gemini_free_pro", ("rpm", "tpm", "rpd")),
        "claude-cli-abo": ("claude_cli_abo", ("tph", "tpm")),
        "gemini-cli-abo": ("gemini_cli_abo", ("tph", "tpm")),
    }

    def __init__(self, model_names: list):
        from core.token_tracker import get_quotas_status
        try:
            self.quotas = get_quotas_status()
        except Exception as e:
            logger.warning(f"Impossible de récupérer les quotas de tokens pour le routage: {e}")
            self.quotas = {}

        # Pré-chargement GROUPÉ des routing scores depuis la BDD (1 requête pour N modèles).
        # Évite N ouvertures/fermetures SQLite dans la closure du tri + ne bloque pas
        # l'event loop car appelé depuis un thread via _DB_EXECUTOR si run_in_executor
        # est utilisé en amont.
        self._bulk_scores: Dict[str, float] = {}
        try:
            from core.models_db import get_bulk_routing_scores
            self._bulk_scores = get_bulk_routing_scores(model_names)
        except Exception:
            pass

    @staticmethod
    def _heuristic_base_score(m_lower: str, channel: str) -> float:
        """Fallback de scoring par heuristique (noms/channels) si la BDD n'est pas disponible."""
        if "local" in m_lower:
            return 1.0  # Local gratuit + confidentiel
        elif channel in ("gemini-free-flash", "gemini-free-pro"):
            return 2.0  # API Gratuit AI Studio
        elif channel == "gemini-cli-abo":
            return 3.0  # CLI Gemini Advanced (amorti ~0.20$/M)
        elif channel == "claude-cli-abo":
            return 3.5  # CLI Claude Pro (amorti ~0.57$/M)
        elif "deepseek-chat" in m_lower or "deepseek-v4-flash" in m_lower:
            return 4.0  # API Payant ultra low-cost DeepSeek Flash
        elif "deepseek-reasoner" in m_lower or "deepseek-v4-pro" in m_lower:
            return 4.5  # API Payant raisonnement DeepSeek
        elif "gemini-3.5-flash" in m_lower or "gemini-3.1-flash" in m_lower:
            return 5.0  # API Payant GCP standard
        else:
            return 6.0  # GCP Payant Pro ou autre

    def score(self, model_name: str) -> float:
        """
        Calcule un score de priorité (plus le score est bas, plus le modèle est prioritaire).
        Pénalise fortement les modèles dont le quota glissant est saturé à plus de 90%.
        Pénalise graduellement les modèles approchant de la saturation (>70%).
        Pénalise DeepSeek si le solde prépayé est critique (<1$).
        """
        from core.token_tracker import classify_model_channel
        from core.llm_gateway import get_live_latency_penalty

        m_lower = model_name.lower()
        channel = classify_model_channel(model_name)

        # 1. Détection de saturation de quota (pénalité graduée)
        saturation_penalty = 0.0
        quota_entry = self._QUOTA_KEYS.get(channel)
        if quota_entry:
            quota_name, keys = quota_entry
            if quota_name in self.quotas:
                q = self.quotas[quota_name]
                for key in keys:
                    if key in q and q[key]["limit"] > 0:
                        usage_ratio = q[key]["current"] / q[key]["limit"]
                        if usage_ratio >= 0.95:
                            saturation_penalty = max(saturation_penalty, 1000.0)
                        elif usage_ratio >= 0.80:
                            saturation_penalty = max(saturation_penalty, 200.0)
                        elif usage_ratio >= 0.70:
                            saturation_penalty = max(saturation_penalty, 50.0)

        # 2. Détection de solde DeepSeek critique
        deepseek_penalty = 0.0
        if "deepseek" in m_lower:
            try:
                ds_balance = self.quotas.get("deepseek_balance_usd")
                if ds_balance is not None:
                    if ds_balance < 0.5:
                        deepseek_penalty = 2000.0  # Solde quasi épuisé → bloquer
                    elif ds_balance < 1.0:
                        deepseek_penalty = 500.0   # Solde critique → fortement pénaliser
                    elif ds_balance < 5.0:
                        deepseek_penalty = 50.0    # Solde bas → légère pénalité
            except Exception as _e:
                from core.error_reporter import report_swallowed
                report_swallowed("provider_scorer.deepseek_balance_penalty", _e, level="debug")

        # 3. Priorité base : pré-chargé en bulk depuis models_registry.db (0 I/O ici).
        # Fallback heuristique si le modèle n'est pas dans la BDD (score retourné = 5.0).
        base_score = self._bulk_scores.get(model_name, 5.0)
        if base_score == 5.0 and model_name not in ("gemini-3.5-flash-paid",):
            base_score = self._heuristic_base_score(m_lower, channel)

        # 4. [#T118] Latence live (moyenne mobile par CircuitBreaker) — affine
        # l'arbitrage au-delà du cascade_priority statique, plafonné pour rester
        # un tie-breaker (pattern `lowest-latency` LiteLLM).
        latency_penalty = get_live_latency_penalty(model_name)

        total_score = base_score + saturation_penalty + deepseek_penalty + latency_penalty
        logger.debug(
            f"[Routing Score] {model_name}: base={base_score:.1f} sat={saturation_penalty:.0f} "
            f"ds={deepseek_penalty:.0f} lat={latency_penalty:.2f} → total={total_score:.1f}"
        )
        return total_score

    def sort_providers(self, providers_list: list, elo_order: Optional[list] = None) -> list:
        """
        Trie une liste [(model_name, provider), ...] par Elo (si fourni, priorité
        principale) puis par score coût/quota/latence (départage) ; sinon par
        score coût/quota/latence uniquement. Trie en place et retourne la liste.
        """
        if elo_order:
            # Construire un mapping Elo : nom normalisé → rang (0 = meilleur)
            elo_rank_map = {}
            for idx, entry in enumerate(elo_order):
                model_key = entry.get("model", "").strip().lower()
                if model_key:
                    elo_rank_map[model_key] = idx

            # Tri composite : d'abord par rang Elo (croissant = meilleur d'abord),
            # puis par score coût (croissant = moins cher d'abord) pour départager
            def _combined_sort_key(item):
                m_name = item[0].strip().lower()
                elo_rank = elo_rank_map.get(m_name, 999)  # Inconnu → fin de liste
                cost_score = self.score(item[0])
                return (elo_rank, cost_score)

            providers_list.sort(key=_combined_sort_key)
            logger.info(
                f"[LLMGateway] [ELO] Tri combiné Elo+coût appliqué — "
                f"ordre: {[p[0] for p in providers_list]}"
            )
        else:
            # Tri classique par score de coût/disponibilité uniquement
            providers_list.sort(key=lambda x: self.score(x[0]))

        return providers_list
