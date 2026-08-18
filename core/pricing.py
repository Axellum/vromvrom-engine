"""
core/pricing.py — Barème de prix unifié (P1-2.4).

Source de vérité UNIQUE des tarifs LLM : `pricing_strategy.json` (à la racine du
moteur). Auparavant, `token_tracker.PRICING` (codé en dur) et
`pricing_strategy.json` divergeaient — les coûts trackés ne correspondaient plus
au barème affiché.

Ce module aplatit `pricing_strategy.json` en un table `{modèle: prix/token USD}`
et expose `get_model_pricing(model)`. `token_tracker` délègue ici.

- `apis[].rates[model]` : coût réel par million de tokens (`*_cost_per_m`, ou
  `*_cost_per_m_eur` converti en USD via EUR_USD). Le tarif d'entrée servi par
  le cache est lu dans `input_cache_hit_cost_per_m` (USD) ou `cached_input_per_m_eur`
  (EUR), exposé sous la clé `cache` ; absent, il retombe sur `input` (#T347).
- `subscriptions[].models` et `local.models` : coût marginal nul (forfait/local).

Un petit barème de repli (`FALLBACK_PRICING`) couvre les alias/legacy absents du
JSON (ex. alias "gemini", "minimax", "claude") et le cas où le fichier manque.
"""

import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)

_PRICING_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pricing_strategy.json",
)

# Taux EUR→USD pour convertir les tarifs GCP libellés en EUR. Approximation
# surchargée par la variable d'environnement EUR_USD_RATE si définie.
try:
    EUR_USD = float(os.getenv("EUR_USD_RATE", "1.08"))
except ValueError:
    EUR_USD = 1.08

# Barème de repli (alias/legacy + secours si pricing_strategy.json est absent).
# Valeurs en USD par token. `cache` = tarif d'entrée servi par le cache (cache-hit).
# Quand le barème ne dit rien, `cache` vaut `input` (voir _normaliser_barème) : un
# modèle sans tarif cache coûte exactement ce qu'il coûtait avant cette PR.
FALLBACK_PRICING = {
    "deepseek-chat": {"input": 0.14 / 1_000_000, "output": 0.28 / 1_000_000, "cache": 0.003 / 1_000_000},
    "deepseek-reasoner": {"input": 0.55 / 1_000_000, "output": 2.19 / 1_000_000, "cache": 0.14 / 1_000_000},
    "gemini-2.5-pro": {"input": 1.25 / 1_000_000, "output": 5.00 / 1_000_000},
    "gemini-2.5-flash": {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000},
    "gemini": {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000},  # alias générique
    "minimax": {"input": 0.60 / 1_000_000, "output": 0.90 / 1_000_000},  # alias générique
    "claude": {"input": 0.0, "output": 0.0},
    "gemini-cli": {"input": 0.0, "output": 0.0},
    "local": {"input": 0.0, "output": 0.0},
}

_FREE = {"input": 0.0, "output": 0.0}

# Cache du barème chargé (invalidé sur changement de mtime du fichier).
_cache = {"table": None, "mtime": 0.0}


def _rate_to_usd_per_token(rates: dict, kind: str) -> float:
    """Convertit un tarif (`input`/`output`) du JSON en USD par token."""
    usd_m = rates.get(f"{kind}_cost_per_m")
    if usd_m is not None:
        return float(usd_m) / 1_000_000
    eur_m = rates.get(f"{kind}_cost_per_m_eur")
    if eur_m is not None:
        return float(eur_m) * EUR_USD / 1_000_000
    return 0.0


def _rate_cache_to_usd_per_token(rates: dict) -> float | None:
    """Convertit le tarif d'entrée cache-hit du JSON en USD par token.

    Deux formes coexistent dans `pricing_strategy.json` :
    - USD : `input_cache_hit_cost_per_m` (DeepSeek, GLM, Mistral…) ;
    - EUR : `cached_input_per_m_eur` (GCP, converti via EUR_USD), produit par
      `core/models_db.py` sous le nom `cached_input_per_m_eur`.

    Renvoie None quand le barème ne dit rien : l'appelant retombe alors sur le
    tarif d'entrée plein (`_normaliser_barème`).
    """
    usd_m = rates.get("input_cache_hit_cost_per_m")
    if usd_m is not None:
        return float(usd_m) / 1_000_000
    eur_m = rates.get("cached_input_per_m_eur")
    if eur_m is not None:
        return float(eur_m) * EUR_USD / 1_000_000
    return None


