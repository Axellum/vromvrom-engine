"""
scripts/test_campaign_deep.py — Grande campagne de tests & de données du moteur.

Reconstruit depuis test_output/deep_campaign_execution.log et
test_output/deep_campaign_dataset.jsonl (run du 11/08) : ce script n'était pas
suivi par git (#T284).

Étapes :
  1. context_audit      : analyse des contextes injectés (RAG + compression)
  2. fast_path_route    : route Fast-Path (casual_chat)
  3. real_task_analysis : analyse réelle de tâches backlog via le tier "moyen"

⚠️ Un test n'est un SUCCÈS que si sa réponse l'est (champ `success` explicite).
L'étape context_audit n'est PAS gagnante par construction : sa compression doit
avoir abouti sans exception ET sans inflation du contexte (ratio >= 0). Le 11/08,
le résumé annonçait successful_tests: 1 alors qu'aucun des 7 tests n'avait réussi
— l'étape context_audit était comptée gagnante par un `or e.get("step") ==
"context_audit"` (#T284).

⚠️ [T286] Deuxième correction du MÊME défaut, mesurée en rejouant la campagne
le 11/08 une fois les clés réellement chargées : le harnais annonçait 6 succès
sur 7, alors que TROIS de ces « succès » ne répondaient pas à la question —
`success` était posé à True dès que `generate_async()` ne levait pas :
  - #T270 : réponse vide (0 caractère) comptée gagnante ;
  - #T278 et #T280 : `{'role':'assistant','content':None,'tool_calls':[...]}`
    brut compté gagnant, alors qu'AUCUN outil n'était déclaré dans l'appel.
La cause est côté cascade : `FallbackProvider._is_response_adequate()`
(core/llm/providers/deepseek.py) rend True pour tout objet non-`str`, donc un
appel d'outil est jugé adéquat et remonté tel quel à l'appelant. Le harnais ne
peut pas s'y fier : il qualifie lui-même la nature de la réponse
(`_qualifier_reponse`).
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# [T284] Chargement unique du .env via le point de chargement du coeur : un
# script lancé depuis n'importe quel répertoire dispose des mêmes clés.
from core.env_bootstrap import bootstrap_env  # noqa: E402
from core.llm.fallback_trace import lire_modele_repondu  # noqa: E402

bootstrap_env()

logger = logging.getLogger("test_campaign_deep")

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_output"
DATASET_FILE = OUTPUT_DIR / "deep_campaign_dataset.jsonl"
SUMMARY_FILE = OUTPUT_DIR / "deep_campaign_summary.json"

PROMPT_CONTEXT_AUDIT = "Comment est gérée la résilience dans core/llm_gateway.py ?"
PROMPT_FAST_PATH = "Quel est le statut de l'écosystème domotique ?"

# Tâches backlog analysées lors de la campagne du 11/08.
TACHES_BACKLOG = [
    ("#T278", "Les stash de git_safety ne sont pas restaurés et s'accumulent dans le dépôt."),
    ("#T279", "Aliasing Tiers IHM ↔ Gateway non résolu directement dans LLMGateway."),
    ("#T280", "Endpoint Azure Inference GitHub Models en 404 sur github/gpt-*."),
    ("#T281", "AttributeError: 'dict' object has no attribute 'metadata' dans le pipeline."),
    ("#T270", "La latence vocale n'est mesurée que sur 6 % des échanges vocaux."),
]


def _qualifier_reponse(reponse) -> tuple[str, bool]:
    """Nature réelle d'une réponse LLM et verdict de succès. [T286]

    Retourne (nature, succes) où nature vaut :
      - "tool_call" : le modèle a demandé un outil au lieu de répondre. Aucun
        outil n'étant déclaré par cette campagne, c'est un ÉCHEC : la question
        posée n'a pas reçu de réponse.
      - "vide"      : réponse textuelle vide ou blanche → ÉCHEC.
      - "texte"     : vraie réponse → SUCCÈS.
    """
    if isinstance(reponse, dict):
        return "tool_call", False
    texte = str(reponse or "").strip()
    return ("texte", True) if texte else ("vide", False)


def _construire_contexte_rag(requete: str) -> str:
    """Contexte RAG depuis la mémoire de faits ("" si indisponible)."""
    try:
        from memory.memory_db import MemoryDB

        faits = MemoryDB().search_facts(requete, limit=5) or []
        return "\n".join(str(f.get("content", "")) for f in faits if f.get("content"))
    except Exception as e:
        logger.debug(f"[CONTEXT AUDIT] RAG indisponible ({e}), contexte vide.")
        return ""


async def audit_contextes() -> dict:
    """Étape 1 : construit le contexte RAG, le compresse, mesure le succès réel."""
    debut = time.perf_counter()
    entree = {
        "step": "context_audit",
        "prompt": PROMPT_CONTEXT_AUDIT,
        "rag_context_length": 0,
        "raw_prompt_length": 0,
        "compressed_prompt_length": 0,
        "compression_ratio_percent": None,
        # [T284] Jamais gagnant par construction : succès seulement si la
        # compression aboutit sans exception ET sans inflation du contexte.
        "success": False,
        # [T286] Qualifie l'échec, sinon la synthèse range cette étape sous
        # « exception » alors qu'aucune exception n'a été levée.
        "nature": None,
        "error": None,
        "elapsed_ms": 0.0,
    }
    try:
        from core.router_context_compressor import RouterContextCompressor

        contexte_rag = _construire_contexte_rag(PROMPT_CONTEXT_AUDIT)
        entree["rag_context_length"] = len(contexte_rag)
        contexte_brut = (
            f"{PROMPT_CONTEXT_AUDIT}\n\n{contexte_rag}" if contexte_rag else PROMPT_CONTEXT_AUDIT
        )
        entree["raw_prompt_length"] = len(contexte_brut)

        compresseur = RouterContextCompressor(max_chars=8000)
        compresse = compresseur.compress({"rag": contexte_rag})
        entree["compressed_prompt_length"] = len(compresse)
        ratio = 0.0
        if entree["raw_prompt_length"]:
            ratio = round((1 - len(compresse) / entree["raw_prompt_length"]) * 100, 1)
        entree["compression_ratio_percent"] = ratio
        # [T284] Succès = compression EFFECTIVE : un résultat non vide, sans
        # exception, et sans inflation du contexte. Un contexte vide (aucune
        # source à compresser) n'est pas une compression réussie.
        entree["success"] = bool(compresse) and ratio >= 0.0
        # [T286] « rien à compresser » (aucun fait remonté par le RAG) et
        # « compression qui gonfle le contexte » sont deux échecs distincts :
        # les confondre a déjà fait accuser le mauvais composant.
        if entree["success"]:
            entree["nature"] = "texte"
        elif not contexte_rag:
            entree["nature"] = "rag_vide"
        else:
            entree["nature"] = "compression_inefficace"
        logger.info(
            f"  -> Contexte RAG généré: {len(contexte_rag)} chars | "
            f"Taux de compression: {ratio}%"
        )
    except Exception as e:
        entree["nature"] = "exception"
        entree["error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"  -> Compression échouée : {e}")
    entree["elapsed_ms"] = round((time.perf_counter() - debut) * 1000, 2)
    return entree


async def route_fast_path() -> dict:
    """Étape 2 : route Fast-Path (casual_chat) avec cascade de providers rapides."""
    from cachetools import TTLCache

    from core import token_tracker
    from core.llm_gateway import LLMGateway
    from services.pipeline_service import run_fast_path

    entree = {
        "step": "fast_path_route",
        "prompt": PROMPT_FAST_PATH,
        "success": False,
        "elapsed_ms": 0.0,
        "error": None,
        "response_len": 0,
        "nature": None,
    }
    debut = time.perf_counter()
    try:
        reponse = await run_fast_path(
            user_prompt=PROMPT_FAST_PATH,
            session_id="test_deep_fast",
            gateway=LLMGateway(),
            token_tracker=token_tracker,
            fast_path_cache=TTLCache(maxsize=100, ttl=15),
        )
        # [T286] `bool(reponse)` rendait True sur un dict tool_calls.
        entree["nature"], entree["success"] = _qualifier_reponse(reponse)
        entree["response_len"] = len(str(reponse or ""))
        if not entree["success"]:
            entree["error"] = f"Réponse de nature '{entree['nature']}' : la question n'a pas de réponse."
    except Exception as e:
        entree["error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"  -> Fast-Path en erreur : {entree['error']}")
    entree["elapsed_ms"] = round((time.perf_counter() - debut) * 1000, 2)
    return entree


async def analyser_tache(gateway, config: dict, task_id: str, description: str) -> dict:
    """Étape 3 : analyse d'une tâche backlog via le tier 'moyen'."""
    entree = {
        "step": "real_task_analysis",
        "task_id": task_id,
        # [T286] Anciennement "model_used", qui valait en réalité "tier-moyen" :
        # get_provider_for_tier() rend le TIER résolu, pas le modèle. Le modèle
        # qui a réellement répondu est choisi par la cascade au moment de
        # l'appel et est exposé depuis #T288 (champ modele_repondu ci-dessous).
        "tier_resolu": None,
        # [T288] Nom du modèle qui a réellement répondu, lu juste après
        # l'appel dans le même contexte (tâche) que celui-ci.
        "modele_repondu": None,
        "success": False,
        "elapsed_ms": 0.0,
        "error": None,
        "nature": None,
        "summary_snippet": None,
    }
    debut = time.perf_counter()
    try:
        tier_resolu, provider = gateway.get_provider_for_tier("moyen", config)
        entree["tier_resolu"] = tier_resolu
        resume = await provider.generate_async(
            system_prompt="Tu es un assistant technique. Résume l'analyse de la tâche en 3 phrases max.",
            user_prompt=f"Analyse de la tâche {task_id} : {description}",
            max_tokens=200,
        )
        # [T288] La cascade choisit son candidat au moment du `return res` : le
        # nom du modèle n'est lisible qu'après l'appel, dans le même contexte
        # (tâche) que celui-ci. None = appel servi par le cache ou en échec.
        entree["modele_repondu"] = lire_modele_repondu()
        entree["summary_snippet"] = str(resume)[:200]
        # [T286] `success = True` dès l'absence d'exception : une réponse vide
        # ou un appel d'outil brut passaient pour une analyse réussie.
        entree["nature"], entree["success"] = _qualifier_reponse(resume)
        if not entree["success"]:
            entree["error"] = (
                f"Réponse de nature '{entree['nature']}' : la tâche {task_id} n'a pas été analysée."
            )
            logger.warning(f"  -> {entree['error']}")
    except Exception as e:
        entree["error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"  -> Analyse {task_id} en erreur : {entree['error']}")
    entree["elapsed_ms"] = round((time.perf_counter() - debut) * 1000, 2)
    return entree


async def main():
    logger.info("=" * 70)
    logger.info("      DÉMARRAGE DE LA GRANDE CAMPAGNE DE TESTS & DE DONNÉES")
    logger.info("=" * 70)

    from core.llm_gateway import LLMGateway, load_config

    gateway = LLMGateway()
    config = load_config()

    entrees = []

    logger.info("🔍 [CONTEXT AUDIT] Démarrage de l'analyse des contextes injectés...")
    entrees.append(await audit_contextes())

    logger.info("⚡ [FAST-PATH ROUTE] Test de la route Fast-Path...")
    entrees.append(await route_fast_path())

    for task_id, description in TACHES_BACKLOG:
        logger.info(f"🎯 [ANALYSIS TASK {task_id}] Lancement de l'analyse : '{description[:60]}...'")
        entrees.append(await analyser_tache(gateway, config, task_id, description))

    # Dataset : append JSONL (historique conservé d'un run à l'autre).
    with open(DATASET_FILE, "a", encoding="utf-8") as f:
        for entree in entrees:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")

    # [T284] Comptage HONNÊTE : seules les entrées avec `success` explicite à
    # True sont comptées — plus aucun « or e.get('step') == 'context_audit' ».
    reussis = [e for e in entrees if e.get("success") is True]
    # [T286] Répartition des ÉCHECS par nature : un run à 0 succès doit dire
    # POURQUOI (réponses vides ? appels d'outils ? exceptions ?), sinon le
    # rapport se relit comme une panne unique alors que ce sont trois défauts.
    natures: dict[str, int] = {}
    for e in entrees:
        if e.get("success") is not True:
            natures[e.get("nature") or "exception"] = natures.get(e.get("nature") or "exception", 0) + 1
    resume = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tests_executed": len(entrees),
        "successful_tests": len(reussis),
        "echecs_par_nature": natures,
        "dataset_file": str(DATASET_FILE),
        "entries": entrees,
    }
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(resume, f, indent=2, ensure_ascii=False)

    logger.info("=" * 70)
    logger.info(f"✅ CAMPAGNE TERMINÉE — Dataset dans {DATASET_FILE}")
    logger.info(f"📊 Rapport synthétique dans {SUMMARY_FILE} (succès : {len(reussis)}/{len(entrees)})")
    if natures:
        logger.info(f"❌ Échecs par nature : {natures}")
    logger.info("=" * 70)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(OUTPUT_DIR / "deep_campaign_execution.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    asyncio.run(main())
