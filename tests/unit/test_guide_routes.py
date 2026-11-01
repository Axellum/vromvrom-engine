"""
Routes de l'écran « Guide » : /api/guide/overview, /api/guide/cascade, /api/mcp/overview.

Ce que ces tests défendent, dans l'ordre d'importance :
  1. VÉRITÉ : un chiffre inconnu vaut `null` (jamais 0), une source illisible produit une
     alerte, et si TOUTES les sources sont hors service la route répond 503 — pas un 200
     de zéros ;
  2. les chiffres affichés sont ceux de la base (fournisseurs, sondes, usage, vocal) et la
     part des sondes de santé est séparée de l'usage réel ;
  3. aucun secret dans les réponses (clé masquée à la lecture, jeton HA jamais recopié) ;
  4. les routes sont rapides (< 300 ms) et ne sortent pas du process (aucun réseau) ;
  5. l'authentification Bearer fail-closed.

Tout vit dans des bases temporaires : aucune base du dépôt n'est lue ni écrite.
"""

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import core.model_health_probe as sonde
import core.models_db as models_db
import core.runtime_db as runtime_db
from api.routes import guide, mcp_vue
from core import sauvegarde
from core.auth import require_auth

# Fausses valeurs fabriquées à l'exécution : aucune chaîne ressemblant à un vrai secret
# n'est écrite dans le dépôt.
FAUSSE_CLE_GOOGLE = "AIza" + "Sy" + "A" * 33
FAUX_JETON_HA = "jeton-ha-" + "z" * 30


# ═══════════════════════════════════════════════════════════════════════════
# Jeu de données
# ═══════════════════════════════════════════════════════════════════════════

def _maintenant_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch, tmp_path):
    """Réglages d'exploitation neutres + dossier de sauvegardes isolé."""
    for nom in (
        "MODEL_HEALTH_PROBE", "MODEL_HEALTH_AUTO_DISABLE", "MODEL_HEALTH_INTERVAL_SECONDS",
        "QUOTA_ALERTE_ENABLED", "MOTEUR_VEILLE_ENABLED", "MOTEUR_VIGIE_ENABLED",
        "TASK_DISCOVERY_ENABLED", "MQTT_HOST", "MOTEUR_SAUVEGARDE_GARDER",
        "HASS_TOKEN", "HA_TOKEN",
    ):
        monkeypatch.delenv(nom, raising=False)
    monkeypatch.setenv("HASS_TOKEN", FAUX_JETON_HA)
    monkeypatch.setenv("MOTEUR_SAUVEGARDE_DIR", str(tmp_path / "sauvegardes"))


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """
    Catalogue de 4 fournisseurs, 6 modèles et leur état de santé :

      google    g-1 (actif, test réussi)                      -> UTILISABLE
      deepseek  d-1 (actif, 3 échecs), d-2 (éteint par la sonde,
                erreur « sale » portant une clé)              -> EN PANNE
      ollama    o-1 (actif, jamais testé)                     -> NON TESTÉ
      vide      v-1 (désactivé à la main)                     -> SANS MODÈLE ACTIF
    """
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "models_registry.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    runtime_db.get_connection().close()           # crée le schéma runtime (model_health, token_usage…)
    conn = sqlite3.connect(str(tmp_path / "models_registry.db"))
    models_db._ensure_schema(conn)
    conn.commit()
    conn.close()

    for pid, nom, prio in (("google", "Google Gemini", 1), ("deepseek", "DeepSeek", 2),
                           ("ollama", "Ollama local", 3), ("vide", "Fournisseur vide", 4)):
        assert models_db.upsert_provider(pid, name=nom, type="cloud", cascade_priority=prio)
    for mid, pid, statut, niveau in (
        ("g-1", "google", "active", "leger"), ("g-2", "google", "active", "moyen"),
        ("d-1", "deepseek", "active", "moyen"), ("d-2", "deepseek", "inactive", "fort"),
        ("o-1", "ollama", "active", "fort"), ("v-1", "vide", "inactive", "leger"),
    ):
        assert models_db.upsert_model(mid, provider_id=pid, status=statut, routing_tier=niveau)

    maintenant = time.time()
    sonde._ecrire_sante("g-1", dernier_test=maintenant, dernier_succes=maintenant,
                        echecs_consecutifs=0, succes_consecutifs=4)
    sonde._ecrire_sante("d-1", dernier_test=maintenant, echecs_consecutifs=3, succes_consecutifs=0,
                        derniere_erreur="HTTP 500 du fournisseur")
    # Ligne « sale » écrite avant le correctif de sécurité : insérée en SQL brut, sans passer
    # par l'écriture qui masque. La lecture doit masquer à son tour.
    conn = runtime_db.get_connection()
    conn.execute(
        "INSERT INTO model_health (model_id, dernier_test, echecs_consecutifs, succes_consecutifs, "
        "desactive_par_sonde, derniere_erreur) VALUES (?, ?, 5, 0, 1, ?)",
        ("d-2", maintenant - 60,
         f"400 Client Error for url: https://api.example.test/v1/x:generate?key={FAUSSE_CLE_GOOGLE}"),
    )
    conn.commit()
    conn.close()
    return tmp_path


