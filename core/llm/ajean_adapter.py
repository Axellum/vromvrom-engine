"""
core/llm/ajean_adapter.py — Adaptateur AJEAN pour les workflows (DreamCoder / DAG).

AJEAN = llama.cpp (`llama-server`) sur le PC local, API OpenAI-compatible
port 8080. C'est le SEUL local élu pour les workflows : Ollama et LM Studio
restent déclarés dans le registre (#T354) mais hors élection.

Les drapeaux `llama-server` ci-dessous sont ceux mesurés le 17/08 (#T353) :
Qwen2.5-14B-Instruct-1M Q4_K_M, GPU locale 16 Go, contexte 32k, cache KV q8_0.
On ne remplace PAS ce GGUF sur le port 8080 (le vocal / Tab s'en sert aussi).
"""

from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import urlparse

from core.openai_compat_provider import AJEAN_DEFAULT_MODEL, OPENAI_COMPAT_PROVIDERS

logger = logging.getLogger("ajean_adapter")

# Hôtes AJEAN, dans l'ordre de préférence pour un workflow.
# ajean_pc = llama-server du PC (LAN, joignable depuis le Deck).
# ajean_deck = même API en loopback si une instance tourne sur l'hôte local.
PROVIDERS_WORKFLOW: tuple[str, ...] = ("ajean_pc", "ajean_deck")

# Locaux encore au catalogue, jamais élus pour DreamCoder / BudgetGuard.
PROVIDERS_LOCAUX_HORS_ELECTION: frozenset[str] = frozenset({
    "ollama",
    "lmstudio",
    "ollama_local",
    "ollama_pc",
    "local",
    "deck_ollama",
})

# Profil llama-server mesuré (#T353). Source de vérité pour le lancement
# hors moteur (`ajean.exe start` / ligne de commande). Le moteur n'exécute
# pas ce binaire : il parle HTTP à l'API déjà levée.
LLAMA_SERVER_PROFILE: dict[str, object] = {
    "model": AJEAN_DEFAULT_MODEL,
    "n_gpu_layers": 999,
    "ctx_size": 32768,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "host": "0.0.0.0",  # LAN : le Deck doit joindre 192.168.1.10:8080
    "port": 8080,
    "jinja": True,
    "timeouts_s": (2.0, 120.0),  # connect court, read long (générations executor)
    "json_prompt_only": True,    # pas de response_format : llama.cpp 500 peg-native
    "temperature_workflow": 0.0,
}


def llama_server_argv(gguf_path: str, *, binaire: str = "llama-server") -> list[str]:
    """Ligne de commande documentée — pas lancée par le moteur.

    `gguf_path` = chemin Windows du `.gguf` (ex. celui géré par AJEAN).
    """
    p = LLAMA_SERVER_PROFILE
    argv = [
        binaire,
        "-m", gguf_path,
        "-ngl", str(p["n_gpu_layers"]),
        "-c", str(p["ctx_size"]),
        "-ctk", str(p["cache_type_k"]),
        "-ctv", str(p["cache_type_v"]),
        "--host", str(p["host"]),
        "--port", str(p["port"]),
    ]
    if p.get("jinja"):
        argv.append("--jinja")
    return argv


def base_url_models(provider_id: str) -> str | None:
    """URL `/v1/models` du provider AJEAN (sonde cheap, pas une complétion)."""
    cfg = OPENAI_COMPAT_PROVIDERS.get(provider_id) or {}
    chat_url = str(cfg.get("base_url") or "")
    if not chat_url:
        return None
    if chat_url.endswith("/chat/completions"):
        return chat_url[: -len("/chat/completions")] + "/models"
    parsed = urlparse(chat_url)
    return f"{parsed.scheme}://{parsed.netloc}/v1/models"


def premier_eligible(candidats: Iterable[str]) -> str | None:
    """Premier provider AJEAN présent dans `candidats`, sinon None."""
    presents = set(candidats)
    for pid in PROVIDERS_WORKFLOW:
        if pid in presents:
            return pid
    return None


def providers_workflow_actifs() -> tuple[str, ...]:
    """Hôtes AJEAN que l'exploitation laisse aux workflows, dans l'ordre de préférence.

    06/10 Axel : AJEAN du PC ne démarre plus au logon (il prenait 9 Go de VRAM
    pendant les jeux). C'est le réglage qui fait foi, pas le code : un hôte
    absent de config.json → routing_policy.providers_sondes n'est plus ni sondé,
    ni élu, ni interrogé par GET /v1/models. Pour le remettre : l'ajouter à la
    liste (relue à chaud), sans redéployer.
    """
    from core.sante_providers import candidats_sondes

    sondes = set(candidats_sondes())
    return tuple(pid for pid in PROVIDERS_WORKFLOW if pid in sondes)


async def ajean_http_ok(provider_id: str, timeout_s: float = 3.0) -> bool:
    """True si `/v1/models` répond 200. Aucune complétion (piège DashScope)."""
    url = base_url_models(provider_id)
    if not url:
        return False
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout_s) as response:
                return response.status == 200
    except Exception as err:
        logger.debug("[AJEAN] %s injoignable : %s", provider_id, err)
        return False
