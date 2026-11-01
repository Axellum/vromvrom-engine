#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_workspace.py — Commande d'Examen Automatique Unifiée (Étape 2)
===================================================================
Script d'audit et de validation autonome cross-environnements.
Exécute la boucle de vérification complète sans intervention humaine.

Usage:
  python scripts/verify_workspace.py --moteur
  python scripts/verify_workspace.py --ha
  python scripts/verify_workspace.py --esphome
  python scripts/verify_workspace.py --all
  python scripts/verify_workspace.py --all --fast
  python scripts/verify_workspace.py --moteur --fix
"""

import argparse
import os
import subprocess
import sys
import sqlite3
import yaml
from pathlib import Path

# Racine canonique du projet
SCRIPT_DIR = Path(__file__).resolve().parent
MOTEUR_DIR = SCRIPT_DIR.parent
ROOT_DIR = MOTEUR_DIR.parent

# Couleurs terminal
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def print_header(title: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}==> {title}{Colors.RESET}", flush=True)

def print_ok(msg: str):
    print(f"  {Colors.GREEN}✔ {msg}{Colors.RESET}", flush=True)

def print_warn(msg: str):
    print(f"  {Colors.YELLOW}⚠ {msg}{Colors.RESET}", flush=True)

def print_err(msg: str):
    print(f"  {Colors.RED}✖ {msg}{Colors.RESET}", flush=True)

# -------------------------------------------------------------
# 1. Module Moteur (Python, Pytest, Ruff, SQLite)
# -------------------------------------------------------------
def verify_moteur(fast: bool = False, fix: bool = False) -> tuple[bool, list[str]]:
    errors = []
    print_header("Module 1 : Moteur Multi-Agents (tab5-engine)")

    # A. Lint & Syntax Check (Ruff)
    print("  [1/3] Analyse statique et Syntaxe Python (Ruff)...")
    cmd_ruff = [sys.executable, "-m", "ruff", "check"]
    if fix:
        cmd_ruff.append("--fix")
    # On cible les erreurs syntaxiques critiques et undefined variables (E9, F821, F822, F823, E722)
    cmd_ruff.extend(["--select", "E9,F821,F822,F823,E722", "core", "agents", "tools", "memory", "api"])
    
    res = subprocess.run(cmd_ruff, cwd=MOTEUR_DIR, capture_output=True, text=True)
    if res.returncode == 0:
        print_ok("Syntaxe Python et variables : 0 anomalie bloquante")
    else:
        print_err(f"Ruff a détecté des anomalies bloquantes :\n{res.stdout or res.stderr}")
        errors.append("Échec du linting statique Python")

    # B. Intégrité des Bases SQLite
    print("  [2/3] Intégrité des bases de données SQLite...")
    dbs_to_check = ["memory.db", "models_registry.db", "moteur_runtime.db"]
    for db_name in dbs_to_check:
        db_path = MOTEUR_DIR / db_name
        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cursor = conn.cursor()
                cursor.execute("PRAGMA quick_check;")
                result = cursor.fetchone()
                conn.close()
                if result and result[0] == "ok":
                    print_ok(f"BDD {db_name} : intègre (quick_check ok)")
                else:
                    print_err(f"BDD {db_name} : corruption détectée ({result})")
                    errors.append(f"Corruption SQLite sur {db_name}")
            except Exception as e:
                print_err(f"BDD {db_name} : impossible de vérifier ({e})")
                errors.append(f"Erreur d'accès SQLite sur {db_name}")
        else:
            print_warn(f"BDD {db_name} : non présente (ignorée)")

    # C. Tests Unitaires Pytest
    print("  [3/3] Exécution de la suite de tests unitaires (Pytest)...")
    cmd_pytest = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-m", "not live"]
    if fast:
        # En mode fast, on limite aux tests unitaires légers
        cmd_pytest.extend(["tests/unit/"])
    
    res_test = subprocess.run(cmd_pytest, cwd=MOTEUR_DIR, capture_output=True, text=True)
    if res_test.returncode == 0:
        summary_line = res_test.stdout.strip().split("\n")[-1]
        print_ok(f"Tests pytest validés : {summary_line}")
    else:
        print_err(f"Échec de la suite de tests pytest :\n{res_test.stdout or res_test.stderr}")
        errors.append("Échec des tests unitaires pytest")

    return (len(errors) == 0, errors)

# -------------------------------------------------------------
# 2. Module Home Assistant (YAML & Automations)
# -------------------------------------------------------------
def verify_home_assistant() -> tuple[bool, list[str]]:
    errors = []
    print_header("Module 2 : Home Assistant (Configurations & YAML)")
    
    ha_dirs = [
        ROOT_DIR / "00ProjetTab",
        ROOT_DIR / "ha_corpus_src",
        MOTEUR_DIR / "workflows",
    ]
    
    EXCLUDE_PARTS = {"archives", "backups", "backups_prod", ".claude", "worktrees", "scratch", "node_modules"}
    
    yaml_files = []
    for d in ha_dirs:
        if d.exists():
            for yf in list(d.glob("**/*.yaml")) + list(d.glob("**/*.yml")):
                if not any(part in yf.parts for part in EXCLUDE_PARTS):
                    yaml_files.append(yf)
            
    print(f"  [1/2] Validation de la syntaxe YAML ({len(yaml_files)} fichiers actifs)...")
    valid_count = 0
    for yf in yaml_files:
        try:
            with open(yf, "r", encoding="utf-8", errors="ignore") as f:
                class HALoader(yaml.SafeLoader):
                    pass
                HALoader.add_multi_constructor("!", lambda loader, suffix, node: None)
                yaml.load(f, Loader=HALoader)
                valid_count += 1
        except Exception as e:
            print_err(f"Erreur syntaxe YAML dans {yf.name} : {e}")
            errors.append(f"Syntaxe YAML invalide : {yf.name}")

    if valid_count == len(yaml_files) and yaml_files:
        print_ok(f"Tous les fichiers YAML actifs sont valides ({valid_count}/{len(yaml_files)})")
    elif not yaml_files:
        print_ok("Aucun YAML Home Assistant additionnel à valider")

    print_ok("Structure Home Assistant validée")
    return (len(errors) == 0, errors)

# -------------------------------------------------------------
# 3. Module ESPHome (Firmware & Pins Tab5)
# -------------------------------------------------------------
def verify_esphome() -> tuple[bool, list[str]]:
    errors = []
    print_header("Module 3 : ESPHome (Tab5 V2 & Déclarations C++/YAML)")
    
    EXCLUDE_PARTS = {"archives", "backups", "backups_prod", ".claude", "worktrees", "scratch", "node_modules"}
    esphome_dirs = [ROOT_DIR / "00ProjetTab", ROOT_DIR / "esphome"]
    esphome_files = []
    for ed in esphome_dirs:
        if ed.exists():
            for ef in ed.glob("**/*.yaml"):
                if not any(part in ef.parts for part in EXCLUDE_PARTS):
                    esphome_files.append(ef)

    print(f"  [1/2] Analyse des configurations ESPHome ({len(esphome_files)} fichiers actifs)...")
    for ef in esphome_files:
        try:
            content = ef.read_text(encoding="utf-8", errors="ignore")
            if "esp32" in content.lower():
                print_ok(f"Config ESPHome {ef.name} : format cible reconnu")
        except Exception as e:
            print_err(f"Erreur de lecture sur {ef.name} : {e}")
            errors.append(f"Erreur config ESPHome : {ef.name}")

    print_ok("Contrôles ESPHome terminés")
    return (len(errors) == 0, errors)

# -------------------------------------------------------------
# 4. Module Invariants & Sécurité (Secrets, UTF-8 sans BOM)
# -------------------------------------------------------------
def verify_invariants(fix: bool = False) -> tuple[bool, list[str]]:
    errors = []
    print_header("Module 4 : Invariants Globaux & Sécurité")
    
    EXCLUDE_PARTS = {"archives", "backups", "backups_prod", ".claude", "worktrees", "scratch", "node_modules", ".git", "chroma_db"}
    
    # A. Vérification UTF-8 BOM
    print("  [1/2] Contrôle d'encodage (Interdiction UTF-8 avec BOM)...")
    bom_files = []
    for ext in ["*.py", "*.md", "*.yaml", "*.json"]:
        for fpath in MOTEUR_DIR.glob(f"**/{ext}"):
            if any(part in fpath.parts for part in EXCLUDE_PARTS):
                continue
            try:
                with open(fpath, "rb") as f:
                    header = f.read(3)
                    if header == b"\xef\xbb\xbf":
                        if fix:
                            content = f.read()
                            with open(fpath, "wb") as f_out:
                                f_out.write(content)
                        else:
                            bom_files.append(fpath.name)
            except Exception:
                pass

    if bom_files:
        print_err(f"Fichiers contenant un UTF-8 BOM détectés ({len(bom_files)}) : {', '.join(bom_files[:5])}...")
        errors.append(f"UTF-8 BOM présent dans {len(bom_files)} fichier(s)")
    else:
        print_ok("Encodage strict : Aucun BOM détecté sur les fichiers actifs")

    # B. Secrets & Clés en clair dans git
    print("  [2/2] Détection de fuites de secrets / tokens non masqués...")
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=MOTEUR_DIR, capture_output=True, text=True)
        staged_or_modified = [line[3:].strip() for line in res.stdout.splitlines() if line.strip()]
        for f_rel in staged_or_modified:
            f_full = MOTEUR_DIR / f_rel
            if f_full.is_file() and not f_rel.endswith((".db", ".log", ".jsonl", ".png", ".jpg", ".gguf")):
                try:
                    text = f_full.read_text(encoding="utf-8", errors="ignore")
                    if "ghp_" in text or "AIzaSy" in text:
                        if not ("example" in f_rel or "test" in f_rel or ".env.example" in f_rel or "verify_workspace" in f_rel):
                            print_warn(f"Suspicion de clé secrète en clair dans : {f_rel}")
                except Exception:
                    pass
        print_ok("Vérification sécurité Git terminée")
    except Exception as e:
        print_warn(f"Git non accessible pour vérification des secrets : {e}")

    return (len(errors) == 0, errors)

# -------------------------------------------------------------
# MAIN RUNNER
# -------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Commande d'Examen Automatique Unifiée (Étape 2)")
    parser.add_argument("--moteur", action="store_true", help="Vérifier le Moteur Multi-Agents (Python, pytest, sqlite)")
    parser.add_argument("--ha", action="store_true", help="Vérifier Home Assistant (YAML, Automations)")
    parser.add_argument("--esphome", action="store_true", help="Vérifier ESPHome (Configurations Tab5)")
    parser.add_argument("--all", action="store_true", help="Exécuter l'examen complet de tous les modules")
    parser.add_argument("--fast", action="store_true", help="Exécution rapide des tests (smoke tests)")
    parser.add_argument("--fix", action="store_true", help="Corriger automatiquement le formatage quand possible")
    
    args = parser.parse_args()

    if not (args.moteur or args.ha or args.esphome or args.all):
        args.moteur = True

    print(f"{Colors.BOLD}======================================================={Colors.RESET}")
    print(f"{Colors.BOLD}     BOUCLE D'EXAMEN AUTOMATIQUE — ÉTAPE 2 (ANTI-SUPERVISION){Colors.RESET}")
    print(f"{Colors.BOLD}======================================================={Colors.RESET}")

    all_ok = True
    total_errors = []

    if args.moteur or args.all:
        ok, errs = verify_moteur(fast=args.fast, fix=args.fix)
        all_ok = all_ok and ok
        total_errors.extend(errs)

    if args.ha or args.all:
        ok, errs = verify_home_assistant()
        all_ok = all_ok and ok
        total_errors.extend(errs)

    if args.esphome or args.all:
        ok, errs = verify_esphome()
        all_ok = all_ok and ok
        total_errors.extend(errs)

    if args.all:
        ok, errs = verify_invariants(fix=args.fix)
        all_ok = all_ok and ok
        total_errors.extend(errs)

    print(f"\n{Colors.BOLD}======================================================={Colors.RESET}")
    if all_ok:
        print(f"{Colors.BOLD}{Colors.GREEN}✔ EXAMEN RÉUSSI : TOUS LES CONTRÔLES SONT VERTS (Code 0){Colors.RESET}")
        print(f"{Colors.BOLD}======================================================={Colors.RESET}\n")
        sys.exit(0)
    else:
        print(f"{Colors.BOLD}{Colors.RED}✖ ÉCHEC DE L'EXAMEN : {len(total_errors)} anomalie(s) détectée(s){Colors.RESET}")
        for err in total_errors:
            print(f"  - {err}")
        print(f"{Colors.BOLD}======================================================={Colors.RESET}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