def _inserer_usage(lignes):
    """lignes : (age_s, session_id, agent_name, cout_usd). Le timestamp est un epoch flottant."""
    from core.session_history import _ensure_token_table

    conn = runtime_db.get_connection()
    _ensure_token_table(conn)
    maintenant = time.time()
    for age, session, agent, cout in lignes:
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, completion_tokens, "
            "total_tokens, cost_usd, channel, agent_name) VALUES (?, ?, 'g-1', 10, 5, 15, ?, 'test', ?)",
            (session, maintenant - age, cout, agent),
        )
    conn.commit()
    conn.close()


def _inserer_vocal(lignes):
    """lignes : (phase, latence_ms, created_at UTC)."""
    conn = runtime_db.get_connection()
    for phase, latence, cree in lignes:
        conn.execute(
            "INSERT INTO vocal_audit_log (created_at, session_id, user_prompt, phase, latency_ms, source_mode) "
            "VALUES (?, 's', 'allume la lumière', ?, ?, 'ha')",
            (cree, phase, latence),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(guide.router, dependencies=[Depends(require_auth)])
    app.include_router(mcp_vue.router, dependencies=[Depends(require_auth)])
    return TestClient(app)


def _overview(client, auth_headers):
    r = client.get("/api/guide/overview", headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _faire_une_sauvegarde(tmp_path, monkeypatch, age=None):
    """Crée une vraie sauvegarde (faux dépôt minimal). `age` : la vieillit dans son manifeste."""
    racine = tmp_path / "faux_depot"
    racine.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(racine / "memory.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('x')")
    conn.commit()
    conn.close()
    (racine / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sauvegarde, "racine_depot", lambda: racine)
    manifeste = sauvegarde.creer_sauvegarde()
    if age is not None:
        chemin = sauvegarde.dossier_sauvegardes() / manifeste["id"] / sauvegarde.NOM_MANIFESTE
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        donnees["cree_le"] = (datetime.now().astimezone() - age).isoformat(timespec="seconds")
        chemin.write_text(json.dumps(donnees), encoding="utf-8")
    return manifeste


# ═══════════════════════════════════════════════════════════════════════════
# Authentification
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("chemin", ["/api/guide/overview", "/api/guide/cascade", "/api/mcp/overview"])
def test_les_routes_exigent_l_authentification(client, chemin):
    assert client.get(chemin).status_code == 401
    assert client.get(chemin, headers={"Authorization": "Bearer mauvais-jeton"}).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# /api/guide/overview
# ═══════════════════════════════════════════════════════════════════════════

def test_overview_respecte_le_contrat(client, auth_headers, catalogue):
    d = _overview(client, auth_headers)
    assert set(d) == {"genere_le", "moteur", "fournisseurs", "modeles", "usage", "vocal",
                      "home_assistant", "sauvegardes", "boucles", "alertes"}
    datetime.fromisoformat(d["genere_le"])
    assert set(d["moteur"]) == {"commit", "demarre_le", "uptime_s"}
    assert {"total", "utilisables", "en_panne"} <= set(d["fournisseurs"])
    assert set(d["modeles"]) == {"total", "actifs"}
    assert set(d["usage"]) >= {"appels_24h", "appels_reels_7j", "appels_sondes_7j", "cout_30j_usd"}
    assert set(d["vocal"]) == {"echanges_7j", "latence_p50_ms", "dernier_echange"}
    assert set(d["home_assistant"]) == {"configure"}
    assert set(d["sauvegardes"]) == {"derniere", "nombre", "dossier"}
    for b in d["boucles"]:
        assert set(b) == {"nom", "active", "description", "reglage"}
        assert b["active"] in (True, False, None)
        assert b["description"] and b["reglage"]
    for a in d["alertes"]:
        assert set(a) == {"niveau", "texte"}
        assert a["niveau"] in ("info", "attention", "critique")


def test_fournisseurs_et_modeles_viennent_de_la_base(client, auth_headers, catalogue):
    d = _overview(client, auth_headers)
    f = d["fournisseurs"]
    assert f["total"] == 4
    assert f["utilisables"] == 1                       # google
    assert [p["nom"] for p in f["en_panne"]] == ["DeepSeek"]
    assert f["non_testes"] == 1                        # ollama : jamais testé, ni utilisable ni en panne
    assert f["sans_modele_actif"] == 1                 # vide : modèle désactivé à la main
    assert f["utilisables"] + len(f["en_panne"]) + f["non_testes"] + f["sans_modele_actif"] == f["total"]
    assert d["modeles"] == {"total": 6, "actifs": 4}


def test_la_raison_de_panne_est_expliquee_et_sans_cle(client, auth_headers, catalogue):
    d = _overview(client, auth_headers)
    raison = d["fournisseurs"]["en_panne"][0]["raison"]
    assert "2 modèle(s) sur 2" in raison
    assert "éteint(s) par la sonde" in raison
    # La ligne sale en base porte une clé : elle ne doit pas ressortir.
    assert FAUSSE_CLE_GOOGLE not in json.dumps(d)
    assert FAUSSE_CLE_GOOGLE not in raison


def test_usage_separe_les_sondes_de_l_usage_reel(client, auth_headers, catalogue):
    jour, h = 86400, 3600
    _inserer_usage([
        (h, "s1", "coder", 0.25), (2 * h, "s2", None, 0.25),                  # 2 réels, < 24 h
        (h, None, None, 0.01), (2 * h, None, None, 0.01), (3 * h, "", "", 0.01),  # 3 sondes, < 24 h
        (3 * jour, "s3", "coder", 0.25),                                     # 1 réel, 3 j
        *[(3 * jour, None, None, 0.01)] * 4,                                 # 4 sondes, 3 j
        *[(10 * jour, None, None, 1.0)] * 2,                                 # hors 7 j mais dans 30 j
        (40 * jour, "vieux", "coder", 99.0),                                 # hors 30 j
    ])
    u = _overview(client, auth_headers)["usage"]
    assert u["appels_24h"] == 5
    assert u["appels_reels_7j"] == 3
    assert u["appels_sondes_7j"] == 7
    assert u["cout_30j_usd"] == pytest.approx(0.5 + 0.03 + 0.25 + 0.04 + 2.0)
    assert "borne haute" in u["note"]


def test_usage_vide_vaut_zero_pas_null(client, auth_headers, catalogue):
    """Une base lisible mais sans appel : 0 est une réponse vraie (≠ source illisible)."""
    u = _overview(client, auth_headers)["usage"]
    assert (u["appels_24h"], u["appels_reels_7j"], u["appels_sondes_7j"], u["cout_30j_usd"]) == (0, 0, 0, 0.0)


def test_alerte_quand_les_sondes_noient_l_usage(client, auth_headers, catalogue):
    _inserer_usage([(3600, None, None, 0.0)] * 120 + [(3600, "s1", "coder", 0.0)] * 2)
    d = _overview(client, auth_headers)
    assert any(a["niveau"] == "info" and "sondes de santé" in a["texte"] for a in d["alertes"])


def test_vocal_sans_echange_a_des_null(client, auth_headers, catalogue):
    v = _overview(client, auth_headers)["vocal"]
    assert v == {"echanges_7j": 0, "latence_p50_ms": None, "dernier_echange": None}


def test_vocal_reprend_les_chiffres_de_l_ecran_vocal(client, auth_headers, catalogue):
    recent = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _inserer_vocal([
        ("request", None, recent),
        ("response", 1000.0, recent), ("response", 2000.0, recent), ("response", 3000.0, recent),
        ("response", 0.0, recent),          # latence 0 = « jamais mesurée » : exclue du p50
    ])
    v = _overview(client, auth_headers)["vocal"]
    assert v["echanges_7j"] == 4
    assert v["latence_p50_ms"] == 2000
    assert v["dernier_echange"] == recent.replace(" ", "T") + "+00:00"


def test_home_assistant_configure_sans_appel_reseau(client, auth_headers, catalogue, monkeypatch):
    assert _overview(client, auth_headers)["home_assistant"] == {"configure": True}
    monkeypatch.delenv("HASS_TOKEN")
    d = _overview(client, auth_headers)
    assert d["home_assistant"] == {"configure": False}
    assert any(a["niveau"] == "attention" and "Home Assistant" in a["texte"] for a in d["alertes"])


def test_les_jetons_ne_sont_jamais_recopies(client, auth_headers, catalogue):
    import os
    r = client.get("/api/guide/overview", headers=auth_headers)
    assert r.status_code == 200
    assert FAUX_JETON_HA not in r.text
    assert os.environ["MOTEUR_API_KEY"] not in r.text


def test_aucune_sauvegarde_est_une_alerte_critique(client, auth_headers, catalogue):
    d = _overview(client, auth_headers)
    assert d["sauvegardes"]["derniere"] is None
    assert d["sauvegardes"]["nombre"] == 0
    assert d["sauvegardes"]["dossier"].endswith("sauvegardes")
    critiques = [a for a in d["alertes"] if a["niveau"] == "critique"]
    assert any("Aucune sauvegarde" in a["texte"] for a in critiques)


def test_sauvegarde_recente_pas_d_alerte(client, auth_headers, catalogue, tmp_path, monkeypatch):
    manifeste = _faire_une_sauvegarde(tmp_path, monkeypatch)
    d = _overview(client, auth_headers)
    assert d["sauvegardes"]["nombre"] == 1
    assert d["sauvegardes"]["derniere"] == manifeste["cree_le"]
    assert not [a for a in d["alertes"] if "sauvegarde" in a["texte"].lower()]


def test_sauvegarde_de_plus_de_48h_est_critique(client, auth_headers, catalogue, tmp_path, monkeypatch):
    _faire_une_sauvegarde(tmp_path, monkeypatch, age=timedelta(hours=60))
    d = _overview(client, auth_headers)
    assert d["sauvegardes"]["nombre"] == 1
    assert any(a["niveau"] == "critique" and "60 h" in a["texte"] for a in d["alertes"])


def test_sauvegarde_de_47h_n_alerte_pas(client, auth_headers, catalogue, tmp_path, monkeypatch):
    _faire_une_sauvegarde(tmp_path, monkeypatch, age=timedelta(hours=47))
    d = _overview(client, auth_headers)
    assert not [a for a in d["alertes"] if a["niveau"] == "critique"]


def test_fournisseur_en_panne_est_une_alerte_attention(client, auth_headers, catalogue):
    d = _overview(client, auth_headers)
    assert any(a["niveau"] == "attention" and "DeepSeek" in a["texte"] for a in d["alertes"])


def test_les_alertes_sont_triees_par_gravite(client, auth_headers, catalogue):
    ordre = {"critique": 0, "attention": 1, "info": 2}
    niveaux = [ordre[a["niveau"]] for a in _overview(client, auth_headers)["alertes"]]
    assert niveaux == sorted(niveaux)
    assert len(niveaux) >= 2


def test_sonde_desactivee_est_signalee_et_dans_les_boucles(client, auth_headers, catalogue, monkeypatch):
    monkeypatch.setenv("MODEL_HEALTH_PROBE", "false")
    d = _overview(client, auth_headers)
    boucle = next(b for b in d["boucles"] if "Sonde" in b["nom"])
    assert boucle["active"] is False
    assert "MODEL_HEALTH_PROBE" in boucle["reglage"]
    assert any("sonde de vivacité" in a["texte"] and a["niveau"] == "info" for a in d["alertes"])


def test_sonde_active_par_defaut_et_reglage_lu(client, auth_headers, catalogue, monkeypatch):
    monkeypatch.setenv("MODEL_HEALTH_INTERVAL_SECONDS", "777")
    boucle = next(b for b in _overview(client, auth_headers)["boucles"] if "Sonde" in b["nom"])
    assert boucle["active"] is True
    assert "777" in boucle["reglage"]


def test_etat_de_sonde_perime_est_signale(client, auth_headers, catalogue, monkeypatch):
    monkeypatch.setenv("MODEL_HEALTH_INTERVAL_SECONDS", "60")
    conn = runtime_db.get_connection()
    conn.execute("UPDATE model_health SET dernier_test = ?", (time.time() - 3600,))
    conn.commit()
    conn.close()
    d = _overview(client, auth_headers)
    assert "périmé" in d["fournisseurs"]["note"]
    assert any("périmé" in a["texte"] for a in d["alertes"])


def test_boucles_interrupteurs_lus_dans_l_environnement(client, auth_headers, catalogue, monkeypatch):
    monkeypatch.setenv("MOTEUR_VIGIE_ENABLED", "1")
    monkeypatch.setenv("TASK_DISCOVERY_ENABLED", "0")
    monkeypatch.setenv("MQTT_HOST", "broker.test")
    par_nom = {b["nom"]: b for b in _overview(client, auth_headers)["boucles"]}
    assert par_nom["Vigie"]["active"] is True
    assert par_nom["Découverte de tâches"]["active"] is False
    assert par_nom["Watchdog MQTT"]["active"] is True
    assert "Démon sentinelle" in par_nom and "Dreamer et DreamCoder" in par_nom


def test_source_illisible_donne_null_et_une_alerte(client, auth_headers, catalogue, monkeypatch):
    """Si seule la consommation est illisible : 200, usage à null, alerte nommant la source."""
    def casse():
        raise RuntimeError("base verrouillée")
    monkeypatch.setattr(guide, "_section_usage", casse)
    d = _overview(client, auth_headers)
    assert d["usage"] == {"appels_24h": None, "appels_reels_7j": None, "appels_sondes_7j": None,
                          "cout_30j_usd": None}
    assert d["modeles"]["total"] == 6                       # les autres sections restent vraies
    assert any(a["niveau"] == "attention" and "consommation de tokens" in a["texte"] for a in d["alertes"])


def test_catalogue_illisible_donne_null(client, auth_headers, catalogue, monkeypatch):
    monkeypatch.setattr(models_db, "get_db_stats", lambda: {"error": "disk I/O error"})
    d = _overview(client, auth_headers)
    assert d["fournisseurs"]["total"] is None and d["fournisseurs"]["utilisables"] is None
    assert d["modeles"] == {"total": None, "actifs": None}
    assert any("catalogue des modèles" in a["texte"] for a in d["alertes"])


def test_journal_vocal_illisible_donne_null_pas_zero(client, auth_headers, catalogue):
    conn = runtime_db.get_connection()
    conn.execute("DROP TABLE vocal_audit_log")
    conn.commit()
    conn.close()
    # `get_connection` recrée la table au prochain appel : on casse la vérification elle-même.
    def casse():
        raise sqlite3.OperationalError("no such table: vocal_audit_log")
    original = guide._verifier_journal_vocal
    guide._verifier_journal_vocal = casse
    try:
        d = _overview(client, auth_headers)
    finally:
        guide._verifier_journal_vocal = original
    assert d["vocal"] == {"echanges_7j": None, "latence_p50_ms": None, "dernier_echange": None}
    assert any("journal vocal" in a["texte"] for a in d["alertes"])


def test_toutes_les_sources_hors_service_donnent_503(client, auth_headers, catalogue, monkeypatch):
    def casse(*_a, **_k):
        raise RuntimeError("tout est en panne")

    async def casse_async(*_a, **_k):
        raise RuntimeError("tout est en panne")
    monkeypatch.setattr(guide, "_section_catalogue", casse)
    monkeypatch.setattr(guide, "_section_usage", casse)
    monkeypatch.setattr(guide, "_section_vocal", casse_async)
    r = client.get("/api/guide/overview", headers=auth_headers)
    assert r.status_code == 503
    assert "Aucune source de données" in r.json()["detail"]


def test_overview_est_rapide(client, auth_headers, catalogue):
    _overview(client, auth_headers)                    # chauffe les imports et le cache de version
    meilleur = min(_chrono(client, auth_headers) for _ in range(3))
    assert meilleur < 0.3, f"{meilleur * 1000:.0f} ms (budget : 300 ms)"


def _chrono(client, auth_headers):
    debut = time.perf_counter()
    assert client.get("/api/guide/overview", headers=auth_headers).status_code == 200
    return time.perf_counter() - debut


def test_overview_ne_sort_pas_du_process(client, auth_headers, catalogue, monkeypatch):
    import socket

    connect_reel = socket.socket.connect

    def connect_filtre(self, adresse, *a, **k):
        # La boucle asyncio de TestClient ouvre une paire de sockets locale (127.0.0.1) :
        # ce n'est pas du réseau. Tout autre hôte est une sortie du process.
        hote = adresse[0] if isinstance(adresse, tuple) else adresse
        if hote not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"appel réseau interdit dans /api/guide/overview : {hote}")
        return connect_reel(self, adresse, *a, **k)

    def resolution_interdite(*_a, **_k):
        raise AssertionError("résolution DNS interdite dans /api/guide/overview")

    _overview(client, auth_headers)                    # chauffe (imports paresseux)
    monkeypatch.setattr(socket.socket, "connect", connect_filtre)
    monkeypatch.setattr(socket, "getaddrinfo", resolution_interdite)
    d = _overview(client, auth_headers)
    assert d["modeles"]["total"] == 6


# ═══════════════════════════════════════════════════════════════════════════
# /api/guide/cascade
# ═══════════════════════════════════════════════════════════════════════════

CONFIG_TEST = {
    "tiers": {"leger": ["x-leger"], "moyen": ["x-moyen"], "fort": ["x-fort"], "automatique": ["x-auto"]},
    "routing_policy": {"fast_path_providers": ["ajean_test", "gemini-test"], "excluded_models": ["g-2"],
                       "tab_llm_providers": ["cerebras-test"]},
    "budget_guard": {"daily_budget_usd": 0.5, "gemini_free_tokens_per_hour": 1000, "note": "texte ignoré"},
}


@pytest.fixture
def config_test(monkeypatch):
    import core.llm_gateway as gateway
    monkeypatch.setattr(gateway, "load_config", lambda *a, **k: json.loads(json.dumps(CONFIG_TEST)))


def test_cascade_respecte_le_contrat(client, auth_headers, catalogue, config_test):
    r = client.get("/api/guide/cascade", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "config.json"
    assert d["fast_path_providers"] == ["ajean_test", "gemini-test"]
    assert d["tiers"] == CONFIG_TEST["tiers"]
    assert d["budget"] == {"daily_budget_usd": 0.5, "gemini_free_tokens_per_hour": 1000}   # texte écarté
    assert set(d["explication"]) >= {"fast_path_providers", "tiers"}
    assert all(isinstance(t, str) and t for t in d["explication"].values())


def test_cascade_montre_la_realite_du_catalogue(client, auth_headers, catalogue, config_test):
    d = client.get("/api/guide/cascade", headers=auth_headers).json()
    # Le catalogue prime sur config.json → tiers ; g-2 est exclu par routing_policy.
    assert d["tiers_catalogue"]["leger"] == ["g-1"]
    assert d["tiers_catalogue"]["moyen"] == ["d-1"]
    assert d["tiers_catalogue"]["fort"] == ["o-1"]
    assert d["tiers_source"]["leger"] == "models_registry.db"
    assert "g-2" in d["exclus_de_la_cascade"]
    assert d["fast_path_providers_tab5"][0] == "cerebras-test"
    assert "filet de sécurité" in d["explication"]["tiers"]


def test_cascade_repli_sur_config_quand_le_catalogue_est_vide(client, auth_headers, tmp_path, monkeypatch, config_test):
    monkeypatch.setattr(models_db, "_DB_PATH", str(tmp_path / "vide.db"))
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    d = client.get("/api/guide/cascade", headers=auth_headers).json()
    assert d["tiers_catalogue"]["leger"] == ["x-leger"]
    assert d["tiers_source"]["leger"].startswith("config.json")


def test_cascade_catalogue_illisible_reste_un_200_honnete(client, auth_headers, catalogue, config_test, monkeypatch):
    def casse(_niveau):
        raise RuntimeError("catalogue verrouillé")
    monkeypatch.setattr(models_db, "get_models_for_tier", casse)
    d = client.get("/api/guide/cascade", headers=auth_headers).json()
    assert d["tiers_catalogue"] is None
    assert "illisible" in d["note"]
    assert d["fast_path_providers"] == ["ajean_test", "gemini-test"]


def test_cascade_config_illisible_donne_503(client, auth_headers, monkeypatch):
    import core.llm_gateway as gateway

    def casse(*_a, **_k):
        raise ValueError("JSON invalide")
    monkeypatch.setattr(gateway, "load_config", casse)
    r = client.get("/api/guide/cascade", headers=auth_headers)
    assert r.status_code == 503
    assert "config.json illisible" in r.json()["detail"]


def test_cascade_sur_le_vrai_config_json(client, auth_headers, catalogue):
    """Sans doublure de config : la forme tient avec le config.json réel du dépôt."""
    d = client.get("/api/guide/cascade", headers=auth_headers).json()
    assert d["source"] == "config.json"
    assert d["fast_path_providers"] and all(isinstance(p, str) for p in d["fast_path_providers"])
    assert set(d["tiers"]) >= {"leger", "moyen", "fort"}
    assert d["budget"] is None or all(isinstance(v, int | float | bool) for v in d["budget"].values())


# ═══════════════════════════════════════════════════════════════════════════
# /api/mcp/overview
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mcp_config(tmp_path, monkeypatch):
    """Faux mcp_config.json + faux règles d'activation, sous un dossier temporaire."""
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "serveur-fichiers", "--token", FAUX_JETON_HA, "/data"],
                       "env": {"SECRET": FAUX_JETON_HA}},
        "github": {"command": "npx", "args": ["-y", "serveur-github", f"--api-key={FAUX_JETON_HA}"]},
        "distant": {"url": f"https://user:pass@mcp.example.test/sse?key={FAUSSE_CLE_GOOGLE}"},
        "tab5-engine": {"command": "python", "args": ["mcp_server.py"]},
        "orphelin": {"command": "npx", "args": ["x"]},
        "casse": {"args": ["x"]},
    }}), encoding="utf-8")
    regles = tmp_path / "regles.json"
    regles.write_text(json.dumps({
        "rules": [{"keywords": ["github", "commit"], "activate": ["github"]},
                  {"keywords": ["web"], "activate": ["navigateur-absent"]}],
        "default": ["filesystem", "tab5-engine"],
    }), encoding="utf-8")
    monkeypatch.setattr(mcp_vue, "_chemins_config", lambda: [str(config)])
    monkeypatch.setattr(mcp_vue, "_FICHIER_REGLES", regles)
    return config, regles


