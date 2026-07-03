"""
tools/vertex_dataset_factory.py — Usine à dataset via Vertex AI Batch Prediction.

But : valoriser le crédit GCP qui expire en générant/notant un dataset
d'instruction de HAUTE qualité avec Gemini 2.5 Pro/Flash, en mode BATCH
(asynchrone, ~50 % du prix online, AUCUN coût d'endpoint au repos).

Le résultat est un .jsonl propre, durable, que l'on peut fine-tuner ENSUITE
gratuitement en local (QLoRA sur la RTX 5070 Ti) — bien après expiration du crédit.

────────────────────────────────────────────────────────────────────────────
WORKFLOW EN 3 ÉTAPES
────────────────────────────────────────────────────────────────────────────
  1) build    : parcourt le code source, découpe en chunks (en EXCLUANT le code
                tiers/vendorisé), et écrit un .jsonl de requêtes batch.
                Deux objectifs possibles :
                  --task generate : génère des paires instruction→réponse ancrées
                                    dans chaque chunk (+ auto-notation qualité).
                  --task score    : re-note un dataset existant (0-10) pour le
                                    filtrer (quality > quantity).
  2) submit   : upload le .jsonl vers GCS et crée le job Vertex Batch.
  3) collect  : récupère la sortie GCS du job terminé, parse, filtre par seuil
                de qualité, et écrit le dataset final propre.

────────────────────────────────────────────────────────────────────────────
PRÉREQUIS
────────────────────────────────────────────────────────────────────────────
  pip install google-genai google-cloud-storage
  gcloud auth application-default login           # ADC
  gcloud config set project <PROJECT_ID>
  # Activer les APIs : aiplatform.googleapis.com, storage.googleapis.com
  # Un bucket GCS dans la MÊME région que le job (défaut us-central1).

GARDE-FOUS (lis la doc d'audit) :
  - Pose un Budget Alert AVANT de lancer (Billing → Budgets & alerts).
  - Le mode batch n'a PAS de coût d'idle : rien à éteindre après.
  - Préfère gemini-2.5-flash pour la génération (volume), 2.5-pro pour la notation.

Exemples :
  python tools/vertex_dataset_factory.py build  --task generate \
      --src ../ --out batch_gen.jsonl --pairs 4 --model gemini-2.5-flash
  python tools/vertex_dataset_factory.py submit  --input batch_gen.jsonl \
      --bucket gs://mon-bucket-audit --project mon-projet
  python tools/vertex_dataset_factory.py collect --job <JOB_NAME> \
      --bucket gs://mon-bucket-audit --project mon-projet \
      --out dataset_clean.jsonl --min-score 7
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator

# ──────────────────────────────────────────────────────────────────────────
# Configuration du parcours du code source
# ──────────────────────────────────────────────────────────────────────────

# Extensions de code à indexer.
CODE_EXTENSIONS = {
    ".py", ".cpp", ".h", ".hpp", ".c", ".cc", ".ino",
    ".yaml", ".yml", ".js", ".ts", ".md",
}

# Dossiers EXCLUS : code tiers/vendorisé, artefacts, secrets. Indexer ça
# pollue le dataset (cf. flatbuffers/reflection.h) et gaspille du crédit.
EXCLUDED_DIRS = {
    "third_party", "managed_components", "vendor", "node_modules",
    ".esphome", ".pio", ".git", "__pycache__", "venv", ".venv", "env",
    "build", "dist", ".pytest_cache", "chroma_db", "chromadb_data",
    "site-packages", ".mypy_cache", "egg-info",
}

# SÉCURITÉ : on n'envoie JAMAIS un fichier de secrets à un service externe.
# Un fichier dont le nom (minuscule) contient l'un de ces fragments est ignoré,
# quelle que soit son extension (secrets.yaml, .env, *_credentials.json…).
SENSITIVE_NAME_TOKENS = (
    "secret", ".env", "password", "credential", "private_key", "id_rsa",
    "id_ed25519", "token",
)


def _is_sensitive(filename: str) -> bool:
    """True si le nom de fichier suggère un secret (jamais envoyé à Vertex)."""
    low = filename.lower()
    return any(tok in low for tok in SENSITIVE_NAME_TOKENS)

# Taille d'un chunk en caractères (≈ 1k tokens) et chevauchement.
CHUNK_SIZE = 4000
CHUNK_OVERLAP = 300
MIN_CHUNK_CHARS = 200  # En dessous, le chunk n'a pas assez de substance.

# Modèles par défaut selon la tâche (flash = volume/coût, pro = qualité de jugement).
DEFAULT_MODEL = {"generate": "gemini-2.5-flash", "score": "gemini-2.5-pro"}

# ──────────────────────────────────────────────────────────────────────────
# Prompts système
# ──────────────────────────────────────────────────────────────────────────

SYS_GENERATE = (
    "Tu es un ingénieur senior qui construit un dataset d'instruction pour "
    "fine-tuner un modèle de code spécialisé en domotique (Home Assistant, "
    "ESPHome, ESP32-P4, Python asyncio multi-agents). À partir d'un EXTRAIT de "
    "code/doc, génère des paires instruction→réponse réalistes, variées et "
    "AUTONOMES (compréhensibles sans voir l'extrait). Couvre : explication, "
    "debug, refactor, génération, bonnes pratiques. Réponses correctes, "
    "concises, idiomatiques. Rédige TOUT en français."
)

# On impose une sortie JSON stricte pour un parsing fiable au collect.
SCHEMA_GENERATE = (
    "Réponds UNIQUEMENT par un objet JSON: "
    '{\"pairs\": [{\"instruction\": str, \"input\": str, \"output\": str, '
    '\"tags\": [str], \"quality\": int (0-10), \"rationale\": str}]}. '
    "`input` peut être vide. `quality` = ton auto-évaluation honnête de la "
    "paire (10 = excellente donnée d'entraînement). Pas de texte hors JSON."
)

SYS_SCORE = (
    "Tu es un évaluateur impitoyable de données d'entraînement. On te donne une "
    "paire instruction→réponse. Note-la de 0 à 10 sur : correction technique, "
    "clarté, utilité pour fine-tuner un modèle de code domotique, autonomie. "
    "Sois sévère : une réponse fausse ou vague = note basse. Réponds UNIQUEMENT "
    'par {\"score\": int (0-10), \"reason\": str}. Tout en français.'
)


# ──────────────────────────────────────────────────────────────────────────
# Parcours & chunking
# ──────────────────────────────────────────────────────────────────────────

def iter_code_files(root: Path, extra_excluded: set[str] | None = None) -> Iterator[Path]:
    """Itère sur les fichiers de code, en élaguant dossiers exclus et secrets."""
    excluded = EXCLUDED_DIRS | (extra_excluded or set())
    for dirpath, dirnames, filenames in os.walk(root):
        # Élagage in-place : os.walk ne descend pas dans les dossiers retirés.
        dirnames[:] = [
            d for d in dirnames
            if d not in excluded and not d.endswith(".egg-info")
        ]
        for fname in filenames:
            if _is_sensitive(fname):
                continue  # Sécurité : on ne transmet jamais de secrets.
            if Path(fname).suffix.lower() in CODE_EXTENSIONS:
                yield Path(dirpath) / fname


def chunk_text(text: str) -> list[str]:
    """Découpe un texte en chunks avec chevauchement (fenêtre glissante simple)."""
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for i in range(0, len(text), step):
        chunk = text[i:i + CHUNK_SIZE]
        if len(chunk.strip()) >= MIN_CHUNK_CHARS:
            chunks.append(chunk)
    return chunks


# ──────────────────────────────────────────────────────────────────────────
# Construction du JSONL de requêtes batch (format Vertex Gemini)
# ──────────────────────────────────────────────────────────────────────────

def _batch_request(system: str, user: str, *, json_out: bool,
                   max_tokens: int) -> dict:
    """Construit une ligne de requête au format batch Vertex (clé `request`)."""
    gen_config: dict = {"temperature": 0.4, "maxOutputTokens": max_tokens}
    if json_out:
        gen_config["responseMimeType"] = "application/json"
    return {
        "request": {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_config,
        }
    }


def build_generate(src: Path, out: Path, pairs: int,
                   extra_excluded: set[str] | None = None) -> int:
    """Construit les requêtes de GÉNÉRATION de paires à partir du code source."""
    n = 0
    with out.open("w", encoding="utf-8") as fout:
        for path in iter_code_files(src, extra_excluded):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = path.relative_to(src) if path.is_relative_to(src) else path.name
            for ci, chunk in enumerate(chunk_text(text)):
                user = (
                    f"FICHIER: {rel} (chunk {ci})\n"
                    f"LANGAGE: {path.suffix.lstrip('.')}\n"
                    f"Génère exactement {pairs} paires instruction→réponse de "
                    f"haute qualité ancrées dans cet extrait.\n\n"
                    f"{SCHEMA_GENERATE}\n\n--- EXTRAIT ---\n{chunk}"
                )
                fout.write(json.dumps(
                    _batch_request(SYS_GENERATE, user, json_out=True,
                                   max_tokens=4096),
                    ensure_ascii=False) + "\n")
                n += 1
    return n


def build_score(dataset: Path, out: Path) -> int:
    """Construit les requêtes de NOTATION d'un dataset existant (1 ligne = 1 paire)."""
    n = 0
    with dataset.open("r", encoding="utf-8") as fin, \
         out.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Tolérant aux schémas courants (instruction/output ou prompt/completion).
            instr = ex.get("instruction") or ex.get("prompt") or ex.get("input", "")
            answer = ex.get("output") or ex.get("completion") or ex.get("response", "")
            if not instr or not answer:
                continue
            # Vertex ré-émet toujours la requête en sortie : on encode donc les
            # métadonnées DANS le prompt (pas de clé sibling, rejetée à la
            # validation). Au collect, on les relit depuis la requête échoée.
            tags_json = json.dumps(ex.get("tags", []), ensure_ascii=False)
            user = (
                f"<<META_TAGS>>{tags_json}<<END_META>>\n"
                f"INSTRUCTION:\n{instr}\n\nRÉPONSE:\n{answer}"
            )
            req = _batch_request(SYS_SCORE, user, json_out=True, max_tokens=2048)
            fout.write(json.dumps(req, ensure_ascii=False) + "\n")
            n += 1
    return n


