"""
context_self_healing.py — Module de validation automatique de la cohérence du contexte.

Détecte les incohérences entre les fichiers Markdown de contexte (contexte_ia/)
et le code réel du projet (moteur_agents/). Ce module est appelé de manière non-bloquante
dans le cycle de consolidation mémoire de l'Engine.

Types de validations :
1. Fichiers Python référencés dans les docs -> vérification d'existence
2. Adresses IP mentionnées dans les docs -> cohérence avec YAML ESPHome et workers.json
3. Fichiers de contexte obsolètes -> détection par date de modification + modules supprimés

Usage standalone : python -m tools.context_self_healing
"""

import os
import re
import time
import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class ContextSelfHealer:
    """
    Détecte les incohérences entre les fichiers Markdown de contexte
    et le code réel du projet.

    Conçu pour être léger et non-bloquant : pas d'appel LLM,
    uniquement du parsing filesystem et regex.
    """

    # Chemin racine du contexte IA (relatif au moteur_agents)
    CONTEXTE_IA_PATH = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "contexte_ia")
    )

    # Chemin racine du tab5-engine
    tab5_engine_PATH = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )

    # Chemin des configs ESPHome
    ESPHOME_PATH = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ServeurHA", "config", "esphome")
    )

    # Seuil d'obsolescence en jours
    STALE_THRESHOLD_DAYS = 30

    def __init__(self):
        """Initialise le self-healer avec les chemins de référence."""
        self._diagnostics: List[Dict[str, Any]] = []

    def validate_all(self) -> List[Dict[str, Any]]:
        """
        Exécute toutes les validations configurées et retourne les diagnostics.

        Returns:
            Liste de dicts avec les clés :
            - level: 'warning' | 'error' | 'info'
            - type: type de validation
            - message: description du problème
            - file: fichier concerné (optionnel)
            - suggestion: correction suggérée (optionnel)
        """
        self._diagnostics = []

        # Validation 1 : Fichiers Python référencés dans les docs
        self._validate_python_references()

        # Validation 2 : Adresses IP mentionnées dans les docs
        self._validate_ip_addresses()

        # Validation 3 : Fichiers de contexte obsolètes
        self._validate_stale_context_files()

        return self._diagnostics

    def _validate_python_references(self):
        """
        Vérifie que les fichiers Python mentionnés dans 05_MOTEUR_AGENTS_PYTHON.md
        et rules_moteur_agents.md existent réellement dans le moteur.

        Regex utilisé : détecte les patterns comme core/engine.py, memory/rag.py,
        tools/context_self_healing.py, etc.
        """
        doc_files = [
            os.path.join(self.CONTEXTE_IA_PATH, "03_Software", "05_MOTEUR_AGENTS_PYTHON.md"),
            os.path.join(self.CONTEXTE_IA_PATH, "03_Software", "rules_moteur_agents.md"),
        ]

        # Pattern pour détecter les chemins Python relatifs au moteur
        # Ex: core/engine.py, memory/rag.py, tools/xyz.py, tests/test_*.py
        py_path_pattern = re.compile(
            r'(?:^|[\s`\(])([a-z_]+/[a-z_]+\.py)(?:[\s`\)\.,;:]|$)',
            re.MULTILINE
        )

        for doc_file in doc_files:
            if not os.path.exists(doc_file):
                continue

            try:
                with open(doc_file, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue

            # Trouver tous les chemins Python référencés
            matches = py_path_pattern.findall(content)
            unique_paths = set(matches)

            for rel_path in unique_paths:
                full_path = os.path.join(self.tab5_engine_PATH, rel_path)
                if not os.path.exists(full_path):
                    self._diagnostics.append({
                        "level": "warning",
                        "type": "missing_python_file",
                        "message": (
                            f"Le fichier '{rel_path}' est référencé dans "
                            f"'{os.path.basename(doc_file)}' mais n'existe pas "
                            f"dans le tab5-engine."
                        ),
                        "file": doc_file,
                        "suggestion": (
                            f"Mettre à jour la documentation pour retirer "
                            f"la référence à '{rel_path}' ou créer le fichier."
                        ),
                    })

    def _validate_ip_addresses(self):
        """
        Extrait les adresses IP privées (192.168.x.x) des fichiers de contexte
        et vérifie la cohérence avec les YAML ESPHome et workers.json.
        """
        # Collecter les IPs depuis les fichiers ESPHome YAML
        esphome_ips = set()
        if os.path.exists(self.ESPHOME_PATH):
            for fname in os.listdir(self.ESPHOME_PATH):
                if fname.endswith('.yaml') or fname.endswith('.yml'):
                    fpath = os.path.join(self.ESPHOME_PATH, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            yaml_content = f.read()
                        ips = re.findall(r'192\.168\.\d+\.\d+', yaml_content)
                        esphome_ips.update(ips)
                    except Exception:
                        continue

        # Collecter les IPs depuis workers.json
        workers_path = os.path.join(self.tab5_engine_PATH, "workers.json")
        workers_ips = set()
        if os.path.exists(workers_path):
            try:
                with open(workers_path, 'r', encoding='utf-8') as f:
                    workers_data = json.load(f)
                # Extraire les IPs des workers (format variable)
                workers_str = json.dumps(workers_data)
                ips = re.findall(r'192\.168\.\d+\.\d+', workers_str)
                workers_ips.update(ips)
            except Exception:
                pass

        # Ajouter les IPs de l'infrastructure (DHCP statiques / PC / HA) pour éviter les faux positifs
        known_infra_ips = {
            "${LMSTUDIO_HOST:-localhost}",  # Host PC de dev (LM Studio)
            "192.168.1.100", # PC Ethernet primaire
            "192.168.1.101", # PC Ethernet secondaire
            "${TAB5_HOST:-192.168.1.x}",  # M5Stack Tab5 V2 (DHCP lease)
            "192.168.1.102",  # MicHA AtomS3R (DHCP lease)
            "192.168.1.10",  # HA VM Local IP (NGINX proxy)
            "${HA_HOST:-192.168.1.x}",  # VM Freebox (Worker Sentinelle)
            "192.168.1.254", # Passerelle Freebox Delta
        }
        all_known_ips = esphome_ips | workers_ips | known_infra_ips

        # Scanner les fichiers de contexte pour trouver les IPs mentionnées
        context_dirs = ["01_Core", "02_Hardware", "03_Software"]
        for subdir in context_dirs:
            dir_path = os.path.join(self.CONTEXTE_IA_PATH, subdir)
            if not os.path.exists(dir_path):
                continue

            for fname in os.listdir(dir_path):
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(dir_path, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                except Exception:
                    continue

                doc_ips = set(re.findall(r'192\.168\.\d+\.\d+', content))

                # Vérifier que chaque IP documentée existe bien dans la config réelle
                for ip in doc_ips:
                    if all_known_ips and ip not in all_known_ips:
                        self._diagnostics.append({
                            "level": "warning",
                            "type": "ip_mismatch",
                            "message": (
                                f"L'adresse IP {ip} est documentée dans "
                                f"'{fname}' mais n'est pas trouvée dans "
                                f"les fichiers YAML ESPHome ni dans workers.json."
                            ),
                            "file": fpath,
                            "suggestion": (
                                f"Vérifier que l'IP {ip} est toujours valide "
                                f"ou mettre à jour la documentation."
                            ),
                        })

    def _validate_stale_context_files(self):
        """
        Détecte les fichiers de contexte Markdown qui n'ont pas été modifiés
        depuis plus de STALE_THRESHOLD_DAYS jours ET qui contiennent des
        références à des modules Python supprimés.
        """
        now = time.time()

        # Pattern pour détecter les références à des modules Python
        py_ref_pattern = re.compile(r'`([a-z_]+/[a-z_]+\.py)`')

        context_dirs = ["01_Core", "02_Hardware", "03_Software"]
        for subdir in context_dirs:
            dir_path = os.path.join(self.CONTEXTE_IA_PATH, subdir)
            if not os.path.exists(dir_path):
                continue

            for fname in os.listdir(dir_path):
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(dir_path, fname)

                try:
                    mtime = os.path.getmtime(fpath)
                    age_days = (now - mtime) / 86400

                    if age_days < self.STALE_THRESHOLD_DAYS:
                        continue  # Le fichier a été modifié récemment

                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Chercher des références à des modules Python
                    refs = py_ref_pattern.findall(content)
                    missing_refs = []
                    for ref in refs:
                        full_ref = os.path.join(self.tab5_engine_PATH, ref)
                        if not os.path.exists(full_ref):
                            missing_refs.append(ref)

                    if missing_refs:
                        self._diagnostics.append({
                            "level": "warning",
                            "type": "stale_context_file",
                            "message": (
                                f"Le fichier '{fname}' n'a pas été modifié depuis "
                                f"{int(age_days)} jours et contient des références "
                                f"à des modules supprimés : {missing_refs}"
                            ),
                            "file": fpath,
                            "suggestion": (
                                f"Mettre à jour ou archiver '{fname}' — "
                                f"les modules {missing_refs} n'existent plus."
                            ),
                        })
                except Exception:
                    continue

    def run_and_report(self) -> str:
        """
        Exécute toutes les validations et retourne un résumé formaté en Markdown.

        Returns:
            Rapport Markdown des diagnostics trouvés.
        """
        diagnostics = self.validate_all()

        if not diagnostics:
            return "Self-Healing Contexte : Aucune incohérence détectée."

        # Construire le rapport Markdown
        lines = [
            "## Rapport Self-Healing du Contexte",
            "",
            f"**{len(diagnostics)} incohérence(s) détectée(s)**",
            "",
        ]

        # Grouper par type
        by_type = {}
        for d in diagnostics:
            t = d["type"]
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(d)

        type_labels = {
            "missing_python_file": "Fichiers Python manquants",
            "ip_mismatch": "Adresses IP incohérentes",
            "stale_context_file": "Fichiers de contexte obsolètes",
        }

        for type_key, items in by_type.items():
            label = type_labels.get(type_key, type_key)
            lines.append(f"### {label}")
            lines.append("")
            for item in items:
                level_icon = "WARNING" if item["level"] == "warning" else "ERROR"
                lines.append(f"- [{level_icon}] {item['message']}")
                if item.get("suggestion"):
                    lines.append(f"  - Suggestion: {item['suggestion']}")
            lines.append("")

        return "\n".join(lines)


# Point d'entrée standalone pour exécution manuelle
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    healer = ContextSelfHealer()
    report = healer.run_and_report()
    print(report)