def test_mcp_expose_les_outils_du_serveur(client, auth_headers, mcp_config):
    r = client.get("/api/mcp/overview", headers=auth_headers)
    assert r.status_code == 200, r.text
    e = r.json()["expose"]
    assert e["disponible"] is True
    assert "mcp_server.py" in e["point_d_entree"]
    noms = {o["nom"] for o in e["outils"]}
    # Outils connus des trois modules d'outils (voir mcp_server.py).
    assert {"get_engine_status", "search_memory", "execute_ha_action"} <= noms
    assert len(e["outils"]) == e["nombre_outils"] >= 17
    assert all(set(o) == {"nom", "description"} and o["description"] for o in e["outils"])
    assert len(noms) == len(e["outils"])


def test_mcp_outils_lus_sans_importer_le_serveur(client, auth_headers, mcp_config):
    import sys
    sys.modules.pop("mcp_server", None)
    client.get("/api/mcp/overview", headers=auth_headers)
    assert "mcp_server" not in sys.modules, "l'analyse doit être statique (ast), pas un import"


def test_mcp_outils_mis_en_cache_puis_invalides(client, auth_headers, mcp_config, monkeypatch):
    mcp_vue._cache.update(signature=None, valeur=None)
    appels = []
    original = mcp_vue._outils_d_un_fichier
    monkeypatch.setattr(mcp_vue, "_outils_d_un_fichier", lambda f: appels.append(f) or original(f))
    client.get("/api/mcp/overview", headers=auth_headers)
    premiers = len(appels)
    assert premiers >= 3
    client.get("/api/mcp/overview", headers=auth_headers)
    # Seul l'appel de comptage de workspace_mcp.py est refait : les 3 modules d'outils sont en cache.
    assert len(appels) - premiers <= 1


