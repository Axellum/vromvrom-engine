import httpx
import asyncio
from rich.console import Console
from rich.markdown import Markdown
import os

console = Console()

ENDPOINT = "http://localhost:8001/v1/chat/completions"
API_KEY = "antigravity_secret_token_2026"

async def send_chat_request(client, messages, strategy):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    payload = {
        "model": strategy,
        "messages": messages
    }
    
    response = await client.post(ENDPOINT, json=payload, headers=headers, timeout=120.0)
    if response.status_code == 200:
        data = response.json()
        return data["choices"][0]["message"]["content"], data.get("model", strategy)
    return f"Erreur {response.status_code}", "error"

async def main():
    with open("quality_results.md", "w", encoding="utf-8") as f:
        f.write("# Rapport Qualitatif des Modèles (Tours Successifs)\n\n")
        
        async with httpx.AsyncClient() as client:
            # On va faire ce test sur la stratégie "Pleine Puissance" (Auto Elo)
            strategy = "auto"
            f.write(f"## Stratégie testée : {strategy.upper()}\n\n")
            
            messages = []
            
            # --- TOUR 1 : Génération Complexe ---
            prompt_1 = "Écris une courte fonction Python pour vérifier si un mot de passe est valide. Le mot de passe doit faire 8 caractères minimum, avoir une majuscule et un chiffre."
            messages.append({"role": "user", "content": prompt_1})
            
            console.print("[bold yellow]Tour 1: Envoi de la demande initiale...[/bold yellow]")
            response_1, model_1 = await send_chat_request(client, messages, strategy)
            messages.append({"role": "assistant", "content": response_1})
            
            f.write(f"### Tour 1 : Demande Initiale (Modèle utilisé : `{model_1}`)\n")
            f.write(f"**Prompt** : {prompt_1}\n\n")
            f.write(f"**Réponse** :\n{response_1}\n\n---\n\n")
            
            # --- TOUR 2 : Critique (Amélioration par instructions successives) ---
            prompt_2 = "Ta fonction précédente est basique. Ajoute maintenant une vérification pour interdire les caractères spéciaux (seulement alphanumérique autorisé), et optimise-la en utilisant des expressions régulières (regex)."
            messages.append({"role": "user", "content": prompt_2})
            
            console.print("[bold yellow]Tour 2: Envoi de la demande d'amélioration...[/bold yellow]")
            response_2, model_2 = await send_chat_request(client, messages, strategy)
            messages.append({"role": "assistant", "content": response_2})
            
            f.write(f"### Tour 2 : Amélioration Regex (Modèle utilisé : `{model_2}`)\n")
            f.write(f"**Prompt** : {prompt_2}\n\n")
            f.write(f"**Réponse** :\n{response_2}\n\n---\n\n")
            
            # --- TOUR 3 : Audit de sécurité (Mémoire contextuelle) ---
            prompt_3 = "En analysant le code regex que tu viens de générer au tour 2, vois-tu une faille de sécurité potentielle (par exemple un problème de ReDoS ou une mauvaise gestion des retours à la ligne) ? Corrige-la si oui."
            messages.append({"role": "user", "content": prompt_3})
            
            console.print("[bold yellow]Tour 3: Envoi de la demande d'audit...[/bold yellow]")
            response_3, model_3 = await send_chat_request(client, messages, strategy)
            messages.append({"role": "assistant", "content": response_3})
            
            f.write(f"### Tour 3 : Audit de Sécurité (Modèle utilisé : `{model_3}`)\n")
            f.write(f"**Prompt** : {prompt_3}\n\n")
            f.write(f"**Réponse** :\n{response_3}\n\n")

    console.print("[bold green]Test qualitatif terminé. Les résultats sont dans quality_results.md.[/bold green]")

if __name__ == "__main__":
    asyncio.run(main())
