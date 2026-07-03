"""
memory/episodes.py — Mémoire épisodique locale pour le tab5-engine.
Gère à la fois les épisodes stockés sous forme de fichiers JSON (EpisodeStore)
et les fonctions d'historique de sessions en base de données SQLite (MemoryDB).
"""

import os
import re
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Dossier de stockage des épisodes (moteur_agents/memory/episodes/)
_DEFAULT_EPISODES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "episodes"
)

# Durée de rétention des épisodes en jours
EPISODE_TTL_DAYS = 30
# Épisodes compressés (résumés par DreamerAgent) : rétention plus longue
EPISODE_COMPRESSED_TTL_DAYS = 90


class EpisodeStore:
    """
    Stockage et récupération des épisodes (résumés structurés des sessions passées).
    Chaque épisode est un fichier JSON dans memory/episodes/.
    """

    def __init__(self, episodes_dir: str = _DEFAULT_EPISODES_DIR):
        self.episodes_dir = episodes_dir
        os.makedirs(self.episodes_dir, exist_ok=True)
        # Cache en mémoire des épisodes (chargé au premier accès)
        self._cache: Optional[List[dict]] = None
        # Stopwords français simplifiés pour le scoring
        self._stopwords = {
            "le", "la", "les", "un", "une", "des", "ce", "cet", "cette", "ces",
            "de", "du", "d", "l", "en", "et", "ou", "mais", "donc", "ni", "car",
            "a", "à", "dans", "par", "pour", "sur", "avec", "sans", "sous",
            "qui", "que", "quoi", "dont", "où", "est", "sont", "être", "avoir",
            "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
        }
        logger.info(f"[EPISODES] Dossier épisodes : {self.episodes_dir}")

    def _tokenize(self, text: str) -> List[str]:
        """Découpe un texte en mots, filtre les stopwords."""
        words = re.findall(r'[a-zA-Z0-9_\-àâéèêëîïôöùûüç]+', text.lower())
        return [w for w in words if w not in self._stopwords and len(w) > 1]

    def _extract_tags(self, text: str) -> List[str]:
        """Extrait des tags pertinents à partir d'un texte (mots significatifs)."""
        tokens = self._tokenize(text)
        # Garder les mots qui apparaissent au moins une fois et sont assez longs
        return list(set(t for t in tokens if len(t) >= 3))[:20]

    def save_episode(
        self,
        session_id: str,
        objective: str,
        result_summary: str,
        errors: List[str] = None,
        lessons: List[str] = None,
        entities_touched: Dict[str, str] = None,
        total_tokens: int = 0,
        total_cost_usd: float = 0.0,
        execution_phase: str = "completed",
        is_compressed: bool = False,   # True = résumé DreamerAgent (TTL 90j)
    ) -> str:
        """
        Persiste un épisode structuré en JSON après une exécution.
        Retourne le chemin du fichier sauvegardé.
        """
        now = datetime.now()
        episode = {
            "session_id": session_id,
            "timestamp": now.isoformat(),
            "created_at": now.isoformat(),  # Pour le TTL
            "date": now.strftime("%Y-%m-%d"),
            "objective": objective,
            "result_summary": result_summary[:1000] if result_summary else "",
            "errors": errors or [],
            "lessons_learned": lessons or [],
            "entities_touched": entities_touched or {},
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost_usd, 6),
            "execution_phase": execution_phase,
            "is_compressed": is_compressed,  # Flag compression DreamerAgent
            "tags": self._extract_tags(objective + " " + (result_summary or ""))
        }

        # Nom de fichier : YYYY-MM-DD_HHmmss_{session_id_court}.json
        safe_id = session_id[:16].replace(":", "_").replace("/", "_")
        filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{safe_id}.json"
        filepath = os.path.join(self.episodes_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(episode, f, indent=2, ensure_ascii=False)
            logger.info(
                f"[EPISODES] Épisode sauvegardé : {filename} "
                f"(objectif: '{objective[:60]}...', tags: {len(episode['tags'])})"
            )
            # Invalider le cache
            self._cache = None
            return filepath
        except Exception as e:
            logger.error(f"[EPISODES] Échec de sauvegarde : {e}")
            return ""

    def _load_all_episodes(self, include_expired: bool = False) -> List[dict]:
        """
        Charge tous les épisodes depuis le dossier (avec cache).
        Filtre automatiquement les épisodes expirés (TTL 30/90j).
        """
        if self._cache is not None:
            return self._cache

        episodes = []
        if not os.path.exists(self.episodes_dir):
            return episodes

        now = datetime.now()
        cutoff_normal = now - timedelta(days=EPISODE_TTL_DAYS)
        cutoff_compressed = now - timedelta(days=EPISODE_COMPRESSED_TTL_DAYS)
        expired_count = 0

        for filename in sorted(os.listdir(self.episodes_dir), reverse=True):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(self.episodes_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    episode = json.load(f)

                # Filtrage TTL
                if not include_expired:
                    created_str = episode.get("created_at") or episode.get("timestamp", "")
                    if created_str:
                        try:
                            created_dt = datetime.fromisoformat(created_str[:19])
                            is_compressed = episode.get("is_compressed", False)
                            cutoff = cutoff_compressed if is_compressed else cutoff_normal
                            if created_dt < cutoff:
                                expired_count += 1
                                continue  # Épisode expiré → ignoré
                        except (ValueError, TypeError):
                            pass  # Timestamp invalide → inclure quand même

                episodes.append(episode)
            except Exception as e:
                logger.warning(f"[EPISODES] Impossible de lire {filename} : {e}")

        self._cache = episodes
        logger.info(
            f"[EPISODES] {len(episodes)} épisode(s) chargé(s) "
            f"({expired_count} expiré(s) ignorés, TTL={EPISODE_TTL_DAYS}j)"
        )
        return episodes

    def purge_old_episodes(self, dry_run: bool = False) -> dict:
        """
        Supprime les épisodes expirés du disque.
        Appelé par DreamerAgent lors de la consolidation nocturne.

        Args:
            dry_run: Si True, rapporte sans supprimer (audit).

        Returns:
            Dict avec stats : {"deleted": N, "kept": M, "freed_bytes": X}
        """
        if not os.path.exists(self.episodes_dir):
            return {"deleted": 0, "kept": 0, "freed_bytes": 0}

        now = datetime.now()
        cutoff_normal = now - timedelta(days=EPISODE_TTL_DAYS)
        cutoff_compressed = now - timedelta(days=EPISODE_COMPRESSED_TTL_DAYS)

        deleted = 0
        kept = 0
        freed_bytes = 0

        for filename in os.listdir(self.episodes_dir):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(self.episodes_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    episode = json.load(f)

                created_str = episode.get("created_at") or episode.get("timestamp", "")
                if not created_str:
                    kept += 1
                    continue

                created_dt = datetime.fromisoformat(created_str[:19])
                is_compressed = episode.get("is_compressed", False)
                cutoff = cutoff_compressed if is_compressed else cutoff_normal

                if created_dt < cutoff:
                    size = os.path.getsize(filepath)
                    if not dry_run:
                        os.remove(filepath)
                        logger.debug(f"[EPISODES] Supprimé : {filename} ({size} octets)")
                    deleted += 1
                    freed_bytes += size
                else:
                    kept += 1

            except Exception as e:
                logger.warning(f"[EPISODES] Erreur purge {filename} : {e}")
                kept += 1

        self._cache = None  # Invalider le cache
        action = "[DRY-RUN]" if dry_run else ""
        logger.info(
            f"[EPISODES] Purge {action} : {deleted} supprimé(s) "
            f"({freed_bytes/1024:.1f} KB libérés), {kept} conservé(s)"
        )
        return {"deleted": deleted, "kept": kept, "freed_bytes": freed_bytes}

    def query_relevant_episodes(self, current_objective: str, max_results: int = 3) -> str:
        """
        Recherche les épisodes passés pertinents pour la tâche courante.
        Utilise un scoring TF-IDF simplifié sur les tags + objectifs.
        Retourne un texte formaté prêt à être injecté dans le contexte du prompt.
        """
        episodes = self._load_all_episodes()
        if not episodes:
            return ""

        query_tokens = set(self._tokenize(current_objective))
        if not query_tokens:
            return ""

        # Calcul du score de pertinence pour chaque épisode
        scored_episodes = []
        for ep in episodes:
            ep_tokens = set(ep.get("tags", []))
            # Ajouter les tokens de l'objectif de l'épisode
            ep_tokens.update(self._tokenize(ep.get("objective", "")))
            # Score = nombre de tokens communs (Jaccard simplifié)
            if not ep_tokens:
                continue
            intersection = query_tokens & ep_tokens
            union = query_tokens | ep_tokens
            score = len(intersection) / len(union) if union else 0.0
            if score > 0.05:  # Seuil minimal de pertinence
                scored_episodes.append((score, ep))

        if not scored_episodes:
            return ""

        # Trier par pertinence décroissante
        scored_episodes.sort(key=lambda x: x[0], reverse=True)
        top_episodes = scored_episodes[:max_results]

        # Formater pour injection dans le prompt
        parts = ["*** MÉMOIRE ÉPISODIQUE (sessions passées pertinentes) ***"]
        for score, ep in top_episodes:
            parts.append(
                f"\n📋 Session du {ep.get('date', '?')} (pertinence: {score*100:.0f}%)\n"
                f"   Objectif : {ep.get('objective', '?')[:200]}\n"
                f"   Résultat : {ep.get('result_summary', '?')[:200]}"
            )
            if ep.get("errors"):
                parts.append(f"   ⚠️ Erreurs : {'; '.join(ep['errors'][:3])}")
            if ep.get("lessons_learned"):
                parts.append(f"   💡 Leçons : {'; '.join(ep['lessons_learned'][:3])}")
            if ep.get("entities_touched"):
                entities_str = ", ".join(f"{k}={v}" for k, v in list(ep["entities_touched"].items())[:5])
                parts.append(f"   🏠 Entités : {entities_str}")

        return "\n".join(parts)

    def get_recent_episodes(self, n: int = 5) -> List[dict]:
        """Retourne les N derniers épisodes chronologiquement."""
        episodes = self._load_all_episodes()
        return episodes[:n]

    def get_episode_count(self) -> int:
        """Retourne le nombre total d'épisodes stockés."""
        return len(self._load_all_episodes())


# ──────────────────────────────────────────────────────────────
# Fonctions d'historique de sessions en base de données SQLite
# ──────────────────────────────────────────────────────────────

def upsert_episode(db, session_date: str, session_folder: str,
                   summary: str, category: str = "general",
                   tags: str = "", source_file: str = "") -> int:
    """Insère ou met à jour un épisode de session dans la base SQLite."""
    now = time.time()
    with db._write_lock:
        conn = db._get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM episodes WHERE session_folder = ?",
                (session_folder,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE episodes SET summary = ?, category = ?, tags = ?, "
                    "source_file = ? WHERE id = ?",
                    (summary, category, tags, source_file, existing["id"])
                )
                conn.commit()
                return existing["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO episodes (session_date, session_folder, summary, "
                    "category, tags, source_file, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_date, session_folder, summary, category, tags, source_file, now)
                )
                conn.commit()
                return cursor.lastrowid
        finally:
            conn.close()


def search_episodes(db, query: str, limit: int = 10) -> List[Dict]:
    """Recherche dans les résumés de sessions stockés dans la base SQLite."""
    conn = db._get_conn()
    try:
        words = query.lower().split()
        conditions = []
        params = []

        for word in words:
            conditions.append("(LOWER(summary) LIKE ? OR LOWER(tags) LIKE ?)")
            params.extend([f"%{word}%", f"%{word}%"])

        sql = f"SELECT * FROM episodes WHERE {' AND '.join(conditions)} "
        sql += "ORDER BY session_date DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def upsert_episode_async(db, session_date: str, session_folder: str,
                                summary: str, category: str = "general",
                                tags: str = "", source_file: str = "") -> int:
    """Enregistre asynchronement un épisode de session dans la base SQLite."""
    now = time.time()
    async with db._write_lock_async:
        conn = await db._get_conn_async()
        try:
            cursor = await conn.execute(
                "SELECT id FROM episodes WHERE session_folder = ?",
                (session_folder,)
            )
            existing = await cursor.fetchone()
            if existing:
                await conn.execute(
                    "UPDATE episodes SET summary = ?, category = ?, tags = ?, "
                    "source_file = ? WHERE id = ?",
                    (summary, category, tags, source_file, existing["id"])
                )
                await conn.commit()
                return existing["id"]
            else:
                cursor = await conn.execute(
                    "INSERT INTO episodes (session_date, session_folder, summary, "
                    "category, tags, source_file, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_date, session_folder, summary, category, tags, source_file, now)
                )
                await conn.commit()
                return cursor.lastrowid
        finally:
            await conn.close()
