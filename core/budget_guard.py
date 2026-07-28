"""
Module : core.budget_guard.py
Version : V12 (Antigravity Engine)
Description : Gardien de budget et de quotas pour les agents du tab5-engine.
              Gère le basculement dynamique (cascade) entre les différents providers LLM
              en fonction de la disponibilité locale, des quotas gratuits et du budget quotidien.
              Optimisé pour Windows, ultra-fiable et thread-safe via db_lock_context.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiohttp

from core.backlog_db import db_read_lock_context, db_write_lock_context

# Imports requis du tab5-engine
from core.runtime_db import get_connection, get_db_path

# Configuration du logging
logger = logging.getLogger("Antigravity.BudgetGuard")

# Cache module-level (PAS un attribut d'instance) : BudgetGuard() est
# ré-instancié à chaque appel un peu partout (dreamer_agent.py, tests...),
# donc un cache par instance ne servirait à rien. Évite de rappeler l'API
# de solde DeepSeek à chaque décision de cascade (potentiellement plusieurs
# fois par seconde).
_deepseek_balance_cache: dict[str, Any] = {"value": None, "checked_at": 0.0}
_DEEPSEEK_BALANCE_CACHE_TTL = 600.0  # 10 min


async def _get_cached_deepseek_balance_usd() -> float | None:
    """Solde réel DeepSeek (core.provider_balances), rafraîchi au plus toutes les 10 min."""
    now = time.time()
    if now - _deepseek_balance_cache["checked_at"] < _DEEPSEEK_BALANCE_CACHE_TTL:
        return _deepseek_balance_cache["value"]
    from core.provider_balances import fetch_deepseek_balance_usd
    balance = await fetch_deepseek_balance_usd()
    _deepseek_balance_cache["value"] = balance
    _deepseek_balance_cache["checked_at"] = now
    return balance


# Cooldown module-level après échec d'un provider (ex: CLI Gemini/Claude non
# trustée dans l'environnement) — évite qu'une boucle de drainage (plusieurs
# tâches par cycle) retente en boucle un provider cassé sur chaque tâche du
# même cycle, ce qui brûlerait le temps du cycle sur des échecs répétés au
# lieu d'avancer sur le backlog via les providers suivants de la cascade.
_provider_cooldown: dict[str, float] = {}
_PROVIDER_COOLDOWN_SECONDS = 1800.0  # 30 min


def mark_provider_failed(provider: str) -> None:
    """
    Place un provider en cooldown après un échec d'exécution (appelé par
    agents/dreamer_agent.py sur un pipeline_err). get_available_provider()
    ne le re-proposera pas avant _PROVIDER_COOLDOWN_SECONDS.
    """
    _provider_cooldown[provider] = time.time()
    logger.warning(f"[BudgetGuard] Provider '{provider}' mis en cooldown ({_PROVIDER_COOLDOWN_SECONDS/60:.0f} min) après échec.")


def _in_cooldown(provider: str) -> bool:
    ts = _provider_cooldown.get(provider, 0.0)
    return (time.time() - ts) < _PROVIDER_COOLDOWN_SECONDS


# Index de rotation round-robin module-level (BudgetGuard() est réinstancié
# à chaque appel, cf. commentaire sur _deepseek_balance_cache) — fait tourner
# les API cloud gratuites disponibles plutôt que de toujours privilégier la
# même (retour Axel 07/07 : Ollama gagnait systématiquement, empêchant
# Gemini/Cerebras/Cohere/Mistral d'être jamais réellement exercés).
_free_api_rotation_index = 0


class BudgetGuard:
    """
    Gestionnaire de budget et de quotas pour les appels LLM.
    Assure la transition transparente entre les providers locaux (LM Studio),
    les paliers gratuits (Gemini, DeepSeek) et les paliers payants (Claude Haiku)
    tout en respectant strictement les limites financières définies.
    """

    def __init__(self) -> None:
        # [P1-2.4] Chemin dérivé de la racine du moteur (et non un chemin Windows
        # codé en dur `e:\...` qui n'existe pas sur le Deck en prod → la config
        # budget_guard n'était jamais chargée).
        self.config_path = Path(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        )
        self.db_path = get_db_path()
        self.config = self._load_config()
        self._initialized = False

    def _load_config(self) -> dict[str, Any]:
        """
        Charge la configuration depuis le fichier config.json.
        Retourne des valeurs par défaut robustes en cas d'absence ou d'erreur.
        """
        defaults = {
            "gemini_free_tokens_per_hour": 1_000_000,
            "deepseek_free_requests_per_day": 200,
            "daily_budget_usd": 0.50,
            "auditor_weekly_budget_usd": 1.00,
            "total_daily_budget_usd": 1.00
        }

        if not self.config_path.exists():
            logger.warning(f"Fichier de configuration introuvable à {self.config_path}. Utilisation des valeurs par défaut.")
            return defaults

        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
                bg_config = data.get("budget_guard", {})

                return {
                    "gemini_free_tokens_per_hour": bg_config.get("gemini_free_tokens_per_hour", defaults["gemini_free_tokens_per_hour"]),
                    "deepseek_free_requests_per_day": bg_config.get("deepseek_free_requests_per_day", defaults["deepseek_free_requests_per_day"]),
                    "daily_budget_usd": bg_config.get("daily_budget_usd", defaults["daily_budget_usd"]),
                    "total_daily_budget_usd": bg_config.get("total_daily_budget_usd", defaults["total_daily_budget_usd"]),
                    "auditor_weekly_budget_usd": bg_config.get("auditor_weekly_budget_usd", defaults["auditor_weekly_budget_usd"])
                }
        except Exception as e:
            logger.error(f"Erreur lors de la lecture de la configuration : {e}. Utilisation des valeurs par défaut.")
            return defaults

    async def initialize(self) -> None:
        """
        Initialise le composant et effectue la migration de la base de données à chaud si nécessaire.
        Garantit la présence de toutes les colonnes requises pour la V12.
        """
        if self._initialized:
            return

        logger.info("Initialisation du BudgetGuard et vérification du schéma de la base de données...")

        # #T64 : le schéma de billing_history (y compris model/tokens_used/cost_usd/window_type)
        # et sa migration additive sont désormais centralisés dans runtime_db._init_schema.
        # get_connection() crée/migre le schéma automatiquement → plus de définition dupliquée ici.
        async with db_write_lock_context():
            def ensure_schema():
                conn = get_connection()
                conn.close()

            await asyncio.to_thread(ensure_schema)

        self._initialized = True
        logger.info("BudgetGuard initialisé avec succès.")

    async def _check_lmstudio_availability(self) -> bool:
        """
        Vérifie si l'instance locale de LM Studio est active et répond.
        """
        url = "http://localhost:1234/v1/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=1.0) as response:
                    return response.status == 200
        except Exception:
            return False

    async def _check_ollama_availability(self) -> bool:
        """
        Vérifie si le démon Ollama local est actif et répond.
        Endpoint natif `/api/tags` (cohérent avec ollama_local = 127.0.0.1:11434
        dans openai_compat_provider.py). Préféré à LM Studio car il tourne aussi
        sur le Steam Deck et héberge directement notre fine-tune domotique.
        """
        url = "http://127.0.0.1:11434/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=1.0) as response:
                    return response.status == 200
        except Exception:
            return False

    def _check_gemini_cli_availability(self) -> bool:
        """
        Vérifie la présence locale du binaire Antigravity IDE (utilisé par
        GeminiCLIProvider, core/llm/providers/gemini.py) — pas une vérification
        de quota (l'abonnement Ultra est quasi-illimité), juste que le CLI existe
        sur cette machine. N'atteste PAS que le dialogue de confiance du
        workspace est accepté : un échec réel à l'exécution est absorbé par
        mark_provider_failed() (cooldown), pas par ce test préalable.
        """
        import shutil
        user_home = os.path.expanduser("~")
        candidates = [
            os.path.join(user_home, "AppData", "Local", "Programs", "Antigravity IDE", "bin", "antigravity-ide.cmd"),
            os.path.join(user_home, "AppData", "Local", "Programs", "Antigravity", "bin", "antigravity.cmd"),
        ]
        if any(os.path.exists(p) for p in candidates):
            return True
        return any(shutil.which(name) for name in ("antigravity-ide", "antigravity-ide.cmd", "antigravity", "antigravity.exe"))

    def _check_claude_cli_availability(self) -> bool:
        """
        Vérifie la présence locale du binaire `claude` (utilisé par
        ClaudeCLIProvider, core/llm/providers/deepseek.py) — même limite que
        _check_gemini_cli_availability() : présence du binaire seulement, pas
        du dialogue de confiance du workspace.
        """
        import shutil
        return shutil.which("claude") is not None

    async def get_available_provider(self) -> str | None:
        """
        Détermine le provider d'EXÉCUTION (executor) optimal (07/07, révisé
        suite à retour Axel : la 1ère version mettait Ollama en tête, or il
        tourne en permanence sur le Deck → il gagnait TOUJOURS, empêchant les
        API cloud gratuites listées (Gemini/Cerebras/Cohere/Mistral) d'être
        jamais utilisées. Nouvelle logique :

        1. Rotation round-robin parmi les API cloud gratuites/abonnements
           disponibles (gemini-cli-abo, claude-cli-abo, cerebras-free,
           dashscope-coding, cohere-free, mistral-free, gemini-free) —
           "lisse" l'usage entre providers au lieu qu'un seul domine
           systématiquement.
        2. Ollama/LM Studio en FILET DE SÉCURITÉ (local, gratuit, mais après
           les API cloud — domotique-qwen7b:q4 sur Ollama est un modèle 7B
           spécialisé HA, pas idéal pour du code généraliste, donc réservé au
           cas où rien d'autre n'est disponible).
        3. deepseek-free (quota + solde réel), puis anthropic-claude-haiku
           (payant, budget dédié) — coupés si total_daily_budget_usd atteint.
        4. None si tout est épuisé.

        Note : ceci reste une cascade de coût, pas un choix de modèle "adapté
        à la tâche" (ça, c'est core/provider_scorer.py::ProviderScorer, utilisé
        par le moteur interactif via LLMGateway.get_provider_for_tier — pas
        branché sur DreamCoder pour l'instant, chantier séparé).
        """
        if not self._initialized:
            await self.initialize()

        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        async with db_read_lock_context():
            def query_db_status() -> tuple[int, int, float]:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()

                    # Gemini : Somme des tokens utilisés dans la dernière heure
                    cursor.execute("""
                        SELECT COALESCE(SUM(tokens_used), 0)
                        FROM billing_history
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (one_hour_ago,))
                    gemini_tokens = int(cursor.fetchone()[0])

                    # DeepSeek : Nombre de requêtes dans les dernières 24 heures
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM billing_history
                        WHERE provider = 'deepseek-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    deepseek_requests = int(cursor.fetchone()[0])

                    # Global/Payant : Somme des coûts USD dans les dernières 24 heures
                    # (toutes sources confondues — sert de plafond quotidien COMBINÉ,
                    # pas seulement pour Claude Haiku, cf. total_daily_budget_usd).
                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0)
                        FROM billing_history
                        WHERE timestamp > ?
                    """, (twenty_four_hours_ago,))
                    global_cost = float(cursor.fetchone()[0])

                    return gemini_tokens, deepseek_requests, global_cost
                finally:
                    conn.close()

            gemini_tokens, deepseek_requests, global_cost = await asyncio.to_thread(query_db_status)

        # 1. Rotation round-robin parmi les API cloud gratuites/abonnements
        # réellement disponibles (clé présente / binaire trouvé / pas en
        # cooldown / quota non dépassé pour gemini-free).
        cloud_candidates: list[str] = []
        if not _in_cooldown("gemini-cli-abo") and self._check_gemini_cli_availability():
            cloud_candidates.append("gemini-cli-abo")
        if not _in_cooldown("claude-cli-abo") and self._check_claude_cli_availability():
            cloud_candidates.append("claude-cli-abo")
        if not _in_cooldown("cerebras-free") and os.getenv("CEREBRAS_API_KEY"):
            cloud_candidates.append("cerebras-free")
        # Coding Plan Alibaba (Lite) — forfait requêtes, amorti comme un abo.
        if not _in_cooldown("dashscope-coding") and (
            os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_CODING_PLAN_API_KEY")
        ):
            cloud_candidates.append("dashscope-coding")
        if not _in_cooldown("cohere-free") and os.getenv("COHERE_API_KEY"):
            cloud_candidates.append("cohere-free")
        if not _in_cooldown("mistral-free") and os.getenv("MISTRAL_API_KEY"):
            cloud_candidates.append("mistral-free")
        if not _in_cooldown("gemini-free") and gemini_tokens < self.config["gemini_free_tokens_per_hour"]:
            cloud_candidates.append("gemini-free")

        if cloud_candidates:
            global _free_api_rotation_index
            chosen = cloud_candidates[_free_api_rotation_index % len(cloud_candidates)]
            _free_api_rotation_index += 1
            return chosen

        # 2. Ollama/LM Studio — filet de sécurité local si aucune API cloud
        # gratuite/abonnement n'est disponible (toutes en cooldown/absentes).
        if await self._check_ollama_availability():
            return "ollama"
        if await self._check_lmstudio_availability():
            return "lmstudio"

        total_cap_reached = global_cost >= self.config["total_daily_budget_usd"]
        if total_cap_reached:
            logger.warning(
                f"[BudgetGuard] Plafond quotidien combiné atteint (${global_cost:.4f}/"
                f"{self.config['total_daily_budget_usd']} USD) — DeepSeek/Claude-Haiku coupés, "
                "gratuit/abonnement uniquement pour le reste de la journée."
            )
            return None

        # 3. DeepSeek Free — quota de requêtes ET solde réel (API officielle
        # DeepSeek, cache 10 min). Le comptage de requêtes seul ne dit rien si
        # le compte est simplement à sec ; on vérifie les deux.
        if deepseek_requests < self.config["deepseek_free_requests_per_day"]:
            balance = await _get_cached_deepseek_balance_usd()
            if balance is None or balance > 0.01:
                return "deepseek-free"
            logger.warning(
                f"[BudgetGuard] DeepSeek : quota requêtes OK mais solde réel quasi nul (${balance:.4f}) — provider sauté."
            )

        # 4. Anthropic Claude Haiku (payant sous contrôle de budget dédié)
        if global_cost < self.config["daily_budget_usd"]:
            return "anthropic-claude-haiku"

        # 5. Hors budget / Quotas épuisés
        logger.warning("Alerte critique : Tous les quotas et budgets LLM sont épuisés !")
        return None

    async def record_usage(self, provider: str, tokens: int, cost: float, model: str,
                            window_type: str = "daily", sync_source: str = "dreamcoder") -> None:
        """
        Enregistre la consommation d'un appel LLM dans l'historique de facturation.
        Assure la rétrocompatibilité avec l'ancien schéma de données.

        :param sync_source: Origine de l'appel (défaut 'dreamcoder' pour préserver le
            comportement existant). L'auditeur autonome (core/auditor_agent.py) passe
            'auditor' pour que ses dépenses soient comptabilisées séparément via
            get_scoped_spend_usd(), sans impacter le budget quotidien global de DreamCoder.
        """
        if not self._initialized:
            await self.initialize()

        timestamp = time.time()

        async with db_write_lock_context():
            def insert_usage():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO billing_history (
                            timestamp, provider, metric, value, currency, sync_source,
                            model, tokens_used, cost_usd, window_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        timestamp,
                        provider,
                        "tokens_and_cost",
                        cost,
                        "USD",
                        sync_source,
                        model,
                        tokens,
                        cost,
                        window_type
                    ))
                    conn.commit()
                    logger.info(f"Consommation enregistrée pour {provider} ({model}) : {tokens} tokens, {cost:.6f} USD.")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Erreur lors de l'enregistrement de la consommation : {e}")
                    raise e
                finally:
                    conn.close()

            await asyncio.to_thread(insert_usage)

    async def get_scoped_spend_usd(self, sync_source: str, since_seconds: float) -> float:
        """
        Coût cumulé (USD) attribué à une source d'appel donnée (ex: 'auditor')
        sur une fenêtre glissante — utilisé pour le plafond hebdomadaire de
        l'auditeur autonome, indépendant du budget quotidien global de DreamCoder.

        :param sync_source: Origine à filtrer (colonne billing_history.sync_source).
        :param since_seconds: Taille de la fenêtre glissante en secondes (ex: 7*86400).
        :return: Somme des coûts USD sur la fenêtre, 0.0 si aucune dépense.
        """
        if not self._initialized:
            await self.initialize()

        cutoff = time.time() - since_seconds

        async with db_read_lock_context():
            def query_scoped_spend() -> float:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0)
                        FROM billing_history
                        WHERE sync_source = ? AND timestamp > ?
                    """, (sync_source, cutoff))
                    return float(cursor.fetchone()[0])
                finally:
                    conn.close()

            return await asyncio.to_thread(query_scoped_spend)

    async def get_quota_summary(self) -> dict[str, Any]:
        """
        Retourne un état complet de l'utilisation des quotas et budgets pour l'IHM de supervision.
        """
        if not self._initialized:
            await self.initialize()

        ollama_online = await self._check_ollama_availability()
        lmstudio_online = await self._check_lmstudio_availability()
        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        async with db_read_lock_context():
            def fetch_summary_data() -> tuple[int, int, float]:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()

                    cursor.execute("""
                        SELECT COALESCE(SUM(tokens_used), 0) 
                        FROM billing_history 
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (one_hour_ago,))
                    gemini_used = int(cursor.fetchone()[0])

                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM billing_history 
                        WHERE provider = 'deepseek-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    deepseek_used = int(cursor.fetchone()[0])

                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0) 
                        FROM billing_history 
                        WHERE timestamp > ?
                    """, (twenty_four_hours_ago,))
                    cost_used = float(cursor.fetchone()[0])

                    return gemini_used, deepseek_used, cost_used
                finally:
                    conn.close()

            gemini_used, deepseek_used, cost_used = await asyncio.to_thread(fetch_summary_data)

        gemini_limit = self.config["gemini_free_tokens_per_hour"]
        deepseek_limit = self.config["deepseek_free_requests_per_day"]
        budget_limit = self.config["daily_budget_usd"]

        return {
            "timestamp": now,
            "providers": {
                "ollama": {
                    "available": ollama_online,
                    "metric": "disponibilité locale",
                    "used": 1 if ollama_online else 0,
                    "limit": 1,
                    "unit": "status"
                },
                "lmstudio": {
                    "available": lmstudio_online,
                    "metric": "disponibilité locale",
                    "used": 1 if lmstudio_online else 0,
                    "limit": 1,
                    "unit": "status"
                },
                "gemini-free": {
                    "available": gemini_used < gemini_limit,
                    "metric": "tokens_1h",
                    "used": gemini_used,
                    "limit": gemini_limit,
                    "unit": "tokens"
                },
                "deepseek-free": {
                    "available": deepseek_used < deepseek_limit,
                    "metric": "requêtes_24h",
                    "used": deepseek_used,
                    "limit": deepseek_limit,
                    "unit": "requêtes"
                },
                "anthropic-claude-haiku": {
                    "available": cost_used < budget_limit,
                    "metric": "budget_global_24h",
                    "used": round(cost_used, 4),
                    "limit": budget_limit,
                    "unit": "USD"
                }
            }
        }
