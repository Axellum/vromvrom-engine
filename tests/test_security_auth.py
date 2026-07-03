# -*- coding: utf-8 -*-
"""
tests/test_security_auth.py — Tests de sécurité de CARACTÉRISATION (item 0.3 du
plan de remédiation, cf. docs/plan_remediation_2026-06-18.md).

Ces tests figent le comportement sécurisé ATTENDU *avant* de corriger. Ils sont
volontairement ROUGES tant que la Vague 1 (P0 sécurité) n'est pas livrée, et
passeront au VERT au fur et à mesure des correctifs :

  - test_sensitive_api_routes_require_auth  → item 1.1 (auth fail-closed)
  - test_no_shell_true_in_source            → item 1.3 (RCE / injection shell)
  - test_no_insecure_tls_in_source          → item 1.5 (TLS CERT_NONE)
  - test_no_hardcoded_whatsapp_token        → item 1.5 (secret en dur) [déjà vert]
  - test_cors_not_wildcard_with_credentials → item 1.2 (CORS) [déjà vert]

Principe : aucune requête destructrice n'est émise. L'auth est vérifiée par
INTROSPECTION de l'arbre de dépendances des routes (pas d'appel réel), et les
items shell/TLS/secret par SCAN STATIQUE du code source (pas d'exécution).
"""

import re
from pathlib import Path

import pytest

_ENGINE_DIR = Path(__file__).resolve().parent.parent

# Répertoires de code de production à auditer (hors tests, caches, backups).
_SOURCE_DIRS = ["core", "tools", "api"]
_SOURCE_FILES = ["gui_server.py", "mcp_server.py", "workspace_mcp.py"]
_EXCLUDE_PARTS = {"__pycache__", "backups_prod", "scratch", "node_modules", ".venv", "llama.cpp"}


def _iter_source_files():
    """Itère sur les fichiers .py de production (hors tests/caches/backups)."""
    for d in _SOURCE_DIRS:
        for p in (_ENGINE_DIR / d).rglob("*.py"):
            if not any(part in _EXCLUDE_PARTS for part in p.parts):
                yield p
    for f in _SOURCE_FILES:
        p = _ENGINE_DIR / f
        if p.exists():
            yield p


def _grep_source(pattern: re.Pattern):
    """Retourne [(chemin_relatif, n°ligne, ligne)] des correspondances dans le code."""
    hits = []
    for p in _iter_source_files():
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                # Ignore les lignes de commentaire (ne représentent pas du code exécuté).
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    hits.append((p.relative_to(_ENGINE_DIR).as_posix(), i, line.strip()))
        except (UnicodeDecodeError, OSError):
            continue
    return hits


# ──────────────────────────────────────────────────────────────────
# 1.1 — Authentification fail-closed sur les routes sensibles
# ──────────────────────────────────────────────────────────────────

# Seules ces routes peuvent rester publiques (webhooks à vérification propre).
# Tout le reste des /api/* doit exiger require_auth (fail-closed).
_PUBLIC_PATHS = {
    "/api/webhook/whatsapp/meta",
    "/api/webhook/whatsapp/twilio",
}


def _route_has_require_auth(route) -> bool:
    """Détecte require_auth dans l'arbre de dépendances d'une route (récursif)."""
    found = []

    def walk(dep):
        call = getattr(dep, "call", None)
        if call is not None and getattr(call, "__name__", "") == "require_auth":
            found.append(True)
        for sub in getattr(dep, "dependencies", []):
            walk(sub)

    walk(route.dependant)
    return bool(found)


def _unprotected_sensitive_routes():
    from gui_server import app
    from fastapi.routing import APIRoute

    unprotected = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        if not r.path.startswith("/api"):
            continue
        if r.path in _PUBLIC_PATHS:
            continue
        if not _route_has_require_auth(r):
            methods = ",".join(sorted(m for m in r.methods if m != "HEAD"))
            unprotected.append(f"{methods} {r.path}")
    return sorted(set(unprotected))


def test_sensitive_api_routes_require_auth():
    """[1.1] Toute route /api sensible doit dépendre de require_auth (fail-closed).

    Garde-fou actif depuis le câblage de require_auth au montage des routers.
    """
    unprotected = _unprotected_sensitive_routes()
    assert not unprotected, (
        f"{len(unprotected)} route(s) /api sensibles sans require_auth "
        f"(fail-closed) :\n  - " + "\n  - ".join(unprotected)
    )


