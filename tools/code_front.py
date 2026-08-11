"""
tools/code_front.py — CLI du coding front (#T204).

Point d'entrée qui remplace les sessions manuelles "toujours Claude au tarif
fort" pour les tâches de code : détection de complexité → tier moyen ou fort →
Cascade (Circuit Breaker + bascule) → escalade automatique moyen→fort en filet.

Usage (depuis moteur_agents/, .env chargé automatiquement) :
    python -m tools.code_front "écris une fonction de tri fusion en python"
    python -m tools.code_front "refactore ce module" --file core/router.py
    python -m tools.code_front --stdin < prompt_long.txt        # >8191 chars Windows
    python -m tools.code_front "..." --tier fort --json

Alias shell conseillé (PowerShell) :
    function code-ai { python -m tools.code_front @args }
"""

import argparse
import asyncio
import json
import os
import sys


def _load_env() -> None:
    """Charge le .env du moteur (clés API) comme le fait gui_server."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except ImportError:
        pass


def _read_context_files(paths: list[str]) -> dict[str, str]:
    """Lit les fichiers de contexte fournis via --file (borne à 40 ko chacun)."""
    contents = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                contents[path] = f.read()[:40_000]
        except OSError as e:
            print(f"⚠️  Fichier de contexte ignoré ({path}) : {e}", file=sys.stderr)
    return contents


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="code_front",
        description="Coding front du tab5-engine : routage auto moyen/fort des tâches de code (#T204).",
    )
    parser.add_argument("prompt", nargs="?", help="La demande de code (ou utiliser --stdin).")
    parser.add_argument("--stdin", action="store_true", help="Lit le prompt sur stdin (prompts longs, limite CLI Windows).")
    parser.add_argument("--file", action="append", default=[], metavar="CHEMIN", help="Fichier de contexte (répétable).")
    parser.add_argument("--tier", choices=["moyen", "fort"], help="Force le tier (court-circuite la détection).")
    parser.add_argument("--json", action="store_true", help="Sortie JSON complète (réponse + méta routage).")
    args = parser.parse_args()

    if args.stdin:
        prompt = sys.stdin.read().strip()
    else:
        prompt = (args.prompt or "").strip()
    if not prompt:
        parser.error("Aucun prompt fourni (argument positionnel ou --stdin).")

    _load_env()
    from services.coding_front import run_coding_task

    file_contents = _read_context_files(args.file)
    result = asyncio.run(
        run_coding_task(prompt, file_contents=file_contents or None, force_tier=args.tier)
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        meta = (
            f"[tier {result['tier_used']}"
            f"{' ⬆ escaladé' if result['escalated'] else ''}"
            f" · {'complexe' if result['complex'] else 'simple'}]"
        )
        print(result["response"])
        print(f"\n--- {meta}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
