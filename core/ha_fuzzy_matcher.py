"""
ha_fuzzy_matcher.py — Matching flou d'entités Home Assistant sans LLM.

Workflow :
1. Charger la liste des entités HA via l'API REST (GET /api/states)
2. Extraire les friendly_name + entity_id des domaines pertinents (light, switch, cover, etc.)
3. Scorer la phrase utilisateur contre ces noms avec difflib.SequenceMatcher
4. Si score > seuil → retourner service + entity_id directement (Zero-LLM)
5. Cache TTL=60s pour éviter de surcharger l'API HA à chaque requête

Utilisation :
    matcher = HAFuzzyMatcher(ha_url, ha_token)
    result = await matcher.find_entity("allume le truc du couloir")
    # → {"service": "light.turn_on", "entity_id": "light.couloir", "score": 0.82}

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from core.ha_tls import ha_ssl_context  # [P0-1.5] politique TLS HA centralisée

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Domaines HA éligibles au fuzzy match (commandes physiques uniquement)
HA_ELIGIBLE_DOMAINS = {"light", "switch", "cover", "fan", "climate", "media_player", "input_boolean"}

# Mapping action → services HA par domaine
ACTION_SERVICE_MAP = {
    "light":        {"on": "light.turn_on",    "off": "light.turn_off",   "toggle": "light.toggle"},
    "switch":       {"on": "switch.turn_on",   "off": "switch.turn_off",  "toggle": "switch.toggle"},
    "cover":        {"on": "cover.open_cover", "off": "cover.close_cover","toggle": "cover.toggle_cover"},
    "fan":          {"on": "fan.turn_on",      "off": "fan.turn_off",     "toggle": "fan.toggle"},
    "climate":      {"on": "climate.turn_on",  "off": "climate.turn_off", "toggle": "climate.toggle"},
    "media_player": {"on": "media_player.media_play", "off": "media_player.media_pause", "toggle": "media_player.media_play_pause"},
    "input_boolean":{"on": "input_boolean.turn_on", "off": "input_boolean.turn_off", "toggle": "input_boolean.toggle"},
}

# Mots-clés d'action → intention (on/off/toggle)
ACTION_KEYWORDS_ON  = {"allume", "ouvre", "active", "démarre", "lance", "met", "mets", "enclenche", "monte"}
ACTION_KEYWORDS_OFF = {"éteins", "ferme", "désactive", "arrête", "coupe", "stop", "baisse", "eteins", "etein"}
ACTION_KEYWORDS_TOG = {"bascule", "inverse", "toggle"}

# Seuil de confiance minimum pour un match sans LLM (0.0 à 1.0)
FUZZY_MATCH_THRESHOLD = 0.68        # difflib (fallback) — STT vocal Tab5
FUZZY_MATCH_THRESHOLD_EMB = 0.72   # cosinus embeddings LM Studio
# Si le 2e candidat est à moins de ce delta du meilleur → ambigu, refus
FUZZY_AMBIGUITY_DELTA = 0.08

# Durée de vie du cache des entités en secondes
ENTITY_CACHE_TTL = 60.0

# URL LM Studio pour les embeddings (PC dev par défaut ; override via .env Deck)
LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://${LM_STUDIO_HOST:-192.168.1.x}:1234").rstrip("/")
LM_STUDIO_EMBED_MODEL = "nomic-embed-text"
LM_STUDIO_EMBED_TIMEOUT = 3.0  # secondes


@dataclass
class FuzzyMatchResult:
    """Résultat d'un fuzzy match sur une entité HA."""
    service: str           # Ex: "light.turn_on"
    entity_id: str         # Ex: "light.couloir"
    friendly_name: str     # Ex: "Couloir"
    score: float           # Score de confiance (0.0 à 1.0)
    action: str            # "on" | "off" | "toggle"

    def to_response_text(self) -> str:
        """Génère la phrase de confirmation pour TTS (friendly_name naturel)."""
        from core.vocal_tts_cache import canonical_text_for_ha_action

        cached = canonical_text_for_ha_action(self.entity_id, self.service)
        if cached:
            return cached

        verb_map = {
            "on":     {"light": "allumée", "switch": "activé", "cover": "ouvert",
                       "fan": "allumé", "climate": "allumée", "media_player": "lancé",
                       "input_boolean": "activé"},
            "off":    {"light": "éteinte", "switch": "désactivé", "cover": "fermé",
                       "fan": "arrêté", "climate": "éteinte", "media_player": "en pause",
                       "input_boolean": "désactivé"},
            "toggle": {"light": "basculée", "switch": "basculé", "cover": "basculé",
                       "fan": "basculé", "climate": "basculée", "media_player": "basculé",
                       "input_boolean": "basculé"},
        }
        domain = self.entity_id.split(".")[0]
        verbe = verb_map.get(self.action, {}).get(domain, "exécuté")
        name = (self.friendly_name or "").strip()
        if domain == "light":
            if name:
                return f"Lumière {name.lower()} {verbe}."
            return f"Lumière {verbe}."
        if name:
            return f"{name} {verbe}."
        return "Commande exécutée."


