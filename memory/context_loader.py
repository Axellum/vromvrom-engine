"""
context_loader.py — Chargeur de contexte structuré pour le tab5-engine.

Indexe les fichiers Markdown de l'architecture 3-Layers (contexte_ia/) dans un 
dictionnaire en mémoire catégorisé, permettant au Router d'injecter le bon 
contexte selon la nature de la tâche.

Auteur : Antigravity IDE + Axel
Dernière mise à jour : 2026-05-25
"""

import os
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Mapping : catégorie de tâche → fichiers de contexte à charger
# ──────────────────────────────────────────────────────────────────
CATEGORY_FILES_MAP: Dict[str, List[str]] = {
    "home_assistant": [
        "03_Software/rules_home_assistant.md",
        "03_Software/03_LOGIQUE_ET_APIS.md",
        "03_Software/01_SERVEUR_HA.md",
    ],
    "esphome": [
        "02_Hardware/rules_esphome.md",
        "02_Hardware/02_MATERIEL_ET_ECRANS.md",
        "02_Hardware/lecons_esphome_hardware.md",
        "03_Software/03_LOGIQUE_ET_APIS.md",
    ],
    "moteur": [
        "03_Software/rules_moteur_agents.md",
        "03_Software/05_MOTEUR_AGENTS_PYTHON.md",
        "03_Software/lecons_moteur_agents.md",
        # disponibilite_infos_limitations_tarifs.md retiré du contexte LLM
        # Les données de routing/tarifs sont maintenant dans models_registry.db
        # et lues côté Python — économie de ~15K tokens/appel
    ],
    "analysis": [
        "03_Software/lecons_gcp_apis.md",
        # Tarifs retirés — consultés via models_registry.db
    ],
    "code_generation": [
        "03_Software/lecons_moteur_agents.md",
    ],
    "hardware": [
        "02_Hardware/02_MATERIEL_ET_ECRANS.md",
        "01_Core/hardware_pc_ia_locale.md",
        "02_Hardware/lecons_esphome_hardware.md",
    ],
    "hmi": [
        "03_Software/lecons_hmi.md",
    ],
    # Contexte minimal chargé dans tous les cas (profil utilisateur + FIN DE SESSION)
    "core": [
        "01_Core/rules_global.md",
    ],
}

# Taille maximale de contexte injectable (en caractères) pour ne pas saturer le prompt
MAX_CONTEXT_CHARS = 12000


@dataclass
class LoadedDocument:
    """Représente un fichier de contexte chargé en mémoire."""
    relative_path: str
    full_path: str
    content: str
    size_bytes: int
    last_modified: float  # timestamp
    category_tags: List[str] = field(default_factory=list)


