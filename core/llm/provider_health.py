"""
core/llm/provider_health.py — Sonde d'accessibilité d'hôte (fast-path).

Répond à « la machine décroche-t-elle ? » par une ouverture TCP seule, sans
requête HTTP ni inférence. C'est le maillon qui manquait à #T352 : le
disjoncteur (core/llm/circuit_breaker.py) agit après trois échecs, la sonde
agit AVANT le premier.

Journal de la PRODUCTION, 17/08 (après le déploiement de #T352) :
    16:49:57,020  [FAST_PATH] Tentative → ollama_pc
    16:49:57,022  [FALLBACK GATEWAY] Tentative avec le modèle : ollama_pc (CB: CLOSED)
    16:49:59,027  [CIRCUIT BREAKER] Échec détecté sur ollama_pc (1/3) :
                  ConnectTimeoutError(host='192.168.1.10', port=11434, connect timeout=2.0)
    16:49:59,028  [FALLBACK GATEWAY] Échec du modèle ollama_pc […] Backoff 0.6s avant le suivant…
    16:49:59,581  [FAST_PATH] ollama_pc échoué

Soit 2,56 s perdues avant bascule, sur une machine (192.168.1.10) simplement
éteinte. #T352 n'y suffit pas : le disjoncteur n'ouvre qu'après trois échecs
(seuil 3), et le délai de réouverture progressif ne s'applique qu'APRÈS cette
ouverture — le provider est donc réessayé au prix fort à chaque phrase tant que
le PC reste éteint. La sonde, elle, écarte l'hôte muet en ~0,3 s, dès le premier
essai.

Cache en mémoire du processus, par (hôte, port) :
- succès mémorisé TTL_SUCCES_S (300 s) : on ne ressonde pas un hôte qui répond ;
- échec mémorisé TTL_ECHEC_S (60 s) : le TTL court permet au PC d'être repris
  automatiquement dès qu'il se rallume.

Une seule sonde en vol par hôte (verrou par clé) : dix requêtes simultanées ne
lancent pas dix sondes.
"""

import asyncio
import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Constantes nommées (pas de nombres nus dans le code) ────────────────────
# TTL de mémorisation d'un SUCCÈS de sonde (hôte qui décroche), en secondes.
TTL_SUCCES_S: float = 300.0
# TTL de mémorisation d'un ÉCHEC de sonde (hôte muet), en secondes. Court pour
# que le PC soit repris automatiquement dès qu'il se rallume.
TTL_ECHEC_S: float = 60.0
# Délai d'ouverture TCP de la sonde : on ne veut pas payer un connect timeout de
# 2 s (celui du provider) à chaque phrase — 0,3 s suffit à dire « ça décroche ».
TIMEOUT_SONDE_S: float = 0.3

# Cache en mémoire : (hôte, port) -> (expiration monotonic, booléen joignable).
_cache: dict[tuple[str, int], tuple[float, bool]] = {}
# Verrous par clé : une seule sonde en vol par hôte.
_verrous: dict[tuple[str, int], asyncio.Lock] = {}


def hote_et_port(base_url: str) -> tuple[str, int] | None:
    """Extrait (hôte, port) d'une base_url, ou None si illisible.

    None (hôte absent, port absent ou invalide) = « on ne sait pas » : l'appelant
    doit alors tenter le provider comme avant, jamais l'écarter sur un doute.
    """
    try:
        parsed = urlparse(base_url)
        hote = parsed.hostname
        port = parsed.port
        if not hote or not port:
            return None
        return (hote, port)
    except Exception:
        return None


def base_url_provider(provider) -> str | None:
    """Extrait le base_url d'un provider en déballant wrappers et cascades.

    Déballe récursivement un `FallbackProvider` (on lit son premier candidat) et
    les wrappers décorateurs exposant `.provider` (ex. ClaudeInstructionsWrapper)
    jusqu'à atteindre un provider portant un `base_url` en chaîne. Retourne None
    si aucun base_url exploitable (doute → on tente le provider comme avant).

    Le déballage est générique (duck-typing) pour ne dépendre d'aucune classe
    concrète lourde et ne pas créer de cycle d'import.
    """
    vus: set[int] = set()
    while provider is not None and id(provider) not in vus:
        vus.add(id(provider))
        # FallbackProvider : cascade de candidats, on lit le premier.
        sous = getattr(provider, "providers", None)
        if isinstance(sous, list) and sous:
            provider = sous[0][1]
            continue
        base_url = getattr(provider, "base_url", None)
        if isinstance(base_url, str):
            return base_url
        # Wrapper décorateur exposant un provider interne porteur du base_url.
        interne = getattr(provider, "provider", None)
        if interne is not None and hasattr(interne, "base_url"):
            provider = interne
            continue
        return None
    return None


async def _sonder(host: str, port: int, timeout: float) -> bool:
    """Ouvre une connexion TCP vers (host, port) et la referme immédiatement.

    Aucune requête HTTP, aucune inférence : on répond seulement à « la machine
    décroche-t-elle ? ». Toute exception (socket, DNS, boucle asyncio, timeout)
    remonte — `hote_joignable` la convertit en « on ne sait pas ».
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=timeout,
    )
    writer.close()
    try:
        await writer.wait_closed()
    except Exception as _e:
        # La fermeture peut échouer (socket déjà fermé côté pair) : sans
        # conséquence pour la sonde, on l'ignore après l'avoir journalisée.
        logger.debug(f"[PROVIDER_HEALTH] Fermeture de la sonde {host}:{port} : {_e}")
    return True


async def hote_joignable(base_url: str, timeout: float = TIMEOUT_SONDE_S) -> bool:
    """True si l'hôte de base_url « décroche » (port TCP joignable), False sinon.

    Mémorise le résultat par (hôte, port) avec un TTL différent selon l'issue :
    succès 300 s, échec 60 s (constantes nommées). Une seule sonde en vol par
    hôte : les appels simultanés attendent le même verrou puis relisent le cache.

    Toute exception de la sonde (socket, DNS, boucle asyncio, timeout) est
    convertie en False — « on ne sait pas » — jamais en refus bloquant.
    """
    cle = hote_et_port(base_url)
    if cle is None:
        # Hôte/port illisibles : doute → on considère l'hôte joignable pour ne
        # jamais écarter un provider sur une information qu'on ne peut pas lire.
        return True

    maintenant = time.monotonic()
    cache = _cache.get(cle)
    if cache is not None and cache[0] > maintenant:
        return cache[1]

    verrou = _verrous.get(cle)
    if verrou is None:
        verrou = asyncio.Lock()
        _verrous[cle] = verrou

    async with verrou:
        # Re-vérification après acquisition : la sonde a pu être faite entre-temps
        # par une autre coroutine (verrou par clé).
        cache = _cache.get(cle)
        if cache is not None and cache[0] > time.monotonic():
            return cache[1]
        try:
            resultat = await _sonder(cle[0], cle[1], timeout)
        except Exception as _e:
            logger.debug(f"[PROVIDER_HEALTH] Sonde {cle[0]}:{cle[1]} en échec : {_e}")
            resultat = False
        ttl = TTL_SUCCES_S if resultat else TTL_ECHEC_S
        _cache[cle] = (time.monotonic() + ttl, resultat)
        return resultat
