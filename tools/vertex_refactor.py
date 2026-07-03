# -*- coding: utf-8 -*-
"""
tools/vertex_refactor.py — Utilise Vertex AI pour appliquer des corrections complexes de code in-place.

Usage :
  python tools/vertex_refactor.py --file <file_path> --finding <finding_id_or_description> --model <model_name>
"""
import argparse
import json
import os
import sys
import requests

PROJECT = "ha-delta"

def refresh(cid, cs, rt):
    return requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": cs,
        "refresh_token": rt, "grant_type": "refresh_token"}, timeout=20).json()["access_token"]

def main():
    parser = argparse.ArgumentParser(description="Refactorise un fichier via Vertex AI")
    parser.add_argument("--file", required=True, help="Chemin du fichier à modifier")
    parser.add_argument("--finding", required=True, help="Description ou ID du problème à résoudre (ex: SEC-1)")
    parser.add_argument("--model", default="gemini-3.1-pro-preview", help="Modèle Vertex à utiliser")
    parser.add_argument("--apply", action="store_true", help="Applique les modifications in-place")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"Erreur : Le fichier {file_path} n'existe pas.")
        return 1

    print(f"Lecture de {file_path}...")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original_code = f.read()
    except Exception as e:
        print(f"Erreur lors de la lecture du fichier : {e}")
        return 1

    print("Authentification OAuth Vertex AI...")
    try:
        dv = json.load(open("google_token_vertex.json", encoding="utf-8"))
        AT = refresh(dv["client_id"], dv["client_secret"], dv["refresh_token"])
    except Exception as e:
        print(f"Erreur d'authentification OAuth : {e}")
        return 1

    # Utiliser le SDK google-genai
    from google import genai
    from google.genai import types

    # Utiliser l'endpoint 'global' pour les versions Gemini 3
    location = "global" if "3." in args.model else "us-central1"
    client = genai.Client(vertexai=True, project=PROJECT, location=location)

    system_instruction = (
        "Tu es un ingénieur en développement senior spécialisé dans la réécriture de code sécurisé, "
        "robuste et asynchrone (Python asyncio, FastAPI). On te donne un fichier de code source "
        "et la description d'un problème technique (bug, vulnérabilité de sécurité, problème de performance) "
        "identifié lors d'un audit de ce fichier.\n\n"
        "Ta mission est de retourner l'INTÉGRALITÉ du fichier de code corrigé. "
        "Conserve toute la logique métier d'origine, tous les commentaires importants et l'architecture générale, "
        "mais applique le correctif le plus propre possible pour le problème signalé.\n\n"
        "Ne renvoie RIEN d'autre que le code corrigé complet, enveloppé dans un unique bloc de code Markdown standard, "
        "sans explications textuelles avant ou après. "
        "Exemple de format attendu :\n"
        "```python\n"
        "# Le code corrigé complet ici...\n"
        "```"
    )

    prompt = (
        f"FICHIER À CORRIGER : {os.path.basename(file_path)}\n"
        f"PROBLÈME SIGNALÉ : {args.finding}\n\n"
        f"--- CODE SOURCE D'ORIGINE ---\n"
        f"{original_code}"
    )

    print(f"Appel de Vertex AI avec le modèle {args.model} (location={location})...")
    try:
        resp = client.models.generate_content(
            model=args.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=32768
            )
        )
    except Exception as e:
        print(f"Erreur lors de l'appel Vertex : {e}")
        return 1

    response_text = resp.text or ""
    if not response_text.strip():
        print("Erreur : Vertex AI a renvoyé une réponse vide.")
        return 1

    # Extraire le code du bloc Markdown
    code_lines = []
    in_code_block = False
    for line in response_text.splitlines():
        if line.strip().startswith("```python") or line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                continue
            else:
                in_code_block = False
                break
        if in_code_block:
            code_lines.append(line)

    if not code_lines:
        # Si aucun bloc n'a été trouvé, on prend le texte brut
        print("Avertissement : Bloc de code Markdown non trouvé, utilisation du texte brut...")
        corrected_code = response_text
    else:
        corrected_code = "\n".join(code_lines) + "\n"

    # Vérification basique
    if len(corrected_code.strip()) < 50:
        print("Erreur : Le code généré est anormalement court. Annulation.")
        return 1

    if args.apply:
        print(f"Application des corrections in-place dans {file_path}...")
        try:
            # Sauvegarder un backup temporaire
            backup_path = file_path + ".bak"
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(original_code)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(corrected_code)
            print(f"✅ Fichier modifié avec succès. Backup créé : {os.path.basename(backup_path)}")
        except Exception as e:
            print(f"Erreur lors de l'écriture du fichier : {e}")
            return 1
    else:
        print("\n[DRY-RUN] Code corrigé généré avec succès (aperçu des 30 premières lignes) :")
        print("-" * 60)
        for line in corrected_code.splitlines()[:30]:
            print(line)
        print("...")
        print("-" * 60)
        print("\nPour appliquer la correction in-place, relancez avec l'option --apply")

    return 0

if __name__ == "__main__":
    sys.exit(main())
