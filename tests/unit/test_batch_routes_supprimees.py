"""Vérifie le retrait des routes batch fantômes de l'API (#T249).

Les routes /api/batch/* (submit, status, results, list) dépendaient de
core.batch_processor, un module inexistant : chaque appel répondait 501
« Module batch non disponible » tout en apparaissant dans le schéma OpenAPI.
Elles sont désormais retirées — un client ne doit plus voir que des 404 (ou
le 405 global du serveur pour un POST inconnu, cf. infra), et le schéma
OpenAPI ne doit plus les annoncer.

Note : le Mount StaticFiles racine (IHM v2 servie sous /) n'accepte que
GET/HEAD ; tout POST vers un chemin inconnu du serveur répond donc 405
(comportement global, vérifié identique sur /api/chemin-inconnu-xyz). Le
critère de ce test est « plus de 501 » : aucune route batch ne subsiste.

Le TestClient est utilisé hors context manager : le lifespan de gui_server
(HA, MQTT, boucles de fond) ne s'exécute alors pas, conformément au garde-fou
réseau de tests/unit/conftest.py.
"""

import os

from fastapi.testclient import TestClient

from gui_server import app

_ROUTES_BATCH = [
    "/api/batch/submit",
    "/api/batch/status/job-test",
    "/api/batch/results/job-test",
    "/api/batch/list",
]


def _client():
    """Client de test authentifié (clé fixée par tests/conftest.py)."""
    return TestClient(app, headers={"Authorization": f"Bearer {os.environ['MOTEUR_API_KEY']}"})


def test_schema_openapi_sans_route_batch():
    """Le schéma OpenAPI ne contient plus aucun chemin /api/batch/*."""
    schema = _client().get("/openapi.json").json()
    chemins = schema["paths"]
    assert not any(p.startswith("/api/batch/") for p in chemins)


def test_route_batch_get_renvoie_404():
    """GET sur chaque route batch retirée répond 404 (route inexistante)."""
    client = _client()
    for chemin in _ROUTES_BATCH:
        reponse = client.get(chemin)
        assert reponse.status_code == 404, f"{chemin} devrait répondre 404, reçu {reponse.status_code}"


def test_submit_post_ne_repond_plus_501():
    """POST /api/batch/submit ne répond plus 501 « Module batch non disponible ».

    Statut attendu : 405 — le 405 global du Mount StaticFiles racine pour
    tout POST inconnu (comportement du serveur, pas une route batch).
    """
    reponse = _client().post("/api/batch/submit", json={"prompts": ["test"]})
    assert reponse.status_code != 501
    # Identique à un chemin inconnu quelconque : aucune logique batch ne subsiste.
    assert reponse.status_code == _client().post("/api/chemin-inconnu-xyz").status_code