# ──────────────────────────────────────────────────────────────────────────
# Soumission du job batch (upload GCS + Vertex Batch)
# ──────────────────────────────────────────────────────────────────────────

def _gcs_split(uri: str) -> tuple[str, str]:
    """gs://bucket/prefix → (bucket, prefix)."""
    assert uri.startswith("gs://"), "Le bucket doit être un URI gs://"
    rest = uri[len("gs://"):]
    bucket, _, prefix = rest.partition("/")
    return bucket, prefix.rstrip("/")


def submit(input_file: Path, bucket: str, project: str, location: str,
           model: str) -> None:
    """Upload le JSONL vers GCS puis crée le job Vertex Batch."""
    from google.cloud import storage  # import tardif (dépendance optionnelle)
    from google import genai
    from google.genai.types import CreateBatchJobConfig

    bkt, prefix = _gcs_split(bucket)
    ts = time.strftime("%Y%m%d-%H%M%S")
    in_blob = f"{prefix}/in/{input_file.stem}-{ts}.jsonl" if prefix else f"in/{input_file.stem}-{ts}.jsonl"
    out_prefix = f"{prefix}/out/{ts}/" if prefix else f"out/{ts}/"

    # 1) Upload de l'entrée.
    client_gcs = storage.Client(project=project)
    client_gcs.bucket(bkt).blob(in_blob).upload_from_filename(str(input_file))
    src_uri = f"gs://{bkt}/{in_blob}"
    dest_uri = f"gs://{bkt}/{out_prefix}"
    print(f"[OK] Upload : {src_uri}")

    # 2) Création du job batch sur Vertex AI.
    client = genai.Client(vertexai=True, project=project, location=location)
    job = client.batches.create(
        model=model,
        src=src_uri,
        config=CreateBatchJobConfig(dest=dest_uri),
    )
    print(f"[OK] Job créé : {job.name}")
    print(f"     État      : {job.state}")
    print(f"     Sortie    : {dest_uri}")
    print("\nSuivi : python tools/vertex_dataset_factory.py status "
          f"--job {job.name} --project {project} --location {location}")
    print("Récup : ... collect --job <name> --bucket <gs://...> --project ... "
          "--out dataset_clean.jsonl --min-score 7")


