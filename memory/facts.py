"""
memory/facts.py — Mémoire sémantique (faits) pour le tab5-engine.
Gère à la fois les faits techniques persistants en JSON (FactStore)
et les fonctions d'insertion et de recherche de faits en base SQLite (MemoryDB).
"""

import os
import json
import logging
import time
import sqlite3
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

# Fichier de stockage des faits (moteur_agents/memory/facts.json)
_DEFAULT_FACTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "facts.json"
)


class FactStore:
    """
    Stockage structuré de faits techniques et domotiques persistants entre sessions.
    Les agents peuvent enregistrer des faits via l'outil save_fact.
    Le Router interroge cette mémoire pour enrichir le contexte.
    """

    def __init__(self, facts_file: str = _DEFAULT_FACTS_FILE):
        self.facts_file = facts_file
        self.facts: Dict[str, Dict[str, dict]] = {}  # {category: {key: {value, source, timestamp}}}
        self._load()

    def _load(self):
        """Charge les faits depuis le fichier JSON."""
        if not os.path.exists(self.facts_file):
            self.facts = {}
            return
        try:
            with open(self.facts_file, 'r', encoding='utf-8') as f:
                self.facts = json.load(f)
            total = sum(len(v) for v in self.facts.values())
            logger.info(f"[FACTS] {total} fait(s) chargé(s) depuis {os.path.basename(self.facts_file)}")
        except Exception as e:
            logger.error(f"[FACTS] Erreur de chargement : {e}")
            self.facts = {}

    def _save(self):
        """Sauvegarde les faits dans le fichier JSON."""
        try:
            with open(self.facts_file, 'w', encoding='utf-8') as f:
                json.dump(self.facts, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[FACTS] Erreur de sauvegarde : {e}")

    def add_fact(self, category: str, key: str, value: str, source: str = "unknown") -> str:
        """
        Ajoute ou met à jour un fait structuré.
        
        Args:
            category: Catégorie du fait (sensor, config, hardware, network, automation, etc.)
            key: Identifiant unique du fait dans sa catégorie (ex: "dht22_salon")
            value: Valeur du fait (ex: "offset de température = -1.5°C")
            source: Source du fait (ex: session_id ou "utilisateur")
        
        Returns:
            Message de confirmation.
        """
        category = category.lower().strip()
        key = key.lower().strip()

        if category not in self.facts:
            self.facts[category] = {}

        is_update = key in self.facts[category]
        self.facts[category][key] = {
            "value": value,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        }
        self._save()

        action = "mis à jour" if is_update else "enregistré"
        logger.info(f"[FACTS] Fait {action} : [{category}] {key} = {value}")
        return f"Fait {action} : [{category}] {key} = {value}"

    def remove_fact(self, category: str, key: str) -> str:
        """Supprime un fait obsolète."""
        category = category.lower().strip()
        key = key.lower().strip()

        if category in self.facts and key in self.facts[category]:
            del self.facts[category][key]
            if not self.facts[category]:
                del self.facts[category]
            self._save()
            logger.info(f"[FACTS] Fait supprimé : [{category}] {key}")
            return f"Fait supprimé : [{category}] {key}"
        return f"Fait non trouvé : [{category}] {key}"

    def get_facts_for_context(self, keywords: List[str], max_chars: int = 2000) -> str:
        """
        Retourne les faits pertinents formatés pour injection dans le prompt.
        Recherche par correspondance de mots-clés dans les catégories et les clés.
        """
        if not self.facts or not keywords:
            return ""

        keywords_lower = [kw.lower() for kw in keywords]
        matched_facts = []

        for category, facts_dict in self.facts.items():
            # Vérifier si la catégorie match un mot-clé
            cat_match = any(kw in category for kw in keywords_lower)

            for key, fact_data in facts_dict.items():
                value = fact_data.get("value", "")
                # Match si : catégorie match, ou clé match, ou valeur contient un mot-clé
                key_match = any(kw in key for kw in keywords_lower)
                value_match = any(kw in value.lower() for kw in keywords_lower)

                if cat_match or key_match or value_match:
                    matched_facts.append({
                        "category": category,
                        "key": key,
                        "value": value,
                        "source": fact_data.get("source", "?"),
                    })

        if not matched_facts:
            return ""

        # Formater pour injection dans le prompt
        parts = ["*** MÉMOIRE SÉMANTIQUE (faits connus) ***"]
        total_chars = len(parts[0])
        for fact in matched_facts:
            line = f"  [{fact['category']}] {fact['key']} : {fact['value']}"
            if total_chars + len(line) > max_chars:
                parts.append("  ... (faits tronqués)")
                break
            parts.append(line)
            total_chars += len(line)

        return "\n".join(parts)

    def get_all_facts(self) -> Dict[str, Dict[str, dict]]:
        """Retourne tous les faits (pour l'API de monitoring)."""
        return self.facts

    def get_fact_count(self) -> int:
        """Retourne le nombre total de faits."""
        return sum(len(v) for v in self.facts.values())


# ──────────────────────────────────────────────────────────────
# Fonctions d'insertion et de recherche de faits en base SQLite
# ──────────────────────────────────────────────────────────────

def upsert_fact(db, category: str, title: str, content: str,
                source_file: str = "", tags: str = "",
                commit_hash: str = None, severity: str = "minor") -> int:
    """Insère ou met à jour un fait dans la base SQLite. Retourne l'ID."""
    now = time.time()
    with db._write_lock:
        conn = db._get_conn()
        try:
            # Vérifier si le fait existe déjà (par titre + catégorie)
            existing = conn.execute(
                "SELECT id FROM facts WHERE title = ? AND category = ?",
                (title, category)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE facts SET content = ?, source_file = ?, tags = ?, "
                    "updated_at = ?, relevance_score = 1.0, commit_hash = ?, severity = ? WHERE id = ?",
                    (content, source_file, tags, now, commit_hash, severity, existing["id"])
                )
                conn.commit()
                return existing["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO facts (category, title, content, source_file, "
                    "tags, created_at, updated_at, relevance_score, commit_hash, severity) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?)",
                    (category, title, content, source_file, tags, now, now, commit_hash, severity)
                )
                conn.commit()
                return cursor.lastrowid
        finally:
            conn.close()


def search_facts(db, query: str, category: str = None, limit: int = 10) -> List[Dict]:
    """Recherche des faits via FTS5 (BM25) avec fallback LIKE dans la base SQLite."""
    conn = db._get_conn()
    try:
        # Nettoyer la requête pour FTS5 (enlever les guillemets et astérisques bruts)
        clean_query = " ".join([w.strip('*').strip('"') for w in query.split() if w.strip('*').strip('"')])

        if not clean_query:
            return []

        try:
            # Requête FTS5 avec jointure pour charger toutes les colonnes de facts
            sql = """
                SELECT f.*, fts.rank 
                FROM facts f
                JOIN fts_facts fts ON f.id = fts.fact_id
                WHERE fts_facts MATCH ?
            """
            params = [clean_query]
            if category:
                sql += " AND f.category = ?"
                params.append(category)
            sql += " ORDER BY f.relevance_score DESC, fts.rank LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as e:
            # Fallback sur la recherche LIKE classique en cas d'erreur de syntaxe FTS5 ou d'absence du module
            logger.warning(f"[MEMORY DB] FTS5 search failed, falling back to LIKE: {e}")
            words = query.lower().split()
            conditions = []
            params = []
            for word in words:
                conditions.append("(LOWER(f.title) LIKE ? OR LOWER(f.content) LIKE ? OR LOWER(f.tags) LIKE ?)")
                params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])

            sql = f"SELECT f.* FROM facts f WHERE {' AND '.join(conditions)}"
            if category:
                sql += " AND f.category = ?"
                params.append(category)
            sql += " ORDER BY f.relevance_score DESC, f.updated_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def get_facts_by_category(db, category: str) -> List[Dict]:
    """Retourne tous les faits d'une catégorie depuis la base SQLite."""
    conn = db._get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM facts WHERE category = ? ORDER BY updated_at DESC",
            (category,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_facts_count(db) -> Dict[str, int]:
    """Retourne le nombre de faits par catégorie depuis la base SQLite."""
    conn = db._get_conn()
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) as count FROM facts GROUP BY category"
        ).fetchall()
        return {r["category"]: r["count"] for r in rows}
    finally:
        conn.close()


