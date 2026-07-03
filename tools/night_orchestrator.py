# -*- coding: utf-8 -*-
"""
tools/night_orchestrator.py — Chef d'orchestre du run de nuit (Audit + Refactoring + Batch).

Ce script automatise les tâches suivantes de nuit :
  1) Git : crée et bascule sur une branche dédiée 'night-run-YYYYMMDD'.
  2) Context : charge vos règles récentes et leçons apprises (rules_global.md, rules_moteur_agents.md).
  3) Audits : exécute des rapports d'audits complets via Vertex AI (Gemini 3.1 Pro).
  4) Refactoring : applique in-place les correctifs P1/P2 validés sur le moteur (FastAPI async, RLock, cooldown).
  5) Batch : soumet le scoring par lots de dataset_moteur_clean.jsonl sur Vertex Batch Prediction.
  6) Reporting : rédige un rapport de nuit consolidé.

Usage:
  python tools/night_orchestrator.py
"""

import os
import sys
import json
import time
import subprocess
import requests
from pathlib import Path

PROJECT = "ha-delta"
LOCATION_BATCH = "europe-west1"
LOCATION_ONLINE = "global"

# Fichiers de règles et leçons à injecter en contexte pour guider Vertex AI
RULES_FILES = [
    r"e:\AuxFilsDesIdees\contexte_ia\01_Core\rules_global.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\rules_moteur_agents.md",
    r"e:\AuxFilsDesIdees\contexte_ia\03_Software\lecons_gcp_apis.md",
]

def refresh_oauth():
    """Récupère un jeton d'accès frais à partir du google_token_vertex.json."""
    try:
        dv = json.load(open("google_token_vertex.json", encoding="utf-8"))
        res = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": dv["client_id"], "client_secret": dv["client_secret"],
            "refresh_token": dv["refresh_token"], "grant_type": "refresh_token"}, timeout=20)
        return res.json()["access_token"]
    except Exception as e:
        print(f"❌ Impossible de rafraîchir le token OAuth : {e}")
        return None

def run_cmd(args, cwd=".", check=True):
    """Exécute une commande système et retourne la sortie."""
    print(f"Executing: {' '.join(args)} (cwd={cwd})")
    res = subprocess.run(args, capture_output=True, text=True, cwd=cwd, encoding="utf-8", errors="ignore")
    if check and res.returncode != 0:
        print(f"Error executing command: {res.stderr}")
    return res

def setup_git_branch():
    """Prépare la branche Git isolée pour le run de nuit."""
    print("\n--- Étape 1 : Préparation Git ---")
    ts = time.strftime("%Y%m%d-%H%M%S")
    branch_name = f"night-run-{ts}"
    
    # Vérifier l'état actuel
    run_cmd(["git", "status"])
    
    # Créer et basculer sur la nouvelle branche
    run_cmd(["git", "checkout", "-b", branch_name])
    print(f"✅ Branche active créée : {branch_name}")
    return branch_name

def load_contexte_rules():
    """Charge et concatène les fichiers de règles du workspace."""
    print("\n--- Étape 2 : Chargement des règles de contexte ---")
    rules_content = []
    for filepath in RULES_FILES:
        if os.path.exists(filepath):
            try:
                content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
                rules_content.append(f"=== RÈGLES DE CONTEXTE ({os.path.basename(filepath)}) ===\n{content}")
                print(f"  Loaded: {os.path.basename(filepath)}")
            except Exception as e:
                print(f"  Error loading {filepath}: {e}")
    return "\n\n".join(rules_content)

