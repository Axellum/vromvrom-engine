"""
Sauvegarde à chaud des bases du moteur (core/sauvegarde.py + api/routes/sauvegardes.py).

Ce que ces tests défendent, dans l'ordre d'importance :
  1. la copie est COHÉRENTE : des lignes qui n'existent encore que dans le `-wal`
     d'une base vivante se retrouvent dans la sauvegarde (une copie de fichier brute
     les perdrait) ;
  2. aucun secret n'entre dans une sauvegarde (`.env`, jetons, clés) ;
  3. une sauvegarde en échec ne fait jamais disparaître les anciennes (rétention) ;
  4. une seule sauvegarde à la fois (verrou), et le contrat HTTP (201 / 409 / 503 / 401).

Aucune base du dépôt n'est touchée : tout vit dans un dossier temporaire.
"""

import json
import shutil
import sqlite3

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from filelock import FileLock

from core import sauvegarde
from core.auth import require_auth

FAUX_SECRET = "FAUX_SECRET_" + "x" * 24


def _creer_base(chemin, lignes=3, wal=False):
    """Crée une base SQLite avec une table `t`. En mode WAL, garde la connexion ouverte."""
    conn = sqlite3.connect(str(chemin))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")   # rien n'est reversé dans le .db principal
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"ligne{i}",) for i in range(lignes)])
    conn.commit()
    if wal:
        return conn
    conn.close()
    return None


@pytest.fixture
def depot(tmp_path, monkeypatch):
    """Faux dépôt : 3 bases, configs, prompts, et des fichiers secrets qui ne doivent PAS partir."""
    racine = tmp_path / "depot"
    racine.mkdir()
    ouvertes = []
    ouvertes.append(_creer_base(racine / "moteur_runtime.db", lignes=5, wal=True))
    _creer_base(racine / "memory.db", lignes=2)
    _creer_base(racine / "models_registry.db", lignes=1)
    (racine / "config.json").write_text('{"tiers": {}}', encoding="utf-8")
    (racine / "ha_commands.json").write_text("{}", encoding="utf-8")
    (racine / "mcp_activation_rules.json").write_text("{}", encoding="utf-8")
    (racine / "agents_workflows.json").write_text("[]", encoding="utf-8")
    prompts = racine / "prompts" / "agents"
    prompts.mkdir(parents=True)
    (prompts / "planner.md").write_text("# planner", encoding="utf-8")
    (prompts / "executor.md").write_text("# executor", encoding="utf-8")
    # Pièges : doivent être ignorés même s'ils traînent à côté ou dans un dossier copié.
    (racine / ".env").write_text(f"MOTEUR_API_KEY={FAUX_SECRET}\n", encoding="utf-8")
    (racine / "google_token.json").write_text(f'{{"refresh_token": "{FAUX_SECRET}"}}', encoding="utf-8")
    (prompts / "api_token_notes.md").write_text(FAUX_SECRET, encoding="utf-8")
    (prompts / ".env.local").write_text(FAUX_SECRET, encoding="utf-8")

    dossier = tmp_path / "sauvegardes"
    monkeypatch.setenv("MOTEUR_SAUVEGARDE_DIR", str(dossier))
    monkeypatch.delenv("MOTEUR_SAUVEGARDE_GARDER", raising=False)
    monkeypatch.setattr(sauvegarde, "racine_depot", lambda: racine)
    yield racine, dossier
    for conn in ouvertes:
        conn.close()


# ── Création ───────────────────────────────────────────────────────────────

