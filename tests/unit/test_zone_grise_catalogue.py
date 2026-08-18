"""
test_zone_grise_catalogue.py — Zone grise du routage : règle, audit, non-récidive.

Mesure prod du 17/08 : 52 modèles `active` sans `routing_tier` — ignorés par le
routage par tier (`get_models_for_tier` filtre sur le tier) mais testés chaque
heure par la sonde de vivacité (vraies inférences, vrais tokens). Ces tests
fixent :

  1. l'outil d'état des lieux (tools/audit_catalogue_sante.py) : sur une base
     temporaire, il rend les bons comptes (matrice, profondeur, causes de mort,
     classement de la zone grise) ;
  2. la règle explicite : un modèle sans tier est légitime si sa spécialité est
     hors routage chat-complétion (SPECIALITES_HORS_ROUTAGE), sinon anomalie ;
  3. la non-récidive : l'audit échoue sur un catalogue fabriqué contenant la
     zone grise et passe sur un catalogue sain ;
  4. la surface API : /api/models-health expose les nouveaux compteurs sans
     casser sa forme historique (consommateurs existants).

Toutes les bases sont temporaires et isolées — jamais la base du poste.
"""

import os
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.models_db as models_db
import core.runtime_db as runtime_db

# ══════════════════════════════════════════════════════════════════
# Fabrique de bases temporaires pour l'outil d'audit
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def bases_outil(tmp_path):
    """Catalogue + base runtime temporaires, peuplés avec une zone grise connue."""
    chemin_registre = str(tmp_path / "models_registry.db")
    chemin_runtime = str(tmp_path / "moteur_runtime.db")

    conn_registre = sqlite3.connect(chemin_registre)
    conn_registre.row_factory = sqlite3.Row
    models_db._ensure_schema(conn_registre)
    conn_registre.execute(
        "INSERT INTO providers (id, name, type) VALUES ('p_cloud', 'Cloud', 'api')"
    )
    # Un catalogue avec : deux tiers pourvus, un modèle légitime hors routage
    # (embeddings), un modèle en zone grise, deux morts de causes distinctes.
    conn_registre.executemany(
        "INSERT INTO models (id, provider_id, status, routing_tier, speciality) "
        "VALUES (?, 'p_cloud', ?, ?, ?)",
        [
            ("m-fort", "active", "fort", "raisonnement"),
            ("m-moyen", "active", "moyen", "polyvalent"),
            ("m-embed", "active", None, "embeddings"),
            ("m-gris", "active", None, "polyvalent"),
            ("m-mort-sonde", "inactive", None, "polyvalent"),
            ("m-mort-humain", "inactive", "moyen", "polyvalent"),
        ],
    )
    conn_registre.commit()

    conn_runtime = sqlite3.connect(chemin_runtime)
    conn_runtime.row_factory = sqlite3.Row
    runtime_db._init_schema(conn_runtime)
    maintenant = time.time()
    conn_runtime.executemany(
        "INSERT INTO model_health (model_id, echecs_consecutifs, succes_consecutifs, "
        "desactive_par_sonde, dernier_succes) VALUES (?, ?, ?, ?, ?)",
        [
            ("m-fort", 0, 12, 0, maintenant),
            ("m-gris", 0, 12, 0, maintenant),          # vivant mais jamais routé
            ("m-embed", 0, 12, 0, maintenant),
            ("m-mort-sonde", 112, 0, 1, None),         # éteint par la sonde
        ],
    )
    # Usage réel : m-embed sert (RAG), m-gris n'a JAMAIS été appelé.
    conn_runtime.execute(
        "INSERT INTO token_usage (timestamp, model, total_tokens) VALUES (?, 'm-embed', 10)",
        (maintenant,),
    )
    conn_runtime.commit()

    yield conn_registre, conn_runtime
    conn_registre.close()
    conn_runtime.close()


