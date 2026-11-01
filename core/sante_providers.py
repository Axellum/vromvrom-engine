"""
core/sante_providers.py — Disponibilité RÉELLE des providers d'exécution (#T387).

POURQUOI CE MODULE EXISTE
La rotation de `BudgetGuard.get_available_provider()` retient un provider dès
que sa clé est présente dans l'environnement. Or « la clé est là » ne veut pas
dire « le provider peut inférer ». Mesuré sur le Deck le 18/08, sur les huit
providers d'exécution déclarés :

  ✅ mistral-free    359 ms      ✅ cohere-free    429 ms
  ✅ deepseek-chat   934 ms      ✅ gemini-free   1905 ms
  ❌ cerebras-free   HTTP 402 Payment Required      (clé présente)
  ❌ zai-glm-4.7     HTTP 404 model_archived        (escalade qualité)
  ❌ gemini-cli-abo  binaire `antigravity-ide` absent en prod
  ⚠️ claude-cli-abo  répond « Credit balance is too low » — succès APPARENT

Trois d'entre eux étaient donc élus par la rotation puis perdaient leur tâche.
Le cas `claude-cli-abo` est le plus vicieux : l'appel réussit, rend un texte, et
ce texte est un message d'erreur — un contrôle qui se contenterait d'un code de
retour le déclarerait sain. C'est la même leçon que la clé DashScope de juillet
(200 sur `/v1/models`, 401 sur une complétion) : **un contrôle de santé doit
exercer l'opération réelle, et regarder ce qu'elle rend.**

D'où une sonde qui demande un mot de passe convenu et vérifie qu'il revient.
Aucune liste de providers cassés n'est figée dans le code : ce qui marche est
constaté à chaque cycle, pas décrété.
"""

import asyncio
import logging
import time
import unicodedata
from typing import Any

logger = logging.getLogger("sante_providers")

# Provider d'exécution → modèle utilisé pour l'exécution brute d'une tâche.
# Source unique : `agents/dreamer_agent.py` importe cette table au lieu d'en
# tenir une copie, sinon les deux divergent au premier ajout de provider.
MODELE_PAR_PROVIDER: dict[str, str] = {
    "ollama": "ollama_local",
    "lmstudio": "local",
    "ajean_pc": "ajean_pc",  # llama.cpp workflows (23/08, seul local élu)
    "ajean_deck": "ajean_deck",
    "gemini-cli-abo": "gemini-3.5-flash-medium-cli",  # agy → gemini-3.7-flash-medium
    "claude-cli-abo": "claude-sonnet-4.6-thinking-cli",
    "cerebras-free": "gpt-oss-120b",  # id historique ; PayGo depuis D-15 (23/08)
    "cohere-free": "command-r-plus-08-2024",
    "mistral-free": "open-mistral-nemo",
    "gemini-free": "gemini-3.5-flash-free",
    "deepseek-free": "deepseek-chat",
    "anthropic-claude-haiku": "claude-haiku-4-5",
}


def candidats_sondes() -> list[str]:
    """Providers exercés par la sonde de santé en tête de cycle.

    Réglage d'exploitation : la liste vit dans config.json
    (routing_policy.providers_sondes). Repli : tous les providers de
    MODELE_PAR_PROVIDER sauf anthropic-claude-haiku (comportement historique).
    C'est ainsi qu'on retire de la rotation un hôte mort (ollama, LM Studio)
    sans toucher au code.
    """
    try:
        from core.llm_gateway import load_config
        cfg = load_config()
        liste = (cfg.get("routing_policy") or {}).get("providers_sondes")
        if isinstance(liste, list) and liste:
            return [str(p) for p in liste]
    except Exception:
        pass
    return [p for p in MODELE_PAR_PROVIDER if p != "anthropic-claude-haiku"]

# Mot de passe convenu. Court, sans accent, improbable dans un message d'erreur.
_CONSIGNE_SYSTEME = "Tu reponds en un seul mot, sans ponctuation ni explication."
_QUESTION = "Reponds exactement par le mot PRET"
_ATTENDU = "PRET"

# Une sonde n'a pas à être patiente : elle est jouée en tête de cycle, en
# parallèle, et son but est d'écarter ce qui ne répond pas MAINTENANT.
TIMEOUT_SONDE_S = 25.0