def status(job: str, project: str, location: str) -> None:
    """Affiche l'état d'un job batch."""
    from google import genai
    client = genai.Client(vertexai=True, project=project, location=location)
    j = client.batches.get(name=job)
    print(f"Job   : {j.name}\nÉtat  : {j.state}")
    if getattr(j, "dest", None):
        print(f"Sortie: {j.dest}")


# ──────────────────────────────────────────────────────────────────────────
# Collecte & filtrage des résultats
# ──────────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extrait le premier objet JSON d'une réponse modèle (tolérant au bruit)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _job_output_prefix(job: str, project: str) -> str:
    """Retourne le préfixe GCS de sortie du job (scopage : évite de mélanger
    les sorties de plusieurs jobs présents dans le même bucket)."""
    from google import genai
    # La région est encodée dans le nom du job (.../locations/<loc>/...).
    loc = "us-central1"
    parts = job.split("/")
    if "locations" in parts:
        loc = parts[parts.index("locations") + 1]
    client = genai.Client(vertexai=True, project=project, location=loc)
    j = client.batches.get(name=job)
    dest = getattr(j, "dest", None)
    # dest peut être un objet (GcsDestination) ou une string selon la version.
    uri = getattr(dest, "gcs_uri", None) or getattr(dest, "output_uri_prefix", None) \
        or (dest if isinstance(dest, str) else None)
    if not uri:
        raise RuntimeError(f"Impossible de déterminer la sortie GCS du job {job}.")
    return uri