def test_protected_route_returns_401_without_token(monkeypatch):
    """[1.1] Avec MOTEUR_API_KEY configurée, une route protégée renvoie 401 sans token.

    Sûr : le rejet a lieu à la résolution de dépendance, AVANT le handler (aucun
    effet de bord). On cible une route GET de données.
    """
    from fastapi.testclient import TestClient
    from core import auth

    monkeypatch.setenv("MOTEUR_API_KEY", "test-secret-key-0.3")
    auth.invalidate_key_cache()
    try:
        from gui_server import app
        client = TestClient(app)
        resp = client.get("/api/backlog/stats")  # route protégée, sans Authorization
        assert resp.status_code == 401, (
            f"Attendu 401 sans token, obtenu {resp.status_code} : la route n'est pas "
            f"protégée par require_auth (ou MOTEUR_API_KEY non prise en compte)."
        )
    finally:
        auth.invalidate_key_cache()  # évite la fuite du cache vers les autres tests


def test_query_token_rejected_post_t65(monkeypatch):
    """[1.1] Le fallback ?token= est SUPPRIMÉ depuis #T65 (IHM v2 tickets-first).

    La vraie clé ne doit plus transiter en URL. Les SSE/WS utilisent désormais
    des tickets éphémères POST /api/auth/ticket → ?ticket=<ticket>.
    """
    from fastapi.testclient import TestClient
    from core import auth

    monkeypatch.setenv("MOTEUR_API_KEY", "test-secret-key-qp")
    auth.invalidate_key_cache()
    try:
        from gui_server import app
        client = TestClient(app)
        # ?token= ne doit plus fonctionner (retrait du fallback core/auth.py)
        resp = client.get("/api/backlog/stats?token=test-secret-key-qp")
        assert resp.status_code == 401, (
            f"?token= ne devrait plus être accepté après #T65 (status {resp.status_code}). "
            f"Le fallback query-param doit être retiré de require_auth."
        )
    finally:
        auth.invalidate_key_cache()


# ──────────────────────────────────────────────────────────────────
# 1.3 — Aucune exécution shell injectable
# ──────────────────────────────────────────────────────────────────

def test_no_shell_true_in_source():
    """[1.3] Aucun subprocess avec shell=True (vecteur d'injection / RCE)."""
    hits = _grep_source(re.compile(r"shell\s*=\s*True"))
    assert not hits, (
        f"{len(hits)} occurrence(s) de shell=True (remplacer par une liste d'args) :\n  - "
        + "\n  - ".join(f"{f}:{n}  {l}" for f, n, l in hits)
    )


# ──────────────────────────────────────────────────────────────────
# 1.5 — Secrets & TLS
# ──────────────────────────────────────────────────────────────────

def test_no_insecure_tls_in_source():
    """[1.5] Pas de vérification TLS désactivée hors du module audité.

    La politique TLS HA est centralisée dans `core/ha_tls.py` (vérifiée par défaut ;
    CERT_NONE uniquement sur opt-out explicite HA_VERIFY_TLS=false). Ce module unique
    est exclu du scan : c'est le seul endroit autorisé à manipuler la vérif TLS.
    """
    pat = re.compile(r"CERT_NONE|ssl\s*=\s*False|verify\s*=\s*False")
    hits = [(f, n, l) for (f, n, l) in _grep_source(pat) if f != "core/ha_tls.py"]
    assert not hits, (
        f"{len(hits)} désactivation(s) de TLS hors du module audité core/ha_tls.py :\n  - "
        + "\n  - ".join(f"{f}:{n}  {l}" for f, n, l in hits)
    )


def test_no_hardcoded_whatsapp_token():
    """[1.5] Le token de vérification WhatsApp ne doit pas avoir de défaut en dur."""
    hits = _grep_source(re.compile(r"antigravity_secret_token_2026"))
    assert not hits, (
        "Token WhatsApp en dur détecté (doit venir de l'env, fail-closed) :\n  - "
        + "\n  - ".join(f"{f}:{n}" for f, n, _ in hits)
    )


# ──────────────────────────────────────────────────────────────────
# 1.2 — CORS
# ──────────────────────────────────────────────────────────────────

def test_cors_not_wildcard_with_credentials():
    """[1.2] Jamais allow_origins=['*'] ET allow_credentials=True simultanément."""
    from starlette.middleware.cors import CORSMiddleware
    from gui_server import app

    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            kwargs = mw.kwargs
            origins = kwargs.get("allow_origins", [])
            creds = kwargs.get("allow_credentials", False)
            assert not ("*" in origins and creds), (
                "CORS dangereux : allow_origins=['*'] avec allow_credentials=True."
            )
            return
    pytest.skip("CORSMiddleware non monté sur l'app.")
