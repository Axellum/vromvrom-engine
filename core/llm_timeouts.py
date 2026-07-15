"""
core/llm_timeouts.py — Timeouts HTTP centralisés par famille de provider (#T138).

Avant : valeurs dispersées en dur dans chaque provider (Gemini 120s,
Anthropic 180s, Claude CLI 240s...). Un seul point de vérité désormais,
overridable via `config.json` (clé `provider_timeouts`) sans toucher au
code des providers, ex. :

    "provider_timeouts": {
        "gemini": [5.0, 90.0],
        "claude_cli": 300.0
    }
"""
import os
import json
import logging

logger = logging.getLogger("core.llm_timeouts")

# (connect_timeout, read_timeout) pour les familles HTTP request/response classiques ;
# une seule valeur (secondes) pour les familles à invocation bloquante (CLI).
DEFAULT_TIMEOUTS = {
    "gemini": (5.0, 120.0),
    "openai_compat": (5.0, 120.0),
    "lmstudio": (2.0, 120.0),   # provider LOCAL (LAN) : connect timeout plus court, réseau rapide
    "anthropic": (5.0, 180.0),
    "claude_cli": 240.0,
}

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
_overrides_cache = None


def _load_overrides() -> dict:
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _overrides_cache = json.load(f).get("provider_timeouts", {})
    except Exception as e:
        logger.debug(f"[LLMTimeouts] Pas d'override config.json ({e}), défauts utilisés.")
        _overrides_cache = {}
    return _overrides_cache


def get_timeout(family: str):
    """Retourne le timeout pour une famille de provider (tuple (connect, read) ou float)."""
    value = _load_overrides().get(family, DEFAULT_TIMEOUTS[family])
    return tuple(value) if isinstance(value, list) else value