def test_creation_manifeste_et_integrite(depot):
    racine, dossier = depot

    manifeste = sauvegarde.creer_sauvegarde()

    repertoire = dossier / manifeste["id"]
    assert repertoire.is_dir()
    sur_disque = json.loads((repertoire / "manifeste.json").read_text(encoding="utf-8"))
    assert sur_disque["id"] == manifeste["id"]
    assert sur_disque["version_manifeste"] == 1
    noms = {f["nom"] for f in sur_disque["fichiers"]}
    assert {"moteur_runtime.db", "memory.db", "models_registry.db", "config.json",
            "ha_commands.json", "mcp_activation_rules.json", "agents_workflows.json",
            "prompts/agents/planner.md", "prompts/agents/executor.md"} <= noms
    for f in sur_disque["fichiers"]:
        assert f["sha256"] and len(f["sha256"]) == 64
        assert f["taille_octets"] == (repertoire / f["nom"]).stat().st_size
        assert f["integrite"] == ("ok" if f["nom"].endswith(".db") else None)
    assert sur_disque["taille_octets"] == sum(f["taille_octets"] for f in sur_disque["fichiers"])
    # Le commit est soit un SHA, soit None : jamais une valeur d'apparence valide inventée.
    assert sur_disque["commit"] is None or len(sur_disque["commit"]) >= 7


def test_copie_a_chaud_coherente_avec_le_wal(depot):
    """Les lignes encore dans le -wal d'une base vivante sont dans la sauvegarde."""
    racine, dossier = depot
    # Preuve que le piège existe ici : une copie de fichier brute perd ces lignes.
    brute = racine.parent / "copie_brute.db"
    shutil.copyfile(racine / "moteur_runtime.db", brute)
    try:
        lignes_brutes = sqlite3.connect(str(brute)).execute("SELECT COUNT(*) FROM t").fetchone()[0]
    except sqlite3.OperationalError:
        lignes_brutes = 0
    assert lignes_brutes < 5

    manifeste = sauvegarde.creer_sauvegarde()

    copie = sqlite3.connect(str(dossier / manifeste["id"] / "moteur_runtime.db"))
    try:
        assert copie.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
        assert copie.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        copie.close()
    # Un seul fichier autonome : pas de -wal / -shm à restaurer avec la copie.
    assert not list((dossier / manifeste["id"]).glob("*.db-wal"))
    assert not list((dossier / manifeste["id"]).glob("*.db-shm"))


def test_la_base_vivante_n_est_pas_modifiee(depot):
    racine, _dossier = depot
    avant = (racine / "memory.db").read_bytes()
    sauvegarde.creer_sauvegarde()
    assert (racine / "memory.db").read_bytes() == avant


def test_aucun_secret_dans_la_sauvegarde(depot):
    racine, dossier = depot

    manifeste = sauvegarde.creer_sauvegarde()

    repertoire = dossier / manifeste["id"]
    noms = {f["nom"] for f in manifeste["fichiers"]}
    assert ".env" not in noms
    assert "google_token.json" not in noms
    assert "prompts/agents/api_token_notes.md" not in noms
    assert "prompts/agents/.env.local" not in noms
    # Le .env et le jeton de la racine ne sont jamais candidats ; ceux qui traînent
    # dans un dossier copié sont écartés ET déclarés au manifeste.
    assert {"prompts/agents/api_token_notes.md", "prompts/agents/.env.local"} == set(
        manifeste["exclus_secrets"]
    )
    # Le contenu secret n'est dans AUCUN fichier de la sauvegarde, manifeste compris.
    for fichier in repertoire.rglob("*"):
        if fichier.is_file():
            assert FAUX_SECRET.encode() not in fichier.read_bytes(), fichier.name


def test_fichiers_absents_sont_dits_pas_inventes(depot):
    racine, _dossier = depot
    (racine / "ha_commands.json").unlink()
    (racine / "models_registry.db").unlink()

    manifeste = sauvegarde.creer_sauvegarde()

    assert {"ha_commands.json", "models_registry.db"} <= set(manifeste["absents"])
    assert "ha_commands.json" not in {f["nom"] for f in manifeste["fichiers"]}


def test_deux_sauvegardes_dans_la_meme_seconde_ont_des_ids_distincts(depot, monkeypatch):
    from datetime import datetime as vrai_datetime

    class _Fige(vrai_datetime):
        @classmethod
        def now(cls, tz=None):
            return vrai_datetime(2026, 11, 1, 4, 30, 0, tzinfo=tz)

    monkeypatch.setattr(sauvegarde, "datetime", _Fige)
    a = sauvegarde.creer_sauvegarde()["id"]
    b = sauvegarde.creer_sauvegarde()["id"]
    assert a == "20261101_043000"
    assert b == "20261101_043000_2"


