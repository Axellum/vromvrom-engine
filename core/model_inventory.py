"""
core/model_inventory.py — Instantané de ce que les API offrent réellement (#T243).

Le catalogue (`models_registry.db`) dit ce qu'on *croit* avoir ; les endpoints de
listing des providers disent ce qui *existe* chez eux aujourd'hui. Ce module conserve
le résultat du second dans un instantané sur disque, pour que l'IHM puisse afficher
le troisième signal de curation — « absent du listing de son API » — sans déclencher
une douzaine d'appels réseau à chaque affichage du registre.

⚠️ **Ce signal ne prouve JAMAIS qu'un modèle est mort.** Vérifié le 10/08 : un endpoint
`/models` n'expose pas forcément les alias encore servis — `deepseek-chat` est absent
du listing de DeepSeek et répond pourtant HTTP 200, alors que c'est l'un des modèles
les plus appelés en production. Seul un appel réel prouve une absence (c'est ainsi
qu'a été confirmé `gemma-4-31b-cerebras` → HTTP 404). D'où le vocabulaire retenu
partout ici et dans l'IHM : « absent du listing », jamais « mort » ni « supprimé ».

L'instantané est de l'état runtime (comme `config.json`) : gitignoré, écrit
atomiquement via `core.safe_io`.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from core.safe_io import safe_json_write

logger = logging.getLogger(__name__)

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHEMIN_INSTANTANE = os.path.join(RACINE, "data", "model_inventory.json")

# Suffixes/préfixes que le CATALOGUE ajoute pour désambiguïser un identifiant, et que
# l'API ne connaît évidemment pas. Sans ce détourage, une trentaine de modèles seraient
# signalés « absents » à tort : le catalogue porte `gemini-3.5-flash-paid` là où l'API
# annonce `gemini-3.5-flash`, `deepinfra/deepseek-r1` pour `deepseek-r1`, etc.
# (Le champ propre est `ModelSpec.api_model_id`, #T231 — mais la table `models` ne le
# porte pas encore ; ce détourage est le repli tant que la migration n'est pas faite.)
_SUFFIXES_CATALOGUE = ("-free", "-paid", "-cli", "-preview-paid")


def _variantes(model_id: str, provider_id: str) -> set[str]:
    """Noms sous lesquels ce modèle du catalogue peut apparaître chez son API."""
    base = (model_id or "").strip()
    variantes = {base, base.lower()}

    sans_prefixe = base.split("/", 1)[1] if "/" in base else base
    variantes |= {sans_prefixe, sans_prefixe.lower()}

    for depart in (base, sans_prefixe):
        for suffixe in _SUFFIXES_CATALOGUE:
            if depart.endswith(suffixe):
                coupe = depart[: -len(suffixe)]
                variantes |= {coupe, coupe.lower()}

    # Préfixe explicite du provider (`dashscope/qwen…` → `qwen…`)
    if provider_id and base.lower().startswith(f"{provider_id.lower()}/"):
        coupe = base[len(provider_id) + 1:]
        variantes |= {coupe, coupe.lower()}

    return {v for v in variantes if v}


def charger() -> dict[str, Any] | None:
    """Lit l'instantané, ou None s'il n'a jamais été généré / est illisible."""
    if not os.path.exists(CHEMIN_INSTANTANE):
        return None
    try:
        import json
        with open(CHEMIN_INSTANTANE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("[INVENTAIRE] Instantané illisible (%s) : %s", CHEMIN_INSTANTANE, exc)
        return None


def offert_par_api(
    provider_id: str, model_id: str, instantane: dict[str, Any] | None
) -> bool | None:
    """True / False / **None si on ne sait pas**.

    None couvre les deux cas d'ignorance, qu'il ne faut surtout pas confondre avec
    « absent » : aucun instantané généré, ou provider qui n'a pas répondu lors du
    dernier passage (sans clé, HTTP 4xx, injoignable).
    """
    if not instantane:
        return None
    entree = (instantane.get("providers") or {}).get(provider_id)
    if not entree or entree.get("statut") != "OK":
        return None
    offerts = {str(m) for m in entree.get("offerts", [])}
    offerts_bas = {m.lower() for m in offerts}
    for variante in _variantes(model_id, provider_id):
        if variante in offerts or variante.lower() in offerts_bas:
            return True
    return False


def rafraichir() -> dict[str, Any]:
    """Interroge toutes les API et réécrit l'instantané. Bloquant (appels réseau).

    Rendu tel quel pour que l'appelant puisse afficher le bilan ; la route HTTP le
    lance dans un thread pour ne pas bloquer la boucle asyncio.
    """
    from tools.discover_provider_models import SPECIAUX, _endpoints_openai_compat, interroger

    endpoints = {**_endpoints_openai_compat(), **SPECIAUX}
    providers: dict[str, Any] = {}
    for pid, conf in endpoints.items():
        statut, modeles, detail = interroger(pid, conf)
        providers[pid] = {"statut": statut, "detail": detail, "offerts": modeles}

    instantane = {
        "genere_le": datetime.now(UTC).isoformat(timespec="seconds"),
        "providers": providers,
    }
    os.makedirs(os.path.dirname(CHEMIN_INSTANTANE), exist_ok=True)
    safe_json_write(CHEMIN_INSTANTANE, instantane)

    ok = sum(1 for p in providers.values() if p["statut"] == "OK")
    logger.info(
        "[INVENTAIRE] %d/%d provider(s) ont répondu, %d modèle(s) recensés.",
        ok, len(providers), sum(len(p["offerts"]) for p in providers.values()),
    )
    return instantane


def resume(instantane: dict[str, Any] | None) -> dict[str, Any]:
    """Bilan compact pour l'IHM (sans la liste complète des modèles)."""
    if not instantane:
        return {"genere_le": None, "providers_ok": 0, "providers_total": 0, "modeles_recenses": 0}
    providers = instantane.get("providers") or {}
    return {
        "genere_le": instantane.get("genere_le"),
        "providers_ok": sum(1 for p in providers.values() if p.get("statut") == "OK"),
        "providers_total": len(providers),
        "modeles_recenses": sum(len(p.get("offerts") or []) for p in providers.values()),
        "par_provider": {
            pid: {"statut": p.get("statut"), "offerts": len(p.get("offerts") or [])}
            for pid, p in providers.items()
        },
    }
