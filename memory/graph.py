"""
memory/graph.py — Gestion du graphe de connaissances (entités, relations, Garbage Collection, liaisons fait-entité).
"""

import time
import json
import logging
import sqlite3
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def upsert_graph_entity(db, name: str, entity_type: str,
                        observations: List[str] = None) -> int:
    """Insère ou met à jour une entité du graphe."""
    now = time.time()
    obs_json = json.dumps(observations or [], ensure_ascii=False)
    with db._write_lock:
        conn = db._get_conn()
        try:
            existing = conn.execute(
                "SELECT id, observations FROM graph_entities WHERE name = ?",
                (name,)
            ).fetchone()

            if existing:
                # Fusionner les observations existantes avec les nouvelles.
                # [P2-3.2] dict.fromkeys (et non set()) pour DÉDUPLIQUER en
                # PRÉSERVANT l'ordre chronologique : le GC garde les N dernières
                # observations (obs[-N:]) en supposant cet ordre.
                existing_obs = json.loads(existing["observations"])
                merged = list(dict.fromkeys(existing_obs + (observations or [])))
                conn.execute(
                    "UPDATE graph_entities SET entity_type = ?, observations = ?, "
                    "updated_at = ? WHERE id = ?",
                    (entity_type, json.dumps(merged, ensure_ascii=False), now, existing["id"])
                )
                conn.commit()
                return existing["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO graph_entities (name, entity_type, observations, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (name, entity_type, obs_json, now, now)
                )
                conn.commit()
                return cursor.lastrowid
        finally:
            conn.close()


def upsert_graph_relation(db, from_entity: str, to_entity: str,
                          relation_type: str) -> int:
    """Insère une relation (ignore si déjà existante)."""
    now = time.time()
    with db._write_lock:
        conn = db._get_conn()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO graph_relations "
                "(from_entity, to_entity, relation_type, created_at) "
                "VALUES (?, ?, ?, ?)",
                (from_entity, to_entity, relation_type, now)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


def search_graph(db, query: str, limit: int = 10) -> Dict[str, Any]:
    """Recherche dans les entités et leurs observations."""
    conn = db._get_conn()
    try:
        words = query.lower().split()
        conditions = []
        params = []

        for word in words:
            conditions.append(
                "(LOWER(name) LIKE ? OR LOWER(entity_type) LIKE ? OR LOWER(observations) LIKE ?)"
            )
            params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])

        entities = conn.execute(
            f"SELECT * FROM graph_entities WHERE {' AND '.join(conditions)} LIMIT ?",
            params + [limit]
        ).fetchall()

        result_entities = []
        entity_names = set()
        for e in entities:
            entity_names.add(e["name"])
            result_entities.append({
                "name": e["name"],
                "entity_type": e["entity_type"],
                "observations": json.loads(e["observations"]),
            })

        # Récupérer les relations connectées
        relations = []
        if entity_names:
            placeholders = ",".join(["?"] * len(entity_names))
            rels = conn.execute(
                f"SELECT * FROM graph_relations WHERE from_entity IN ({placeholders}) "
                f"OR to_entity IN ({placeholders})",
                list(entity_names) * 2
            ).fetchall()
            relations = [dict(r) for r in rels]

        return {"entities": result_entities, "relations": relations}
    finally:
        conn.close()


def get_full_graph(db) -> Dict[str, Any]:
    """Retourne le graphe complet (toutes les entités et relations)."""
    conn = db._get_conn()
    try:
        entities = conn.execute("SELECT * FROM graph_entities ORDER BY name").fetchall()
        relations = conn.execute("SELECT * FROM graph_relations ORDER BY id").fetchall()

        return {
            "entities": [
                {
                    "name": e["name"],
                    "entityType": e["entity_type"],
                    "observations": json.loads(e["observations"]),
                }
                for e in entities
            ],
            "relations": [
                {
                    "from": r["from_entity"],
                    "to": r["to_entity"],
                    "relationType": r["relation_type"],
                }
                for r in relations
            ],
        }
    finally:
        conn.close()