class HAFuzzyMatcher:
    """
    Matcher flou d'entités Home Assistant.

    Thread-safe (asyncio lock sur le cache). Instance unique recommandée
    au niveau du moteur (singleton).
    """

    def __init__(self, ha_url: str, ha_token: str):
        """
        Args:
            ha_url: URL de l'instance HA (ex: https://${HA_HOST:-192.168.1.x}:8123)
            ha_token: Long-lived access token HA
        """
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token

        # Cache {entity_id: {friendly_name, domain, ...}}
        self._entity_cache: dict = {}
        self._cache_ts: float = 0.0
        self._cache_lock = asyncio.Lock()

        # Cache des embeddings pré-calculés par entity_id
        self._entity_embeddings: dict[str, list] = {}   # entity_id → vecteur
        self._embeddings_ts: float = 0.0                # timestamp de dernière mise à jour
        self._lm_studio_online: bool | None = None   # None = inconnu, True/False après premier appel

        # [P0-1.5] Contexte TLS HA centralisé (vérifié par défaut ; opt-out explicite).
        self._ssl_ctx = ha_ssl_context()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }

    def _detect_action(self, prompt: str) -> str:
        """
        Détecte l'intention d'action depuis la phrase utilisateur.
        Retourne 'on', 'off' ou 'toggle'.
        """
        words = set(self._normalize(prompt).split())
        if words & ACTION_KEYWORDS_OFF:
            return "off"
        if words & ACTION_KEYWORDS_TOG:
            return "toggle"
        return "on"  # Défaut : allumer

    def _normalize(self, text: str) -> str:
        """Normalise le texte : minuscules, sans accents, sans ponctuation."""
        text = text.lower()
        # Remplacement des accents courants
        for src, dst in [
            ("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"),
            ("à", "a"), ("â", "a"), ("ä", "a"),
            ("î", "i"), ("ï", "i"),
            ("ô", "o"), ("ö", "o"),
            ("ù", "u"), ("û", "u"), ("ü", "u"),
            ("ç", "c"),
        ]:
            text = text.replace(src, dst)
        # Supprimer les mots vides domotiques
        stop_words = {
            "le", "la", "les", "l", "de", "du", "des", "un", "une",
            "allume", "eteins", "ouvre", "ferme", "active", "desactive",
            "mets", "met", "s'il", "vous", "plait", "stp", "svp",
        }
        words = re.findall(r"[a-z0-9]+", text)
        filtered = [w for w in words if w not in stop_words]
        return " ".join(filtered) if filtered else text

    async def _embed(self, text: str) -> list | None:
        """
        Calcule l'embedding vectoriel d'un texte via LM Studio.
        Retourne None si LM Studio est hors ligne (fallback vers difflib).
        """
        if not _NUMPY_AVAILABLE:
            return None
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=LM_STUDIO_EMBED_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    f"{LM_STUDIO_URL}/v1/embeddings",
                    json={"input": text, "model": LM_STUDIO_EMBED_MODEL}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        embedding = data["data"][0]["embedding"]
                        self._lm_studio_online = True
                        return embedding
                    logger.debug(f"[HA FUZZY] LM Studio HTTP {resp.status}")
                    self._lm_studio_online = False
                    return None
        except Exception as e:
            logger.debug(f"[HA FUZZY] LM Studio offline ou timeout : {e}")
            self._lm_studio_online = False
            return None

    @staticmethod
    def _cosine(a: list, b: list) -> float:
        """Similarité cosinus entre deux vecteurs numpy."""
        if not _NUMPY_AVAILABLE:
            return 0.0
        va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(np.dot(va, vb) / norm) if norm > 1e-9 else 0.0

    async def _ensure_entity_embeddings(self, entities: dict) -> None:
        """
        Pré-calcule les embeddings de toutes les entités HA.
        Invalide automatiquement quand le cache entités est renouvelé.
        """
        if not _NUMPY_AVAILABLE:
            return
        # Si déjà à jour avec le cache entités actuel
        if self._embeddings_ts >= self._cache_ts and self._entity_embeddings:
            return
        logger.info(f"[HA FUZZY] Calcul embeddings pour {len(entities)} entités...")
        new_embs = {}
        for entity_id, info in entities.items():
            name = info["friendly_name"]
            emb = await self._embed(name)
            if emb:
                new_embs[entity_id] = emb
            else:
                # LM Studio offline dès le premier échec → arrêter
                break
        if new_embs:
            self._entity_embeddings = new_embs
            self._embeddings_ts = time.monotonic()
            logger.info(f"[HA FUZZY] {len(new_embs)} embeddings calculés (LM Studio online)")
        else:
            logger.warning("[HA FUZZY] ⚠️  LM Studio hors ligne → fallback difflib actif")

    async def _load_entities(self) -> dict:
        """
        Charge la liste des entités HA depuis l'API REST.
        Utilise le cache si encore valide (TTL=60s).
        """
        async with self._cache_lock:
            if (self._entity_cache
                    and (time.monotonic() - self._cache_ts) < ENTITY_CACHE_TTL):
                logger.debug(f"[HA FUZZY] Cache entités valide ({len(self._entity_cache)} entités)")
                return self._entity_cache

            logger.info("[HA FUZZY] Rechargement du cache entités HA...")
            try:
                import aiohttp
                connector = aiohttp.TCPConnector(ssl=self._ssl_ctx)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(
                        f"{self.ha_url}/api/states",
                        headers=self._headers(),
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"[HA FUZZY] Erreur API HA ({resp.status})")
                            return self._entity_cache  # Garder l'ancien cache

                        states = await resp.json()

                # Filtrer et indexer les entités éligibles
                cache = {}
                for state in states:
                    entity_id = state.get("entity_id", "")
                    domain = entity_id.split(".")[0]
                    if domain not in HA_ELIGIBLE_DOMAINS:
                        continue
                    attrs = state.get("attributes", {})
                    friendly_name = attrs.get("friendly_name", entity_id.split(".")[-1])
                    cache[entity_id] = {
                        "entity_id": entity_id,
                        "domain": domain,
                        "friendly_name": friendly_name,
                        "normalized_name": self._normalize(friendly_name),
                        "normalized_id": self._normalize(entity_id.split(".")[-1]),
                    }

                self._entity_cache = cache
                self._cache_ts = time.monotonic()
                # Invalider les embeddings quand les entités changent
                self._embeddings_ts = 0.0
                self._entity_embeddings.clear()
                logger.info(f"[HA FUZZY] {len(cache)} entités indexées "
                            f"({', '.join(HA_ELIGIBLE_DOMAINS)})")
                return cache

            except Exception as e:
                logger.warning(f"[HA FUZZY] Erreur chargement entités : {e}")
                return self._entity_cache  # Retourner l'ancien cache si dispo

    def _score_entity(self, normalized_prompt: str, entity_info: dict) -> float:
        """
        Score de correspondance entre le prompt et une entité.
        Combine le score sur le friendly_name ET sur l'entity_id slug.
        """
        # Score sur le friendly_name normalisé
        score_name = SequenceMatcher(
            None, normalized_prompt, entity_info["normalized_name"]
        ).ratio()

        # Score sur le slug de l'entity_id (ex: "h6008_2" → "h6008 2")
        score_id = SequenceMatcher(
            None, normalized_prompt, entity_info["normalized_id"]
        ).ratio()

        # Bonus si le nom de l'entité est entièrement contenu dans le prompt
        bonus = 0.15 if (entity_info["normalized_name"]
                         and entity_info["normalized_name"] in normalized_prompt) else 0.0

        return min(1.0, max(score_name, score_id) + bonus)

    @staticmethod
    def _is_ambiguous(scores: list[float], threshold: float) -> bool:
        """True si deux entités sont trop proches en score (risque de mauvaise action)."""
        if len(scores) < 2:
            return False
        ordered = sorted(scores, reverse=True)
        if ordered[0] < threshold:
            return False
        return (ordered[0] - ordered[1]) < FUZZY_AMBIGUITY_DELTA

    async def find_entity(self, prompt: str) -> FuzzyMatchResult | None:
        """
        Point d'entrée principal : cherche l'entité HA la plus proche du prompt.

        Args:
            prompt: La phrase utilisateur brute (ex: "allume le truc du couloir")

        Returns:
            FuzzyMatchResult si score >= FUZZY_MATCH_THRESHOLD, None sinon.
        """
        if not self.ha_token:
            logger.warning("[HA FUZZY] Token HA manquant, fuzzy match désactivé")
            return None

        entities = await self._load_entities()
        if not entities:
            return None

        action = self._detect_action(prompt)
        normalized = self._normalize(prompt)

        # ── Tentative d'abord avec les embeddings LM Studio ──
        best_score = 0.0
        best_entity = None
        used_embeddings = False

        if _NUMPY_AVAILABLE and self._lm_studio_online is not False:
            await self._ensure_entity_embeddings(entities)
            if self._entity_embeddings:
                query_emb = await self._embed(prompt)
                if query_emb:
                    used_embeddings = True
                    emb_scores: list[float] = []
                    emb_best_entity = None
                    emb_best_score = 0.0
                    for entity_id, emb in self._entity_embeddings.items():
                        score = self._cosine(query_emb, emb)
                        emb_scores.append(score)
                        if score > emb_best_score:
                            emb_best_score = score
                            emb_best_entity = entities.get(entity_id)

                    threshold = FUZZY_MATCH_THRESHOLD_EMB
                    if self._is_ambiguous(emb_scores, threshold):
                        logger.info(
                            f"[HA FUZZY] Match ambigu (embeddings) pour : '{prompt}' "
                            f"(meilleur={emb_best_score:.3f})"
                        )
                        return None
                    best_score = emb_best_score
                    best_entity = emb_best_entity
                    if best_score >= threshold and best_entity:
                        domain = best_entity["domain"]
                        service = ACTION_SERVICE_MAP.get(domain, {}).get(
                            action, f"{domain}.turn_{action}"
                        )
                        result = FuzzyMatchResult(
                            service=service,
                            entity_id=best_entity["entity_id"],
                            friendly_name=best_entity["friendly_name"],
                            score=best_score,
                            action=action,
                        )
                        logger.info(
                            f"[HA FUZZY] ✨ Embeddings match : '{prompt}' → {result.entity_id} "
                            f"(cosinus={best_score:.3f}, action={action})"
                        )
                        return result

        # ── Fallback : difflib si embeddings échoués ou LM Studio offline ──
        if used_embeddings:
            logger.debug("[HA FUZZY] Cosinus < seuil, bascule sur difflib")
        best_score = 0.0
        best_entity = None
        difflib_scores: list[float] = []
        for entity_info in entities.values():
            score = self._score_entity(normalized, entity_info)
            difflib_scores.append(score)
            if score > best_score:
                best_score = score
                best_entity = entity_info

        if self._is_ambiguous(difflib_scores, FUZZY_MATCH_THRESHOLD):
            logger.info(
                f"[HA FUZZY] Match ambigu (difflib) pour : '{prompt}' "
                f"(meilleur={best_score:.2f})"
            )
            return None

        if best_score < FUZZY_MATCH_THRESHOLD or best_entity is None:
            method = "emb+difflib" if used_embeddings else "difflib"
            logger.info(
                f"[HA FUZZY] Aucun match suffisant ({method}, meilleur={best_score:.2f} < "
                f"{FUZZY_MATCH_THRESHOLD}) pour : '{prompt}'"
            )
            return None

        domain = best_entity["domain"]
        service = ACTION_SERVICE_MAP.get(domain, {}).get(action, f"{domain}.turn_{action}")

        result = FuzzyMatchResult(
            service=service,
            entity_id=best_entity["entity_id"],
            friendly_name=best_entity["friendly_name"],
            score=best_score,
            action=action,
        )
        logger.info(
            f"[HA FUZZY] ✅ Match difflib : '{prompt}' → {result.entity_id} "
            f"(score={best_score:.2f}, action={action})"
        )
        return result

    def invalidate_cache(self):
        """Invalide le cache entités (utile après ajout/suppression d'entités HA)."""
        self._cache_ts = 0.0
        logger.info("[HA FUZZY] Cache entités invalidé manuellement.")


# ──────────────────────────────────────────────────────────────────
# Singleton global (chargé une fois au démarrage du moteur)
# ──────────────────────────────────────────────────────────────────

_fuzzy_matcher_instance: HAFuzzyMatcher | None = None


def get_fuzzy_matcher() -> HAFuzzyMatcher | None:
    """Retourne l'instance singleton du fuzzy matcher (si configurée)."""
    return _fuzzy_matcher_instance


def init_fuzzy_matcher(ha_url: str, ha_token: str) -> HAFuzzyMatcher:
    """Initialise le singleton avec les credentials HA. À appeler au startup."""
    global _fuzzy_matcher_instance
    _fuzzy_matcher_instance = HAFuzzyMatcher(ha_url=ha_url, ha_token=ha_token)
    logger.info(f"[HA FUZZY] Matcher initialisé → {ha_url}")
    return _fuzzy_matcher_instance
