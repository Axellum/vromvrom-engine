"""
services/coding_front.py — Coding front : routeur des tâches de code (#T204).

Remplace les sessions manuelles "toujours Claude au tarif fort" pour le code
(~90 % de l'usage LLM d'Axel) par un routage automatique :

1. Détection de complexité AVANT l'appel (heuristique déterministe, 0 ms) :
   tâche simple → tier « moyen » (codestral, deepseek, sonnet CLI...),
   tâche complexe → tier « fort » (opus CLI, deepseek-v4-pro, gemini pro...).
2. Exécution via la Cascade existante (get_provider_for_tier → FallbackProvider :
   Circuit Breaker, retry 429, bascule inter-modèles, Elo/coût via ProviderScorer).
3. Filet de sécurité POST-appel : si le tier « moyen » échoue (exception de toute
   la cascade) ou rend une réponse manifestement vide, UNE escalade vers « fort ».

Choix d'architecture (décisions #T204, 12/07/2026) :
- **Point d'entrée = CLI locale** (tools/code_front.py) et non un endpoint HTTP :
  l'usage visé est le poste de développement (alias shell, pipe, IDE) ; les clés
  API du .env y sont déjà présentes, et un aller-retour HTTP vers le Deck
  n'apporterait que latence et dépendance réseau. Un endpoint /api/code pourra
  réutiliser tel quel `run_coding_task()` si le besoin IHM apparaît.
- **Escalade pré-appel par heuristique déterministe** (mots-clés + longueur +
  nombre de fichiers de contexte) plutôt que par LLM-juge : coût nul, latence
  nulle, comportement prévisible/testable. Un juge LLM (score de qualité façon
  ReviewLoop #T117) reste une évolution possible si les heuristiques se
  révèlent insuffisantes à l'usage.
- **Filet post-appel volontairement minimal** (échec de cascade ou réponse
  quasi vide) : le FallbackProvider bascule déjà entre modèles DU tier ; le
  front n'escalade ENTRE tiers qu'une seule fois, pour garder le coût borné.
- **claude-fable-5 reste hors routage** : décision d'Axel (éviter
  l'auto-escalade vers le modèle le plus cher) — cf. config.json
  `routing_policy.excluded_models`. Ne pas l'ajouter aux tiers.
"""

import logging

logger = logging.getLogger(__name__)

# Seuil de longueur au-delà duquel une demande de code est considérée complexe
# (aligné sur Router._detect_complexity, core/router.py).
COMPLEX_PROMPT_LENGTH = 220

# Nombre de fichiers de contexte au-delà duquel la tâche est multi-fichiers → fort
COMPLEX_FILES_THRESHOLD = 2

# Signaux lexicaux d'une tâche de code structurelle/architecturale
COMPLEX_CODE_KEYWORDS = [
    "refactor", "architecture", "audit", "migration", "migrer",
    "race condition", "deadlock", "multithread", "multi-thread", "asyncio",
    "concurrence", "concurrent", "parallèle", "parallele",
    "circuit breaker", "dag", "optimise", "optimiser", "optimisation",
    "réécri", "reecri", "redesign", "conception", "plusieurs fichiers",
    "multi-fichiers", "toute la codebase", "tout le module", "self-healing",
]

# En-dessous de cette taille, une réponse du tier moyen est jugée inutilisable
# (filet de sécurité post-appel — pas un juge de qualité).
MIN_USABLE_RESPONSE_CHARS = 50

CODING_SYSTEM_PROMPT = (
    "Tu es le coding front du tab5-engine, un ingénieur logiciel senior "
    "(Python/asyncio, C++/ESPHome, TypeScript/React, YAML Home Assistant).\n"
    "Règles strictes du projet :\n"
    "- Tous les commentaires de code en FRANÇAIS.\n"
    "- Jamais de secret/clé API en clair (utiliser !secret ou variables d'env).\n"
    "- Réponds directement avec le code demandé dans des blocs ``` et des "
    "explications concises. Pas de préambule inutile."
)