def gc_graph_entities(db, max_observations: int = 15,
                      max_age_days: int = 30) -> Dict[str, int]:
    """Garbage Collection du graphe de connaissances."""
    now = time.time()
    max_age_ts = now - (max_age_days * 24 * 3600)

    summarized = 0
    archived = 0

    with db._write_lock:
        conn = db._get_conn()
        try:
            entities = conn.execute(
                "SELECT id, name, entity_type, observations FROM graph_entities"
            ).fetchall()

            for entity in entities:
                obs = json.loads(entity["observations"])
                if len(obs) > max_observations:
                    kept = obs[-max_observations:]
                    removed_count = len(obs) - max_observations
                    summary_line = f"[GC] {removed_count} observations archivées le {time.strftime('%Y-%m-%d')}"
                    kept.insert(0, summary_line)

                    conn.execute(
                        "UPDATE graph_entities SET observations = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(kept, ensure_ascii=False), now, entity["id"])
                    )
                    summarized += 1
                    logger.info(
                        f"[MEMORY GC] Entité '{entity['name']}' résumée : "
                        f"{len(obs)} → {len(kept)} observations"
                    )

            temp_types = ('event', 'bugfix', 'bug_fix')
            placeholders = ','.join(['?'] * len(temp_types))

            old_entities = conn.execute(
                f"SELECT id, name, entity_type FROM graph_entities "
                f"WHERE entity_type IN ({placeholders}) AND updated_at < ?",
                list(temp_types) + [max_age_ts]
            ).fetchall()

            for entity in old_entities:
                conn.execute(
                    "DELETE FROM graph_relations WHERE from_entity = ? OR to_entity = ?",
                    (entity["name"], entity["name"])
                )
                conn.execute("DELETE FROM graph_entities WHERE id = ?", (entity["id"],))
                archived += 1
                logger.info(
                    f"[MEMORY GC] Entité temporaire archivée : "
                    f"'{entity['name']}' ({entity['entity_type']})"
                )

            conn.commit()

            result = {"summarized": summarized, "archived": archived}
            db.set_sync_metadata("last_gc_run", time.strftime("%Y-%m-%d %H:%M:%S"))
            return result
        finally:
            conn.close()


async def gc_graph_entities_async(db, max_observations: int = 15,
                                  max_age_days: int = 30) -> Dict[str, int]:
    """Garbage Collection asynchrone du graphe de connaissances."""
    now = time.time()
    max_age_ts = now - (max_age_days * 24 * 3600)
    summarized = 0
    archived = 0
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            cursor = await conn.execute(
                "SELECT id, name, entity_type, observations FROM graph_entities"
            )
            entities = await cursor.fetchall()
            for entity in entities:
                obs = json.loads(entity["observations"])
                if len(obs) > max_observations:
                    kept = obs[-max_observations:]
                    removed_count = len(obs) - max_observations
                    summary_line = f"[GC] {removed_count} observations archivées le {time.strftime('%Y-%m-%d')}"
                    kept.insert(0, summary_line)
                    await conn.execute(
                        "UPDATE graph_entities SET observations = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(kept, ensure_ascii=False), now, entity["id"])
                    )
                    summarized += 1

            temp_types = ('event', 'bugfix', 'bug_fix')
            placeholders = ','.join(['?'] * len(temp_types))
            cursor = await conn.execute(
                f"SELECT id, name, entity_type FROM graph_entities "
                f"WHERE entity_type IN ({placeholders}) AND updated_at < ?",
                list(temp_types) + [max_age_ts]
            )
            old_entities = await cursor.fetchall()
            for entity in old_entities:
                await conn.execute(
                    "DELETE FROM graph_relations WHERE from_entity = ? OR to_entity = ?",
                    (entity["name"], entity["name"])
                )
                await conn.execute("DELETE FROM graph_entities WHERE id = ?", (entity["id"],))
                archived += 1

            await conn.commit()

            # Mettre à jour last_gc_run asynchronement
            await conn.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                ("last_gc_run", time.strftime("%Y-%m-%d %H:%M:%S"), now)
            )
            await conn.commit()

            return {"summarized": summarized, "archived": archived}
        finally:
            await conn.close()