def test_mcp_nouvel_outil_detecte_apres_modification(tmp_path, monkeypatch):
    """Ajouter un `@mcp.tool()` dans un module est vu sans redémarrage (cache sur mtime)."""
    depot = tmp_path
    (depot / "core" / "mcp_tools").mkdir(parents=True)
    (depot / "mcp_server.py").write_text("import core.mcp_tools.un\n", encoding="utf-8")
    module = depot / "core" / "mcp_tools" / "un.py"
    module.write_text('@mcp.tool()\nasync def premier():\n    """Premier outil."""\n', encoding="utf-8")
    monkeypatch.setattr(mcp_vue, "_DEPOT", depot)
    monkeypatch.setattr(mcp_vue, "_FICHIER_SERVEUR", depot / "mcp_server.py")
    mcp_vue._cache.update(signature=None, valeur=None)
    assert [o["nom"] for o in mcp_vue._outils_exposes()] == ["premier"]
    module.write_text('@mcp.tool()\nasync def premier():\n    """Premier outil."""\n\n'
                      '@mcp.tool()\ndef second():\n    """Second\n    outil.\n\n    Détail ignoré.\n    """\n'
                      'def pas_un_outil():\n    pass\n', encoding="utf-8")
    outils = mcp_vue._outils_exposes()
    assert [o["nom"] for o in outils] == ["premier", "second"]
    assert outils[1]["description"] == "Second outil."          # premier paragraphe, sur une ligne
    mcp_vue._cache.update(signature=None, valeur=None)


