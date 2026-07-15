"""
core/key_pool.py — Pool de clés API Gemini avec rotation automatique.

[Phase 1] Gère N clés API Free Tier Gemini avec rotation intelligente
sur rate-limit (429). Chaque clé Free Tier a ses propres quotas
indépendants (RPM, TPD), ce qui multiplie la capacité du moteur.

Architecture :
  - Pool de clés Free Tier (rotation round-robin + fallback sur 429)
  - 6 clés Free Tier = sextuple quota (3000 RPD Flash, 9000 RPD Lite)
  - Clé payante séparée (jamais en rotation, utilisée uniquement si explicite)
  - Tracking du nombre de requêtes par clé
  - Cooldown automatique sur les clés rate-limitées

Quotas Free Tier par clé (mai 2026) :
  - gemini-2.5-flash : 10 RPM, 250K TPM, 500 RPD
  - gemini-3.5-flash : 10 RPM, 250K TPM, 500 RPD
  - gemini-3.1-flash-lite : 30 RPM, 1M TPM, 1500 RPD
"""

import os
import time
import logging
from typing import Optional, List, Dict
from threading import RLock

logger = logging.getLogger("core.key_pool")


class GeminiKeyPool:
    """Pool de clés API Gemini avec rotation automatique sur rate-limit.
    
    Usage:
        pool = GeminiKeyPool()
        key = pool.get_free_key()         # Prochaine clé Free Tier disponible
        pool.report_rate_limit(key)        # Signaler un 429 sur cette clé
        key = pool.get_free_key()          # Retourne la suivante automatiquement
        paid = pool.get_paid_key()         # Clé payante (jamais en rotation)
    """
    
    # Durée de cooldown après un 429 (secondes)
    COOLDOWN_SECONDS = 65  # 65s > 60s (fenêtre RPM)
    
    def __init__(self):
        self._lock = RLock()
        
        # Clés Free Tier (rotation round-robin)
        self._free_keys: List[Dict] = []
        self._current_index = 0
        
        # Clé payante (pas de rotation)
        self._paid_key: Optional[str] = None
        self._paid_project: Optional[str] = None
        
        # Charger les clés depuis les variables d'environnement
        self._load_keys()
    
    def _load_keys(self):
        """Charge toutes les clés API depuis les variables d'environnement."""
        # Clés Free Tier (rotation round-robin)
        # [T114] 6 clés = sextuple quota (3000 RPD Flash, 9000 RPD Lite)
        free_key_configs = [
            ("GEMINI_API_KEY", "moteur-ia-free"),
            ("GEMINI_API_KEY_2", "Gemini Project"),
            ("GEMINI_API_KEY_3", "Default Gemini Project"),
            ("GEMINI_API_KEY_4", "home-assistant-aeb75"),
            ("GEMINI_API_KEY_5", "ha-delta"),
            ("GEMINI_API_KEY_6", "Gemini Project 6"),
        ]
        
        for env_var, project_name in free_key_configs:
            key = os.environ.get(env_var, "").strip()
            if key:
                self._free_keys.append({
                    "key": key,
                    "project": project_name,
                    "env_var": env_var,
                    "requests_count": 0,
                    "rate_limited_until": 0.0,  # timestamp de fin de cooldown
                    "total_429s": 0,
                })
        
        # Clé payante (unique, pas de rotation)
        self._paid_key = os.environ.get("GEMINI_PAYANT_API_KEY", "").strip() or None
        self._paid_project = "gen-lang-client-0619520185"
        
        self._paid_backup_key = None  # Plus de backup payant (converti en Free Tier)
        self._paid_backup_project = None
        
        paid_count = sum(1 for k in [self._paid_key, self._paid_backup_key] if k)
        logger.info(
            f"[KeyPool] Initialisé : {len(self._free_keys)} clés Free Tier"
            f" + {paid_count} clé(s) payante(s)"
        )
        for i, kd in enumerate(self._free_keys):
            logger.info(f"  [{i}] {kd['env_var']} → {kd['project']}")
    
    @property
    def free_key_count(self) -> int:
        """Nombre total de clés Free Tier configurées."""
        return len(self._free_keys)
    
    @property
    def has_paid_key(self) -> bool:
        """Indique si une clé payante est disponible."""
        return self._paid_key is not None
    
    def get_free_key(self, allow_cooldown: bool = True) -> Optional[str]:
        """Retourne la prochaine clé Free Tier disponible (pas en cooldown).

        Algorithme :
        1. Essayer la clé courante (round-robin)
        2. Si en cooldown, essayer les suivantes
        3. Si toutes en cooldown :
           - `allow_cooldown=True` (défaut) : retourner celle dont le cooldown
             expire le plus tôt (dernier recours, rétro-compatible).
           - `allow_cooldown=False` : retourner None — évite de rendre une clé
             garantie-429 et laisse l'appelant attendre/escalader (cf. #T62).

        Args:
            allow_cooldown: si False, ne jamais rendre une clé encore en cooldown.

        Returns:
            La clé API, ou None si aucune clé configurée / aucune disponible
            (quand `allow_cooldown=False`).
        """
        if not self._free_keys:
            return None

        with self._lock:
            now = time.time()
            n = len(self._free_keys)

            # 1. Chercher une clé pas en cooldown (round-robin)
            for attempt in range(n):
                idx = (self._current_index + attempt) % n
                kd = self._free_keys[idx]

                if now >= kd["rate_limited_until"]:
                    # Clé disponible
                    self._current_index = (idx + 1) % n  # Avancer pour le prochain appel
                    kd["requests_count"] += 1
                    return kd["key"]

            # 2. Toutes en cooldown
            soonest = min(self._free_keys, key=lambda k: k["rate_limited_until"])
            wait = soonest["rate_limited_until"] - now
            if not allow_cooldown:
                # Ne pas rendre une clé garantie-429 : l'appelant escaladera (#T62)
                logger.warning(
                    f"[KeyPool] ⚠️ Toutes les clés en cooldown. "
                    f"Aucune disponible avant {wait:.0f}s → escalade (None)."
                )
                return None
            # Dernier recours : la clé qui expire le plus tôt (rétro-compatible)
            logger.warning(
                f"[KeyPool] ⚠️ Toutes les clés en cooldown. "
                f"Plus proche disponible dans {wait:.0f}s ({soonest['project']})"
            )
            soonest["requests_count"] += 1
            return soonest["key"]

    def seconds_until_available(self) -> float:
        """Secondes avant qu'une clé Free Tier soit disponible.

        Returns:
            0.0 si au moins une clé est disponible maintenant (ou aucune clé
            configurée) ; sinon le délai avant l'expiration du cooldown le plus court.
        """
        if not self._free_keys:
            return 0.0
        with self._lock:
            now = time.time()
            soonest_until = min(k["rate_limited_until"] for k in self._free_keys)
            return max(0.0, soonest_until - now)
    
    def get_paid_key(self) -> Optional[str]:
        """Retourne la clé payante principale (pas de rotation)."""
        return self._paid_key
    
    def get_paid_backup_key(self) -> Optional[str]:
        """Retourne la clé payante de backup (si la principale est en erreur)."""
        return self._paid_backup_key
    
    def report_rate_limit(self, key: str):
        """Signale un rate-limit (429) sur une clé → cooldown automatique.
        
        Args:
            key: La clé API qui a reçu un 429
        """
        with self._lock:
            for kd in self._free_keys:
                if kd["key"] == key:
                    kd["rate_limited_until"] = time.time() + self.COOLDOWN_SECONDS
                    kd["total_429s"] += 1
                    logger.info(
                        f"[KeyPool] 🔄 Clé {kd['project']} en cooldown "
                        f"({self.COOLDOWN_SECONDS}s). Total 429s: {kd['total_429s']}"
                    )
                    return
    
    def report_success(self, key: str):
        """Signale un succès sur une clé (réinitialise le cooldown si nécessaire)."""
        with self._lock:
            for kd in self._free_keys:
                if kd["key"] == key:
                    # Si la clé était en cooldown mais fonctionne, réinitialiser
                    if kd["rate_limited_until"] > 0:
                        kd["rate_limited_until"] = 0.0
                    return
    
    def get_stats(self) -> dict:
        """Retourne les statistiques du pool de clés."""
        now = time.time()
        with self._lock:
            free_stats = []
            for kd in self._free_keys:
                cooldown_remaining = max(0.0, kd["rate_limited_until"] - now)
                free_stats.append({
                    "project": kd["project"],
                    "env_var": kd["env_var"],
                    "requests_count": kd["requests_count"],
                    "total_429s": kd["total_429s"],
                    "cooldown_remaining_s": round(cooldown_remaining, 0),
                    "available": cooldown_remaining == 0,
                })
            
            return {
                "free_keys": free_stats,
                "free_key_count": len(self._free_keys),
                "has_paid_key": self.has_paid_key,
                "has_paid_backup": self._paid_backup_key is not None,
                "current_index": self._current_index,
                "total_requests": sum(k["requests_count"] for k in self._free_keys),
                "total_429s": sum(k["total_429s"] for k in self._free_keys),
            }


# ── Singleton global ──────────────────────────────────────────

_pool: Optional[GeminiKeyPool] = None

def get_key_pool() -> GeminiKeyPool:
    """Retourne le singleton du pool de clés."""
    global _pool
    if _pool is None:
        _pool = GeminiKeyPool()
    return _pool