async def upsert_graph_entity_async(db, name: str, entity_type: str,
                                     observations: List[str] = None) -> int:
    """Insère ou met à jour asynchronement une entité du graphe."""
    now = time.time()
    obs_json = json.dumps(observations or [], ensure_ascii=False)
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            cursor = await conn.execute(
                "SELECT id, observations FROM graph_entities WHERE name = ?",
                (name,)
            )
            existing = await cursor.fetchone()
            if existing:
                # [P2-3.2] dict.fromkeys : dédup en préservant l'ordre chronologique
                # (cf. version synchrone) — le GC garde les N dernières observations.
                existing_obs = json.loads(existing["observations"])
                merged = list(dict.fromkeys(existing_obs + (observations or [])))
                await conn.execute(
                    "UPDATE graph_entities SET entity_type = ?, observations = ?, "
                    "updated_at = ? WHERE id = ?",
                    (entity_type, json.dumps(merged, ensure_ascii=False), now, existing["id"])
                )
                await conn.commit()
                return existing["id"]
            else:
                cursor = await conn.execute(
                    "INSERT INTO graph_entities (name, entity_type, observations, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (name, entity_type, obs_json, now, now)
                )
                await conn.commit()
                return cursor.lastrowid
        finally:
            await conn.close()


def link_fact_to_entity(db, fact_id: int, entity_name: str) -> bool:
    """Crée une relation entre un fait (leçon) et une entité du graphe (Graph-RAG)."""
    with db._write_lock:
        conn = db._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fact_entity_links (fact_id, entity_name) "
                "VALUES (?, ?)",
                (fact_id, entity_name)
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"[MEMORY DB] Erreur de liaison fait-entité : {e}")
            return False
        finally:
            conn.close()


def get_connected_facts_for_entity(db, entity_name: str, limit: int = 5) -> List[Dict]:
    """Récupère les faits (leçons) liés à une entité du graphe."""
    conn = db._get_conn()
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM facts f
            JOIN fact_entity_links l ON f.id = l.fact_id
            WHERE l.entity_name = ?
            ORDER BY f.relevance_score DESC, f.updated_at DESC
            LIMIT ?
            """,
            (entity_name, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_connected_entities_for_fact(db, fact_id: int) -> List[Dict]:
    """Récupère les entités du graphe liées à un fait."""
    conn = db._get_conn()
    try:
        rows = conn.execute(
            """
            SELECT e.* FROM graph_entities e
            JOIN fact_entity_links l ON e.name = l.entity_name
            WHERE l.fact_id = ?
            ORDER BY e.name
            """,
            (fact_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def link_fact_to_entity_async(db, fact_id: int, entity_name: str) -> bool:
    """Crée asynchronement une relation entre un fait et une entité du graphe."""
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO fact_entity_links (fact_id, entity_name) "
                "VALUES (?, ?)",
                (fact_id, entity_name)
            )
            await conn.commit()
            return True
        except Exception as e:
            logger.error(f"[MEMORY DB] Erreur liaison asynchrone fait-entité : {e}")
            return False
        finally:
            await conn.close()


async def get_connected_facts_for_entity_async(db, entity_name: str, limit: int = 5) -> List[Dict]:
    """Récupère asynchronement les faits liés à une entité."""
    conn = await db._get_conn_async()
    try:
        cursor = await conn.execute(
            """
            SELECT f.* FROM facts f
            JOIN fact_entity_links l ON f.id = l.fact_id
            WHERE l.entity_name = ?
            ORDER BY f.relevance_score DESC, f.updated_at DESC
            LIMIT ?
            """,
            (entity_name, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()
