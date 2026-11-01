"""
agents/task_discovery_agent.py — Agent autonome de découverte de tâches & allocation de tiers.

Analyse de façon autonome :
1. Le backlog Markdown (docs/travail/TACHES.md & contexte_ia/04_Projets/TACHES.md).
2. Les commentaires TODO / FIXME dans le code source.
3. Les erreurs récurrentes dans session_history.db / logs.

Pour chaque tâche découverte, l'agent :
- Évalue son niveau de complexité (simple/moyen/complexe).
- Détermine le Tier LLM optimal (Tier Local/Gratuit vs Tier Fort/Superviseur).
- Rédige une consigne ultra-cadrée (préambule + contrat de sortie) pour éviter toute dégradation par les modèles légers.
- Injecte la tâche dans le backlog runtime (moteur_runtime.db / DAG).

Auteur : Antigravity IDE + Axel
Créé le : 2026-08-18 (Branche : feat/task-discovery-agent)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("task_discovery_agent")

_ENGINE_ROOT = Path(__file__).resolve().parent.parent

# Tâches réservées à l'humain : le board marque d'un « DÉCISION AXEL » tout ce
# qui exige un arbitrage d'Axel (config, HITL, choix d'archi). Les injecter
# dans le backlog autonome ferait trancher par un LLM ce qui ne le regarde pas.
RE_DECISION_HUMAINE = re.compile(r"DÉCISION\s+AXEL", re.IGNORECASE)

# Marqueur de déduplication embarqué dans chaque prompt injecté. La clé est
# STABLE (fichier + texte du TODO, sans numéro de ligne) : un TODO qui descend
# de trois lignes après un commit voisin reste le même travail, il ne doit pas
# être ré-injecté.
RE_MARQUEUR_DEDUP = re.compile(r"\[dedup:(?P<cle>[^\]]+)\]")

# Motifs de détection des TODO dans le code
RE_TODO = re.compile(r"#\s*(?:TODO|FIXME|HACK|BUG)\s*(?::|\s)\s*(?P<desc>.+)", re.IGNORECASE)


def _normaliser_texte(texte: str) -> str:
    """Forme canonique d'un texte pour les clés de dédup (casse, espaces)."""
    return re.sub(r"\s+", " ", texte.strip().lower())


def cle_dedup_board(tache_id: str) -> str:
    """Clé stable d'une tâche du board : son identifiant #Txxx suffit."""
    return f"board:{tache_id}"


def cle_dedup_todo(rel_path: str, texte: str) -> str:
    """
    Clé stable d'un TODO de code : fichier + texte, SANS numéro de ligne.

    Le numéro de ligne bouge dès qu'un commit voisin insère du code ; le
    mettre dans la clé transformait chaque shift en « nouvelle tâche » et
    ré-injectait le même travail en boucle.
    """
    return f"todo:{rel_path}:{_normaliser_texte(texte)}"