def test_mcp_consomme_lit_la_config_et_les_regles(client, auth_headers, mcp_config):
    d = client.get("/api/mcp/overview", headers=auth_headers).json()
    par_nom = {s["nom"]: s for s in d["consomme"]}
    assert set(par_nom) == {"filesystem", "github", "distant", "tab5-engine", "orphelin", "casse"}
    for s in d["consomme"]:
        assert set(s) == {"nom", "type", "actif", "cible", "regles"}
        assert s["regles"] and all(isinstance(x, str) for x in s["regles"])
    assert par_nom["filesystem"]["actif"] is True               # dans « default »
    assert par_nom["github"]["actif"] is None                   # selon les mots de la demande
    assert any("commit" in x for x in par_nom["github"]["regles"])
    assert par_nom["orphelin"]["actif"] is False                # configuré mais jamais activé
    assert par_nom["tab5-engine"]["actif"] is False             # le pont ne se lance pas lui-même
    assert par_nom["casse"]["actif"] is False                   # ni commande ni adresse
    assert par_nom["filesystem"]["type"] == "stdio"
    assert par_nom["distant"]["type"] == "http"


def test_mcp_signale_les_regles_qui_citent_un_serveur_absent(client, auth_headers, mcp_config):
    notes = " ".join(client.get("/api/mcp/overview", headers=auth_headers).json()["notes"])
    assert "navigateur-absent" in notes


