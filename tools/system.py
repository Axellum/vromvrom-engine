import os

def read_file(filepath: str) -> str:
    """Lit le contenu d'un fichier depuis le disque local."""
    if not os.path.exists(filepath):
        return f"Erreur: Le fichier {filepath} n'existe pas."
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Intégration du cache sémantique ContextManager s'il est initialisé
        from memory.context_manager import ContextManager
        manager = ContextManager.get_instance()
        if manager:
            content = manager.optimize_file_read(filepath, content)
            
        return content
    except Exception as e:
        return f"Erreur de lecture: {e}"

def write_file(filepath: str, content: str) -> str:
    """Écrit du texte dans un fichier (crée les dossiers parents si nécessaire)."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Succès: Fichier {filepath} créé/modifié."
    except Exception as e:
        return f"Erreur d'écriture: {e}"

def validate_config_yaml(file_path: str) -> str:
    """Valide un fichier de configuration YAML à l'aide du linter ESPHome."""
    import subprocess
    import re
    if not file_path.endswith(('.yaml', '.yml')):
        return f"Erreur: {file_path} n'est pas un fichier YAML."
    if not os.path.exists(file_path):
        return f"Erreur: Le fichier {file_path} n'existe pas."
    
    # Résoudre le chemin de l'exécutable esphome dans le .venv
    # e:\AuxFilsDesIdees\moteur_agents\tools\system.py -> le dossier parent de moteur_agents est e:\AuxFilsDesIdees
    # et .venv est à e:\AuxFilsDesIdees\.venv
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    is_windows = (os.name == 'nt')
    if is_windows:
        esphome_path = os.path.join(project_root, ".venv", "Scripts", "esphome.exe")
    else:
        esphome_path = os.path.join(project_root, ".venv", "bin", "esphome")
    
    if not os.path.exists(esphome_path):
        # Essayer esphome dans le PATH par défaut si .venv est manquant
        esphome_path = "esphome"
        
    try:
        # Exécuter la commande: esphome config <file_path>
        result = subprocess.run(
            [esphome_path, "config", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode == 0:
            return f"Succès: Le fichier {file_path} est valide."
        else:
            # Nettoyer d'éventuels codes d'échappement ANSI de la console esphome
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            clean_stdout = ansi_escape.sub('', result.stdout)
            clean_stderr = ansi_escape.sub('', result.stderr)
            return f"Erreur de validation pour {file_path} :\nSTDOUT:\n{clean_stdout}\nSTDERR:\n{clean_stderr}"
    except FileNotFoundError:
        return f"Succès: Validation ESPHome sautée pour {file_path} (commande esphome absente sur cette machine)."
    except Exception as e:
        return f"Erreur lors de l'exécution de la validation ESPHome : {e}"

