import argparse
import re
import subprocess
import sys


def lister_stashs(repo_path, tous=False):
    """
    Liste les stashs d'un dépôt git avec leurs détails (index, branche, message, fichiers).
    """
    # Récupération de la liste des stashs
    cmd = ["git", "-C", repo_path, "stash", "list", "--format=%gd: %gs"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    stashs = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue

        parts = line.split(': ', 1)
        if len(parts) < 2:
            continue

        index, message = parts

        if not tous and "user_pre_agent" not in message:
            continue

        # Extraction de la branche
        branche_match = re.search(r"(?:WIP on|On) ([^:]+)", message)
        branche = branche_match.group(1) if branche_match else "inconnue"

        # Fichiers suivis
        files_cmd = ["git", "-C", repo_path, "stash", "show", "--name-only", index]
        files_res = subprocess.run(files_cmd, capture_output=True, text=True, check=True)
        fichiers_suivis = [f for f in files_res.stdout.strip().split('\n') if f]

        # Fichiers non suivis (untracked)
        # Note: git stash show -u <index> ne liste pas toujours les untracked correctement
        # On utilise git ls-tree -r <index>^3 pour lister les fichiers du 3ème parent
        fichiers_non_suivis = []
        check_untracked = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--verify", f"{index}^3"],
            capture_output=True, text=True
        )
        if check_untracked.returncode == 0:
            # Utilisation de ls-tree pour lister les fichiers du commit d'untracked
            untracked_cmd = ["git", "-C", repo_path, "ls-tree", "-r", "--name-only", f"{index}^3"]
            untracked_res = subprocess.run(untracked_cmd, capture_output=True, text=True, check=True)
            fichiers_non_suivis = [f for f in untracked_res.stdout.strip().split('\n') if f]

        stashs.append({
            "index": index,
            "branche": branche,
            "message": message,
            "fichiers_suivis": fichiers_suivis,
            "fichiers_non_suivis": fichiers_non_suivis
        })

    return stashs

def main():
    parser = argparse.ArgumentParser(description="Inventaire des stashs d'agents.")
    parser.add_argument("--repo", default=".", help="Chemin du dépôt git")
    parser.add_argument("--tous", action="store_true", help="Ne pas filtrer sur user_pre_agent")
    args = parser.parse_args()

    try:
        stashs = lister_stashs(args.repo, args.tous)

        if not stashs:
            print("Aucun stash trouvé.")
            return

        for s in stashs:
            print(f"--- {s['index']} ---")
            print(f"Branche : {s['branche']}")
            print(f"Message : {s['message']}")
            print("Fichiers suivis :")
            for f in s['fichiers_suivis']:
                print(f"  - {f}")
            print("Fichiers non suivis :")
            for f in s['fichiers_non_suivis']:
                print(f"  - {f}")
            print()

    except subprocess.CalledProcessError as e:
        print(f"Erreur lors de l'exécution de git : {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
