"""
seed_github_models.py — Ajout de GitHub Models dans models_registry.db.

Ajoute le provider github, ses clés API et ses modèles (gpt-4o, gpt-4o-mini,
meta-llama-3.3-70b-instruct) avec un coût de 0$ pour l'inférence développeur gratuite.
"""

import os
import sys

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.models_db import (
    upsert_provider, upsert_model, upsert_api_key,
    upsert_access_channel
)

def seed_github():
    print("── Enregistrement du Provider GitHub Models ──")
    ok = upsert_provider(
        "github",
        name="GitHub Models",
        type="free",
        api_endpoint="https://models.inference.ai.azure.com",
        auth_method="api_key",
        confidentiality="none",
        cascade_priority=2.1,
        notes="Inférence gratuite pour développeurs via Azure AI."
    )
    print(f"  {'✅' if ok else '❌'} Provider 'github' enregistré.")

    print("\n── Enregistrement des Modèles GitHub ──")
    models = [
        {
            "id": "github/gpt-4o",
            "provider_id": "github",
            "display_name": "GPT-4o (GitHub)",
            "tier": "fort",
            "context_input": 128000,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_vision": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "speciality": "raisonnement",
            "recommended_use": "Raisonnement fort gratuit via GitHub Models"
        },
        {
            "id": "github/gpt-4o-mini",
            "provider_id": "github",
            "display_name": "GPT-4o Mini (GitHub)",
            "tier": "leger",
            "context_input": 128000,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_vision": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "speciality": "agents_rapides",
            "recommended_use": "Exécution rapide gratuite via GitHub Models"
        },
        {
            "id": "github/meta-llama-3.3-70b-instruct",
            "provider_id": "github",
            "display_name": "Llama 3.3 70B Instruct (GitHub)",
            "tier": "moyen",
            "context_input": 128000,
            "context_output": 4096,
            "cost_input_per_m": 0.0,
            "cost_output_per_m": 0.0,
            "supports_tools": 1,
            "supports_json_mode": 1,
            "supports_streaming": 1,
            "speciality": "raisonnement",
            "recommended_use": "Raisonnement open-source fort gratuit via GitHub Models"
        }
    ]

    for m in models:
        mid = m.pop("id")
        ok = upsert_model(mid, **m)
        print(f"  {'✅' if ok else '❌'} Modèle {mid}")

    print("\n── Enregistrement de la Clé API GITHUB_TOKEN ──")
    env_val = os.environ.get("GITHUB_TOKEN")
    ok = upsert_api_key(
        "GITHUB_TOKEN",
        provider_id="github",
        env_var="GITHUB_TOKEN",
        project_name="GitHub Models Developer",
        key_type="free",
        status="active" if env_val else "missing"
    )
    status_icon = "✅" if env_val else "⚠️"
    print(f"  {status_icon} Clé GITHUB_TOKEN — {'trouvée' if env_val else 'ABSENTE du .env'}")

    print("\n── Canaux d'accès ──")
    channels = [
        ("github/gpt-4o", "github/gpt-4o"),
        ("github/gpt-4o-mini", "github/gpt-4o-mini"),
        ("github/meta-llama-3.3-70b-instruct", "github/meta-llama-3.3-70b-instruct"),
    ]
    for model_id, alias in channels:
        ok = upsert_access_channel(
            model_id, "GITHUB_TOKEN",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", is_default=1
        )
        print(f"  {'✅' if ok else '❌'} Canal {model_id} via GITHUB_TOKEN")

if __name__ == "__main__":
    seed_github()
    print("\n── Régénération des documents de référence ──")
    try:
        import subprocess
        subprocess.run([sys.executable, "export_models_docs.py"], check=True)
        print("✅ Documentations modèles exportées avec succès.")
    except Exception as e:
        print(f"❌ Échec de l'export des documentations: {e}")
