"""
memory/skills.py — Mémoire Procédurale (Skill Learning).

Enregistre les séquences d'outils réussies après validation par le Reviewer
pour les réinjecter dans le contexte du Planner lors de tâches similaires.

Architecture :
    1. Après un DAG réussi + review approuvée, SkillStore.record_skill() est appelé
    2. La séquence d'outils (ex: read_file → modify → write_file → validate) est sauvegardée
    3. Lors de la prochaine planification, SkillStore.get_relevant_skills() retourne
       les skills matchant la tâche courante (via recherche par mots-clés)

Format de stockage :
    skills.json = [
        {
            "pattern": "modifier un fichier YAML ESPHome",
            "tools_sequence": ["read_file", "write_file", "validate_config_yaml"],
            "success_count": 3,
            "last_used": "2026-05-25T14:00:00",
            "tags": ["yaml", "esphome", "fichier"]
        },
        ...
    ]
"""

import os
import json
import logging
import time
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from core.safe_io import safe_json_write, file_lock  # [P1-2.3]

logger = logging.getLogger(__name__)

# Fichier de stockage des skills (à côté du moteur)
_DEFAULT_SKILLS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills.json"
)


class SkillStore:
    """
    Mémoire procédurale : enregistre et retrouve les séquences d'outils réussies.
    
    Utilisation :
        store = SkillStore()
        store.record_skill(
            pattern="Modifier un fichier YAML ESPHome",
            tools_sequence=["read_file", "write_file", "validate_config_yaml"],
            tags=["yaml", "esphome"]
        )
        skills = store.get_relevant_skills("Modifie le fichier ESPHome du salon")
    """

    # Seuil de succès pour déclencher la génération d'outil
    TOOLMAKING_THRESHOLD = 3

    def __init__(self, skills_file: str = _DEFAULT_SKILLS_FILE):
        self.skills_file = skills_file
        self._skills: list[dict] = []
        self._load()

    def _load(self):
        """Charge les skills depuis le fichier JSON.

        [P1-2.3] Lecture sous FileLock pour ne jamais lire pendant une écriture
        concurrente (multi-process). Les écritures étant atomiques (os.replace),
        un fichier tronqué est de toute façon impossible.
        """
        if os.path.exists(self.skills_file):
            try:
                with file_lock(self.skills_file):
                    with open(self.skills_file, 'r', encoding='utf-8') as f:
                        self._skills = json.load(f)
                logger.info(f"[SKILLS] {len(self._skills)} skill(s) chargé(s)")
            except Exception as e:
                logger.warning(f"[SKILLS] Erreur de chargement : {e}")
                self._skills = []
        else:
            self._skills = []

    def _save(self):
        """Persiste les skills dans le fichier JSON.

        [P1-2.3] Écriture atomique (fichier temp + os.replace) protégée par un
        FileLock inter-process — empêche la corruption de skills.json en cas
        d'écritures concurrentes (plusieurs SkillStore / processus).
        """
        try:
            safe_json_write(self.skills_file, self._skills)
        except Exception as e:
            logger.error(f"[SKILLS] Erreur de sauvegarde : {e}")

    def record_skill(
        self,
        pattern: str,
        tools_sequence: list[str],
        tags: list[str] = None,
        objective: str = ""
    ):
        """
        Enregistre une séquence d'outils réussie comme un skill réutilisable.
        
        Si un skill avec le même pattern existe déjà, incrémente son compteur de succès.
        Quand success_count >= TOOLMAKING_THRESHOLD, le skill est marqué
        comme candidat pour le ToolMakerAgent.
        
        Args:
            pattern: Description courte du type de tâche (ex: "Modifier un fichier YAML")
            tools_sequence: Liste ordonnée des outils utilisés avec succès
            tags: Mots-clés pour la recherche (ex: ["yaml", "esphome"])
            objective: Objectif original de la tâche (pour le contexte)
        """
        if not tools_sequence:
            return

        # Dédupliquer les outils consécutifs identiques
        deduped = [tools_sequence[0]]
        for tool in tools_sequence[1:]:
            if tool != deduped[-1]:
                deduped.append(tool)

        # Vérifier si un skill similaire existe déjà
        for skill in self._skills:
            if skill["tools_sequence"] == deduped and skill["pattern"] == pattern:
                # Incrémenter le compteur de succès
                skill["success_count"] = skill.get("success_count", 1) + 1
                skill["last_used"] = datetime.now().isoformat()
                self._save()
                logger.info(
                    f"[SKILLS] Skill existant renforcé : '{pattern}' "
                    f"(succès: {skill['success_count']})"
                )
                
                # Détection de candidat pour le ToolMaker
                if (
                    skill["success_count"] >= self.TOOLMAKING_THRESHOLD
                    and not skill.get("auto_tool_generated", False)
                ):
                    logger.info(
                        f"[SKILLS] 🔧 Skill candidat pour ToolMaker : '{pattern}' "
                        f"({skill['success_count']} succès >= seuil {self.TOOLMAKING_THRESHOLD})"
                    )
                    skill["toolmaking_candidate"] = True
                    self._save()
                
                return

        # Nouveau skill
        new_skill = {
            "pattern": pattern,
            "tools_sequence": deduped,
            "tags": tags or [],
            "objective": objective,
            "success_count": 1,
            "created_at": datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
            "auto_tool_generated": False,  # Flag pour éviter la régénération
            "toolmaking_candidate": False,  # Candidat pour ToolMaker
        }
        self._skills.append(new_skill)
        self._save()
        logger.info(
            f"[SKILLS] Nouveau skill enregistré : '{pattern}' "
            f"({len(deduped)} outils : {' → '.join(deduped)})"
        )

    def get_relevant_skills(self, objective: str, max_results: int = 3) -> list[dict]:
        """
        Recherche les skills pertinents pour un objectif donné.
        
        Utilise une correspondance par mots-clés (tags + pattern) et
        priorise par nombre de succès (les skills les plus utilisés en premier).
        
        Args:
            objective: Objectif de la tâche en langage naturel
            max_results: Nombre maximum de skills à retourner
            
        Returns:
            Liste de skills pertinents triés par pertinence décroissante
        """
        if not self._skills or not objective:
            return []

        objective_lower = objective.lower()
        scored_skills = []

        for skill in self._skills:
            score = 0.0

            # Score par tags
            for tag in skill.get("tags", []):
                if tag.lower() in objective_lower:
                    score += 2.0

            # Score par mots du pattern
            for word in skill.get("pattern", "").lower().split():
                if len(word) > 3 and word in objective_lower:
                    score += 1.0

            # Bonus par nombre de succès (log scale)
            import math
            success_count = skill.get("success_count", 1)
            score += math.log2(success_count + 1) * 0.5

            if score > 0:
                scored_skills.append((score, skill))

        # Tri par score décroissant
        scored_skills.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in scored_skills[:max_results]]

    def build_skills_context(self, objective: str) -> str:
        """
        Génère un contexte textuel des skills pertinents pour injection
        dans le prompt du Planner.
        
        Args:
            objective: Objectif de la tâche
            
        Returns:
            Texte formaté pour injection dans le prompt (vide si aucun skill)
        """
        skills = self.get_relevant_skills(objective)
        if not skills:
            return ""

        lines = ["## Compétences procédurales acquises (skills réutilisables) :"]
        for i, skill in enumerate(skills, 1):
            seq = " → ".join(skill["tools_sequence"])
            count = skill.get("success_count", 1)
            lines.append(
                f"  {i}. **{skill['pattern']}** ({count} succès) : {seq}"
            )

        return "\n".join(lines)

    def get_all_skills(self) -> list[dict]:
        """Retourne tous les skills enregistrés (pour l'IHM)."""
        return list(self._skills)

    def delete_skill(self, index: int) -> bool:
        """Supprime un skill par son index."""
        if 0 <= index < len(self._skills):
            removed = self._skills.pop(index)
            self._save()
            logger.info(f"[SKILLS] Skill supprimé : '{removed['pattern']}'")
            return True
        return False

    def clear(self):
        """Supprime tous les skills."""
        self._skills.clear()
        self._save()
        logger.info("[SKILLS] Tous les skills ont été supprimés.")

    # ──────────────────────────────────────────────────────────────────
    # Méthodes pour le cycle de vie ToolMaker
    # ──────────────────────────────────────────────────────────────────

    def get_candidates_for_toolmaking(self, min_success: int = None) -> list[dict]:
        """
        Retourne les skills candidats pour la génération automatique d'outils.
        
        Un skill est candidat si :
        - success_count >= min_success (défaut: TOOLMAKING_THRESHOLD)
        - auto_tool_generated == False (pas encore généré)
        
        Args:
            min_success: Seuil minimum de succès (défaut: TOOLMAKING_THRESHOLD)
            
        Returns:
            Liste des skills éligibles.
        """
        threshold = min_success or self.TOOLMAKING_THRESHOLD
        return [
            skill for skill in self._skills
            if skill.get("success_count", 0) >= threshold
            and not skill.get("auto_tool_generated", False)
        ]

    def mark_as_generated(self, pattern: str, tool_path: str = "") -> bool:
        """
        Marque un skill comme ayant été converti en outil auto-généré.
        
        Args:
            pattern: Le pattern du skill à marquer
            tool_path: Chemin du fichier outil généré
            
        Returns:
            True si le skill a été trouvé et marqué.
        """
        for skill in self._skills:
            if skill["pattern"] == pattern:
                skill["auto_tool_generated"] = True
                skill["toolmaking_candidate"] = False
                skill["generated_tool_path"] = tool_path
                skill["generated_at"] = datetime.now().isoformat()
                self._save()
                logger.info(
                    f"[SKILLS] Skill '{pattern}' marqué comme outil généré : {tool_path}"
                )
                return True
        return False


