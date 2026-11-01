#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worktree_manager.py — Gestionnaire de Bacs à Sable Git Worktrees (Étape 2)
=========================================================================
Permet d'isoler chaque chantier d'agent dans un dossier dédié dans worktrees/, à côté du dépôt
pour éviter les collisions de fichiers et permettre le multi-agents parallèle.

Usage:
  python scripts/worktree_manager.py create <nom_tache> [base_branch]
  python scripts/worktree_manager.py list
  python scripts/worktree_manager.py remove <nom_tache> [--force]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Racine canonique pour les worktrees
SCRIPT_DIR = Path(__file__).resolve().parent
MOTEUR_DIR = SCRIPT_DIR.parent
ROOT_DIR = MOTEUR_DIR.parent

# Dossier des worktrees : à côté du dépôt (surcharge : MOTEUR_WORKTREES_ROOT)
CANONICAL_WORKTREES_ROOT = Path(os.environ.get("MOTEUR_WORKTREES_ROOT") or ROOT_DIR / "worktrees")

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def ensure_worktrees_dir():
    CANONICAL_WORKTREES_ROOT.mkdir(parents=True, exist_ok=True)

def create_worktree(task_name: str, base_branch: str = "master"):
    ensure_worktrees_dir()
    target_path = CANONICAL_WORKTREES_ROOT / task_name

    if target_path.exists():
        print(f"{Colors.RED}✖ Le dossier worktree existe déjà : {target_path}{Colors.RESET}")
        sys.exit(1)

    branch_name = f"agent/{task_name}"
    print(f"{Colors.BOLD}{Colors.BLUE}==> Création du bac à sable pour la tâche '{task_name}'...{Colors.RESET}")
    print(f"  - Branche Git : {branch_name}")
    print(f"  - Emplacement : {target_path}")

    # Commande git worktree add
    cmd = ["git", "worktree", "add", "-b", branch_name, str(target_path), base_branch]
    res = subprocess.run(cmd, cwd=MOTEUR_DIR, capture_output=True, text=True)

    if res.returncode == 0:
        print(f"  {Colors.GREEN}✔ Worktree créé avec succès.{Colors.RESET}")
        print(f"\n{Colors.BOLD}Instructions pour l'agent :{Colors.RESET}")
        print(f"  Pour travailler dans ce bac à sable, positionnez le CWD sur :")
        print(f"  {Colors.YELLOW}{target_path}{Colors.RESET}")
        print(f"  Pour valider vos modifications avant merge :")
        print(f"  {Colors.YELLOW}python scripts/verify_workspace.py --all{Colors.RESET}\n")
    else:
        print(f"  {Colors.RED}✖ Erreur Git Worktree :{Colors.RESET}\n{res.stderr or res.stdout}")
        sys.exit(1)

def list_worktrees():
    print(f"{Colors.BOLD}{Colors.BLUE}==> Bacs à sable Git Worktrees actifs :{Colors.RESET}\n")
    res = subprocess.run(["git", "worktree", "list"], cwd=MOTEUR_DIR, capture_output=True, text=True)
    if res.returncode == 0:
        print(res.stdout)
    else:
        print(f"{Colors.RED}✖ Impossible de lister les worktrees : {res.stderr}{Colors.RESET}")

def remove_worktree(task_name: str, force: bool = False):
    target_path = CANONICAL_WORKTREES_ROOT / task_name
    print(f"{Colors.BOLD}{Colors.BLUE}==> Suppression du bac à sable '{task_name}'...{Colors.RESET}")

    cmd = ["git", "worktree", "remove", str(target_path)]
    if force:
        cmd.append("--force")

    res = subprocess.run(cmd, cwd=MOTEUR_DIR, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  {Colors.GREEN}✔ Worktree supprimé avec succès.{Colors.RESET}")
    else:
        print(f"  {Colors.RED}✖ Échec de la suppression : {res.stderr or res.stdout}{Colors.RESET}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Gestionnaire de Bacs à Sable Git Worktrees (Étape 2)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    create_parser = subparsers.add_parser("create", help="Créer un nouveau bac à sable isolé")
    create_parser.add_argument("task_name", help="Nom de la tâche (ex: refacto-router, fix-esphome)")
    create_parser.add_argument("--base", default="master", help="Branche de départ (défaut: master)")

    # list
    subparsers.add_parser("list", help="Lister les bacs à sable actifs")

    # remove
    remove_parser = subparsers.add_parser("remove", help="Supprimer un bac à sable après merge")
    remove_parser.add_argument("task_name", help="Nom de la tâche")
    remove_parser.add_argument("--force", action="store_true", help="Forcer la suppression si modifications non commitées")

    args = parser.parse_args()

    if args.command == "create":
        create_worktree(args.task_name, args.base)
    elif args.command == "list":
        list_worktrees()
    elif args.command == "remove":
        remove_worktree(args.task_name, args.force)

if __name__ == "__main__":
    main()