def _texte_de_reponse(brut: Any) -> str:
    """
    Ramène une réponse de provider à du texte comparable.

    Tous ne rendent pas une `str` : `open-mistral-nemo` rend le message brut
    (`{'role': 'assistant', 'content': …, 'tool_calls': None}`). Comparer la
    repr du dict au mot attendu déclarerait le provider mort à tort.
    """
    if brut is None:
        return ""
    if isinstance(brut, dict):
        for cle in ("content", "text", "response", "reasoning_content", "reasoning"):
            valeur = brut.get(cle)
            if isinstance(valeur, str) and valeur.strip():
                return valeur
        return ""
    return str(brut)


def _sans_accents(texte: str) -> str:
    """« PRÊT » et « PRET » désignent la même réponse — l'accent ne doit pas tuer un provider."""
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def reponse_valide(brut: Any) -> bool:
    """Le provider a-t-il rendu le mot convenu ? (et non un message d'erreur poli)"""
    return _ATTENDU in _sans_accents(_texte_de_reponse(brut)).upper()


async def sonder_un(gateway, provider: str, timeout_s: float = TIMEOUT_SONDE_S) -> dict:
    """
    Exerce UNE complétion réelle sur le modèle d'exécution du provider.

    :return: {"provider", "sain": bool, "ms": int, "motif": str}
    """
    modele = MODELE_PAR_PROVIDER.get(provider)
    if not modele:
        return {"provider": provider, "sain": False, "ms": 0, "motif": "provider inconnu"}

    instance = getattr(gateway, "providers", {}).get(modele)
    if instance is None:
        return {"provider": provider, "sain": False, "ms": 0,
                "motif": f"modèle '{modele}' absent du catalogue"}

    debut = time.time()
    try:
        brut = await asyncio.wait_for(
            instance.generate_async(_CONSIGNE_SYSTEME, _QUESTION, max_tokens=64),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return {"provider": provider, "sain": False,
                "ms": int((time.time() - debut) * 1000), "motif": f"timeout {timeout_s}s"}
    except Exception as e:
        return {"provider": provider, "sain": False,
                "ms": int((time.time() - debut) * 1000),
                "motif": f"{type(e).__name__}: {str(e)[:120]}"}

    ms = int((time.time() - debut) * 1000)
    if reponse_valide(brut):
        return {"provider": provider, "sain": True, "ms": ms, "motif": "ok"}

    # Le provider a répondu, mais pas ce qu'on lui demandait : quota épuisé,
    # refus, message d'erreur rendu comme du texte normal (claude-cli-abo).
    apercu = _texte_de_reponse(brut).strip().replace("\n", " ")[:100]
    return {"provider": provider, "sain": False, "ms": ms,
            "motif": f"réponse inattendue : {apercu!r}"}


async def sonder_providers(candidats: list[str], gateway=None,
                           timeout_s: float = TIMEOUT_SONDE_S) -> dict[str, dict]:
    """
    Sonde tous les candidats EN PARALLÈLE et rend le détail par provider.

    En parallèle parce que la sonde est un péage en tête de cycle : mesuré à
    2,5 s pour huit providers, contre 6,7 s en séquentiel. Ce temps est pris sur
    `dreamcoder_max_cycle_minutes`, le budget de toutes les tâches de la nuit.
    """
    if not candidats:
        return {}

    if gateway is None:
        from core.llm_gateway import LLMGateway
        # Signature : LLMGateway(deepseek_key, gemini_key). Ne JAMAIS lui passer
        # le dict de config en positionnel : sa repr partirait comme clé
        # DeepSeek et le provider reviendrait 401 — faux négatif spectaculaire.
        gateway = LLMGateway()

    debut = time.time()
    resultats = await asyncio.gather(
        *[sonder_un(gateway, p, timeout_s) for p in candidats],
        return_exceptions=True,
    )

    par_provider: dict[str, dict] = {}
    for candidat, res in zip(candidats, resultats):
        if isinstance(res, BaseException):
            par_provider[candidat] = {"provider": candidat, "sain": False, "ms": 0,
                                      "motif": f"sonde en erreur : {res}"}
        else:
            par_provider[candidat] = res

    sains = [p for p, r in par_provider.items() if r["sain"]]
    ecartes = [f"{p} ({r['motif'][:60]})" for p, r in par_provider.items() if not r["sain"]]
    logger.info(
        f"[SANTE] {len(sains)}/{len(candidats)} provider(s) sain(s) en "
        f"{int((time.time() - debut) * 1000)} ms : {', '.join(sains) or 'aucun'}"
    )
    if ecartes:
        logger.info(f"[SANTE] Écartés : {' | '.join(ecartes)}")
    return par_provider


def providers_sains(resultats: dict[str, dict]) -> set[str]:
    """Extrait l'ensemble des providers utilisables d'un résultat de sonde."""
    return {p for p, r in resultats.items() if r.get("sain")}