def _iter_gcs_predictions(bucket: str, project: str, job: str) -> Iterator[dict]:
    """Itère sur les prédictions du job, SCOPÉES à son préfixe de sortie."""
    from google.cloud import storage
    bkt, _ = _gcs_split(bucket)
    # On ne scanne que le préfixe de CE job (pas tout le bucket).
    _, prefix = _gcs_split(_job_output_prefix(job, project))
    client = storage.Client(project=project)
    for blob in client.list_blobs(bkt, prefix=prefix):
        if "predictions" in blob.name and blob.name.endswith(".jsonl"):
            for line in blob.download_as_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def _response_text(pred: dict) -> str:
    """Extrait le texte généré d'une ligne de prédiction batch Vertex."""
    resp = pred.get("response") or pred.get("prediction") or {}
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def _echoed_user_text(pred: dict) -> str:
    """Récupère le texte de la requête utilisateur ré-émise par Vertex (mode score)."""
    req = pred.get("request") or {}
    try:
        return req["contents"][0]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def _parse_score_meta(user_text: str) -> dict:
    """Relit instruction/réponse/tags encodés dans le prompt de notation."""
    import re
    tags: list = []
    m = re.search(r"<<META_TAGS>>(.*?)<<END_META>>", user_text, re.DOTALL)
    if m:
        try:
            tags = json.loads(m.group(1))
        except json.JSONDecodeError:
            tags = []
    instr = answer = ""
    im = re.search(r"INSTRUCTION:\n(.*?)\n\nRÉPONSE:\n(.*)", user_text, re.DOTALL)
    if im:
        instr, answer = im.group(1).strip(), im.group(2).strip()
    return {"instruction": instr, "output": answer, "tags": tags}


