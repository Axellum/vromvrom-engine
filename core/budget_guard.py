"""
Module : core.budget_guard.py
Version : V12 (Antigravity Engine)
Description : Gardien de budget et de quotas pour les agents du tab5-engine.
              Gère le basculement dynamique (cascade) entre les différents providers LLM
              en fonction de la disponibilité locale, des quotas gratuits et du budget quotidien.
              Optimisé pour Windows, ultra-fiable et thread-safe via db_lock_context.
"""

import os
import json
import time
import logging
import sqlite3
import asyncio
import aiohttp
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

# Imports requis du tab5-engine
from core.runtime_db import get_db_path, get_connection
from core.backlog_db import db_write_lock_context, db_read_lock_context

# Configuration du logging
logger = logging.getLogger("Antigravity.BudgetGuard")


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

    def _load_config(self) -> Dict[str, Any]:
        """
        Charge la configuration depuis le fichier config.json.
        Retourne des valeurs par défaut robustes en cas d'absence ou d'erreur.
        """
        defaults = {
            "gemini_free_tokens_per_hour": 1_000_000,
            "deepseek_free_requests_per_day": 200,
            "daily_budget_usd": 0.50
        }
        
        if not self.config_path.exists():
            logger.warning(f"Fichier de configuration introuvable à {self.config_path}. Utilisation des valeurs par défaut.")
            return defaults

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                bg_config = data.get("budget_guard", {})
                
                return {
                    "gemini_free_tokens_per_hour": bg_config.get("gemini_free_tokens_per_hour", defaults["gemini_free_tokens_per_hour"]),
                    "deepseek_free_requests_per_day": bg_config.get("deepseek_free_requests_per_day", defaults["deepseek_free_requests_per_day"]),
                    "daily_budget_usd": bg_config.get("daily_budget_usd", defaults["daily_budget_usd"])
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

    async def get_available_provider(self) -> Optional[str]:
        """
        Détermine le provider LLM optimal à utiliser selon la cascade de priorité :
        1. ollama (local, gratuit, illimité — runtime local préféré)
        2. lmstudio (local, gratuit, illimité — secours)
        3. gemini-free (quota horaire de tokens)
        4. deepseek-free (quota journalier de requêtes)
        5. anthropic-claude-haiku (budget financier quotidien)
        6. None (si tous les quotas/budgets sont épuisés)
        """
        if not self._initialized:
            await self.initialize()

        # 1. Test d'Ollama (Local, runtime préféré)
        if await self._check_ollama_availability():
            return "ollama"

        # 2. Test de LM Studio (Local, secours)
        if await self._check_lmstudio_availability():
            return "lmstudio"

        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        async with db_read_lock_context():
            def query_db_status() -> Tuple[int, int, float]:
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

        # 2. Test de Gemini Free
        if gemini_tokens < self.config["gemini_free_tokens_per_hour"]:
            return "gemini-free"

        # 3. Test de DeepSeek Free
        if deepseek_requests < self.config["deepseek_free_requests_per_day"]:
            return "deepseek-free"

        # 4. Test d'Anthropic Claude Haiku (Payant sous contrôle de budget)
        if global_cost < self.config["daily_budget_usd"]:
            return "anthropic-claude-haiku"

        # 5. Hors budget / Quotas épuisés
        logger.warning("Alerte critique : Tous les quotas et budgets LLM sont épuisés !")
        return None

    async def record_usage(self, provider: str, tokens: int, cost: float, model: str, window_type: str = "daily") -> None:
        """
        Enregistre la consommation d'un appel LLM dans l'historique de facturation.
        Assure la rétrocompatibilité avec l'ancien schéma de données.
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
                        "dreamcoder",
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

    async def get_quota_summary(self) -> Dict[str, Any]:
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
            def fetch_summary_data() -> Tuple[int, int, float]:
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