def test_mcp_ne_revele_aucun_secret(client, auth_headers, mcp_config):
    r = client.get("/api/mcp/overview", headers=auth_headers)
    assert FAUX_JETON_HA not in r.text                          # ni --token, ni --api-key=, ni le bloc env
    assert FAUSSE_CLE_GOOGLE not in r.text                      # ni ?key= dans l'URL
    assert "user:pass" not in r.text                            # ni identifiants dans l'URL
    cibles = {s["nom"]: s["cible"] for s in r.json()["consomme"]}
    assert "serveur-fichiers" in cibles["filesystem"] and "/data" in cibles["filesystem"]
    assert "--token ***" in cibles["filesystem"]
    assert cibles["github"].endswith("--api-key=***")
    assert cibles["distant"] == "https://mcp.example.test/sse"


def test_mcp_masque_les_identifiants_d_une_chaine_de_connexion(client, auth_headers, tmp_path, monkeypatch):
    motdepasse = "mdp-" + "m" * 16
    config = tmp_path / "mcp_config.json"
    config.write_text(json.dumps({"mcpServers": {"base": {
        "command": "serveur-sql", "args": [f"mysql://utilisateur:{motdepasse}@hote.example.test/base"]}}}),
        encoding="utf-8")
    monkeypatch.setattr(mcp_vue, "_chemins_config", lambda: [str(config)])
    r = client.get("/api/mcp/overview", headers=auth_headers)
    assert motdepasse not in r.text
    assert "utilisateur" not in r.text
    assert r.json()["consomme"][0]["cible"] == "serveur-sql mysql://***@hote.example.test/base"