class TaskDiscoveryAgent:
    """Agent de découverte automatique de tâches et de ventilation par Tier LLM."""

    def __init__(self, engine_root: Path | None = None) -> None:
        self.root = engine_root or _ENGINE_ROOT
        self.db_path = self.root / "moteur_runtime.db"

    def scan_board_tasks(self) -> list[dict[str, Any]]:
        """Extrait les tâches ouvertes depuis docs/travail/TACHES.md."""
        board_path = self.root / "docs" / "travail" / "TACHES.md"
        if not board_path.exists():
            logger.warning("[TASK_DISCOVERY] TACHES.md introuvable: %s", board_path)
            return []

        taches: list[dict[str, Any]] = []
        pattern = re.compile(
            r"^-\s*⏳\s*#(?P<id>T\d+)\s*—\s*(?P<zone>[\w\-/]+)\s*—\s*\*\*(?P<prio>P\d)\*\*\s*—\s*(?P<desc>.+)",
            re.MULTILINE
        )

        try:
            content = board_path.read_text(encoding="utf-8")
            for match in pattern.finditer(content):
                d = match.groupdict()
                if RE_DECISION_HUMAINE.search(d["desc"]):
                    # Tâche réservée à Axel : jamais déléguée à un modèle.
                    continue
                if re.search(r"à\s+analyser", d["desc"], re.IGNORECASE):
                    # [#T432 suite] Une analyse n'est pas un livrable de code.
                    continue
                # Ne pas réinjecter une #T déjà cochée ✅ ailleurs sur le board
                # (mesuré 23/08 : #T414 rejouée alors que git l'avait déjà).
                if re.search(rf"✅[^\n]*#{re.escape(d['id'])}\b", content):
                    continue
                taches.append({
                    "id": d["id"],
                    "source": "board_md",
                    "zone": d["zone"],
                    "prio": d["prio"],
                    "title": f"[{d['id']}] ({d['zone']}) {d['desc'][:60]}",
                    "description": d["desc"],
                    "complexity": self.evaluate_complexity(d["desc"], d["zone"]),
                    "dedup_key": cle_dedup_board(d["id"]),
                    "project_id": self._projet_depuis_zone(d["zone"]),
                })
        except Exception as exc:
            logger.error("[TASK_DISCOVERY] Erreur lecture TACHES.md: %s", exc)

        return taches

    @staticmethod
    def _projet_depuis_zone(zone: str) -> str:
        from core.projects import resoudre_projet_pour_tache
        return resoudre_projet_pour_tache({"zone": zone}).id

    def scan_code_todos(self) -> list[dict[str, Any]]:
        """Scanne le code Python pour identifier les commentaires TODO/FIXME."""
        todos: list[dict[str, Any]] = []
        # Hors périmètre : dépendances vendues, brouillons, worktrees et copies.
        # Mesuré le 18/08 sur ce dépôt : sans ce filtre, 116 TODO découverts,
        # dont 80 dans tools/llama.cpp (code tiers embarqué) et 32 dans
        # .claude/ (worktrees) — le signal utile se noyait sous le bruit.
        # Exclusions : on ignore les répertoires techniques, les dépendances,
        # les worktrees et les zones de stockage temporaire pour éviter
        # de polluer le backlog avec des TODOs résiduels ou non pertinents.
        # On conserve les répertoires applicatifs (agents, services, etc.)
        # pour maintenir la découverte de tâches active sur le code métier.
        exclusions = {
            ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
            "checkpoints", "backups_prod", "scratch", ".claude", "llama.cpp",
            "archives", "worktrees", "vromvrom-engine", "base_connaissance_v13",
            "site-packages", "dist-packages", ".tox", ".nox", "vendor",
            "third_party", "thirdparty", ".pytest_cache", ".mypy_cache",
            ".ruff_cache", "build", "dist", ".eggs", ".cache",
        }

        for py_file in self.root.rglob("*.py"):
            if any(part in py_file.parts for part in exclusions):
                continue
            if any(part.endswith(".egg-info") for part in py_file.parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                rel_path = py_file.relative_to(self.root).as_posix()
                for idx, line in enumerate(content.splitlines(), start=1):
                    m = RE_TODO.search(line)
                    if m:
                        desc = m.group("desc").strip()
                        fichier_test = f"tests/unit/test_{py_file.stem}.py"
                        todos.append({
                            "id": f"TODO_{py_file.stem}_{idx}",
                            "source": "code_todo",
                            "zone": rel_path,
                            "prio": "P3",
                            # Le numéro de ligne reste dans la description
                            # (utile pour localiser) mais ni dans le titre ni
                            # dans la clé : c'est une coordonnée, pas une
                            # identité.
                            "title": f"TODO dans {rel_path} — {desc[:50]}",
                            "description": f"Fichier {rel_path} ligne {idx}: {desc}",
                            "complexity": "low",
                            "dedup_key": cle_dedup_todo(rel_path, desc),
                            "project_id": "moteur",
                            "validation": {
                                "fichier_test": fichier_test,
                                "commande": f"python -m pytest {fichier_test} -q",
                            },
                        })
            except Exception:
                continue

        return todos

    def evaluate_complexity(self, description: str, zone: str) -> str:
        """
        Évalue la complexité d'une tâche pour assigner le bon Tier LLM.
        - 'low'  -> Modèle Local (Qwen 3 Coder / AJEAN / Gemini Flash)
        - 'high' -> Modèle Fort (Claude CLI / Gemini Pro / DeepSeek Reasoner)
        """
        desc_lower = description.lower()

        # Mots-clés de haute complexité
        high_complexity_keywords = [
            "archi", "architecture", "refactor", "securite", "security",
            "refonte", "multi-machine", "systemd", "c++", "esphome", "p0", "bloquant"
        ]

        if any(kw in desc_lower for kw in high_complexity_keywords):
            return "high"

        # Tâches simples / répétitives
        low_complexity_keywords = [
            "doc", "documentation", "test", "nettoyage", "linter", "format",
            "typographie", "typo", "log", "commentaire", "export"
        ]

        if any(kw in desc_lower for kw in low_complexity_keywords):
            return "low"

        return "medium"

    def select_llm_tier(self, complexity: str) -> dict[str, str]:
        """Sélectionne le tier et le modèle conseillé selon la complexité."""
        if complexity == "low":
            return {
                "tier": "local_free",
                "recommended_models": ["ajean_pc", "gemini-3.1-flash-lite", "dashscope/qwen3-coder-next"],
                "reason": "Tâche simple / répétitive attribuée aux modèles gratuits / locaux.",
            }
        elif complexity == "high":
            return {
                "tier": "supervisor_pro",
                "recommended_models": ["claude-sonnet-4-6", "gemini-3.5-flash-high-cli", "deepseek-reasoner"],
                "reason": "Tâche complexe d'architecture nécessitant supervision d'élite.",
            }
        else:
            return {
                "tier": "standard_fast",
                "recommended_models": ["gemini-3.5-flash", "deepseek-chat"],
                "reason": "Tâche intermédiaire nécessitant rapidité et précision.",
            }

    def contrat_validation(self, zone: str) -> list[str]:
        """
        Ingrédients n°3 du contrat de délégation : les validations OBLIGATOIRES
        avant de déclarer un succès, adaptées à la nature de la zone touchée.
        Un préambule générique disait « valide par test » sans dire lesquels ;
        ici la consigne est exécutable telle quelle.
        """
        z = zone.lower()
        if z.startswith("tab5") or z.startswith("camha"):
            return [
                "Valide la syntaxe YAML de chaque fichier ESPHome modifié "
                "(python -c \"import yaml; yaml.safe_load(open('<fichier>', encoding='utf-8'))\").",
                "Vérifie les GPIO ESP32-P4 réservés au boot avant d'écrire une pin.",
                "N'exécute PAS pytest du moteur : le livrable va dans le git Tab5 "
                "(00ProjetTab / Axellum/M5-Tab5-ESPHome-LVGL), pas dans moteur_agents.",
            ]
        if z.endswith((".yaml", ".yml")):
            return [
                "Valide la syntaxe YAML du fichier modifié "
                "(ex. python -c \"import yaml; yaml.safe_load(open('<fichier>', encoding='utf-8'))\").",
                "Vérifie que les clés/indentations existantes n'ont pas bougé hors de la modification demandée.",
            ]
        if z.endswith(".md") or z.startswith("docs"):
            return [
                "Relis le rendu : orthographe, hiérarchie des titres, aucun lien cassé.",
                "Vérifie que le Markdown source ne contient pas de balises HTML parasites.",
            ]
        return [
            "Exécute `python -m pytest tests/unit -q` (restreins avec `-k` au module touché "
            "si la suite complète est trop longue) et n'annonce le succès que s'il est vert.",
            "Exécute `ruff check` sur chaque fichier modifié et corrige les erreurs signalées.",
            # Deux invariants d'arborescence, violés le 19/08 par trois tâches
            # d'affilée : un test posé dans `core/`, et un fichier livré sans
            # extension (`runtime/llm`, du nom de la zone). Ils sont énoncés ici
            # ET vérifiés par une porte de sortie côté DreamCoder — la consigne
            # seule ne suffit pas, on l'a mesuré.
            "Tout fichier de test que tu écris va sous `tests/unit/` — jamais dans "
            "`core/`, `agents/`, `api/` ni `services/`.",
            "Tout fichier que tu livres porte une extension (`.py`, `.md`, `.json`…). "
            "Un fichier sans extension n'est pas un livrable : c'est une sortie de "
            "commande écrite au mauvais endroit.",
        ]

    def prepare_task_payload(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Enrobe la tâche avec un CONTRAT DE DÉLÉGATION automatique à quatre
        ingrédients — pas un préambule générique :

        1. PÉRIMÈTRE   : la zone/les fichiers exacts, et l'interdit de toucher au reste.
        2. OBJECTIF    : la description de la tâche, telle que formulée.
        3. VALIDATION  : les commandes/contrôles obligatoires avant succès.
        4. CONTRAT DE SORTIE : le format du résumé attendu en retour.

        Le routage (tier + modèles conseillés) et le marqueur `[dedup:…]`
        sont ajoutés en pied de prompt.
        """
        tier_info = self.select_llm_tier(task["complexity"])
        from core.projects import resoudre_projet_pour_tache
        projet = resoudre_projet_pour_tache(task)
        validations = self.contrat_validation(task["zone"])
        validation_meca = task.get("validation")
        if validation_meca:
            validations = [
                f"Exécute `{validation_meca['commande']}` et n'annonce le succès "
                f"que si le fichier `{validation_meca['fichier_test']}` est vert.",
            ] + validations
        liste_validations = "\n".join(f"- {v}" for v in validations)
        cle = task.get("dedup_key", f"libre:{task['id']}")

        prompt_final = (
            "Contexte d'exécution : tu travailles de façon autonome dans un "
            "conteneur/clone Git isolé. Ce contrat tient lieu de consigne unique.\n\n"
            f"**1. PÉRIMÈTRE (ne touche QUE cela)**\n"
            f"- Projet Git : {projet.id} ({projet.label}) — remote {projet.git_remote}\n"
            f"- Zone (ÉTIQUETTE de classement du board, PAS un chemin de fichier) : "
            f"{task['zone']}\n"
            f"- Ne crée NI dossier NI fichier portant ce nom. Commence par LOCALISER "
            f"dans le dépôt le ou les fichiers existants qui portent déjà le "
            f"comportement visé (`grep`, `find`), puis modifie-les. Si tu ne les "
            f"trouves pas, dis-le et échoue : n'invente pas un module neuf.\n"
            f"- Ne modifie AUCUN fichier hors de ce périmètre ; toute modification "
            f"annexe doit être signalée dans le résumé, pas faite.\n\n"
            f"**2. OBJECTIF**\n"
            f"- Tâche {task['id']} (priorité {task['prio']}) : {task['description']}\n\n"
            f"**3. VALIDATION OBLIGATOIRE**\n"
            f"{liste_validations}\n"
            f"- Si la validation échoue, n'essaie pas de la contourner : échoue proprement "
            f"et décris pourquoi.\n\n"
            f"**4. CONTRAT DE SORTIE**\n"
            f"- Termine par un résumé court : fichiers modifiés, validations exécutées "
            f"(avec leur résultat), risques restants.\n\n"
            f"**Routage** : tier {tier_info['tier']} — {', '.join(tier_info['recommended_models'])} "
            f"({tier_info['reason']})\n\n"
            f"[dedup:{cle}]\n"
        )

        return {
            "task_id": task["id"],
            "title": task["title"],
            "prompt": prompt_final,
            "complexity": task["complexity"],
            "tier_info": tier_info,
            "source": task["source"],
            "prio": task["prio"],
            "dedup_key": cle,
            "project_id": task.get("project_id") or projet.id,
            "zone": task.get("zone"),
            "validation": task.get("validation"),
        }

    def run_discovery(self) -> list[dict[str, Any]]:
        """Exécute un balayage complet et retourne les tâches découvertes et typées."""
        all_tasks = self.scan_board_tasks() + self.scan_code_todos()
        payloads = [self.prepare_task_payload(t) for t in all_tasks]
        logger.info("[TASK_DISCOVERY] %d tâches découvertes (%d high, %d low).",
                    len(payloads),
                    sum(1 for p in payloads if p["complexity"] == "high"),
                    sum(1 for p in payloads if p["complexity"] == "low"))
        return payloads


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = TaskDiscoveryAgent()
    results = agent.run_discovery()
    print(f"=== {len(results)} TÂCHES DÉCOUVERTES ===")
    for r in results[:5]:
        print(f"- {r['task_id']} ({r['complexity']}) [{r['tier_info']['tier']}]: {r['title']}")
