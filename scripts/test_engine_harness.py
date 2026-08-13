"""
Script de test & d'audit du Moteur Multi-Agents (tab5-engine) - Version 2.
Teste la résolution des Tiers (leger, moyen, fort, local), les Fallback cascades,
la concurrence de charge, et génère le rapport d'analyse.
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# [T284] Chargement unique du .env via le point de chargement du coeur : un
# script lancé depuis n'importe quel répertoire dispose des mêmes clés.
from core.env_bootstrap import bootstrap_env  # noqa: E402
from core.llm_gateway import LLMGateway  # noqa: E402

bootstrap_env()

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "test_output"
OUTPUT_FILE = OUTPUT_DIR / "harness_results.json"


def _est_payload_erreur(resultat: Any) -> bool:
    """True si la réponse porte un statut d'erreur (échec sans exception).

    [T284] Un payload qui répond « error » est un échec, même quand aucune
    exception n'a été levée : le 11/08, des tests ont été comptés réussis avec
    un result_snippet contenant `{'status': 'error', ...}`.
    """
    if not isinstance(resultat, dict):
        return False
    if str(resultat.get("status", "")).lower() == "error":
        return True
    return bool(resultat.get("error"))


async def test_tier_gateway_single(gateway: LLMGateway, tier_or_alias: str, prompt: str) -> dict[str, Any]:
    """Test d'appel via le tier ou alias du gateway (ex: leger, moyen, fort, local)."""
    start_time = time.perf_counter()
    success = False
    error_msg = None
    response_text = ""
    provider_name = ""

    try:
        try:
            provider = gateway.get_provider_for_tier(tier_or_alias)
            provider_name = f"tier:{tier_or_alias}"
        except Exception:
            provider = gateway.get_provider(tier_or_alias)
            provider_name = f"alias:{tier_or_alias}"

        if hasattr(provider, "generate_async"):
            res = await provider.generate_async(
                system_prompt="Tu es un assistant de test. Sois concis.",
                user_prompt=prompt,
                max_tokens=150
            )
        else:
            res = provider.generate(
                system_prompt="Tu es un assistant de test. Sois concis.",
                user_prompt=prompt,
                max_tokens=150
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        response_text = str(res)
        # [T284] Un statut d'erreur dans la réponse est un échec (pas de
        # « succès » au seul motif qu'aucune exception n'a été levée).
        if _est_payload_erreur(res):
            error_msg = f"payload error: {response_text[:200]}"
        else:
            success = True
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"❌ Exception sur [{tier_or_alias}]: {error_msg}")

    return {
        "target": tier_or_alias,
        "resolved_provider": provider_name,
        "success": success,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error_msg,
        "snippet": response_text[:200] if response_text else None
    }


async def main():
    print("=" * 70)
    print("      HARNESS DE TEST MOTEUR V2 (RESOLUTION TIERS & CASCADE)")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gateway = LLMGateway()

    results_summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier_tests": [],
        "parallel_tier_tests": [],
        "circuit_breaker_states": {}
    }

    tiers_to_test = ["leger", "moyen", "fort", "local", "github"]

    prompt = "Résume l'avantage de la domotique en 1 phrase."

    print("\n--- PHASE 1: TESTS DES TIERS DU CATALOGUE ---")
    for t in tiers_to_test:
        print(f"🔍 Test de résolution du tier/alias: [{t}]...")
        res = await test_tier_gateway_single(gateway, t, prompt)
        results_summary["tier_tests"].append(res)
        print(f"   -> Succès: {res['success']} | Durée: {res['elapsed_ms']} ms")

    print("\n--- PHASE 2: PARALLÉLISME MULTI-TIERS (asyncio.gather) ---")
    start_parallel = time.perf_counter()
    tasks = [
        test_tier_gateway_single(gateway, "leger", "Qu'est-ce que le zigbee ?"),
        test_tier_gateway_single(gateway, "moyen", "Qu'est-ce que le MQTT ?"),
        test_tier_gateway_single(gateway, "fort", "Qu'est-ce que Home Assistant ?"),
        test_tier_gateway_single(gateway, "local", "Qu'est-ce qu'un ESP32 ?")
    ]
    parallel_res = await asyncio.gather(*tasks, return_exceptions=True)
    total_p_ms = (time.perf_counter() - start_parallel) * 1000

    for r in parallel_res:
        if isinstance(r, dict):
            results_summary["parallel_tier_tests"].append(r)

    print(f"⚡ Vague de 4 tiers en parallèle terminée en {total_p_ms:.2f} ms")

    # Capture états Circuit Breakers
    cb_states = getattr(gateway, "circuit_breakers", {})
    for cb_name, cb_obj in cb_states.items():
        results_summary["circuit_breaker_states"][cb_name] = {
            "state": getattr(cb_obj, "state", "UNKNOWN"),
            "failures": getattr(cb_obj, "total_failures", 0),
            "calls": getattr(cb_obj, "total_calls", 0)
        }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results_summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print(f"✅ V2 TERMINÉE — Synthèse dans {OUTPUT_FILE}")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
