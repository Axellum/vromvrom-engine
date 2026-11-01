"""
Expurgation des clés d'API dans les erreurs de santé des modèles.

Constat en prod : `GET /api/models-health` renvoyait en clair une clé Google dans
`derniere_erreur` (6 modèles sur 76). Cause : la sonde stockait `str(exc)[:300]`
tel quel, et le message d'une `HTTPError` de `requests` contient l'URL complète
(`...:generateContent?key=<CLÉ>`).

Trois barrières, trois tests :
  1. à l'ÉCRITURE (la base ne contient jamais la clé) ;
  2. à la LECTURE (les lignes déjà stockées avant le correctif ressortent masquées) ;
  3. avant la TRONCATURE (une clé coupée à 300 caractères échapperait au motif).

Les fausses clés sont fabriquées à l'exécution : aucune chaîne ressemblant à un
vrai secret n'est écrite dans le dépôt.
"""

import json
import sqlite3
import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import core.model_health_probe as sonde
import core.models_db as models_db
import core.runtime_db as runtime_db
from core.auth import require_auth

# Fausses clés de forme réaliste (AIza + 35 caractères ; sk- + 24 caractères).
FAUSSE_CLE_GOOGLE = "AIza" + "Sy" + "A" * 33
FAUSSE_CLE_SK = "sk-" + "b" * 24
FAUX_JETON = "j" * 30


def _url_google(cle: str) -> str:
    return (
        "400 Client Error: Bad Request for url: "
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-x:generateContent?key={cle}"
    )


