"""
tools/doc_generator.py — Générateur automatique de documentation .

Extrait les signatures de fonctions/classes des fichiers Python modifiés
via l'AST et génère des entrées de changelog dans docs/CHANGELOG_AUTO.md.
"""

import ast
import os
import logging
import subprocess
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class DocGenerator:
    """
    Générateur de documentation basé sur l'AST Python.
    
    Fonctionnalités :
    - Extraction des signatures de fonctions et classes
    - Détection des fichiers .py modifiés via Git
    - Génération d'entrées de changelog au format Markdown
    - Mise à jour incrémentale du fichier CHANGELOG_AUTO.md
    """
    
    def __init__(self, changelog_path: Optional[str] = None):
        """
        Initialise le générateur de documentation.
        
        Args:
            changelog_path: Chemin vers le fichier CHANGELOG_AUTO.md.
                           Si None, utilise docs/CHANGELOG_AUTO.md relatif au moteur.
        """
        if changelog_path is None:
            # Chemin par défaut : moteur_agents/docs/CHANGELOG_AUTO.md
            moteur_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.changelog_path = os.path.join(moteur_dir, "docs", "CHANGELOG_AUTO.md")
        else:
            self.changelog_path = changelog_path
    
    def extract_signatures(self, filepath: str) -> List[Dict[str, str]]:
        """
        Parse un fichier Python et retourne les signatures de fonctions/classes.
        
        Args:
            filepath: Chemin absolu vers le fichier Python à analyser.
            
        Returns:
            Liste de dictionnaires {type, name, signature, docstring, line}
        """
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except SyntaxError as se:
            logger.warning(f"[DOC_GEN] Erreur de syntaxe dans '{filepath}' : {se}")
            return []
        except Exception as e:
            logger.warning(f"[DOC_GEN] Impossible de parser '{filepath}' : {e}")
            return []
        
        signatures = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                sig = self._format_function(node, filepath)
                signatures.append(sig)
            elif isinstance(node, ast.ClassDef):
                sig = self._format_class(node, filepath)
                signatures.append(sig)
        
        return signatures
    
    def _format_function(self, node: ast.FunctionDef, filepath: str) -> Dict[str, str]:
        """
        Formate la signature d'une fonction/méthode.
        
        Args:
            node: Nœud AST de la fonction
            filepath: Fichier source
            
        Returns:
            Dictionnaire avec les métadonnées de la fonction
        """
        # Extraction des arguments avec types si disponibles
        args_list = []
        for arg in node.args.args:
            arg_name = arg.arg
            if arg.annotation:
                try:
                    arg_type = ast.unparse(arg.annotation)
                    args_list.append(f"{arg_name}: {arg_type}")
                except Exception:
                    args_list.append(arg_name)
            else:
                args_list.append(arg_name)
        
        # Type de retour
        return_type = ""
        if node.returns:
            try:
                return_type = f" -> {ast.unparse(node.returns)}"
            except Exception:
                return_type = ""
        
        args_str = ", ".join(args_list)
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        signature = f"{prefix}def {node.name}({args_str}){return_type}"
        
        # Docstring (première instruction si c'est une string)
        docstring = ast.get_docstring(node) or ""
        if docstring:
            # Première ligne seulement pour le changelog
            docstring = docstring.split("\n")[0].strip()
        
        return {
            "type": "function",
            "name": node.name,
            "signature": signature,
            "docstring": docstring,
            "line": node.lineno,
            "file": filepath
        }
    
    def _format_class(self, node: ast.ClassDef, filepath: str) -> Dict[str, str]:
        """
        Formate la signature d'une classe.
        
        Args:
            node: Nœud AST de la classe
            filepath: Fichier source
            
        Returns:
            Dictionnaire avec les métadonnées de la classe
        """
        # Bases de la classe
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except Exception:
                bases.append("?")
        
        bases_str = f"({', '.join(bases)})" if bases else ""
        signature = f"class {node.name}{bases_str}"
        
        docstring = ast.get_docstring(node) or ""
        if docstring:
            docstring = docstring.split("\n")[0].strip()
        
        return {
            "type": "class",
            "name": node.name,
            "signature": signature,
            "docstring": docstring,
            "line": node.lineno,
            "file": filepath
        }
    
    def get_modified_python_files(self, repo_path: str = ".") -> List[str]:
        """
        Détecte les fichiers .py modifiés via Git (diff HEAD).
        
        Args:
            repo_path: Racine du dépôt Git.
            
        Returns:
            Liste des chemins absolus des fichiers .py modifiés.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=repo_path,
                encoding='utf-8',
                errors='ignore'
            )
            
            if result.returncode != 0:
                # Fallback : fichiers non commités (staged + unstaged)
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=repo_path,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode != 0:
                    return []
                
                files = []
                for line in result.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2 and parts[1].endswith('.py'):
                        files.append(os.path.abspath(os.path.join(repo_path, parts[1])))
                return files
            
            files = []
            for f in result.stdout.strip().splitlines():
                f = f.strip()
                if f.endswith('.py'):
                    abs_path = os.path.abspath(os.path.join(repo_path, f))
                    if os.path.exists(abs_path):
                        files.append(abs_path)
            return files
            
        except Exception as e:
            logger.warning(f"[DOC_GEN] Erreur lors de la détection des fichiers modifiés : {e}")
            return []
    
    def generate_changelog_entry(self, modified_files: List[str], session_id: str) -> str:
        """
        Génère une entrée de changelog Markdown basée sur les fichiers modifiés.
        
        Args:
            modified_files: Liste des chemins des fichiers Python modifiés.
            session_id: Identifiant de la session.
            
        Returns:
            Texte Markdown de l'entrée de changelog.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        lines = [
            f"## Session `{session_id[:12]}` — {now}",
            "",
            "### Fichiers modifiés",
        ]
        
        all_signatures = []
        
        for filepath in modified_files:
            basename = os.path.basename(filepath)
            rel_path = filepath  # On garde le chemin pour le contexte
            
            # Extraction des signatures AST
            sigs = self.extract_signatures(filepath)
            
            if sigs:
                sig_names = ", ".join(s["name"] for s in sigs[:5])
                if len(sigs) > 5:
                    sig_names += f" (+{len(sigs) - 5})"
                lines.append(f"- `{basename}` : {sig_names}")
                all_signatures.extend(sigs)
            else:
                lines.append(f"- `{basename}` : (pas de signatures extraites)")
        
        # Section des signatures extraites
        if all_signatures:
            lines.append("")
            lines.append("### Signatures extraites")
            
            # Grouper par fichier
            by_file = {}
            for sig in all_signatures:
                fname = os.path.basename(sig["file"])
                if fname not in by_file:
                    by_file[fname] = []
                by_file[fname].append(sig)
            
            for fname, sigs in by_file.items():
                lines.append(f"\n**{fname}**")
                for sig in sigs[:10]:  # Limiter à 10 signatures par fichier
                    icon = "🔷" if sig["type"] == "class" else "🔹"
                    doc_part = f" — *{sig['docstring'][:80]}*" if sig["docstring"] else ""
                    lines.append(f"- {icon} `{sig['signature']}`{doc_part}")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        
        return "\n".join(lines)
    
    def update_docs(self, repo_path: str = ".", session_id: str = ""):
        """
        Point d'entrée principal : détecte les .py modifiés, extrait les signatures AST,
        et appende au fichier CHANGELOG_AUTO.md.
        
        Args:
            repo_path: Racine du dépôt Git.
            session_id: Identifiant de la session courante.
        """
        # 1. Détecter les fichiers Python modifiés
        modified_files = self.get_modified_python_files(repo_path)
        
        if not modified_files:
            logger.info("[DOC_GEN] Aucun fichier Python modifié détecté. Documentation non mise à jour.")
            return
        
        logger.info(f"[DOC_GEN] {len(modified_files)} fichier(s) Python modifié(s) détecté(s). Génération du changelog...")
        
        # 2. Générer l'entrée de changelog
        entry = self.generate_changelog_entry(modified_files, session_id)
        
        # 3. Créer le répertoire docs/ si nécessaire
        docs_dir = os.path.dirname(self.changelog_path)
        os.makedirs(docs_dir, exist_ok=True)
        
        # 4. Lire le contenu existant
        existing_content = ""
        header = "# Changelog Automatique — tab5-engine\n\n"
        header += "> Ce fichier est généré automatiquement par le `DocGenerator` .\n"
        header += "> Il contient les signatures AST des fichiers Python modifiés lors de chaque session.\n\n"
        header += "---\n\n"
        
        if os.path.exists(self.changelog_path):
            with open(self.changelog_path, 'r', encoding='utf-8') as f:
                existing_content = f.read()
            # Séparer le header du contenu existant
            if "---\n\n" in existing_content:
                idx = existing_content.index("---\n\n") + len("---\n\n")
                existing_body = existing_content[idx:]
            else:
                existing_body = existing_content
        else:
            existing_body = ""
        
        # 5. Écrire le fichier mis à jour (nouvelles entrées en haut)
        with open(self.changelog_path, 'w', encoding='utf-8') as f:
            f.write(header)
            f.write(entry)
            if existing_body:
                f.write(existing_body)
        
        logger.info(f"[DOC_GEN] Changelog mis à jour : {self.changelog_path}")