def test_mcp_sans_fichier_de_config_le_dit(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_vue, "_chemins_config", lambda: [str(tmp_path / "absent.json")])
    d = client.get("/api/mcp/overview", headers=auth_headers).json()
    assert d["consomme"] == []
    assert d["consomme_source"]["etat"] == "absent"
    assert any("Aucun mcp_config.json" in n and "désactivé" in n for n in d["notes"])
    assert d["expose"]["outils"]                                 # la partie exposée reste servie


def test_mcp_config_illisible_le_dit(client, auth_headers, tmp_path, monkeypatch):
    casse = tmp_path / "mcp_config.json"
    casse.write_text("{ pas du json", encoding="utf-8")
    monkeypatch.setattr(mcp_vue, "_chemins_config", lambda: [str(casse)])
    d = client.get("/api/mcp/overview", headers=auth_headers).json()
    assert d["consomme"] == []
    assert d["consomme_source"]["etat"] == "illisible"
    assert any("illisible" in n for n in d["notes"])


def test_mcp_serveur_illisible_donne_503(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_vue, "_FICHIER_SERVEUR", tmp_path / "mcp_server.py")
    (tmp_path / "mcp_server.py").write_text("def casse(:\n", encoding="utf-8")
    mcp_vue._cache.update(signature=None, valeur=None)
    r = client.get("/api/mcp/overview", headers=auth_headers)
    assert r.status_code == 503
    assert "illisibles" in r.json()["detail"]


def test_mcp_sur_les_vrais_fichiers_du_depot(client, auth_headers):
    """Sans doublure : la forme tient avec les vrais mcp_server.py et règles d'activation."""
    mcp_vue._cache.update(signature=None, valeur=None)
    d = client.get("/api/mcp/overview", headers=auth_headers).json()
    assert d["expose"]["nombre_outils"] >= 17
    assert isinstance(d["consomme"], list) and isinstance(d["notes"], list) and d["notes"]


def test_mcp_est_rapide(client, auth_headers):
    client.get("/api/mcp/overview", headers=auth_headers)       # chauffe
    debut = time.perf_counter()
    assert client.get("/api/mcp/overview", headers=auth_headers).status_code == 200
    assert time.perf_counter() - debut < 0.3