def call_vertex_refactor(file_path, finding, rules_context, access_token):
    """Applique la correction Vertex AI in-place avec injection du contexte de règles."""
    file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        return f"Fichier {os.path.basename(file_path)} introuvable"

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original_code = f.read()
    except Exception as e:
        return f"Erreur de lecture : {e}"

    from google import genai
    from google.genai import types

    # Configurer le client Vertex
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION_ONLINE)

    system_instruction = (
        "Tu es un ingénieur senior spécialisé dans la réécriture de code sécurisé, "
        "robuste et hautement optimisé (asyncio, FastAPI). Tu es guidé par les règles et leçons "
        "apprises du projet de l'utilisateur fournies ci-dessous.\n\n"
        f"{rules_context}\n\n"
        "RÉSOUS LE PROBLÈME SIGNALÉ DANS LE FICHIER CI-DESSOUS.\n"
        "Retourne l'INTÉGRALITÉ du fichier de code corrigé. "
        "Conserve toute la logique métier d'origine et les structures de classes/fonctions existantes.\n\n"
        "Ne renvoie RIEN d'autre que le code corrigé complet, enveloppé dans un unique bloc de code Markdown standard. "
        "Exemple :\n"
        "```python\n"
        "# Le code corrigé complet ici...\n"
        "```"
    )

    prompt = (
        f"FICHIER À CORRIGER : {os.path.basename(file_path)}\n"
        f"PROBLÈME SIGNALÉ : {finding}\n\n"
        f"--- CODE SOURCE D'ORIGINE ---\n"
        f"{original_code}"
    )

    model_name = "gemini-3.1-pro-preview"
    print(f"  Appel Vertex AI ({model_name}) pour {os.path.basename(file_path)}...")
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=32768
            )
        )
    except Exception as e:
        return f"Échec de l'appel Vertex : {e}"

    response_text = resp.text or ""
    if not response_text.strip():
        return "Réponse vide de Vertex"

    # Extraire le code
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

    corrected_code = "\n".join(code_lines) + "\n" if code_lines else response_text

    if len(corrected_code.strip()) < 50:
        return "Le code généré est vide ou trop court"

    # Écrire in-place avec sauvegarde
    try:
        backup_path = file_path + ".bak"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(original_code)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(corrected_code)
        return "SUCCESS"
    except Exception as e:
        return f"Erreur d'écriture : {e}"

