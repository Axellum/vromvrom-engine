"""Tests de `core.version_info` et de son exposition (#T355).

Couvre les trois exigences mécaniques :
1. LE TEST CENTRAL — quand la résolution du commit échoue (git absent, dépôt
   absent, commande en erreur), la réponse porte une valeur EXPLICITEMENT
   indéterminée (`None`), jamais une chaîne qui ressemble à une version.
2. LE TEST JUMEAU — la résolution n'est effectuée qu'une seule fois : deux appels
   consécutifs ne lancent qu'un seul sous-processus.
3. `/healthz` continue de répondre 200 sans authentification et n'ajoute aucun
   champ sensible (au plus le SHA court).

Aucun de ces tests ne lance de vraie commande `git` : `subprocess.run` est mocké.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.version_info as version_info
from api.routes.health import router as health_router


@pytest.fixture(autouse=True)
def _reset_cache():
    """Réinitialise le cache module entre chaque test (indépendance)."""
    with patch.object(version_info, "_resolved", False), patch.object(version_info, "_cached_info", None):
        yield


def _run_git_failure(exception):
    """Mock de subprocess.run qui lève une exception (git absent, timeout…)."""
    def _fake(args, **kwargs):  # noqa: ANN001, ANN202
        raise exception
    return _fake


def _run_git_nonzero():
    """Mock de subprocess.run qui simule une commande git en erreur (rc != 0)."""
    class _Proc:
        returncode = 128
        stdout = "fatal: not a git repository"
    return lambda args, **kwargs: _Proc()  # noqa: ANN001, ANN202


# ── 1. LE TEST CENTRAL : échec ⇒ indéterminé, jamais une valeur valide ─────────

@pytest.mark.parametrize("fake_run", [
    _run_git_failure(FileNotFoundError("git absent")),  # git non installé
    _run_git_failure(TimeoutError("commande en timeout")),  # commande en erreur
    _run_git_nonzero(),  # dépôt absent / commande en erreur (rc != 0)
])
def test_echec_commit_jamais_valeur_valide(fake_run):
    """Quand le commit est indéterminable, la réponse dit None — jamais une chaîne."""
    with patch("core.version_info.subprocess.run", fake_run):
        info = version_info.get_version_info()

    # Valeur EXPLICITEMENT indéterminée (assertion sur la valeur exacte).
    assert info["commit"] is None
    assert info["commit_date"] is None
    # Jamais une chaîne d'apparence valide.
    assert not isinstance(info["commit"], str)
    assert not isinstance(info["commit_date"], str)
    # Un motif lisible est fourni.
    assert isinstance(info["reason"], str) and info["reason"]


# ── 2. LE TEST JUMEAU : une seule résolution / un seul sous-processus ─────────

def test_resolution_unique_entre_deux_appels():
    """Deux appels consécutifs ne lancent qu'un seul sous-processus git."""
    class _Proc:
        returncode = 0
        stdout = "abcdef1"
    calls = []

    def _fake(args, **kwargs):  # noqa: ANN001, ANN202
        calls.append(tuple(args))
        return _Proc()

    with patch("core.version_info.subprocess.run", _fake):
        first = version_info.get_version_info()
        second = version_info.get_version_info()

    # Le cache renvoie le même objet : la résolution n'a pas été refaite.
    assert first is second
    # Deux commandes (rev-parse + show) pour une résolution, et une seule résolution.
    assert len(calls) == 2


def test_get_short_commit_reutilise_le_cache():
    """get_short_commit (pour /healthz) ne lance aucun sous-processus supplémentaire."""
    class _Proc:
        returncode = 0
        stdout = "abcdef1"
    calls = []

    def _fake(args, **kwargs):  # noqa: ANN001, ANN202
        calls.append(tuple(args))
        return _Proc()

    with patch("core.version_info.subprocess.run", _fake):
        version_info.get_version_info()
        short = version_info.get_short_commit()

    assert short == "abcdef1"
    assert len(calls) == 2  # aucune commande git de plus pour le SHA court


# ── 3. /healthz reste public, 200, sans champ sensible ────────────────────────

def test_healthz_public_sans_champ_sensible():
    """/healthz répond 200 sans auth et n'ajoute qu'au plus le SHA court."""
    # Aucune vraie commande git : on force le SHA à None (indéterminé).
    with patch("core.version_info.get_short_commit", return_value=None):
        client = TestClient(_app_with_health())
        r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["service"] == "tab5-engine"
    # Aucun champ sensible ajouté : pas de chemin, de branche, ni de nom de machine.
    for champ in ("commit_date", "reason", "branch", "host", "machine", "path", "python"):
        assert champ not in data


def test_healthz_avec_sha_court_seulement():
    """Quand le SHA est connu, /healthz n'expose QUE le SHA court (rien de plus)."""
    class _Proc:
        returncode = 0
        stdout = "abcdef1"
    with patch("core.version_info.subprocess.run", lambda args, **kwargs: _Proc()):
        client = TestClient(_app_with_health())
        data = client.get("/healthz").json()
    assert data["commit"] == "abcdef1"
    # Et toujours aucun champ sensible.
    for champ in ("commit_date", "reason", "branch", "host", "machine", "path", "python"):
        assert champ not in data


def _app_with_health() -> FastAPI:
    """Mini app FastAPI montant uniquement la sonde publique (sans auth)."""
    app = FastAPI()
    app.include_router(health_router)
    return app


# ── Route authentifiée /api/observability/version (identité complète) ─────────

def test_route_authentifiee_expose_identite_complete():
    """La route authentifiée expose SHA + date (pas seulement le SHA court).

    Le module `api/routes/observability` est monté avec `_AUTH_DEP` dans
    gui_server.py : cette route est donc protégée (test_sensitive_api_routes_require_auth
    le garantit pour toute route /api hors chemins publics).
    """
    from api.routes.observability import router as obs_router

    class _Proc:
        returncode = 0
        stdout = "abcdef1"
    with patch("core.version_info.subprocess.run", lambda args, **kwargs: _Proc()):
        app = FastAPI()
        app.include_router(obs_router)
        data = TestClient(app).get("/api/observability/version").json()

    assert data["commit"] == "abcdef1"
    assert data["commit_date"] == "abcdef1"  # le mock renvoie la même valeur pour les 2 commandes
    assert data["reason"] is None