CAS_SECRETS = [
    ("cle_google_dans_url", _url_google(FAUSSE_CLE_GOOGLE), FAUSSE_CLE_GOOGLE),
    ("cle_google_nue", f"API key {FAUSSE_CLE_GOOGLE} not valid", FAUSSE_CLE_GOOGLE),
    ("cle_sk", f"Incorrect API key provided: {FAUSSE_CLE_SK}", FAUSSE_CLE_SK),
    ("param_key_url", f"GET https://api.example.test/v1/x?foo=1&key={FAUX_JETON}", FAUX_JETON),
    ("entete_bearer", f"401 Unauthorized, Authorization: Bearer {FAUX_JETON}", FAUX_JETON),
]


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Base de santé (runtime) et registre temporaires, schéma réel du dépôt."""
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    runtime_db.get_connection().close()  # déclenche la création du schéma
    conn = sqlite3.connect(str(tmp_path / "models_registry.db"))
    models_db._ensure_schema(conn)
    conn.commit()
    conn.close()
    return tmp_path


def _lire_brut(model_id: str) -> str | None:
    """Valeur réellement stockée en base (sans passer par `lire_sante`)."""
    conn = sqlite3.connect(runtime_db.get_db_path())
    try:
        ligne = conn.execute(
            "SELECT derniere_erreur FROM model_health WHERE model_id = ?", (model_id,)
        ).fetchone()
    finally:
        conn.close()
    return ligne[0] if ligne else None


# ── 1. Écriture ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nom,erreur,secret", CAS_SECRETS, ids=[c[0] for c in CAS_SECRETS])
def test_la_base_ne_contient_jamais_la_cle(bases, nom, erreur, secret):
    sonde._ecrire_sante("m-ecriture", derniere_erreur=erreur, echecs_consecutifs=1)
    brut = _lire_brut("m-ecriture")
    assert brut, "l'erreur doit être conservée (masquée), pas supprimée"
    assert secret not in brut
    assert "***" in brut


def test_ecriture_garde_le_contexte_utile(bases):
    """Le diagnostic reste lisible : code HTTP et nom du paramètre masqué."""
    sonde._ecrire_sante("m-contexte", derniere_erreur=_url_google(FAUSSE_CLE_GOOGLE))
    brut = _lire_brut("m-contexte")
    assert "400 Client Error" in brut
    assert "generativelanguage.googleapis.com" in brut
    assert "key=***" in brut


def test_ecriture_sans_erreur_inchangee(bases):
    sonde._ecrire_sante("m-ok", derniere_erreur=None, succes_consecutifs=2)
    assert _lire_brut("m-ok") is None


# ── 3. Avant la troncature ──────────────────────────────────────────────────

class _FauxFournisseur:
    def __init__(self, message: str):
        self._message = message

    async def generate_async(self, **_kw):
        raise RuntimeError(self._message)


class _FauxGateway:
    def __init__(self, message: str):
        self._fournisseur = _FauxFournisseur(message)

    def get_provider(self, _model_id):
        return self._fournisseur


@pytest.mark.asyncio
async def test_tester_modele_masque_avant_de_tronquer():
    # Le remplissage place la clé à cheval sur la limite de 300 caractères :
    # tronquer d'abord laisserait un morceau de clé non reconnu par le motif.
    message = "x" * 285 + " AIza" + "Sy" + "A" * 33
    ok, erreur, _lat = await sonde.tester_modele("m", _FauxGateway(message))
    assert ok is False
    assert "AIzaSyAAAA" not in erreur
    assert len(erreur) <= 300


@pytest.mark.asyncio
async def test_tester_modele_masque_la_cle_dans_l_url():
    ok, erreur, _lat = await sonde.tester_modele(
        "m", _FauxGateway(_url_google(FAUSSE_CLE_GOOGLE))
    )
    assert ok is False
    assert FAUSSE_CLE_GOOGLE not in erreur
    assert "key=***" in erreur


@pytest.mark.asyncio
async def test_cycle_complet_ne_stocke_pas_la_cle(bases, monkeypatch):
    """Du provider à la base : la clé de l'exception n'atteint pas model_health."""
    monkeypatch.setattr("core.models_db.get_active_models", lambda provider_id=None: [{"id": "m-cycle"}])
    monkeypatch.setattr("core.models_db.set_model_status", lambda *a, **k: True)
    monkeypatch.setenv("MODEL_HEALTH_PROBE", "true")
    monkeypatch.setenv("MODEL_HEALTH_AUTO_DISABLE", "false")

    await sonde.executer_cycle(gateway=_FauxGateway(_url_google(FAUSSE_CLE_GOOGLE)))

    brut = _lire_brut("m-cycle")
    assert brut and FAUSSE_CLE_GOOGLE not in brut


# ── 2. Lecture (lignes déjà stockées avant le correctif) ────────────────────

def _inserer_brut(model_id: str, erreur: str) -> None:
    """Simule une ancienne ligne : écriture SQL directe, sans expurgation."""
    conn = runtime_db.get_connection()
    conn.execute(
        "INSERT INTO model_health (model_id, echecs_consecutifs, derniere_erreur) "
        "VALUES (?, 3, ?)",
        (model_id, erreur),
    )
    conn.commit()


def test_lire_sante_masque_une_ancienne_ligne(bases):
    _inserer_brut("ancien", _url_google(FAUSSE_CLE_GOOGLE))
    assert FAUSSE_CLE_GOOGLE in _lire_brut("ancien")  # la fixture de test est bien « sale »
    assert FAUSSE_CLE_GOOGLE not in sonde.lire_sante("ancien")["derniere_erreur"]


@pytest.fixture
def client(bases):
    from api.routes.context import router

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_auth)])
    return TestClient(app)


@pytest.mark.parametrize("nom,erreur,secret", CAS_SECRETS, ids=[c[0] for c in CAS_SECRETS])
def test_route_models_health_masque_une_ancienne_ligne(client, auth_headers, nom, erreur, secret):
    _inserer_brut("ancien-route", erreur)

    r = client.get("/api/models-health", headers=auth_headers)

    assert r.status_code == 200
    # Toute la réponse sérialisée, pas seulement le champ attendu.
    assert secret not in json.dumps(r.json(), ensure_ascii=False)
    ligne = next(m for m in r.json()["modeles"] if m["model_id"] == "ancien-route")
    assert "***" in ligne["derniere_erreur"]


def test_route_models_health_exige_l_authentification(client):
    assert client.get("/api/models-health").status_code == 401


# ── Même fuite sur POST /api/models/{id}/ping ───────────────────────────────

def test_route_ping_masque_la_cle(client, auth_headers, monkeypatch):
    """Le ping renvoyait le message d'exception brut : même défaut que la sonde."""
    import core.llm_gateway as llm_gateway

    monkeypatch.setattr(
        llm_gateway, "LLMGateway", lambda: _FauxGateway(_url_google(FAUSSE_CLE_GOOGLE))
    )

    r = client.post("/api/models/un-modele/ping", headers=auth_headers)

    assert r.status_code == 200
    corps = r.json()
    assert corps["ok"] is False
    assert FAUSSE_CLE_GOOGLE not in json.dumps(corps, ensure_ascii=False)
    assert "key=***" in corps["error"]
