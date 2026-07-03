# -*- coding: utf-8 -*-
"""
tools/check_and_collect_jobs.py — Script de surveillance et collecte automatique des jobs Vertex AI Batch.
"""

import sys
import os
import subprocess
import time
from google import genai

PROJECT = "ha-delta"
LOCATION = "europe-west1"
BUCKET = "gs://ha-delta-corpus-axell"

JOBS = {
    "tab5": {
        "id": "projects/75626175038/locations/europe-west1/batchPredictionJobs/8551646229866479616",
        "out_file": "dataset_tab5_scored.jsonl"
    },
    "moteur": {
        "id": "projects/75626175038/locations/europe-west1/batchPredictionJobs/778433273025003520",
        "out_file": "dataset_moteur_scored.jsonl"
    }
}

def check_job_status(job_id: str) -> str:
    try:
        client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        j = client.batches.get(name=job_id)
        return str(j.state).split(".")[-1]
    except Exception as e:
        print(f"Error checking status for {job_id}: {e}")
        return "UNKNOWN"

def collect_job_results(job_id: str, out_file: str):
    print(f"Collecting results for {job_id} -> {out_file}...")
    cmd = [
        sys.executable, "tools/vertex_dataset_factory.py", "collect",
        "--job", job_id,
        "--bucket", BUCKET,
        "--project", PROJECT,
        "--out", out_file,
        "--min-score", "7"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    print(res.stdout)
    if res.returncode != 0:
        print(f"Error collecting results: {res.stderr}")
    else:
        print(f"Successfully collected results for {job_id}.")

def main():
    print(f"--- Running Vertex Job Checker at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    all_done = True
    
    # Fichier d'état pour ne pas collecter plusieurs fois
    state_file = "vertex_jobs_collect_state.json"
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    for name, info in JOBS.items():
        job_id = info["id"]
        out_file = info["out_file"]
        
        # Si déjà marqué comme collecté avec succès, on ignore
        if state.get(job_id) == "COLLECTED" and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
            print(f"Job {name} ({job_id}) already collected.")
            continue
            
        status = check_job_status(job_id)
        print(f"Job {name} ({job_id}) state: {status}")
        
        if status == "JOB_STATE_SUCCEEDED":
            collect_job_results(job_id, out_file)
            state[job_id] = "COLLECTED"
        elif status in ["JOB_STATE_FAILED", "JOB_STATE_CANCELLED"]:
            print(f"Job {name} ({job_id}) finished with state {status}. Cannot collect.")
            state[job_id] = f"FINISHED_{status}"
        else:
            print(f"Job {name} ({job_id}) is still in progress ({status}).")
            all_done = False
            
    with open(state_file, "w", encoding="utf-8") as f:
        import json
        json.dump(state, f, indent=2, ensure_ascii=False)
        
    if all_done:
        print("All jobs have completed processing.")
    else:
        print("Some jobs are still running.")

if __name__ == "__main__":
    main()