class TestOutilAudit:
    """L'outil d'état des lieux rend les bons comptes sur une base temporaire."""

    def test_matrice_tier_statut(self, bases_outil):
        from tools.audit_catalogue_sante import analyser

        rapport = analyser(*bases_outil, cables=None)
        matrice = {(e["routing_tier"], e["status"]): e["nb"] for e in rapport["matrice"]}
        assert matrice[("fort", "active")] == 1
        assert matrice[("moyen", "active")] == 1
        assert matrice[(None, "active")] == 2        # zone grise
        assert matrice[(None, "inactive")] == 1
        assert matrice[("moyen", "inactive")] == 1

    def test_profondeur_reelle_des_tiers(self, bases_outil):
        from tools.audit_catalogue_sante import analyser

        profondeur = analyser(*bases_outil, cables=None)["profondeur_tiers"]
        assert profondeur["fort"] == 1
        assert profondeur["moyen"] == 1
        assert profondeur["leger"] == 0
        # Les inactifs sans tier ne comptent PAS dans la zone grise routable.
        assert profondeur["hors_tier"] == 2

    def test_causes_de_mort(self, bases_outil):
        from tools.audit_catalogue_sante import analyser

        rapport = analyser(*bases_outil, cables=None)
        assert rapport["causes"]["desactive_par_sonde"] == 1
        assert rapport["causes"]["decision_humaine"] == 1
        assert rapport["causes"]["muet_sonde_non_desactive"] == 0

    def test_zone_grise_classee_par_la_regle(self, bases_outil):
        from tools.audit_catalogue_sante import analyser

        rapport = analyser(*bases_outil, cables=None)
        assert [e["model_id"] for e in rapport["justifies_hors_routage"]] == ["m-embed"]
        gris = rapport["zone_grise"]
        assert [e["model_id"] for e in gris] == ["m-gris"]
        assert gris[0]["appels_total"] == 0           # jamais servi
        assert gris[0]["echecs_consecutifs"] == 0     # vivant, donc sondé pour rien
        assert rapport["conforme"] is False           # seuil 0 par défaut
        assert analyser(*bases_outil, cables=None, seuil=1)["conforme"] is True

    def test_cablage_gateway_transmis(self, bases_outil):
        from tools.audit_catalogue_sante import analyser

        avec_cables = analyser(*bases_outil, cables={"m-embed"})
        par_id = {e["model_id"]: e for e in avec_cables["zone_grise"] + avec_cables["justifies_hors_routage"]}
        assert par_id["m-embed"]["cable"] is True
        assert par_id["m-gris"]["cable"] is False
        # Câblage indisponible : None, jamais False inventé.
        sans_cables = analyser(*bases_outil, cables=None)
        assert sans_cables["zone_grise"][0]["cable"] is None


# ══════════════════════════════════════════════════════════════════
# Règle et non-récidive sur core.models_db (base temporaire isolée)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def base_catalogue(tmp_path, monkeypatch):
    """Catalogue neuf et isolé (jamais la base du poste)."""
    chemin = str(tmp_path / "models_registry.db")
    monkeypatch.setattr(models_db, "_DB_PATH", chemin)
    # La connexion est mémorisée dans un thread-local : le remettre à neuf,
    # sinon ce test écrirait dans la base temporaire d'un test précédent.
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    conn = sqlite3.connect(chemin)
    models_db._ensure_schema(conn)
    conn.execute("INSERT INTO providers (id, name, type) VALUES ('p', 'P', 'api')")
    conn.commit()
    conn.close()
    return chemin


def _ajouter(model_id, **champs):
    assert models_db.upsert_model(model_id, provider_id="p", **champs) is True


class TestRegleZoneGrise:
    def test_specialite_hors_routage_legitime(self, base_catalogue):
        assert models_db.est_hors_routage_legitime({"speciality": "embeddings"})
        assert models_db.est_hors_routage_legitime({"speciality": "STT"})  # casse
        assert not models_db.est_hors_routage_legitime({"speciality": "polyvalent"})
        assert not models_db.est_hors_routage_legitime({"speciality": None})

    def test_profondeur_tiers_ignore_inactifs(self, base_catalogue):
        _ajouter("actif-tier", routing_tier="fort")
        _ajouter("actif-gris")
        _ajouter("inactif-sans-tier", status="inactive")

        profondeur = models_db.compter_profondeur_tiers()
        assert profondeur["fort"] == 1
        assert profondeur["hors_tier"] == 1            # l'inactif n'y est pas
        assert profondeur["leger"] == 0


