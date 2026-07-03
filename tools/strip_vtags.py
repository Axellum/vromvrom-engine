"""
Outil de nettoyage des tags de version [Vxx] dans le code source.

Règles :
  - "# [V12] Mon commentaire"          → "# Mon commentaire"
  - triple-quote [V5.5 A12] Docstring   -> Docstring
  - "# [V12]" (tag seul, sans texte)   → ligne supprimée
  - "[V9 AR-1] Texte dans docstring"   → "Texte dans docstring"

Usage :
  python tools/strip_vtags.py [--dry-run] [--path moteur_agents/]
"""

import re
import sys
import os
import argparse
from pathlib import Path

# Correspond à [V<chiffres><séparateur><alphanum optionnel>]
# Exemples : [V12], [V5.5], [V9 AR-1], [V11.2], [V6 Acte 4], [FIX C1], [FIX C3]
# Note : on capture aussi [FIX Cxx] présents dans le code
VTAG_PATTERN = re.compile(
    r'\[(?:V\d+(?:\.\d+)?(?:\s+\S+)*|FIX\s+\S+)\]\s*',
    re.IGNORECASE
)


def strip_vtags_in_content(content: str) -> tuple[str, int]:
    """
    Nettoie les tags [Vxx] dans un contenu de fichier.
    Retourne (nouveau_contenu, nombre_de_modifications).
    """
    lines = content.split('\n')
    new_lines = []
    count = 0

    for line in lines:
        if not VTAG_PATTERN.search(line):
            new_lines.append(line)
            continue

        # Compter une modification par ligne touchée
        count += 1

        # Cas 1 : commentaire Python/JS inline "    # [Vxx] texte"
        # → strip le tag, garder "    # texte"
        inline_comment = re.match(r'^(\s*#\s*)\[(?:V\d+(?:\.\d+)?(?:\s+\S+)*|FIX\s+\S+)\]\s*(.*)', line, re.IGNORECASE)
        if inline_comment:
            prefix = inline_comment.group(1)  # "    # "
            rest = inline_comment.group(2).strip()
            if rest:
                new_lines.append(f"{prefix}{rest}")
            else:
                # Tag seul sur la ligne → supprimer la ligne
                pass  # ne pas ajouter
            continue

        # Cas 2 : dans une docstring ou en début de ligne (pas de #)
        # "    [V5.5 A12] Texte..." → "    Texte..."
        cleaned = VTAG_PATTERN.sub('', line).rstrip()
        if cleaned.strip():
            new_lines.append(cleaned)
        else:
            # Ligne entièrement vide après nettoyage → supprimer
            pass

    return '\n'.join(new_lines), count


def process_file(path: Path, dry_run: bool = False) -> int:
    """Traite un fichier. Retourne le nombre de tags supprimés."""
    try:
        original = path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        print(f"  ERREUR lecture {path}: {e}")
        return 0

    new_content, count = strip_vtags_in_content(original)

    if count == 0:
        return 0

    if new_content == original:
        return 0

    if not dry_run:
        path.write_text(new_content, encoding='utf-8')
        print(f"  ✓ {path.relative_to(path.parent.parent.parent) if path.parent.parent.parent.exists() else path}  ({count} tags)")
    else:
        print(f"  [DRY] {path}  ({count} tags à supprimer)")

    return count


def main():
    parser = argparse.ArgumentParser(description="Supprime les tags [Vxx] du code source.")
    parser.add_argument('--dry-run', action='store_true', help="Afficher sans modifier")
    parser.add_argument('--path', default='.', help="Répertoire racine (défaut: répertoire courant)")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    extensions = {'.py', '.js', '.ts', '.tsx'}
    excludes = {'.venv', 'node_modules', '__pycache__', '.git', 'dist', 'build', '.esphome', 'backups_prod'}
    # Ne pas se nettoyer soi-même (les exemples dans le docstring seraient supprimés)
    self_name = Path(__file__).name

    total_files = 0
    total_tags = 0

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Nettoyage des [Vxx] dans {root}")
    print("=" * 60)

    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix not in extensions:
            continue
        if path.name == self_name:
            continue
        if any(excl in path.parts for excl in excludes):
            continue

        count = process_file(path, dry_run=args.dry_run)
        if count > 0:
            total_files += 1
            total_tags += count

    print("=" * 60)
    print(f"Total : {total_tags} tags dans {total_files} fichiers {'(simulation)' if args.dry_run else 'nettoyés'}")


if __name__ == '__main__':
    main()