def _build_table(data: dict) -> dict:
    """Aplatit pricing_strategy.json en {modèle_minuscule: {input, output, cache}} (USD/token)."""
    table: dict = {}
    for api in data.get("apis", []):
        for model, rates in api.get("rates", {}).items():
            if not isinstance(rates, dict):
                continue
            table[model.lower()] = {
                "input": _rate_to_usd_per_token(rates, "input"),
                "output": _rate_to_usd_per_token(rates, "output"),
                "cache": _rate_cache_to_usd_per_token(rates),
            }
    # Forfaits (abonnements) et local : coût marginal nul pour le tracking.
    for sub in data.get("subscriptions", []):
        for model in sub.get("models", []):
            table.setdefault(model.lower(), dict(_FREE))
    for model in data.get("local", {}).get("models", []):
        table.setdefault(model.lower(), dict(_FREE))
    return table


def _normaliser_barème(res: dict) -> dict:
    """Garantit les clés {input, output, cache} ; `cache` retombe sur `input`.

    Le tarif cache n'est pas toujours renseigné (barème muet, repli, modèle
    gratuit, clé absente du JSON). Dans ce cas les tokens cache-hit DOIVENT
    coûter exactement le tarif d'entrée plein — jamais moins — pour ne rien
    changer au coût affiché d'un modèle sans cache (rétrocompatibilité au
    centime près).
    """
    out = dict(res)
    cache = out.get("cache")
    if cache is None:
        out["cache"] = out.get("input", 0.0)
    out.setdefault("output", 0.0)
    out.setdefault("input", 0.0)
    return out


def load_pricing_table() -> dict:
    """
    Charge (et met en cache) le barème depuis pricing_strategy.json.
    Recharge automatiquement si le fichier a changé (mtime). Renvoie un dict vide
    si le fichier est absent/illisible (le repli prend alors le relais).
    """
    try:
        mtime = os.path.getmtime(_PRICING_FILE)
    except OSError:
        return {}

    if _cache["table"] is not None and _cache["mtime"] == mtime:
        return _cache["table"]

    try:
        with open(_PRICING_FILE, encoding="utf-8") as f:
            data = json.load(f)
        table = _build_table(data)
        _cache["table"] = table
        _cache["mtime"] = mtime
        logger.info(f"[PRICING] Barème chargé depuis pricing_strategy.json ({len(table)} modèles).")
        return table
    except Exception as e:
        logger.warning(f"[PRICING] Échec du chargement de pricing_strategy.json : {e} — repli sur FALLBACK_PRICING.")
        return {}


def is_deepseek_peak_hours(now_utc: datetime.datetime) -> bool:
    """
    Détermine si l'instant donné en UTC correspond aux heures de pic de DeepSeek.
    
    À partir de mi-juillet 2026 (le 15 juillet inclusivement), les heures de pic sont :
    - 1:00 AM à 4:00 AM UTC (9:00 AM à 12:00 noon UTC+8)
    - 6:00 AM à 10:00 AM UTC (2:00 PM à 6:00 PM UTC+8)
    """
    if now_utc.date() < datetime.date(2026, 7, 15):
        return False
        
    t = now_utc.time()
    peak1_start = datetime.time(1, 0)
    peak1_end = datetime.time(4, 0)
    peak2_start = datetime.time(6, 0)
    peak2_end = datetime.time(10, 0)
    
    return (peak1_start <= t <= peak1_end) or (peak2_start <= t <= peak2_end)


def get_model_pricing(model: str) -> dict:
    """
    Retourne {"input": usd/token, "output": usd/token, "cache": usd/token} pour `model`.

    `cache` = tarif d'entrée servi par le cache (cache-hit). Quand le barème ne
    le renseigne pas, il retombe sur `input` : un modèle sans tarif cache coûte
    exactement ce qu'il coûtait avant cette PR (#T347).

    Ordre de résolution :
      1. Modèles gratuits/forfait par convention de nom (-free / -cli / claude / local) → 0.
      2. Barème pricing_strategy.json : correspondance exacte, puis sous-chaîne
         (clé de barème contenue dans le nom du modèle, la plus longue d'abord).
      3. Repli FALLBACK_PRICING (alias/legacy).
      4. 0 par défaut (modèle inconnu).
    """
    m = (model or "").lower()
    if not m:
        return _normaliser_barème(_FREE)

    if "-free" in m or "-cli" in m or m == "claude" or m == "local":
        return _normaliser_barème(_FREE)

    table = load_pricing_table()
    res = None
    if m in table:
        res = dict(table[m])
    else:
        for key in sorted(table, key=len, reverse=True):
            if key in m:
                res = dict(table[key])
                break
        if res is None:
            for key, price in FALLBACK_PRICING.items():
                if key in m:
                    res = dict(price)
                    break
            if res is None:
                res = dict(_FREE)

    res = _normaliser_barème(res)

    # Tarification pic-creux pour DeepSeek à partir de mi-juillet 2026.
    # Le doublement s'applique au tarif cache comme aux autres, par cohérence
    # (verdict #T347 : son bien-fondé n'est pas le sujet de cette PR).
    if "deepseek" in m and res:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if is_deepseek_peak_hours(now_utc):
            res = {k: v * 2.0 for k, v in res.items()}

    return res