class TestNonRecidive:
    """Le test exigé : échec sur catalogue fabriqué avec zone grise, succès sur sain."""

    def test_catalogue_sain_conforme(self, base_catalogue):
        _ajouter("chat-fort", routing_tier="fort")
        _ajouter("chat-moyen", routing_tier="moyen")
        _ajouter("embed-local", speciality="embeddings")   # légitime sans tier
        _ajouter("mort-sans-tier", status="inactive")      # inactif : hors compte

        audit = models_db.audit_zone_grise()
        assert audit["conforme"] is True
        assert audit["zone_grise"] == []
        assert audit["justifies"] == ["embed-local"]

    def test_catalogue_avec_zone_grise_echoue(self, base_catalogue):
        _ajouter("chat-fort", routing_tier="fort")
        # L'anomalie du 17/08 : actif, sans tier, spécialité de chat-complétion —
        # la sonde le teste chaque heure, le routeur ne le choisit jamais.
        _ajouter("residu-gris", speciality="polyvalent")

        audit = models_db.audit_zone_grise()
        assert audit["conforme"] is False
        assert audit["zone_grise"] == ["residu-gris"]

    def test_seuil_documente_tolere(self, base_catalogue):
        _ajouter("gris-un")
        _ajouter("gris-deux")

        assert models_db.audit_zone_grise(seuil=1)["conforme"] is False
        assert models_db.audit_zone_grise(seuil=2)["conforme"] is True


# ══════════════════════════════════════════════════════════════════
# Surface API : /api/models-health additive, forme historique intacte
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def base_api(tmp_path, monkeypatch):
    """Les deux bases temporaires de la route : registre + santé."""
    chemin_registre = str(tmp_path / "models_registry.db")
    chemin_runtime = str(tmp_path / "moteur_runtime.db")

    monkeypatch.setattr(models_db, "_DB_PATH", chemin_registre)
    monkeypatch.setattr(models_db, "_thread_local", threading.local())
    monkeypatch.setattr(runtime_db, "_DB_PATH", chemin_runtime)

    conn = sqlite3.connect(chemin_registre)
    models_db._ensure_schema(conn)
    conn.execute("INSERT INTO providers (id, name, type) VALUES ('p', 'P', 'api')")
    conn.commit()
    conn.close()

    # Une ligne de santé par modèle : la route lit model_health.
    conn_sante = runtime_db.get_connection()
    conn_sante.execute(
        "INSERT INTO model_health (model_id, echecs_consecutifs, desactive_par_sonde) "
        "VALUES ('gris-route', 0, 0)"
    )
    conn_sante.execute(
        "INSERT INTO model_health (model_id, echecs_consecutifs, desactive_par_sonde) "
        "VALUES ('route-fort', 0, 0)"
    )
    conn_sante.execute(
        "INSERT INTO model_health (model_id, echecs_consecutifs, desactive_par_sonde) "
        "VALUES ('eteint-auto', 5, 1)"
    )
    conn_sante.commit()
    conn_sante.close()
    return chemin_registre, chemin_runtime


class TestSurfaceApi:
    def test_forme_historique_inchangee_et_nouveaux_compteurs(self, base_api):
        from api.routes.context import get_models_health

        _ajouter("route-fort", routing_tier="fort")
        _ajouter("gris-route")                       # actif sans tier, présent en santé
        _ajouter("eteint-auto", status="inactive")

        reponse = get_models_health()

        # Les consommateurs existants retrouvent TOUTES les clés historiques.
        assert {"sonde", "modeles", "muets", "eteints_par_la_sonde"} <= set(reponse)
        assert reponse["eteints_par_la_sonde"] == ["eteint-auto"]

        catalogue = reponse["catalogue"]
        assert catalogue["profondeur_tiers"]["fort"] == 1
        assert catalogue["profondeur_tiers"]["hors_tier"] == 1
        assert catalogue["nb_sondes_non_routables"] == 1
        assert catalogue["sondes_non_routables"] == ["gris-route"]
        assert catalogue["zone_grise"] == ["gris-route"]
        assert catalogue["conforme"] is False

    def test_modele_sondable_legitime_hors_routage(self, base_api):
        from api.routes.context import get_models_health

        _ajouter("embed-route", speciality="embeddings")

        catalogue = get_models_health()["catalogue"]
        assert catalogue["zone_grise"] == []
        assert catalogue["justifies_hors_routage"] == ["embed-route"]
        assert catalogue["conforme"] is True