def decay_relevance(db, decay_rate: float = 0.05, min_score: float = 0.1) -> int:
    """
    Applique une décroissance temporelle sur le score de pertinence des faits dans la base SQLite.
    Les faits récemment mis à jour ne sont pas affectés (updated_at < 7 jours).
    """
    now = time.time()
    seven_days_ago = now - (7 * 24 * 3600)

    with db._write_lock:
        conn = db._get_conn()
        try:
            # Décrémenter le score des faits non mis à jour depuis > 7 jours
            cursor = conn.execute(
                "UPDATE facts SET relevance_score = MAX(?, relevance_score - ?) "
                "WHERE updated_at < ? AND relevance_score > ?",
                (min_score, decay_rate, seven_days_ago, min_score)
            )
            affected = cursor.rowcount
            conn.commit()

            if affected > 0:
                logger.info(
                    f"[MEMORY GC] Décroissance appliquée sur {affected} faits "
                    f"(rate={decay_rate}, min={min_score})"
                )

            return affected
        finally:
            conn.close()


def get_stale_facts(db, threshold: float = 0.3) -> List[Dict]:
    """Retourne les faits dont le score de pertinence est bas depuis la base SQLite."""
    conn = db._get_conn()
    try:
        rows = conn.execute(
            "SELECT id, category, title, relevance_score, updated_at "
            "FROM facts WHERE relevance_score <= ? ORDER BY relevance_score ASC",
            (threshold,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def upsert_fact_async(db, category: str, title: str, content: str,
                             source_file: str = "", tags: str = "",
                             commit_hash: str = None, severity: str = "minor") -> int:
    """Insère ou met à jour un fait de façon asynchrone dans la base SQLite."""
    now = time.time()
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            cursor = await conn.execute(
                "SELECT id FROM facts WHERE title = ? AND category = ?",
                (title, category)
            )
            existing = await cursor.fetchone()
            if existing:
                await conn.execute(
                    "UPDATE facts SET content = ?, source_file = ?, tags = ?, "
                    "updated_at = ?, relevance_score = 1.0, commit_hash = ?, severity = ? WHERE id = ?",
                    (content, source_file, tags, now, commit_hash, severity, existing["id"])
                )
                await conn.commit()
                return existing["id"]
            else:
                cursor = await conn.execute(
                    "INSERT INTO facts (category, title, content, source_file, "
                    "tags, created_at, updated_at, relevance_score, commit_hash, severity) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?)",
                    (category, title, content, source_file, tags, now, now, commit_hash, severity)
                )
                await conn.commit()
                return cursor.lastrowid
        finally:
            await conn.close()


def _ensure_facts_columns(db) -> None:
    """Ajoute les colonnes de tracking d'accès si elles sont absentes (migration additive)."""
    colonnes_a_ajouter = {
        "last_accessed_at": "REAL DEFAULT NULL",
        "importance_score": "REAL DEFAULT 1.0",
        "access_count": "INTEGER DEFAULT 0",
    }
    conn = db._get_conn()
    try:
        cursor = conn.execute("PRAGMA table_info(facts)")
        existantes = {row[1] for row in cursor}
        for nom, definition in colonnes_a_ajouter.items():
            if nom not in existantes:
                conn.execute(f"ALTER TABLE facts ADD COLUMN {nom} {definition}")
        conn.commit()
    finally:
        conn.close()


def touch_fact(db, fact_id: int) -> bool:
    """Incrémente access_count et met à jour last_accessed_at. Retourne False si absent."""
    with db._write_lock:
        conn = db._get_conn()
        try:
            if not conn.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone():
                return False
            conn.execute(
                "UPDATE facts SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
                (time.time(), fact_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def search_facts_weighted(db, query: str, limit: int = 10) -> List[Dict]:
    """Recherche FTS5 ordonnée par importance_score * relevance_score décroissant."""
    conn = db._get_conn()
    try:
        clean_query = " ".join([w.strip('*"') for w in query.split() if w.strip('*"')])
        if not clean_query:
            return []
        try:
            sql = """
                SELECT f.*
                FROM facts f
                JOIN fts_facts ON f.id = fts_facts.fact_id
                WHERE fts_facts MATCH ?
                ORDER BY COALESCE(f.importance_score, 1.0) * f.relevance_score DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, limit)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return search_facts(db, query, limit=limit)
    finally:
        conn.close()


async def decay_relevance_async(db, decay_rate: float = 0.05, min_score: float = 0.1) -> int:
    """Décroissance temporelle asynchrone de la pertinence des faits dans la base SQLite."""
    now = time.time()
    seven_days_ago = now - (7 * 24 * 3600)
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            cursor = await conn.execute(
                "UPDATE facts SET relevance_score = MAX(?, relevance_score - ?) "
                "WHERE updated_at < ? AND relevance_score > ?",
                (min_score, decay_rate, seven_days_ago, min_score)
            )
            affected = cursor.rowcount
            await conn.commit()
            if affected > 0:
                logger.info(
                    f"[MEMORY GC] [ASYNC] Décroissance appliquée sur {affected} faits."
                )
            return affected
        finally:
            await conn.close()
