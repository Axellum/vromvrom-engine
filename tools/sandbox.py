"""
tools/sandbox.py — Module d'isolation Sandbox (Maker-Checker) pour le moteur.

Intercepte les outils destructeurs (write_file, run_terminal_command) et génère
un diff preview au lieu d'exécuter réellement en mode dry_run.
Les écritures sont mises en file d'attente et exécutées seulement après validation.
"""

import os
import logging
import difflib
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class SandboxWrapper:
    """
    Encapsuleur sandbox pour le ToolRegistry.
    
    En mode dry_run :
    - write_file → génère un diff unifié (ancien vs nouveau contenu)
    - run_terminal_command → bloque l'exécution et retourne la commande interceptée
    
    En mode normal : passe-plat transparent vers le ToolRegistry d'origine.
    """
    
    # Outils considérés comme destructeurs (modifient l'état du filesystem)
    DESTRUCTIVE_TOOLS = {"write_file", "run_terminal_command"}
    
    def __init__(self, tool_registry, dry_run: bool = False):
        """
        Initialise le wrapper sandbox.
        
        Args:
            tool_registry: Instance du ToolRegistry original
            dry_run: Si True, intercepte les outils destructeurs
        """
        self.registry = tool_registry
        self.dry_run = dry_run
        # File d'attente des écritures en attente de validation
        self._pending_writes: List[Dict[str, Any]] = []
        # Historique des commandes bloquées
        self._blocked_commands: List[str] = []
        # Compteur de diffs générés pour cette session
        self._diff_count: int = 0
    
    async def execute(self, func_name: str, kwargs: Dict[str, Any]) -> Any:
        """
        Point d'entrée principal. Intercepte les outils destructeurs en mode dry_run.
        
        Args:
            func_name: Nom de la fonction/outil à exécuter
            kwargs: Arguments de l'outil
            
        Returns:
            Résultat de l'outil (ou diff preview en mode dry_run)
        """
        if self.dry_run and func_name in self.DESTRUCTIVE_TOOLS:
            if func_name == "write_file":
                return self._generate_diff_preview(kwargs)
            elif func_name == "run_terminal_command":
                return self._block_command(kwargs)
        
        # Mode normal ou outil non destructeur : exécution directe
        return await self.registry.execute(func_name, kwargs)
    
    def _generate_diff_preview(self, kwargs: Dict[str, Any]) -> str:
        """
        Génère un diff unifié entre le contenu actuel du fichier et le contenu proposé.
        Stocke l'écriture en file d'attente pour exécution ultérieure.
        
        Args:
            kwargs: Arguments de write_file (filepath, content)
            
        Returns:
            Diff unifié formaté en texte
        """
        filepath = kwargs.get("filepath", "")
        new_content = kwargs.get("content", "")
        
        # Lire le contenu actuel du fichier (s'il existe)
        old_content = ""
        old_lines = []
        file_exists = os.path.exists(filepath)
        
        if file_exists:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    old_content = f.read()
                old_lines = old_content.splitlines(keepends=True)
            except Exception as e:
                logger.warning(f"[SANDBOX] Impossible de lire le fichier existant '{filepath}' : {e}")
                old_lines = []
        
        new_lines = new_content.splitlines(keepends=True)
        
        # Génération du diff unifié
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{os.path.basename(filepath)}" if file_exists else "/dev/null",
            tofile=f"b/{os.path.basename(filepath)}",
            lineterm=""
        )
        
        diff_text = "\n".join(diff)
        
        if not diff_text:
            diff_text = "(Aucune modification détectée — contenu identique)"
        
        # Stocker l'écriture en file d'attente
        self._pending_writes.append({
            "filepath": filepath,
            "content": new_content,
            "is_new_file": not file_exists
        })
        self._diff_count += 1
        
        # Statistiques du diff
        added = sum(1 for line in diff_text.splitlines() if line.startswith('+') and not line.startswith('+++'))
        removed = sum(1 for line in diff_text.splitlines() if line.startswith('-') and not line.startswith('---'))
        
        result = (
            f"[SANDBOX — MODE DRY RUN] Diff preview #{self._diff_count}\n"
            f"Fichier : {filepath}\n"
            f"{'[NOUVEAU FICHIER]' if not file_exists else f'[MODIFICATION] +{added} -{removed} lignes'}\n"
            f"---\n"
            f"{diff_text}\n"
            f"---\n"
            f"⚠️ Écriture en attente de validation. Total en file : {len(self._pending_writes)}"
        )
        
        logger.info(f"[SANDBOX] Diff preview généré pour '{filepath}' (+{added}/-{removed})")
        return result
    
    def _block_command(self, kwargs: Dict[str, Any]) -> str:
        """
        Bloque une commande terminal en mode dry_run.
        
        Args:
            kwargs: Arguments de run_terminal_command (command)
            
        Returns:
            Message indiquant que la commande a été bloquée
        """
        command = kwargs.get("command", "")
        self._blocked_commands.append(command)
        
        result = (
            f"[SANDBOX — MODE DRY RUN] Commande terminal bloquée.\n"
            f"Commande interceptée : {command}\n"
            f"⚠️ Cette commande sera exécutée seulement après validation du sandbox.\n"
            f"Total commandes bloquées : {len(self._blocked_commands)}"
        )
        
        logger.info(f"[SANDBOX] Commande bloquée : {command[:100]}")
        return result
    
    async def flush_pending_writes(self) -> str:
        """
        Exécute toutes les écritures en file d'attente après validation.
        
        Returns:
            Rapport d'exécution
        """
        if not self._pending_writes:
            return "Aucune écriture en attente."
        
        results = []
        count = len(self._pending_writes)
        
        for write_args in self._pending_writes:
            try:
                filepath = write_args["filepath"]
                content = write_args["content"]
                
                # Créer les répertoires parents si nécessaire
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                
                # Écriture effective du fichier
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                status = "CRÉÉ" if write_args.get("is_new_file") else "MODIFIÉ"
                results.append(f"✅ [{status}] {filepath}")
                logger.info(f"[SANDBOX] Écriture validée et exécutée : {filepath}")
                
            except Exception as e:
                results.append(f"❌ [ERREUR] {write_args.get('filepath', '?')} : {e}")
                logger.error(f"[SANDBOX] Échec d'écriture : {e}")
        
        self._pending_writes.clear()
        
        report = (
            f"[SANDBOX] Flush terminé : {count} écriture(s) exécutée(s)\n"
            + "\n".join(results)
        )
        return report
    
    async def flush_pending_commands(self) -> str:
        """
        Exécute toutes les commandes bloquées après validation.
        
        Returns:
            Rapport d'exécution
        """
        if not self._blocked_commands:
            return "Aucune commande en attente."
        
        results = []
        count = len(self._blocked_commands)
        
        for command in self._blocked_commands:
            try:
                res = await self.registry.execute("run_terminal_command", {"command": command})
                results.append(f"✅ {command[:80]} → {str(res)[:200]}")
            except Exception as e:
                results.append(f"❌ {command[:80]} → Erreur : {e}")
        
        self._blocked_commands.clear()
        
        report = (
            f"[SANDBOX] Flush commandes terminé : {count} commande(s) exécutée(s)\n"
            + "\n".join(results)
        )
        return report
    
    def get_pending_summary(self) -> Dict[str, Any]:
        """
        Retourne un résumé de l'état du sandbox.
        
        Returns:
            Dictionnaire avec les compteurs de pending
        """
        return {
            "dry_run": self.dry_run,
            "pending_writes": len(self._pending_writes),
            "blocked_commands": len(self._blocked_commands),
            "total_diffs_generated": self._diff_count,
            "files_pending": [w.get("filepath", "?") for w in self._pending_writes]
        }
    
    # Méthodes proxy vers le ToolRegistry original (pour compatibilité)
    def get_all_schemas(self, *args, **kwargs):
        """Proxy transparent vers ToolRegistry.get_all_schemas()."""
        return self.registry.get_all_schemas(*args, **kwargs)
    
    def get_tool_names(self, *args, **kwargs):
        """Proxy transparent vers ToolRegistry.get_tool_names()."""
        return self.registry.get_tool_names(*args, **kwargs)
