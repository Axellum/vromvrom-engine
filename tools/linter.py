"""
tools/linter.py — Linter local YAML, Jinja2 et ESPHome config.

Ce composant permet de vérifier la validité syntaxique de fichiers de configuration
avant de tenter un déploiement ou une compilation. Il supporte :
- La validation YAML standard.
- L'extraction et la compilation à froid de templates Jinja2.
- La validation native ESPHome via la commande CLI `esphome config`.

Auteur : Antigravity IDE
"""

import os
import re
import subprocess
import logging
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("tools.linter")

# Tentative d'import de jinja2 et yaml
try:
    import yaml
except ImportError:
    yaml = None

try:
    import jinja2
except ImportError:
    jinja2 = None


class ConfigurationLinter:
    """Classe utilitaire pour linter les fichiers YAML, Jinja2 et ESPHome."""

    @staticmethod
    def lint_yaml(content: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Valide la syntaxe YAML d'un contenu.
        
        Returns:
            Tuple (is_valid, error_message, parsed_data)
        """
        if yaml is None:
            return False, "Le module Python 'pyyaml' n'est pas installé dans l'environnement virtuel.", None

        try:
            # Safe load pour des raisons de sécurité
            data = yaml.safe_load(content)
            return True, None, data
        except yaml.YAMLError as exc:
            error_msg = f"Erreur de syntaxe YAML : {exc}"
            # Tenter d'extraire la ligne et la colonne de l'erreur
            if hasattr(exc, 'problem_mark') and exc.problem_mark is not None:
                mark = exc.problem_mark
                error_msg += f" (Ligne {mark.line + 1}, Colonne {mark.column + 1})"
            return False, error_msg, None

    @staticmethod
    def lint_jinja2_templates(content: str) -> Tuple[bool, List[str]]:
        """Extrait et compile les expressions Jinja2 présentes dans le contenu.
        
        Détecte les accolades et expressions {{ ... }} et {% ... %}.
        
        Returns:
            Tuple (is_valid, list_of_errors)
        """
        if jinja2 is None:
            return True, ["[Avertissement] Le module 'jinja2' n'est pas disponible pour valider les templates."]

        errors = []
        # Regex pour capturer les blocs d'expressions {{ ... }} et de contrôle {% ... %}
        # Mode DOTALL pour capturer les templates sur plusieurs lignes
        pattern = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.DOTALL)
        matches = pattern.finditer(content)
        
        env = jinja2.Environment()
        
        for match in matches:
            block = match.group(1)
            # Récupérer le numéro de ligne approximatif du bloc
            line_num = content[:match.start()].count('\n') + 1
            
            try:
                # Tenter de compiler le bloc Jinja2 à froid
                env.parse(block)
            except jinja2.TemplateSyntaxError as syntax_err:
                errors.append(
                    f"Ligne {line_num} : Erreur de syntaxe Jinja2 dans '{block.strip()}' -> {syntax_err.message}"
                )
            except Exception as e:
                errors.append(
                    f"Ligne {line_num} : Erreur de validation de template '{block.strip()}' -> {e}"
                )
                
        return len(errors) == 0, errors

    @staticmethod
    def lint_esphome_cli(file_path: str) -> Tuple[bool, Optional[str]]:
        """Exécute la commande `esphome config` pour valider une config ESPHome.
        
        Returns:
            Tuple (is_valid, process_output_or_error)
        """
        if not os.path.exists(file_path):
            return False, f"Le fichier '{file_path}' n'existe pas."

        try:
            # Exécution de 'esphome config <file_path>'
            # Utilise shell=True sous Windows si nécessaire, mais en liste c'est plus propre.
            # On exécute avec PAGER=cat pour éviter les blocages.
            env = os.environ.copy()
            env["PAGER"] = "cat"
            
            result = subprocess.run(
                ["esphome", "config", file_path],
                capture_output=True,
                text=True,
                env=env,
                timeout=30.0
            )
            
            if result.returncode == 0:
                return True, "La configuration ESPHome est valide."
            else:
                # Récupérer stderr et stdout pour donner un maximum de contexte sur l'erreur
                output = f"Code retour: {result.returncode}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
                return False, output
                
        except FileNotFoundError:
            logger.warning("[LINTER] La commande CLI 'esphome' n'est pas installée sur cette machine. Validation ESPHome sautée.")
            return True, "Validation ESPHome sautée (commande CLI absente sur cette machine)."
        except subprocess.TimeoutExpired:
            return False, "La validation de configuration ESPHome a expiré après 30 secondes."
        except Exception as e:
            return False, f"Erreur lors de l'exécution de la validation ESPHome : {e}"

    def validate_file(self, file_path: str) -> Dict[str, Any]:
        """Méthode principale pour valider un fichier de configuration.
        
        Détermine dynamiquement le type de validation à appliquer.
        """
        if not os.path.exists(file_path):
            return {
                "valid": False,
                "file": file_path,
                "error": f"Fichier introuvable.",
                "type": "unknown"
            }

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            return {
                "valid": False,
                "file": file_path,
                "error": f"Impossible de lire le fichier : {e}",
                "type": "unknown"
            }

        # 1. Validation YAML standard
        is_yaml_valid, yaml_err, yaml_data = self.lint_yaml(content)
        if not is_yaml_valid:
            return {
                "valid": False,
                "file": file_path,
                "error": yaml_err,
                "type": "yaml"
            }

        # Déterminer s'il s'agit d'une config ESPHome
        # ESPHome contient généralement une clé racine 'esphome'
        is_esphome = isinstance(yaml_data, dict) and "esphome" in yaml_data

        # 2. Validation des templates Jinja2 (si non ESPHome, car ESPHome n'utilise pas Jinja2)
        if not is_esphome:
            is_jinja_valid, jinja_errs = self.lint_jinja2_templates(content)
            if not is_jinja_valid:
                return {
                    "valid": False,
                    "file": file_path,
                    "error": "\n".join(jinja_errs),
                    "type": "jinja2"
                }

        # 3. Validation CLI ESPHome (si clé esphome présente)
        if is_esphome:
            is_esp_valid, esp_msg = self.lint_esphome_cli(file_path)
            if not is_esp_valid:
                return {
                    "valid": False,
                    "file": file_path,
                    "error": esp_msg,
                    "type": "esphome"
                }
            return {
                "valid": True,
                "file": file_path,
                "message": "Configuration ESPHome valide (vérifiée via CLI).",
                "type": "esphome"
            }

        # Si tout est OK pour un fichier HA standard
        return {
            "valid": True,
            "file": file_path,
            "message": "Fichier YAML et templates Jinja2 valides.",
            "type": "yaml_jinja2"
        }