def collect(job: str, bucket: str, project: str, out: Path,
            min_score: int) -> None:
    """Récupère les prédictions, parse, filtre par qualité, écrit le dataset propre."""
    kept = 0
    dropped = 0
    with out.open("w", encoding="utf-8") as fout:
        for pred in _iter_gcs_predictions(bucket, project, job):
            data = _extract_json(_response_text(pred))
            if not data:
                dropped += 1
                continue

            # Cas génération : un objet {"pairs": [...]}.
            if "pairs" in data:
                for p in data["pairs"]:
                    if int(p.get("quality", 0)) >= min_score and p.get("output"):
                        fout.write(json.dumps({
                            "instruction": p.get("instruction", ""),
                            "input": p.get("input", ""),
                            "output": p["output"],
                            "tags": p.get("tags", []),
                            "quality": p.get("quality"),
                        }, ensure_ascii=False) + "\n")
                        kept += 1
                    else:
                        dropped += 1
            # Cas notation : un objet {"score": int, "reason": str}.
            elif "score" in data:
                if int(data.get("score", 0)) >= min_score:
                    meta = _parse_score_meta(_echoed_user_text(pred))
                    fout.write(json.dumps({
                        "instruction": meta["instruction"],
                        "output": meta["output"],
                        "tags": meta["tags"],
                        "score": data["score"],
                    }, ensure_ascii=False) + "\n")
                    kept += 1
                else:
                    dropped += 1

    print(f"[OK] Dataset propre : {out}\n     Gardés={kept}  Rejetés={dropped}  "
          f"(seuil={min_score})")


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="Construit le JSONL de requêtes batch.")
    pb.add_argument("--task", choices=["generate", "score"], required=True)
    pb.add_argument("--src", type=Path, help="Racine du code (mode generate).")
    pb.add_argument("--dataset", type=Path, help="Dataset à noter (mode score).")
    pb.add_argument("--out", type=Path, required=True)
    pb.add_argument("--pairs", type=int, default=3,
                    help="Paires générées par chunk (mode generate).")
    pb.add_argument("--exclude-dir", nargs="*", default=[],
                    help="Noms de dossiers supplémentaires à exclure "
                         "(ex: archives Tab5_backup_20260525).")

    ps = sub.add_parser("submit", help="Upload GCS + crée le job batch.")
    ps.add_argument("--input", type=Path, required=True)
    ps.add_argument("--bucket", required=True, help="gs://bucket[/prefix]")
    ps.add_argument("--project", required=True)
    ps.add_argument("--location", default="us-central1")
    ps.add_argument("--model", default="gemini-2.5-flash")

    pst = sub.add_parser("status", help="État d'un job batch.")
    pst.add_argument("--job", required=True)
    pst.add_argument("--project", required=True)
    pst.add_argument("--location", default="us-central1")

    pc = sub.add_parser("collect", help="Récupère + filtre les résultats.")
    pc.add_argument("--job", required=True)
    pc.add_argument("--bucket", required=True)
    pc.add_argument("--project", required=True)
    pc.add_argument("--out", type=Path, required=True)
    pc.add_argument("--min-score", type=int, default=7)

    args = parser.parse_args()

    if args.cmd == "build":
        if args.task == "generate":
            if not args.src or not args.src.exists():
                print("ERREUR: --src valide requis en mode generate.")
                return 1
            n = build_generate(args.src.resolve(), args.out, args.pairs,
                               extra_excluded=set(args.exclude_dir))
            print(f"[OK] {n} requêtes de génération → {args.out}")
            print(f"     (≈ {n * args.pairs} paires attendues avant filtrage)")
        else:
            if not args.dataset or not args.dataset.exists():
                print("ERREUR: --dataset valide requis en mode score.")
                return 1
            n = build_score(args.dataset, args.out)
            print(f"[OK] {n} requêtes de notation → {args.out}")
        return 0

    if args.cmd == "submit":
        submit(args.input, args.bucket, args.project, args.location, args.model)
        return 0

    if args.cmd == "status":
        status(args.job, args.project, args.location)
        return 0

    if args.cmd == "collect":
        collect(args.job, args.bucket, args.project, args.out, args.min_score)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