# ──────────────────────────────────────────────────────────────────
# Méthodes déléguées pour la base de données MemoryDB
# ──────────────────────────────────────────────────────────────────

def record_learned_lesson(db, category: str, title: str,
                          content: str, source_file: str = "",
                          tags: str = "",
                          severity: str = "minor") -> Dict[str, Any]:
    """
    Enregistre une leçon apprise automatiquement (hook post-DAG).
    """
    # 1. Toujours enregistrer dans la base SQLite
    fact_id = db.upsert_fact(
        category=category, title=title, content=content,
        source_file=source_file, tags=tags
    )

    # Évaluation heuristique de la qualité de la leçon
    quality = _compute_quality_score(db, title, content, category)

    # Enregistrer le score de qualité dans la base
    with db._write_lock:
        conn = db._get_conn()
        try:
            conn.execute(
                "UPDATE facts SET quality_score = ? WHERE id = ?",
                (quality, fact_id)
            )
            conn.commit()
        finally:
            conn.close()

    # Si la qualité est trop faible, forcer severity=minor
    if quality < 0.4:
        logger.info(
            f"[QUALITY] Leçon '{title}' rejetée pour écriture MD "
            f"(score={quality:.2f} < 0.4, severity forcée à 'minor')"
        )
        severity = "minor"
    else:
        logger.info(
            f"[QUALITY] Leçon '{title}' évaluée à {quality:.2f}/1.0 "
            f"(severity effective: {severity})"
        )

    result = {"fact_id": fact_id, "markdown_file": None, "written_to_md": False,
              "quality_score": quality}

    # 2. Si severity >= major, écrire aussi dans le fichier Markdown
    if severity in ("major", "critical"):
        md_path = _get_lecon_md_path(db, category)
        if md_path and os.path.exists(md_path):
            try:
                # Formater l'entrée Markdown
                timestamp = time.strftime("%Y-%m-%d")
                entry = (
                    f"\n- **{title}** : {content}\n"
                    f"  - *Ajouté automatiquement le {timestamp} "
                    f"(severity: {severity})*\n"
                )

                with open(md_path, 'a', encoding='utf-8', newline='\n') as f:
                    f.write(entry)

                result["markdown_file"] = md_path
                result["written_to_md"] = True
                logger.info(
                    f"[CONSOLIDATION] Leçon '{title}' écrite dans {os.path.basename(md_path)} "
                    f"(severity={severity})"
                )
            except Exception as e:
                logger.error(f"[CONSOLIDATION] Erreur écriture MD : {e}")

    # 3. Enregistrer dans le graphe aussi
    db.upsert_graph_entity(
        name=f"Lecon_{title[:50].replace(' ', '_')}",
        entity_type="lecon_apprise",
        observations=[
            f"[{time.strftime('%Y-%m-%d')}] {title}",
            f"Catégorie: {category}, Sévérité: {severity}",
            content[:200],
        ]
    )

    return result