class ContextLoader:
    """
    Charge et indexe les fichiers Markdown de contexte_ia/ au démarrage.
    Expose une méthode get_context_for_categories() qui retourne le contenu
    pertinent pour une liste de catégories de tâches.
    """

    def __init__(self, contexte_ia_path: Optional[str] = None):
        """
        Initialise le ContextLoader.
        
        Args:
            contexte_ia_path: Chemin absolu vers le dossier contexte_ia/.
                              Par défaut, résolu relativement à moteur_agents/../contexte_ia/
        """
        if contexte_ia_path is None:
            # Résolution automatique : moteur_agents/../contexte_ia/
            base_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "contexte_ia")
            )
            self.contexte_ia_path = base_dir
        else:
            self.contexte_ia_path = os.path.abspath(contexte_ia_path)

        # Cache en mémoire : relative_path → LoadedDocument
        self._documents: Dict[str, LoadedDocument] = {}
        self._loaded = False

    def load_all(self) -> int:
        """
        Charge tous les fichiers référencés dans CATEGORY_FILES_MAP en mémoire.
        
        Returns:
            Le nombre de fichiers chargés avec succès.
        """
        self._documents.clear()
        loaded_count = 0

        # Collecter tous les fichiers uniques depuis le mapping
        all_files: Dict[str, List[str]] = {}  # relative_path → [categories]
        for category, files in CATEGORY_FILES_MAP.items():
            for rel_path in files:
                if rel_path not in all_files:
                    all_files[rel_path] = []
                all_files[rel_path].append(category)

        # Charger chaque fichier unique
        for rel_path, categories in all_files.items():
            full_path = os.path.join(self.contexte_ia_path, rel_path)
            
            if not os.path.exists(full_path):
                logger.warning(f"[CONTEXT LOADER] Fichier introuvable : {full_path}")
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()

                stat = os.stat(full_path)
                doc = LoadedDocument(
                    relative_path=rel_path,
                    full_path=full_path,
                    content=content,
                    size_bytes=stat.st_size,
                    last_modified=stat.st_mtime,
                    category_tags=categories,
                )
                self._documents[rel_path] = doc
                loaded_count += 1
                logger.info(
                    f"[CONTEXT LOADER] ✅ {rel_path} ({stat.st_size:,} octets, "
                    f"catégories: {', '.join(categories)})"
                )

            except Exception as e:
                logger.error(f"[CONTEXT LOADER] ❌ Erreur lecture {rel_path}: {e}")

        self._loaded = True
        logger.info(
            f"[CONTEXT LOADER] Chargement terminé : {loaded_count}/{len(all_files)} "
            f"fichiers indexés, {sum(d.size_bytes for d in self._documents.values()):,} octets total."
        )
        return loaded_count

    def get_context_for_categories(
        self, categories: List[str], max_chars: int = MAX_CONTEXT_CHARS
    ) -> str:
        """
        Retourne le contenu concaténé des fichiers pertinents pour les catégories données.
        
        Le contexte "core" (rules_global.md) est toujours inclus.
        Si le contenu total dépasse max_chars, les fichiers sont tronqués 
        proportionnellement.
        
        Args:
            categories: Liste de catégories (ex: ["home_assistant", "esphome"])
            max_chars: Taille maximale du contexte retourné
            
        Returns:
            Contenu Markdown concaténé avec des séparateurs de fichier.
        """
        if not self._loaded:
            self.load_all()

        # Toujours inclure "core"
        all_categories = set(categories) | {"core"}

        # Collecter les fichiers pertinents (sans doublons)
        relevant_files: Dict[str, LoadedDocument] = {}
        for cat in all_categories:
            file_list = CATEGORY_FILES_MAP.get(cat, [])
            for rel_path in file_list:
                if rel_path in self._documents:
                    relevant_files[rel_path] = self._documents[rel_path]

        if not relevant_files:
            return ""

        # Construire le contexte avec séparateurs
        sections = []
        total_chars = 0
        for rel_path, doc in relevant_files.items():
            header = f"\n--- 📄 {rel_path} ---\n"
            section = header + doc.content
            
            if total_chars + len(section) > max_chars:
                # Tronquer ce fichier pour rentrer dans la limite
                remaining = max_chars - total_chars
                if remaining > 200:  # Minimum utile
                    section = section[:remaining] + "\n[... tronqué ...]\n"
                    sections.append(section)
                break
            
            sections.append(section)
            total_chars += len(section)

        return "\n".join(sections)

    def get_status(self) -> Dict:
        """
        Retourne un résumé enrichi de l'état du ContextLoader pour l'API /api/context-status.
        Inclut le mapping des catégories, les previews et la limite de contexte.
        """
        import time
        return {
            "loaded": self._loaded,
            "base_path": self.contexte_ia_path,
            "documents_count": len(self._documents),
            "total_size_bytes": sum(d.size_bytes for d in self._documents.values()),
            "max_context_chars": MAX_CONTEXT_CHARS,
            "categories_map": {
                cat: files for cat, files in CATEGORY_FILES_MAP.items()
            },
            "documents": [
                {
                    "path": doc.relative_path,
                    "size_bytes": doc.size_bytes,
                    "categories": doc.category_tags,
                    "last_modified": doc.last_modified,
                    "preview": self._get_preview(doc.content),
                }
                for doc in self._documents.values()
            ],
            "timestamp": time.time(),
        }

    def _get_preview(self, content: str, max_lines: int = 5) -> str:
        """
        Extrait les premières lignes significatives du contenu Markdown
        (ignore les lignes vides et les séparateurs).
        """
        lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and stripped != "---":
                lines.append(stripped)
            if len(lines) >= max_lines:
                break
        return "\n".join(lines)

    def force_reload(self) -> Dict:
        """
        Force un rechargement complet de tous les fichiers de contexte.
        Retourne le statut mis à jour.
        """
        logger.info("[CONTEXT LOADER] Rechargement forcé demandé depuis le HMI.")
        self.load_all()
        return self.get_status()

    def reload_if_stale(self, max_age_seconds: int = 300) -> bool:
        """
        Recharge les fichiers si l'un d'entre eux a été modifié depuis le dernier chargement.
        
        Args:
            max_age_seconds: Non utilisé pour l'instant (vérification par mtime)
            
        Returns:
            True si un rechargement a été effectué.
        """
        if not self._loaded:
            self.load_all()
            return True

        for doc in self._documents.values():
            try:
                current_mtime = os.stat(doc.full_path).st_mtime
                if current_mtime > doc.last_modified:
                    logger.info(
                        f"[CONTEXT LOADER] Modification détectée sur {doc.relative_path}, rechargement complet..."
                    )
                    self.load_all()
                    return True
            except FileNotFoundError:
                logger.warning(f"[CONTEXT LOADER] Fichier disparu : {doc.relative_path}")
                continue

        return False

    def get_total_expected_files(self) -> int:
        """Retourne le nombre total de fichiers uniques attendus dans CATEGORY_FILES_MAP."""
        all_files = set()
        for files in CATEGORY_FILES_MAP.values():
            all_files.update(files)
        return len(all_files)
