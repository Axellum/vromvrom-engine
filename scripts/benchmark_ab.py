import asyncio
import json
import time

import httpx
from dotenv import load_dotenv

from core.ha_token import get_ha_token  # [T239] lecture centralisée du token HA
from core.ha_url import get_ha_url  # [T330] lecture centralisée de l'URL HA

load_dotenv()

HASS_URL = get_ha_url()
HASS_TOKEN = get_ha_token()
MOTEUR_URL = "http://localhost:8000/api/execute/stream"

PROMPT = "Allume le salon"

async def test_ha_direct():
    print("--- Test API HA Directe ---")
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"entity_id": "light.living_room"}

    start_time = time.time()
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.post(
                f"{HASS_URL}/api/services/light/turn_on",
                headers=headers,
                json=payload
            )
            elapsed = time.time() - start_time
            print(f"✅ Temps de réponse HA : {elapsed:.3f}s (Status: {response.status_code})")
            return elapsed
    except Exception as e:
        print(f"❌ Erreur API HA : {e}")
        return None

async def test_moteur_local():
    print("\n--- Test Moteur Local (tab5-engine) ---")
    headers = {"Content-Type": "application/json"}
    payload = {
        "task_objective": PROMPT,
        "mode": "planner"
    }

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(MOTEUR_URL, headers=headers, json=payload)
            # Puisque c'est un flux SSE, on va lire la première réponse significative ou attendre la fin
            elapsed = time.time() - start_time
            print(f"✅ Temps de réponse initial Moteur : {elapsed:.3f}s (Status: {response.status_code})")

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "orchestration_completed":
                            total_elapsed = time.time() - start_time
                            print(f"✅ Temps total Moteur : {total_elapsed:.3f}s")
                            return total_elapsed
                    except json.JSONDecodeError:
                        pass
            return elapsed
    except Exception as e:
        print(f"❌ Erreur Moteur Local : {e}")
        return None

async def main():
    print(f"🚀 Début du Benchmark de Régression A/B sur le prompt : '{PROMPT}'\n")

    # 1. API HA Directe
    ha_time = await test_ha_direct()

    # 2. Moteur Local
    moteur_time = await test_moteur_local()

    print("\n--- Résultats du Benchmark ---")
    if ha_time:
        print(f"API HA Directe : {ha_time:.3f}s")
    if moteur_time:
        print(f"Moteur Local   : {moteur_time:.3f}s")

    if ha_time and moteur_time:
        diff = moteur_time - ha_time
        print(f"\nL'orchestration Moteur ajoute {diff:.3f}s d'overhead (Raisonnement LLM + Planning + Sécurité).")

if __name__ == "__main__":
    asyncio.run(main())