def _get_lecon_md_path(db, category: str) -> Optional[str]:
    """Retourne le chemin du fichier de leçons Markdown pour une catégorie."""
    # Résoudre le chemin depuis la racine contexte_ia
    base = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "contexte_ia")
    )
    mapping = {
        "esphome": os.path.join(base, "02_Hardware", "lecons_esphome_hardware.md"),
        "moteur": os.path.join(base, "03_Software", "lecons_moteur_agents.md"),
        "gcp": os.path.join(base, "03_Software", "lecons_gcp_apis.md"),
        "hmi": os.path.join(base, "03_Software", "lecons_hmi.md"),
        "infra": os.path.join(base, "01_Core", "lecons_infra_windows.md"),
    }
    return mapping.get(category)


def _compute_quality_score(db, title: str, content: str,
                           category: str) -> float:
    """
    Évalue la qualité d'une leçon apprise par heuristique de mots-clés.
    """
    text_lower = (title + " " + content).lower()
    score = 0.5  # Score de base

    # Bonus : mots-clés techniques critiques (+0.15)
    critical_keywords = [
        "fix", "crash", "bug", "race condition", "bootloop", "erreur",
        "correction", "solution", "workaround", "timeout", "deadlock",
        "oom", "memory leak", "coroutine", "exception", "traceback",
        "security", "vulnérabilité", "circuit breaker", "rollback",
    ]
    if any(kw in text_lower for kw in critical_keywords):
        score += 0.15

    # Bonus : contient du code (+0.10)
    code_markers = ["```", "def ", "class ", "import ", "async ", "await "]
    if any(marker in content for marker in code_markers):
        score += 0.10

    # Bonus : titre descriptif > 15 caractères (+0.10)
    if len(title.strip()) > 15:
        score += 0.10

    # Malus : contenu trop court < 50 caractères (-0.20)
    if len(content.strip()) < 50:
        score -= 0.20

    # Malus : leçon générique de faible valeur (-0.15)
    low_value_patterns = [
        "mise à jour", "update css", "alignement", "nettoyage",
        "cleanup", "refactoring mineur", "typo", "renommage",
        "suppression de commentaire", "formatage",
    ]
    if any(pat in text_lower for pat in low_value_patterns):
        score -= 0.15

    # Clamp entre 0.0 et 1.0
    return max(0.0, min(1.0, score))


