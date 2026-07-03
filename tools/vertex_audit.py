"""
tools/vertex_audit.py — Audit long-contexte via Gemini 2.5 Pro (Vertex AI).

Concatène le code source d'un projet (avec l'hygiène de vertex_dataset_factory :
exclusion des secrets et du code tiers vendorisé), préfixe un prompt d'audit, et
envoie le tout à Gemini 2.5 Pro en un seul appel contexte long. Sauvegarde le
rapport produit.

Consomme le crédit GCP (c'est l'objectif : exploiter le contexte 1M tokens de
Gemini Pro, que la RTX locale ne sait pas faire).

USAGE :
  python tools/vertex_audit.py \
      --src . --prompt docs/prompts/ANALYSE_ULTIME_tab5-engine.md \
      --include core memory --out docs/audits/audit_moteur.md \
      --project ha-delta --location europe-west1

  python tools/vertex_audit.py \
      --src H:/AuxFilsDesIdees/00ProjetTab \
      --prompt docs/prompts/ANALYSE_ULTIME_TAB5.md \
      --exclude-dir archives Tab5_backup_20260525 \
      --out docs/audits/audit_tab5.md --project ha-delta --location europe-west1
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vertex_dataset_factory import iter_code_files  # noqa: E402

# Seuil de sécurité : au-delà, on risque de dépasser le contexte (1M tokens).
MAX_TOKENS_EST = 950_000

# SÉCURITÉ (durci après incident 21/06 : un mot de passe AdGuard codé en dur dans
# un .py HA a été transmis). On ne se fie plus au seul nom de fichier : on scanne
# le CONTENU pour des secrets en clair (affectation d'un littéral non vide à une
# variable type PASS/TOKEN/SECRET/KEY). Un fichier qui matche est EXCLU du corpus.
_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    (pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|
       private[_-]?key|client[_-]?secret|bearer|credential)\b
    \s*[:=]\s*                 # = ou : (l'identifiant peut être préfixé : ADGUARD_PASS)
    ['"][^'"\s]{6,}['"]        # littéral entre quotes, >= 6 caractères
    """
)
# On ignore les fausses alertes : références !secret (HA) et placeholders.
_SECRET_SAFE = re.compile(r"(?i)!secret|<[^>]+>|xxx+|votre_|your_|example|changeme|\.\.\.")


def content_has_hardcoded_secret(text: str) -> bool:
    """True si le contenu semble contenir un secret en clair (hors !secret/placeholder)."""
    for m in _SECRET_ASSIGN.finditer(text):
        line = text[max(0, m.start() - 40): m.end() + 5]
        if not _SECRET_SAFE.search(line):
            return True
    return False


def build_corpus(srcs: list[Path], include: set[str], extra_excluded: set[str]) -> tuple[str, int]:
    """Concatène les fichiers retenus de UNE OU PLUSIEURS racines (analyse
    transverse : moteur + serveurs + docs). Chaque bloc est préfixé de sa racine
    et de son chemin relatif."""
    blocks: list[str] = []
    for src in srcs:
        label = src.name  # nom de la racine, pour désambiguïser entre sources
        for path in sorted(iter_code_files(src, extra_excluded)):
            rel = path.relative_to(src)
            # Si --include fourni : on garde les sous-dossiers ciblés + les
            # fichiers racine (peu nombreux et structurants : points d'entrée).
            if include and len(rel.parts) > 1 and rel.parts[0] not in include:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # SÉCURITÉ : on n'envoie pas un fichier contenant un secret en clair.
            if content_has_hardcoded_secret(content):
                print(f"  ⚠️  EXCLU (secret en clair détecté) : [{label}] {rel}")
                continue
            blocks.append(f"\n\n===== [{label}] {rel} =====\n{content}")
    corpus = "".join(blocks)
    return corpus, len(corpus) // 4  # estimation tokens


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", type=Path, required=True, nargs="+",
                   help="Une ou plusieurs racines à concaténer (analyse transverse).")
    p.add_argument("--prompt", type=Path, required=True, help="Fichier prompt d'audit.")
    p.add_argument("--out", type=Path, required=True, help="Rapport Markdown de sortie.")
    p.add_argument("--include", nargs="*", default=[],
                   help="Sous-dossiers de 1er niveau à inclure (+ fichiers racine).")
    p.add_argument("--exclude-dir", nargs="*", default=[])
    p.add_argument("--project", required=True)
    # `global` : seul endpoint Vertex servant les Gemini 3.x (vérifié 21/06/2026).
    # Modèles Vertex confirmés sur `global` : gemini-3.1-pro-preview (le meilleur,
    # pro), gemini-3.5-flash (volume), gemini-2.5-pro. ⚠️ Le nom EXACT compte :
    # `gemini-3.1-pro` → 404 ; `gemini-3.1-pro-preview` → OK. Défaut = pro pour la
    # qualité d'audit ; passer --model gemini-3.5-flash pour un run économique.
    p.add_argument("--location", default="global")
    p.add_argument("--model", default="gemini-3.1-pro-preview")
    p.add_argument("--max-output", type=int, default=32768)
    args = p.parse_args()

    srcs = [s.resolve() for s in args.src]
    prompt = args.prompt.read_text(encoding="utf-8")
    corpus, est = build_corpus(srcs, set(args.include), set(args.exclude_dir))
    print(f"Corpus : {est:,} tokens estimés")
    if est > MAX_TOKENS_EST:
        print(f"ERREUR: dépasse {MAX_TOKENS_EST:,} tokens. Restreins via --include.")
        return 1

    full = (
        f"{prompt}\n\n"
        f"════════════════════════════════════════════════════════════\n"
        f"CODE SOURCE À AUDITER (concaténé, chemins indiqués par '===== FICHIER:')\n"
        f"════════════════════════════════════════════════════════════\n"
        f"{corpus}"
    )

    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=args.project, location=args.location)
    print(f"Appel {args.model} (peut prendre 1-3 min)...")
    resp = client.models.generate_content(
        model=args.model,
        contents=full,
        config=types.GenerateContentConfig(
            temperature=0.3, max_output_tokens=args.max_output),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(resp.text or "(réponse vide)", encoding="utf-8")
    # Usage tokens si disponible.
    um = getattr(resp, "usage_metadata", None)
    if um:
        print(f"Tokens — entrée: {getattr(um,'prompt_token_count','?')}, "
              f"sortie: {getattr(um,'candidates_token_count','?')}")
    print(f"[OK] Rapport écrit → {args.out} ({len(resp.text or '')} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
