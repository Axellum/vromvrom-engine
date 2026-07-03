# -*- coding: utf-8 -*-
"""
tools/llm_as_a_judge.py — Script d'audit automatique des modifications de code par LLM (Gemini 3.5 Flash).
"""

import os
import sys
import json
import subprocess
import argparse
import requests

def load_dotenv():
    """Charge le fichier .env s'il existe dans le répertoire parent ou courant."""
    for path in [".env", "../.env", "moteur_agents/.env"]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        # Retirer les guillemets éventuels
                        v = v.strip().strip("'").strip('"')
                        os.environ[k.strip()] = v
            break

def get_git_diff(compare_target="HEAD") -> str:
    """Récupère le diff git courant ou par rapport à une cible."""
    try:
        # Tente de récupérer le diff indexé + non indexé
        res = subprocess.run(["git", "diff", compare_target], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        diff = res.stdout
        # S'il est vide, tente de récupérer les derniers changements commités (HEAD~)
        if not diff.strip():
            res = subprocess.run(["git", "diff", "HEAD~1"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
            diff = res.stdout
        return diff
    except Exception as e:
        print(f"Erreur lors de la récupération du git diff : {e}")
        return ""

def call_gemini(system_prompt: str, user_prompt: str, api_key: str, model: str) -> str:
    """Appelle l'API Gemini via l'endpoint de compatibilité OpenAI."""
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        resp_json = response.json()
        return resp_json["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Erreur lors de l'appel à l'API Gemini : {e}")
        if 'response' in locals() and response is not None:
            print(f"Réponse brute de l'API : {response.text}")
        return ""

SYSTEM_PROMPT = """Tu es un expert en domotique, systèmes embarqués (ESPHome/LVGL C++) et architectures multi-agents asynchrones (Python/FastAPI).
Ton rôle est d'analyser le diff de code ou le fichier fourni pour y détecter les anomalies, les fuites de mémoire, les faiblesses de sécurité, et le respect des conventions de développement.

Tu dois impérativement analyser les points suivants :
1. **Gestion de la mémoire (C++ / ESPHome / LVGL)** :
   - Détection des allocations dynamiques (`new`, `std::vector`, `std::string`) dans les boucles de rendu, les lambdas d'événements, ou les fonctions appelées fréquemment (comme les fonctions de parsing de payload).
   - Utilisation in-place des buffers statiques et passage d'arguments par référence constante (`const std::string&`).
   - Destruction propre des objets LVGL créés dynamiquement pour éviter la fragmentation de la SRAM.

2. **Sécurité (Python / APIs / YAML)** :
   - Prévention des injections de templates Jinja2 (SSTI) dans les payloads envoyés à Home Assistant.
   - Validation stricte des arguments d'API.
   - Absence de secrets, tokens ou clés API codés en dur.

3. **Asynchronisme et Performances (Python asyncio)** :
   - Détection des appels bloquants (E/S synchrones, `time.sleep`) dans la boucle d'événements asyncio sans `run_in_executor`.
   - Attente correcte des coroutines (`await`).
   - Éviter la création répétée de pools de threads ou de connexions.

4. **Architecture 3-Layers** :
   - Respect des conventions de la couche Core (profil, règles), Hardware (GPIO, bootloop) et Software (FastAPI, HA, Moteur).

Format de réponse attendu (en français) :
# RAPPORT D'AUDIT DE CODE (LLM-as-a-Judge)

## 🎯 Verdict : [NOTE]/10
*(Une note >= 7/10 est requise pour validation. Sois sévère mais juste.)*

## 🟢 Points forts
- Liste des bonnes pratiques respectées.

## 🔴 Anomalies & Risques (Bloquants)
- Liste des anomalies critiques (fuites mémoire, SSTI, blocages d'Event Loop).

## 🟡 Pistes d'amélioration (Non bloquants)
- Recommandations de refactoring mineures ou d'optimisation de performance.

## 🔍 Analyse détaillée du code
- Analyse technique ligne par ligne des sections problématiques si nécessaire.
"""

def main():
    parser = argparse.ArgumentParser(description="LLM-as-a-Judge : Audit automatique de code.")
    parser.add_argument("--diff", action="store_true", help="Auditer les modifications via git diff.")
    parser.add_argument("--file", type=str, help="Auditer un fichier spécifique.")
    parser.add_argument("--model", type=str, default="gemini-3.5-flash", help="Modèle Gemini à utiliser (défaut: gemini-3.5-flash).")
    args = parser.parse_args()

    # Charger le .env pour avoir les clés
    load_dotenv()
    
    # Récupérer la clé API Gemini
    api_key = os.environ.get("GEMINI_PAYANT_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Erreur : Clé GEMINI_PAYANT_API_KEY ou GEMINI_API_KEY introuvable dans l'environnement ou le fichier .env.")
        sys.exit(1)

    source_content = ""
    target_name = ""

    if args.file:
        if not os.path.exists(args.file):
            print(f"Erreur : Le fichier {args.file} n'existe pas.")
            sys.exit(1)
        target_name = f"Fichier: {args.file}"
        try:
            with open(args.file, "r", encoding="utf-8", errors="ignore") as f:
                source_content = f.read()
        except Exception as e:
            print(f"Erreur lors de la lecture du fichier : {e}")
            sys.exit(1)
    elif args.diff:
        target_name = "Git Diff courant"
        source_content = get_git_diff()
        if not source_content.strip():
            print("Aucune modification détectée dans le git diff (HEAD ou HEAD~1 est propre).")
            sys.exit(0)
    else:
        # Mode par défaut s'il n'y a pas d'arguments : git diff
        target_name = "Git Diff courant (défaut)"
        source_content = get_git_diff()
        if not source_content.strip():
            print("Usage: python tools/llm_as_a_judge.py --diff ou --file <chemin_fichier>")
            sys.exit(0)

    print(f"Lancement de l'audit pour : {target_name} avec {args.model}...")
    user_prompt = f"Voici le contenu à analyser ({target_name}) :\n\n```diff\n{source_content}\n```"
    
    report = call_gemini(SYSTEM_PROMPT, user_prompt, api_key, args.model)
    
    if not report:
        print("Erreur : Impossible d'obtenir le rapport de l'API Gemini.")
        sys.exit(1)
        
    print("\n" + "="*80)
    print(report)
    print("="*80 + "\n")
    
    # Extraction de la note pour retour de code
    try:
        # Cherche le verdict dans le rapport (ex: "Verdict : 8/10")
        import re
        verdict_match = re.search(r"Verdict\s*:\s*(\d+(\.\d+)?)/10", report, re.IGNORECASE)
        if verdict_match:
            score = float(verdict_match.group(1))
            print(f"[JUDGE] Note extraite : {score}/10")
            if score < 7.0:
                print("❌ [ÉCHEC] Le code ne respecte pas les critères de qualité requis (note < 7).")
                sys.exit(2)
            else:
                print("✅ [SUCCÈS] Le code a été approuvé par le Judge.")
                sys.exit(0)
        else:
            print("⚠️ [WARNING] Note de verdict introuvable ou mal formatée dans le rapport.")
    except Exception as e:
        print(f"Erreur lors du traitement de la note : {e}")

if __name__ == "__main__":
    main()
