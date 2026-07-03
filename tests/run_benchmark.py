import httpx
import time
import asyncio
from rich.console import Console
from rich.table import Table

console = Console()

# Configuration
ENDPOINTS = [
    {"name": "Local Windows Native", "url": "http://localhost:8001/v1/chat/completions"},
    {"name": "Remote Steam Deck Podman", "url": "http://192.168.0.43:8002/v1/chat/completions"}
]
API_KEY = "antigravity_secret_token_2026"

# Stratégies à tester (issues de STRATEGIES.md)
STRATEGIES = [
    {"name": "Gratuit (Gemini)", "model": "gemini-2.5-flash"},
    {"name": "Chinois (DeepSeek)", "model": "deepseek-chat"},
    {"name": "Pleine Puissance (Auto Elo)", "model": "auto"}
]

PROMPTS = [
    {"type": "Logique Rapide", "text": "Combien y a-t-il de r dans fraise ? Réponds en un mot."},
    {"type": "Code Complexe", "text": "Écris une fonction Python asynchrone qui implémente un Circuit Breaker avec exponential backoff."},
    {"type": "Domotique HA", "text": "L'entité light.salon est allumée et sensor.temperature affiche 25. Que me conseilles-tu ?"}
]

ROUNDS = 3  # Plusieurs tours pour tester l'optimisation et le cache

async def test_endpoint(client, endpoint, strategy, prompt, round_num):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    payload = {
        "model": strategy["model"],
        "messages": [{"role": "user", "content": prompt["text"]}]
    }
    
    start_time = time.time()
    try:
        response = await client.post(endpoint["url"], json=payload, headers=headers, timeout=60.0)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            # Extraire le vrai modèle utilisé si on était en 'auto'
            actual_model = data.get("model", strategy["model"])
            return True, elapsed, actual_model, len(content)
        else:
            return False, elapsed, f"Erreur {response.status_code}", 0
    except Exception as e:
        return False, time.time() - start_time, str(e), 0

async def main():
    console.print(f"[bold blue]🚀 Démarrage du Benchmark Croisé ({ROUNDS} tours)[/bold blue]")
    
    results = []
    
    async with httpx.AsyncClient() as client:
        for round_num in range(1, ROUNDS + 1):
            console.print(f"\n[bold green]=== TOUR {round_num}/{ROUNDS} ==-[/bold green]")
            
            for endpoint in ENDPOINTS:
                console.print(f"\n[bold yellow]Cible : {endpoint['name']} ({endpoint['url']})[/bold yellow]")
                
                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("Stratégie (Modèle)")
                table.add_column("Type de Prompt")
                table.add_column("Temps (s)", justify="right")
                table.add_column("Modèle Réel", justify="center")
                table.add_column("Statut")
                
                for strategy in STRATEGIES:
                    for prompt in PROMPTS:
                        success, elapsed, actual_model, length = await test_endpoint(
                            client, endpoint, strategy, prompt, round_num
                        )
                        
                        status = "[green]Succès[/green]" if success else f"[red]Échec ({actual_model})[/red]"
                        table.add_row(
                            strategy["name"],
                            prompt["type"],
                            f"{elapsed:.2f}s",
                            actual_model,
                            status
                        )
                        
                        results.append({
                            "round": round_num,
                            "endpoint": endpoint["name"],
                            "strategy": strategy["name"],
                            "prompt": prompt["type"],
                            "time": elapsed,
                            "success": success
                        })
                
                console.print(table)

if __name__ == "__main__":
    asyncio.run(main())