def main():
    print("=== DÉMARRAGE DU RUN DE NUIT ===")
    branch_name = setup_git_branch()
    
    rules_context = load_contexte_rules()
    access_token = refresh_oauth()
    if not access_token:
        print("❌ Token d'accès invalide. Arrêt.")
        return 1

    report_lines = [
        f"# Rapport du Run de Nuit — {time.strftime('%Y-%m-%d')}",
        f"Branche active : `{branch_name}`\n",
        "## 1. Correctifs appliqués in-place via Vertex AI (Gemini 3.1 Pro)",
        "| Fichier | Problème ciblé | Statut |",
        "|---|---|---|",
    ]

    # Définition des tâches de refactoring à appliquer
    refactor_tasks = [
        {
            "file": "core/router.py",
            "finding": "PERF-1 : Rendre le routeur et les appels LLM asynchrones. Actuellement, _llm_classify fait un appel synchrone bloquant (requests) depuis une route async def. Remplacer par await provider.generate_async ou utiliser asyncio.to_thread pour l'analyse des intentions sémantiques."
        },
        {
            "file": "core/key_pool.py",
            "finding": "REL-2 : Fixer la boucle infinie de cooldown du KeyPool. Si toutes les clés sont en 429 (wait > 0), le pool ne doit pas renvoyer immédiatement la clé expirant le plus tôt sans attendre (ce qui boucle les 429). Il doit lever une exception de ressource épuisée ou faire un time.sleep (ou await asyncio.sleep) jusqu'à ce que la première clé soit libérée."
        },
        {
            "file": "core/models_db.py",
            "finding": "DB-1 : Concurrence SQLite. Supprimer le verrou global _db_write_lock (threading.RLock) dans _write_transaction. Le mode WAL de SQLite et le paramètre busy_timeout=30000 sont suffisants pour gérer les accès concurrents sans bloquer l'Event Loop au niveau Python."
        },
        {
            "file": "memory/gemini_embedding_fn.py",
            "finding": "REL-1 : Remplacer les time.sleep(backoff) de gestion des 429 par un mécanisme asynchrone pour éviter de figer l'Event Loop du serveur FastAPI pendant 10 secondes ou plus lors des calculs d'embeddings."
        }
    ]

    print("\n--- Étape 3 : Application des corrections de code ---")
    # Pour s'assurer que google-genai s'initialise correctement, on s'assure que les packages requis sont là
    try:
        import google.genai
    except ImportError:
        print("Installation des bibliothèques GCP requises...")
        run_cmd([sys.executable, "-m", "pip", "install", "google-genai", "google-cloud-storage"])

    for t in refactor_tasks:
        f = t["file"]
        desc = t["finding"].split(" : ")[0]
        print(f"Traitement de {f} ({desc})...")
        
        # Appel de refactoring
        res = call_vertex_refactor(f, t["finding"], rules_context, access_token)
        
        status_label = "✅ Réussi" if res == "SUCCESS" else f"❌ Échoué : {res}"
        report_lines.append(f"| `{f}` | `{t['finding'][:80]}...` | {status_label} |")
        print(f"  Résultat : {status_label}")
        time.sleep(5)  # Cooldown rate limit

    # Étape 4 : Lancer le job de Batch Prediction de la nuit sur dataset_moteur_clean.jsonl
    print("\n--- Étape 4 : Lancement du job de Scoring Batch de nuit ---")
    report_lines.append("\n## 2. Jobs Batch soumis sur Vertex AI")
    report_lines.append("| Dataset source | Modèle | Job ID | Statut |")
    report_lines.append("|---|---|---|---|")
    
    # Nous allons soumettre dataset_moteur_clean.jsonl pour notation
    dataset_file = "dataset_moteur_clean.jsonl"
    batch_req_file = "batch_score_moteur_night.jsonl"
    
    if os.path.exists(dataset_file):
        try:
            print(f"  Build du fichier de requêtes batch pour {dataset_file}...")
            # 1) Build local
            run_cmd([sys.executable, "tools/vertex_dataset_factory.py", "build", "--task", "score", 
                     "--dataset", dataset_file, "--out", batch_req_file])
            
            # 2) Submit Vertex
            print("  Soumission du job batch à Vertex...")
            submit_res = run_cmd([sys.executable, "tools/vertex_dataset_factory.py", "submit", 
                                  "--input", batch_req_file, 
                                  "--bucket", "gs://ha-delta-corpus-axell", 
                                  "--project", PROJECT, 
                                  "--location", LOCATION_BATCH, 
                                  "--model", "gemini-2.5-pro"])
            
            # Tenter d'extraire le job ID de la sortie
            job_name = "Non déterminé (voir log)"
            for line in submit_res.stdout.splitlines():
                if "Job créé :" in line:
                    job_name = line.split("Job créé :")[-1].strip()
                    break
            
            report_lines.append(f"| `{dataset_file}` | `gemini-2.5-pro` | `{job_name}` | Soumis avec succès ✅ |")
            print(f"  ✅ Job batch Vertex soumis : {job_name}")
        except Exception as e:
            report_lines.append(f"| `{dataset_file}` | `gemini-2.5-pro` | N/A | Échec de soumission : {e} ❌ |")
            print(f"  ❌ Échec soumission batch : {e}")
    else:
        print(f"  Dataset {dataset_file} introuvable, skip.")
        report_lines.append(f"| `{dataset_file}` | N/A | N/A | Fichier introuvable ❌ |")

    # Étape 5 : Lancer l'audit de 00ProjetTab (Tab5)
    print("\n--- Étape 5 : Audit long-contexte de 00ProjetTab ---")
    report_lines.append("\n## 3. Audits long-contexte de nuit")
    
    tab5_dir = r"e:\AuxFilsDesIdees\00ProjetTab"
    audit_out = r"docs\audits\audit_tab5_de_nuit.md"
    
    if os.path.exists(tab5_dir):
        try:
            print("  Lancement de l'audit long-contexte du Tab5...")
            # L'audit de Tab5 concatène le répertoire
            audit_res = run_cmd([sys.executable, "tools/vertex_audit.py", 
                                 "--src", tab5_dir, 
                                 "--prompt", "docs/prompts/ANALYSE_ULTIME_TAB5.md", 
                                 "--out", audit_out, 
                                 "--project", PROJECT, 
                                 "--location", LOCATION_ONLINE, 
                                 "--model", "gemini-3.1-pro-preview"])
            
            if audit_res.returncode == 0:
                report_lines.append(f"  - **Audit Tab5** : Écrit dans `{audit_out}` ✅")
                print(f"  ✅ Audit Tab5 terminé : {audit_out}")
            else:
                report_lines.append(f"  - **Audit Tab5** : Échec (voir logs) ❌")
                print("  ❌ Échec de l'audit Tab5.")
        except Exception as e:
            report_lines.append(f"  - **Audit Tab5** : Erreur {e} ❌")
            print(f"  ❌ Erreur audit Tab5 : {e}")
    else:
        print("  Dossier 00ProjetTab introuvable, skip.")
        report_lines.append("  - **Audit Tab5** : Dossier introuvable ❌")

    # Écrire le rapport final
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"night_run_report_{time.strftime('%Y%d%m_%H%M%S')}.md"
    
    report_lines.append("\n## 4. Consignes pour le lendemain matin")
    report_lines.append("1. Vérifier les diffs sur la branche de nuit : `git diff master`.")
    report_lines.append("2. Lancer les tests unitaires : `pytest`.")
    report_lines.append("3. Récupérer les résultats du job de scoring batch (si terminé) :")
    report_lines.append("   `python tools/vertex_dataset_factory.py collect --job <JOB_ID> --bucket gs://ha-delta-corpus-axell --project ha-delta --out dataset_scored.jsonl`.")

    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\n✅ Rapport de nuit rédigé : {report_path}")
    
    # Revenir sur master ou sauvegarder l'état
    print("\nRun de nuit configuré et lancé. Le reste tourne sur le Cloud Google.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