async def record_learned_lesson_async(db, category: str, title: str,
                                       content: str, source_file: str = "",
                                       tags: str = "",
                                       severity: str = "minor") -> Dict[str, Any]:
    """Enregistre asynchronement une leçon apprise (avec scoring V9 et sync Markdown)."""
    fact_id = await db.upsert_fact_async(
        category=category, title=title, content=content,
        source_file=source_file, tags=tags
    )

    # Évaluation heuristique de la qualité de la leçon
    quality = _compute_quality_score(db, title, content, category)

    # Enregistrer le score de qualité dans la base (via thread pour rester async)
    def _update_quality():
        with db._write_lock:
            conn = db._get_conn()
            try:
                conn.execute(
                    "UPDATE facts SET quality_score = ? WHERE id = ?",
                    (quality, fact_id)
                )
                conn.commit()
            finally:
                conn.close()
    await asyncio.to_thread(_update_quality)

    # Si la qualité est trop faible, forcer severity=minor
    if quality < 0.4:
        logger.info(
            f"[QUALITY] [ASYNC] Leçon '{title}' rejetée pour écriture MD "
            f"(score={quality:.2f} < 0.4)"
        )
        severity = "minor"
    else:
        logger.info(
            f"[QUALITY] [ASYNC] Leçon '{title}' évaluée à {quality:.2f}/1.0 "
            f"(severity effective: {severity})"
        )

    result = {"fact_id": fact_id, "markdown_file": None, "written_to_md": False,
              "quality_score": quality}
    if severity in ("major", "critical"):
        md_path = _get_lecon_md_path(db, category)
        if md_path and os.path.exists(md_path):
            try:
                timestamp = time.strftime("%Y-%m-%d")
                entry = (
                    f"\n- **{title}** : {content}\n"
                    f"  - *Ajouté automatiquement le {timestamp} (severity: {severity})*\n"
                )
                with open(md_path, 'a', encoding='utf-8', newline='\n') as f:
                    f.write(entry)
                result["markdown_file"] = md_path
                result["written_to_md"] = True
            except Exception as e:
                logger.error(f"[CONSOLIDATION] [ASYNC] Erreur MD : {e}")

    await db.upsert_graph_entity_async(
        name=f"Lecon_{title[:50].replace(' ', '_')}",
        entity_type="lecon_apprise",
        observations=[
            f"[{time.strftime('%Y-%m-%d')}] {title}",
            f"Catégorie: {category}, Sévérité: {severity}, Qualité: {quality:.2f}",
            content[:200],
        ]
    )
    return result