def test_copie_en_echec_est_marquee_et_la_retention_suspendue(depot):
    racine, dossier = depot
    sauvegarde.creer_sauvegarde(garder=2)
    sauvegarde.creer_sauvegarde(garder=2)
    (racine / "memory.db").write_bytes(b"ceci n'est pas une base sqlite" * 50)

    manifeste = sauvegarde.creer_sauvegarde(garder=1)

    memoire = next(f for f in manifeste["fichiers"] if f["nom"] == "memory.db")
    assert memoire["integrite"] == "ko"
    assert memoire["erreur"]
    assert not (dossier / manifeste["id"] / "memory.db").exists()   # pas de copie partielle
    # Rétention suspendue : les 2 bonnes copies précédentes sont toujours là.
    assert manifeste["supprimees_par_retention"] == []
    assert len(sauvegarde.lister_sauvegardes(dossier)) == 3


# ── Rétention ──────────────────────────────────────────────────────────────

def test_retention_garde_les_plus_recentes(depot):
    _racine, dossier = depot
    ids = [sauvegarde.creer_sauvegarde(garder=3)["id"] for _ in range(5)]

    restantes = [s["id"] for s in sauvegarde.lister_sauvegardes(dossier)]

    assert restantes == sorted(ids, reverse=True)[:3]


def test_retention_ne_touche_pas_aux_dossiers_etrangers(depot):
    _racine, dossier = depot
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "mes_documents").mkdir()
    (dossier / "mes_documents" / "important.txt").write_text("a garder", encoding="utf-8")
    (dossier / "20200101_000000").mkdir()    # bon nom, mais pas de manifeste : jamais supprimé

    for _ in range(3):
        sauvegarde.creer_sauvegarde(garder=1)

    assert (dossier / "mes_documents" / "important.txt").exists()
    assert (dossier / "20200101_000000").exists()


