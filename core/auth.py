"""
core/auth.py — Authentification optionnelle pour les endpoints du moteur.

Middleware Bearer Token basé sur FastAPI Depends().
Mode de fonctionnement :
- Si MOTEUR_API_KEY est défini dans .env → le token est REQUIS sur les routes protégées
- Si MOTEUR_API_KEY est absent/vide → mode développement, toutes requêtes acceptées (log warning)

Usage dans gui_server.py :
    from core.auth import optional_auth
    @app.post("/api/execute")
    async def execute_chat(body: ExecuteRequestBody, _=Depends(optional_auth)):
        ...

Auteur : Antigravity IDE + Axel
Date : 2026-06-06
"""

import os
import hmac
import time
import hashlib
import secrets
import threading
import logging
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Schéma de sécurité FastAPI (auto_error=False = pas d'erreur automatique si header absent)
_bearer_scheme = HTTPBearer(auto_error=False)

# Cache du token (chargé une fois au démarrage)
_api_key_cache: str = None

# ──────────────────────────────────────────────────────────────────────────
# [HMI v2] Tickets d'accès éphémères pour SSE/WebSocket
# ──────────────────────────────────────────────────────────────────────────
# Les EventSource (SSE) et WebSocket du navigateur ne peuvent PAS porter de
# header Authorization. L'ancien fallback `?token=<MOTEUR_API_KEY>` fait fuir la
# clé dans les logs. Solution : le client (qui détient la clé) échange un Bearer
# contre un ticket à USAGE UNIQUE et courte durée via POST /api/auth/ticket,
# puis ouvre le flux avec `?ticket=<ticket>`. La vraie clé ne transite jamais.
_TICKET_TTL_SECONDS = 60
_tickets: dict[str, float] = {}          # ticket -> timestamp d'expiration
_tickets_lock = threading.Lock()


def issue_ticket(ttl: int = _TICKET_TTL_SECONDS) -> str:
    """Génère un ticket éphémère à usage unique (purge les expirés au passage)."""
    now = time.time()
    ticket = secrets.token_urlsafe(32)
    with _tickets_lock:
        # Purge des tickets expirés (évite l'accumulation mémoire).
        for t in [k for k, exp in _tickets.items() if exp <= now]:
            _tickets.pop(t, None)
        _tickets[ticket] = now + ttl
    return ticket


def verify_and_consume_ticket(ticket: str) -> bool:
    """Valide un ticket et le consomme (usage unique). True si valide et non expiré."""
    if not ticket:
        return False
    now = time.time()
    with _tickets_lock:
        exp = _tickets.pop(ticket, None)  # pop = consommation (usage unique)
    return exp is not None and exp > now


def _get_api_key() -> str:
    """
    Retourne la clé API configurée depuis l'environnement.
    Cherche d'abord dans os.environ, puis dans le fichier .env du moteur.
    Résultat mis en cache après le premier appel.
    """
    global _api_key_cache
    if _api_key_cache is not None:
        return _api_key_cache

    # 1. Variable d'environnement (déjà chargée par python-dotenv au startup)
    key = os.environ.get("MOTEUR_API_KEY", "").strip()

    # 2. Lecture directe du .env si vide (robustesse)
    if not key:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        env_path = os.path.normpath(env_path)
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MOTEUR_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    _api_key_cache = key

    if key:
        logger.info("[AUTH] ✅ MOTEUR_API_KEY configuré — authentification activée.")
    else:
        logger.warning(
            "[AUTH] ⚠️ MOTEUR_API_KEY non configuré — mode développement "
            "(toutes les requêtes acceptées sans token)."
        )

    return key


def _fingerprint(token: str) -> str:
    """
    Empreinte non réversible d'un token, pour corréler des tentatives invalides
    répétées dans les logs sans jamais y faire apparaître le secret en clair.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


async def optional_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str | None:
    """
    Dépendance FastAPI : authentification optionnelle Bearer Token.

    - Si MOTEUR_API_KEY est défini ET que la requête n'a pas de token valide → 403
    - Si MOTEUR_API_KEY est absent → passe sans auth (mode dev)
    - Si token valide → retourne le token

    Args:
        credentials: Credentials extraits du header Authorization: Bearer <token>

    Returns:
        Le token si valide, None si pas de clé configurée.

    Raises:
        HTTPException(403) si le token est absent ou invalide en mode prod.
    """
    required_key = _get_api_key()

    # Mode développement : pas de clé configurée → tout passe
    if not required_key:
        return None

    # Mode production : token obligatoire
    if not credentials:
        logger.warning("[AUTH] ❌ Requête rejetée : header Authorization manquant.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="🔐 Authentification requise. Header: 'Authorization: Bearer <MOTEUR_API_KEY>'",
        )

    # Comparaison à temps constant pour éviter les attaques par timing.
    if not hmac.compare_digest(credentials.credentials, required_key):
        logger.warning(
            f"[AUTH] ❌ Token invalide reçu (empreinte {_fingerprint(credentials.credentials)})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="🔐 Token invalide.",
        )

    return credentials.credentials


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """
    Dépendance FastAPI : authentification Bearer Token OBLIGATOIRE (fail-closed).

    Contrairement à `optional_auth`, cette dépendance refuse l'accès si aucune
    clé n'est configurée (MOTEUR_API_KEY absente) — c'est volontaire : un
    endpoint sensible ne doit JAMAIS être ouvert par défaut.

    ⚠️ DÉPLOIEMENT : définir `MOTEUR_API_KEY` dans le `.env` du moteur, sinon
    toutes les routes protégées renverront 503 (et non un accès libre).

    Returns:
        Le token validé.

    Raises:
        HTTPException(503) si aucune clé n'est configurée côté serveur.
        HTTPException(401) si le token est absent ou invalide.
    """
    required_key = _get_api_key()

    # Fail-closed : pas de clé serveur → refus (jamais d'accès libre).
    if not required_key:
        logger.error(
            "[AUTH] ❌ MOTEUR_API_KEY non configurée : refus fail-closed sur une "
            "route protégée. Définissez MOTEUR_API_KEY dans le .env."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="🔐 Authentification serveur non configurée (MOTEUR_API_KEY).",
        )

    # [HMI v2] Ticket éphémère pour SSE/WS : préféré au token brut en query param
    # (qui fuit dans les logs). Si pas de header Bearer mais un `?ticket=` valide,
    # on authentifie via le ticket (usage unique). Le header reste prioritaire.
    ticket = request.query_params.get("ticket", "")
    if not credentials and ticket:
        if verify_and_consume_ticket(ticket):
            return "ticket"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="🔐 Ticket d'accès invalide ou expiré.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Token via header Bearer (REST). Le ticket éphémère (ci-dessus) gère les connexions SSE/WS.
    token = credentials.credentials if credentials else ""

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="🔐 Authentification requise. Header: 'Authorization: Bearer <MOTEUR_API_KEY>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(token, required_key):
        logger.warning(f"[AUTH] ❌ Token invalide reçu (empreinte {_fingerprint(token)})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="🔐 Token invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def invalidate_key_cache():
    """
    Invalide le cache du token (utile si MOTEUR_API_KEY change à chaud via /api/config).
    """
    global _api_key_cache
    _api_key_cache = None
    logger.info("[AUTH] Cache MOTEUR_API_KEY invalidé.")
