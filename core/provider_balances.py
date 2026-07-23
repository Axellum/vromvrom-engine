"""
core/provider_balances.py — Solde en direct auprès des providers qui exposent
une API officielle dédiée (lecture seule, clé déjà présente dans .env).

Contrairement au scraping Chrome (GCP/Claude.ai, cf. tools/billing_scraper.js
+ core/billing_sync.py), ces providers documentent un endpoint HTTP simple :
pas de navigateur headless nécessaire, juste un GET authentifié.

Sources vérifiées par recherche web + test live le 07/07/2026 (pas de simple
endpoint de solde documenté pour Mistral/Cohere/Cerebras/Zhipu à cette date —
ne pas en inventer un) :
- DeepSeek   : https://api-docs.deepseek.com/api/get-user-balance
- OpenRouter : https://openrouter.ai/docs/faq (GET /api/v1/credits)
- MiniMax    : GET /v1/token_plan/remains (testé en direct : le compte d'Axel
  n'a pas de forfait "Token Plan" actif, facturation au solde pay-as-you-go —
  MiniMax n'a PAS d'API de solde pour ce mode, uniquement pour le forfait).
- xAI        : Management API (https://docs.x.ai/developers/rest-api-reference/management/billing),
  GET /v1/billing/teams/{team_id}/prepaid/balance, clé "Management Key" séparée
  de la clé API standard. Testé en direct 07/07/2026 : `total.val` est en
  CENTS et SIGNE INVERSÉ par rapport au solde réel (confirmé par Axel : compte
  à $10.00, API renvoyait total.val="-1000") → balance_usd = -val / 100.
"""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)


async def fetch_deepseek_balance_usd() -> float | None:
    """
    Solde DeepSeek réel via l'API officielle, ou None si clé absente/indisponible.
    Ne renvoie une valeur que si le compte est facturé en USD (le endpoint peut
    aussi renvoyer du CNY selon la région du compte) — sinon laisser le fallback
    scrapé (core.token_tracker.real_billing) prendre le relais.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                for info in data.get("balance_infos", []):
                    if info.get("currency") == "USD":
                        return float(info["total_balance"])
                return None
    except Exception as e:
        logger.warning(f"[BALANCE] DeepSeek indisponible : {e}")
        return None


async def fetch_minimax_token_plan_status() -> dict | None:
    """
    Statut du forfait "Token Plan" MiniMax via l'API officielle (GET
    /v1/token_plan/remains, même clé que les appels moteur). Ne donne PAS un
    solde pay-as-you-go (MiniMax n'expose aucune API pour ce mode) — sert
    juste à distinguer les deux systèmes de facturation MiniMax (forfait
    prépayé vs pay-as-you-go au solde), utile pour comprendre pourquoi aucun
    chiffre n'est disponible automatiquement.
    """
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.minimax.io/v1/token_plan/remains",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                has_plan = data.get("model_remains") is not None
                return {"has_token_plan": has_plan, "model_remains": data.get("model_remains")}
    except Exception as e:
        logger.warning(f"[BALANCE] MiniMax token_plan indisponible : {e}")
        return None


async def fetch_xai_balance_usd() -> float | None:
    """
    Solde prépayé xAI réel via la Management API officielle (nécessite
    XAI_MANAGEMENT_API_KEY + XAI_TEAM_ID dans .env — clé "Management Key"
    distincte de XAI_API_KEY, créée dans console.x.ai avec accès "Billing"
    en lecture seule uniquement).

    `total.val` de la réponse est en cents et son signe est inversé par
    rapport au solde réel (confirmé empiriquement avec Axel le 07/07/2026 :
    compte à $10.00, API renvoyait total.val="-1000") — d'où le `-val/100`.
    """
    api_key = os.environ.get("XAI_MANAGEMENT_API_KEY")
    team_id = os.environ.get("XAI_TEAM_ID")
    if not api_key or not team_id:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://management-api.x.ai/v1/billing/teams/{team_id}/prepaid/balance",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                total_val = data.get("total", {}).get("val")
                if total_val is None:
                    return None
                return round(-float(total_val) / 100, 2)
    except Exception as e:
        logger.warning(f"[BALANCE] xAI indisponible : {e}")
        return None


async def fetch_openrouter_key_info() -> dict | None:
    """
    Usage réel + limites de la clé OpenRouter via l'API officielle (GET
    /api/v1/key) : limit (plafond de dépense configuré sur la clé, None si
    aucun), limit_remaining, usage (all-time), usage_daily/weekly/monthly.
    Plus riche que /credits (solde global) — utile pour une vraie page de
    gestion de limites, pas juste un chiffre de solde.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                return (await resp.json()).get("data", {})
    except Exception as e:
        logger.warning(f"[BALANCE] OpenRouter /key indisponible : {e}")
        return None


async def fetch_openrouter_credits() -> dict | None:
    """
    Crédits OpenRouter réels via l'API officielle : {total_credits, total_usage,
    balance}. None si clé absente/indisponible (`total_credits - total_usage`
    est une estimation ; le tableau de bord OpenRouter reste la source
    d'autorité en cas de doute, cf. FAQ officielle).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[BALANCE] OpenRouter HTTP {resp.status} : {(await resp.text())[:200]}")
                    return None
                data = (await resp.json()).get("data", {})
                total_credits = float(data.get("total_credits", 0.0))
                total_usage = float(data.get("total_usage", 0.0))
                return {
                    "total_credits": total_credits,
                    "total_usage": total_usage,
                    "balance": round(total_credits - total_usage, 4),
                }
    except Exception as e:
        logger.warning(f"[BALANCE] OpenRouter indisponible : {e}")
        return None