def test_reglages_par_environnement(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTEUR_SAUVEGARDE_GARDER", raising=False)
    assert sauvegarde.retention() == 14
    for invalide in ("abc", "0", "-3", ""):
        monkeypatch.setenv("MOTEUR_SAUVEGARDE_GARDER", invalide)
        assert sauvegarde.retention() == 14, invalide
    monkeypatch.setenv("MOTEUR_SAUVEGARDE_GARDER", "5")
    assert sauvegarde.retention() == 5

    monkeypatch.delenv("MOTEUR_SAUVEGARDE_DIR", raising=False)
    assert sauvegarde.dossier_sauvegardes(tmp_path) == tmp_path / "sauvegardes"
    monkeypatch.setenv("MOTEUR_SAUVEGARDE_DIR", str(tmp_path / "ailleurs"))
    assert sauvegarde.dossier_sauvegardes(tmp_path) == tmp_path / "ailleurs"


# ── Garde-fous ─────────────────────────────────────────────────────────────

def test_une_seule_sauvegarde_a_la_fois(depot):
    _racine, dossier = depot
    dossier.mkdir(parents=True, exist_ok=True)
    with FileLock(str(dossier / sauvegarde.NOM_VERROU)):
        with pytest.raises(sauvegarde.SauvegardeEnCours):
            sauvegarde.creer_sauvegarde()
    # Verrou relâché : la suivante passe.
    assert sauvegarde.creer_sauvegarde()["id"]


def test_espace_disque_insuffisant_refuse_sans_rien_creer(depot, monkeypatch):
    _racine, dossier = depot
    monkeypatch.setattr(
        sauvegarde.shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(100, 99, 1),
    )
    with pytest.raises(sauvegarde.SauvegardeImpossible, match="Espace disque"):
        sauvegarde.creer_sauvegarde()
    assert sauvegarde.lister_sauvegardes(dossier) == []


def test_reste_de_copie_interrompue_nettoye(depot):
    _racine, dossier = depot
    dossier.mkdir(parents=True, exist_ok=True)
    reste = dossier / ".20260101_000000.tmp"
    reste.mkdir()
    (reste / "memory.db").write_bytes(b"partiel")

    sauvegarde.creer_sauvegarde()

    assert not reste.exists()


# ── Vérification ───────────────────────────────────────────────────────────

def test_verifier_saine_puis_alteree(depot):
    _racine, dossier = depot
    id_sauvegarde = sauvegarde.creer_sauvegarde()["id"]

    rapport = sauvegarde.verifier_sauvegarde(id_sauvegarde)
    assert rapport["ok"] is True
    assert all(f["sha256_ok"] for f in rapport["fichiers"])

    # Altération d'un octet de la config copiée.
    cible = dossier / id_sauvegarde / "config.json"
    cible.write_text('{"tiers": {"altere": 1}}', encoding="utf-8")
    rapport = sauvegarde.verifier_sauvegarde(id_sauvegarde)
    assert rapport["ok"] is False
    config = next(f for f in rapport["fichiers"] if f["nom"] == "config.json")
    assert config["sha256_ok"] is False


def test_verifier_detecte_une_base_corrompue(depot):
    _racine, dossier = depot
    id_sauvegarde = sauvegarde.creer_sauvegarde()["id"]
    copie = dossier / id_sauvegarde / "memory.db"
    copie.write_bytes(b"x" * 8192)

    rapport = sauvegarde.verifier_sauvegarde(id_sauvegarde)

    assert rapport["ok"] is False
    memoire = next(f for f in rapport["fichiers"] if f["nom"] == "memory.db")
    assert memoire["integrite"] == "ko"


@pytest.mark.parametrize("mauvais", ["../x", "..", "20261101_043000/../..", "abc", "", "20261101"])
def test_verifier_refuse_un_identifiant_hors_motif(depot, mauvais):
    with pytest.raises(sauvegarde.ErreurSauvegarde):
        sauvegarde.verifier_sauvegarde(mauvais)


def test_verifier_sauvegarde_inconnue(depot):
    with pytest.raises(sauvegarde.ErreurSauvegarde, match="introuvable"):
        sauvegarde.verifier_sauvegarde("20200101_000000")


# ── Inventaire ─────────────────────────────────────────────────────────────

def test_inventaire_forme_et_ordre(depot):
    _racine, dossier = depot
    sauvegarde.creer_sauvegarde()
    dernier = sauvegarde.creer_sauvegarde()

    etat = sauvegarde.etat_sauvegardes()

    assert set(etat) == {"dossier", "retention", "sauvegardes", "derniere"}
    assert etat["dossier"] == str(dossier)
    assert etat["retention"] == 14
    assert [s["id"] for s in etat["sauvegardes"]][0] == dernier["id"]
    assert etat["derniere"] == dernier["cree_le"]
    entree = etat["sauvegardes"][0]
    assert {"id", "cree_le", "taille_octets", "fichiers", "commit"} <= set(entree)
    assert set(entree["fichiers"][0]) == {"nom", "taille_octets", "integrite"}   # pas de sha256 exposé


def test_inventaire_dossier_absent_dit_aucune_sauvegarde(depot):
    _racine, dossier = depot
    assert not dossier.exists()
    etat = sauvegarde.etat_sauvegardes()
    assert etat["sauvegardes"] == [] and etat["derniere"] is None


def test_manifeste_illisible_est_liste_comme_invalide(depot):
    _racine, dossier = depot
    id_sauvegarde = sauvegarde.creer_sauvegarde()["id"]
    (dossier / id_sauvegarde / "manifeste.json").write_text("{pas du json", encoding="utf-8")

    etat = sauvegarde.etat_sauvegardes()

    assert etat["sauvegardes"][0]["valide"] is False
    assert etat["derniere"] is None     # une copie invalide n'est pas « la dernière sauvegarde »


# ── CLI ────────────────────────────────────────────────────────────────────

@pytest.fixture
def cli(monkeypatch):
    # Ne jamais charger le vrai .env dans le process de test.
    monkeypatch.setattr("core.env_bootstrap.bootstrap_env", lambda *a, **k: False)


def test_cli_creer_lister_verifier(depot, cli, capsys):
    assert sauvegarde.main([]) == 0
    sortie = capsys.readouterr().out
    assert "Sauvegarde " in sortie

    assert sauvegarde.main(["--lister"]) == 0
    sortie = capsys.readouterr().out
    id_sauvegarde = sauvegarde.lister_sauvegardes()[0]["id"]
    assert id_sauvegarde in sortie

    assert sauvegarde.main(["--verifier", id_sauvegarde]) == 0
    assert "Sauvegarde saine" in capsys.readouterr().out


def test_cli_code_de_sortie_sur_anomalie_et_sur_erreur(depot, cli, capsys):
    _racine, dossier = depot
    sauvegarde.main([])
    id_sauvegarde = sauvegarde.lister_sauvegardes()[0]["id"]
    (dossier / id_sauvegarde / "config.json").write_text("altere", encoding="utf-8")

    assert sauvegarde.main(["--verifier", id_sauvegarde]) == 1
    assert "ALTÉRÉE" in capsys.readouterr().out
    assert sauvegarde.main(["--verifier", "20200101_000000"]) == 2
    assert "Échec" in capsys.readouterr().err


# ── Routes HTTP ────────────────────────────────────────────────────────────

@pytest.fixture
def client(depot):
    from api.routes.sauvegardes import router

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_auth)])
    return TestClient(app)


