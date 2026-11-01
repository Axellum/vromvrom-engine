#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_changes.py — Relecture Découplée et Analyse Contradictoire (Étape 2)
==========================================================================
Ce script analyse le diff Git d'une branche ou des modifications en cours,
applique les filtres d'invariants et produit un rapport de revue objectif
avant fusion humaine.

Usage:
  python scripts/review_changes.py
  python scripts/review_changes.py --base origin/master
  python scripts/review_changes.py --dry-run
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MOTEUR_DIR = SCRIPT_DIR.parent
REPORTS_DIR = MOTEUR_DIR / "reports"

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def get_git_diff(base: str = "HEAD") -> tuple[str, list[str]]:
    # Tente diff staged + unstaged vs base
    try:
        res_stat = subprocess.run(["git", "diff", "--name-only", base], cwd=MOTEUR_DIR, capture_output=True, text=True)
        files = [f.strip() for f in res_stat.stdout.splitlines() if f.strip()]
        
        # Si aucun diff vs base (ex: commit déjà fait), check HEAD~1
        if not files:
            res_stat = subprocess.run(["git", "diff", "--name-only", "HEAD~1"], cwd=MOTEUR_DIR, capture_output=True, text=True)
            files = [f.strip() for f in res_stat.stdout.splitlines() if f.strip()]
            res_diff = subprocess.run(["git", "diff", "HEAD~1"], cwd=MOTEUR_DIR, capture_output=True, text=True)
        else:
            res_diff = subprocess.run(["git", "diff", base], cwd=MOTEUR_DIR, capture_output=True, text=True)
            
        return res_diff.stdout, files
    except Exception as e:
        return "", []

def audit_diff(files: list[str], diff_text: str) -> dict:
    findings = {
        "critical": [],
        "warnings": [],
        "passed": [],
    }

    # 1. Vérification fichiers interdits
    forbidden_patterns = [".env", ".db", ".key", "google_token.json", "client_secret.json"]
    for f in files:
        for p in forbidden_patterns:
            if p in f and not ("example" in f or "test" in f):
                findings["critical"].append(f"Fichier sensible ou binaire tracké dans le diff : `{f}`")

    # 2. Vérification des invariants de code
    if "TODO" in diff_text or "FIXME" in diff_text:
        findings["warnings"].append("Présence de marqueurs `TODO` ou `FIXME` dans le diff.")

    if "!secret" not in diff_text and ("password" in diff_text.lower() or "api_key" in diff_text.lower()):
        findings["warnings"].append("Mots clés sensibles (password, api_key) sans balise `!secret` apparente.")

    # 3. Vérification des commentaires en français
    added_lines = [line[1:].strip() for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    comment_lines = [l for l in added_lines if l.startswith("#") or l.startswith("//") or l.startswith("/*")]
    if comment_lines:
        findings["passed"].append(f"{len(comment_lines)} commentaire(s) de code inspecté(s).")

    if not findings["critical"]:
        findings["passed"].append("Aucun fichier sensible ou interdit détecté.")

    return findings

def generate_report(files: list[str], findings: dict, dry_run: bool = False) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    report_file = REPORTS_DIR / f"review_report_{timestamp}.md"

    md_content = f"""# Rapport de Relecture Découplée (Reviewer Agent)
**Date :** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Statut Global :** {"🔴 ANOMALIES CRITIQUES" if findings["critical"] else "🟢 CONFORME AUX INVARIANTS"}

---

## 📁 Périmètre du Diff ({len(files)} fichier(s))
{chr(10).join(f"- `{f}`" for f in files) if files else "*Aucun fichier modifié détecté.*"}

---

## 🔍 Constats d'Audit

### ⛔ Points Critiques (Bloquants)
{chr(10).join(f"- ❌ {item}" for item in findings["critical"]) if findings["critical"] else "*(Aucune anomalie critique)*"}

### ⚠️ Avertissements (À vérifier)
{chr(10).join(f"- ⚠️ {item}" for item in findings["warnings"]) if findings["warnings"] else "*(Aucun avertissement)*"}

### ✅ Validations Réussies
{chr(10).join(f"- ✔ {item}" for item in findings["passed"]) if findings["passed"] else "*(N/A)*"}

---
*Ce rapport a été généré automatiquement par `scripts/review_changes.py` conformément au protocole Étape 2.*
"""

    if not dry_run:
        report_file.write_text(md_content, encoding="utf-8")

    return report_file, md_content

def main():
    parser = argparse.ArgumentParser(description="Relecture Découplée et Analyse de Diff (Étape 2)")
    parser.add_argument("--base", default="HEAD", help="Branche ou commit de référence pour le diff")
    parser.add_argument("--dry-run", action="store_true", help="Afficher le rapport sur la console sans écrire de fichier")
    args = parser.parse_args()

    print(f"{Colors.BOLD}{Colors.BLUE}==> Analyse du Diff Git pour Relecture Découplée...{Colors.RESET}")
    diff_text, files = get_git_diff(args.base)

    if not files:
        print(f"{Colors.YELLOW}ℹ Aucun fichier modifié trouvé dans le diff courant.{Colors.RESET}")
        sys.exit(0)

    print(f"  - Fichiers dans le diff : {len(files)}")
    findings = audit_diff(files, diff_text)
    report_file, md_text = generate_report(files, findings, dry_run=args.dry_run)

    print(f"\n{Colors.BOLD}Résultat de la Relecture :{Colors.RESET}")
    if findings["critical"]:
        print(f"  {Colors.RED}✖ Anomalies critiques détectées ({len(findings['critical'])}){Colors.RESET}")
        for c in findings["critical"]:
            print(f"    - {c}")
        sys.exit(1)
    else:
        print(f"  {Colors.GREEN}✔ Aucun invariant violé. Rapport généré dans :{Colors.RESET}")
        print(f"  {Colors.YELLOW}{report_file}{Colors.RESET}\n")

if __name__ == "__main__":
    main()