def detect_code_complexity(prompt: str, context_files: list[str] | None = None) -> bool:
    """
    Heuristique déterministe : la tâche de code justifie-t-elle le tier fort ?

    True si le prompt est long, contient un signal structurel/architectural,
    ou embarque plus de COMPLEX_FILES_THRESHOLD fichiers de contexte.
    """
    if len(prompt) > COMPLEX_PROMPT_LENGTH:
        return True
    prompt_lower = prompt.lower()
    if any(kw in prompt_lower for kw in COMPLEX_CODE_KEYWORDS):
        return True
    if context_files and len(context_files) > COMPLEX_FILES_THRESHOLD:
        return True
    return False


def _build_user_prompt(prompt: str, file_contents: dict[str, str] | None) -> str:
    """Assemble le prompt utilisateur avec les fichiers de contexte éventuels."""
    if not file_contents:
        return prompt
    parts = [prompt, "\n\n=== FICHIERS DE CONTEXTE ==="]
    for path, content in file_contents.items():
        parts.append(f"\n--- {path} ---\n{content}")
    return "\n".join(parts)


async def run_coding_task(
    prompt: str,
    file_contents: dict[str, str] | None = None,
    force_tier: str | None = None,
    gateway=None,
    config: dict | None = None,
) -> dict:
    """
    Route et exécute une tâche de code avec escalade automatique moyen→fort.

    Args:
        prompt: Demande de code.
        file_contents: {chemin: contenu} des fichiers de contexte (optionnel).
        force_tier: "moyen"/"fort" pour court-circuiter la détection (optionnel).
        gateway: LLMGateway injectable (tests) — instancié sinon.
        config: config.json déjà chargée (tests) — load_config() sinon.

    Returns:
        {"response": str, "tier_requested": str, "tier_used": str,
         "model": str, "complex": bool, "escalated": bool}
    """
    from core.llm_gateway import LLMGateway, load_config

    if gateway is None:
        gateway = LLMGateway()
    if config is None:
        config = load_config()

    is_complex = detect_code_complexity(prompt, list(file_contents or {}))
    tier = force_tier or ("fort" if is_complex else "moyen")
    user_prompt = _build_user_prompt(prompt, file_contents)

    escalated = False
    model_name, provider = gateway.get_provider_for_tier(tier, config)
    logger.info(
        f"[CODING-FRONT] Tâche {'complexe' if is_complex else 'simple'} → tier '{tier}' ({model_name})"
    )

    try:
        # [T287] Front de codage : conventions du projet demandées explicitement.
        # Elles ne sont plus injectées d'office sur tous les appels du moteur.
        response = await provider.generate_async(
            CODING_SYSTEM_PROMPT, user_prompt, conventions_projet=True
        )
        usable = isinstance(response, str) and len(response.strip()) >= MIN_USABLE_RESPONSE_CHARS
    except Exception as e:
        logger.warning(f"[CODING-FRONT] Échec de toute la cascade du tier '{tier}' : {e}")
        response, usable = None, False

    # Filet de sécurité : UNE escalade moyen→fort si le tier moyen n'a rien produit
    # d'utilisable (l'intra-tier est déjà géré par le FallbackProvider).
    if not usable and tier == "moyen":
        escalated = True
        tier_final = "fort"
        model_name, provider = gateway.get_provider_for_tier(tier_final, config)
        logger.warning(
            f"[CODING-FRONT] ⬆️ Escalade automatique moyen→fort ({model_name})"
        )
        response = await provider.generate_async(
            CODING_SYSTEM_PROMPT, user_prompt, conventions_projet=True
        )
    else:
        tier_final = tier
        if not usable:
            # Tier fort inutilisable : on remonte l'erreur plutôt que de boucler.
            raise RuntimeError(
                f"[CODING-FRONT] Le tier '{tier}' n'a produit aucune réponse utilisable."
            )

    return {
        "response": response,
        "tier_requested": tier,
        "tier_used": tier_final,
        "model": model_name,
        "complex": is_complex,
        "escalated": escalated,
    }