def test_route_exige_l_authentification(client):
    assert client.get("/api/sauvegardes").status_code == 401
    assert client.post("/api/sauvegardes").status_code == 401


def test_route_get_vide_puis_post_201_puis_get(client, auth_headers, depot):
    _racine, dossier = depot

    r = client.get("/api/sauvegardes", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"dossier": str(dossier), "retention": 14, "sauvegardes": [], "derniere": None}

    r = client.post("/api/sauvegardes", headers=auth_headers)
    assert r.status_code == 201
    entree = r.json()
    assert {"id", "cree_le", "taille_octets", "fichiers", "commit"} <= set(entree)
    assert any(f["nom"] == "memory.db" and f["integrite"] == "ok" for f in entree["fichiers"])

    r = client.get("/api/sauvegardes", headers=auth_headers)
    corps = r.json()
    assert [s["id"] for s in corps["sauvegardes"]] == [entree["id"]]
    assert corps["derniere"] == entree["cree_le"]


def test_route_post_409_si_deja_en_cours(client, auth_headers, depot):
    _racine, dossier = depot
    dossier.mkdir(parents=True, exist_ok=True)
    with FileLock(str(dossier / sauvegarde.NOM_VERROU)):
        r = client.post("/api/sauvegardes", headers=auth_headers)
    assert r.status_code == 409
    assert "déjà en cours" in r.json()["detail"]


def test_route_post_503_si_dossier_inaccessible(client, auth_headers, depot, tmp_path, monkeypatch):
    fichier = tmp_path / "pas_un_dossier"
    fichier.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MOTEUR_SAUVEGARDE_DIR", str(fichier / "sous"))

    r = client.post("/api/sauvegardes", headers=auth_headers)

    assert r.status_code == 503
    assert "inaccessible" in r.json()["detail"]


def test_la_reponse_http_ne_contient_aucun_secret(client, auth_headers, depot):
    r = client.post("/api/sauvegardes", headers=auth_headers)
    assert FAUX_SECRET not in r.text
    assert FAUX_SECRET not in client.get("/api/sauvegardes", headers=auth_headers).text


def test_meme_dans_la_liste_un_nom_secret_n_est_jamais_copie(depot, monkeypatch):
    """Garde-fou de dernier recours : ajouter `.env` à la liste par erreur ne le copie pas."""
    racine, dossier = depot
    monkeypatch.setattr(sauvegarde, "FICHIERS_CONFIG", (".env", "google_token.json", "config.json"))

    manifeste = sauvegarde.creer_sauvegarde()

    noms = {f["nom"] for f in manifeste["fichiers"]}
    assert "config.json" in noms
    assert ".env" not in noms and "google_token.json" not in noms
    assert {".env", "google_token.json"} <= set(manifeste["exclus_secrets"])
    assert not (dossier / manifeste["id"] / ".env").exists()
